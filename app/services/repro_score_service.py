# -*- coding: utf-8 -*-
"""
可复现性评分服务  (Owner: 成员 C)

职责:
- 读取 session 已有上下文 (file_tree / report / summary)
- 规则层：检测关键文件→四维度打分
- LLM 层：生成 risks 列表与 summary 文本
- 输出 ReproScoreResult (与 §3.2 对齐)

不负责:
- GitHub Issues/Commits 的底层 API 客户端实现 (见 github_client；抽取编排在本仓库 issue_commit_insight_service)
- 路由注册 (成员 A)
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional

from app.core.config import settings
from app.schemas.repro import (
    DimensionScores,
    ReproScoreResult,
    ScoreRisk,
)
from app.services.vector_service import store_manager
from app.services.issue_commit_insight_service import fetch_issue_commit_insight
from app.utils.llm_client import get_client
from app.utils.session import generate_repo_session_id

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------
# 规则层：关键文件 / 模式 → 维度分数 (0-1)
# ------------------------------------------------------------------

_CODE_STRUCTURE_SIGNALS: List[Dict[str, Any]] = [
    {"pattern": r"(src|lib|app|pkg|cmd)/", "weight": 0.25, "label": "organised src dir"},
    {"pattern": r"(main|index|__main__)\.(py|ts|js|go|rs)$", "weight": 0.20, "label": "entry point"},
    {"pattern": r"tests?/|_test\.(py|go|rs)$|\.test\.(ts|js)$", "weight": 0.20, "label": "test dir"},
    {"pattern": r"(Makefile|justfile|taskfile\.yml)$", "weight": 0.15, "label": "build script"},
    {"pattern": r"\.github/workflows/", "weight": 0.10, "label": "CI workflow"},
    {"pattern": r"(setup\.py|pyproject\.toml|Cargo\.toml|go\.mod|package\.json|pom\.xml)$",
     "weight": 0.10, "label": "manifest"},
]

_DOCS_SIGNALS: List[Dict[str, Any]] = [
    {"pattern": r"README(\.\w+)?$", "weight": 0.35, "label": "README"},
    {"pattern": r"(CONTRIBUTING|CHANGELOG|HISTORY)(\.\w+)?$", "weight": 0.15, "label": "contrib/changelog"},
    {"pattern": r"(docs?|documentation)/", "weight": 0.20, "label": "docs dir"},
    {"pattern": r"LICENSE(\.\w+)?$", "weight": 0.15, "label": "LICENSE"},
    {"pattern": r"(examples?|tutorials?|notebooks?)/", "weight": 0.15, "label": "examples"},
]

_ENV_SIGNALS: List[Dict[str, Any]] = [
    {"pattern": r"(requirements.*\.txt|Pipfile|poetry\.lock|setup\.cfg|pyproject\.toml|go\.sum|Cargo\.lock|package-lock\.json|yarn\.lock|pnpm-lock\.yaml)$",
     "weight": 0.30, "label": "lock / deps"},
    {"pattern": r"Dockerfile|docker-compose", "weight": 0.25, "label": "Docker"},
    {"pattern": r"\.env\.example|\.env\.sample", "weight": 0.15, "label": ".env template"},
    {"pattern": r"(Makefile|scripts/.*\.(sh|bash))$", "weight": 0.15, "label": "setup scripts"},
    {"pattern": r"(setup\.py|pyproject\.toml|Cargo\.toml|go\.mod|package\.json)$",
     "weight": 0.15, "label": "manifest (env)"},
]

# community_stability 规则层；§4 要求再融合 GitHub insight（issue_commit_insight_service）
_COMMUNITY_SIGNALS: List[Dict[str, Any]] = [
    {"pattern": r"\.github/(ISSUE_TEMPLATE|PULL_REQUEST_TEMPLATE)", "weight": 0.25, "label": "issue/PR template"},
    {"pattern": r"(CONTRIBUTING|CODE_OF_CONDUCT)(\.\w+)?$", "weight": 0.25, "label": "community docs"},
    {"pattern": r"\.github/workflows/", "weight": 0.20, "label": "CI (community)"},
    {"pattern": r"SECURITY(\.\w+)?$", "weight": 0.15, "label": "security policy"},
    {"pattern": r"(CHANGELOG|HISTORY)(\.\w+)?$", "weight": 0.15, "label": "changelog"},
]


def _score_dimension(file_tree: str, signals: List[Dict[str, Any]]) -> float:
    """按信号权重叠加打分，命中一个信号即得该 weight，最终 clamp 到 [0, 1]。"""
    score = 0.0
    for sig in signals:
        if re.search(sig["pattern"], file_tree, re.IGNORECASE | re.MULTILINE):
            score += sig["weight"]
    return min(score, 1.0)


def _rule_based_scores(file_tree: str) -> DimensionScores:
    return DimensionScores(
        code_structure=_score_dimension(file_tree, _CODE_STRUCTURE_SIGNALS),
        docs_quality=_score_dimension(file_tree, _DOCS_SIGNALS),
        env_readiness=_score_dimension(file_tree, _ENV_SIGNALS),
        community_stability=_score_dimension(file_tree, _COMMUNITY_SIGNALS),
    )


def _aggregate(dim: DimensionScores) -> float:
    """四维度等权平均 → overall_score_raw (0-1)。"""
    values = [dim.code_structure, dim.docs_quality, dim.env_readiness, dim.community_stability]
    return sum(values) / len(values)


def _adjust_scores_for_insight(dim: DimensionScores, insight: Dict[str, Any]) -> DimensionScores:
    """根据 §3.1 insight 下调 community / env（开放风险 issue 越多惩罚越大，有上限）。"""
    stats = insight.get("stats") or {}
    risks_n = int(stats.get("risk_issue_count", 0) or 0)
    scanned = int(stats.get("issues_total_scanned", 0) or 0)
    high_repro = sum(
        1
        for it in insight.get("issue_risks", [])
        if isinstance(it, dict)
        and it.get("risk_type") == "repro_env"
        and str(it.get("severity", "")).lower() == "high"
    )

    risk_rate = (risks_n / scanned) if scanned else 0.0
    comm_penalty = min(0.38, risks_n * 0.045 + risk_rate * 0.22)
    env_penalty = min(0.28, high_repro * 0.07 + risks_n * 0.025)

    return DimensionScores(
        code_structure=dim.code_structure,
        docs_quality=dim.docs_quality,
        env_readiness=max(0.0, dim.env_readiness - env_penalty),
        community_stability=max(0.0, dim.community_stability - comm_penalty),
    )


def _risks_from_insight(
    insight: Dict[str, Any],
    language: str,
    max_items: int = 8,
) -> List[ScoreRisk]:
    out: List[ScoreRisk] = []
    is_zh = language == "zh"
    for it in (insight.get("issue_risks") or [])[:max_items]:
        if not isinstance(it, dict):
            continue
        iid = it.get("id")
        url = it.get("url") or ""
        ref = f"issue#{iid}" if iid is not None else url
        risk_type = str(it.get("risk_type", "risk"))
        severity = str(it.get("severity", "unknown"))
        reason = (
            f"GitHub {risk_type} ({severity}): see issue tracker"
            if not is_zh
            else f"GitHub {risk_type}（{severity}）：请查看 issue 追踪"
        )
        out.append(
            ScoreRisk(
                title=str(it.get("title", "open issue"))[:200],
                reason=reason,
                evidence_refs=[ref] if ref else [],
            )
        )
    return out


def _insight_evidence_refs(insight: Dict[str, Any], max_each: int = 15) -> List[str]:
    refs: List[str] = []
    for it in (insight.get("issue_risks") or [])[:max_each]:
        if isinstance(it, dict) and it.get("id") is not None:
            refs.append(f"issue#{it['id']}")
    for ft in (insight.get("recent_feats") or [])[:max_each]:
        if isinstance(ft, dict) and ft.get("sha"):
            refs.append(f"commit:{ft['sha']}")
    return refs


# ------------------------------------------------------------------
# LLM 层：生成 risks + summary
# ------------------------------------------------------------------

_RISK_PROMPT = """\
{language_instruction}
You are a reproducibility auditor. Given the repository file tree, an analysis report, \
and optional GitHub issue/commit signals (JSON), identify **reproducibility risks** and write a brief summary.

## File Tree
{file_tree}

## Analysis Report (truncated)
{report}

## GitHub Insight (issues/commits summary, may be empty)
{insight_json}

## Instructions
Return **valid JSON only** (no markdown fences). Schema:
{{
  "risks": [
    {{
      "title": "short title",
      "reason": "why this is a risk",
      "evidence_refs": ["file or issue ref"]
    }}
  ],
  "summary": "1-2 sentence overall reproducibility assessment"
}}
If no risks, return {{"risks": [], "summary": "..."}}.
"""


def _strip_json_fences(raw: str) -> str:
    text = (raw or "").strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    return text


def _language_instruction(language: str) -> str:
    return "请用中文输出。" if language == "zh" else "Please respond in English."


async def _llm_risks_and_summary(
    file_tree: str,
    report: str,
    insight: Dict[str, Any],
    language: str,
) -> tuple[List[ScoreRisk], str]:
    """Call LLM to generate risk list and summary."""
    client = get_client()
    if not client:
        logger.warning("LLM client unavailable; skipping risk analysis")
        return [], "LLM unavailable – rule-based score only."

    try:
        insight_json = json.dumps(insight, ensure_ascii=False)[:3500]
    except Exception:
        insight_json = "{}"

    prompt = _RISK_PROMPT.format(
        language_instruction=_language_instruction(language),
        file_tree=file_tree[:6000],
        report=report[:4000],
        insight_json=insight_json,
    )

    try:
        response = await client.chat.completions.create(
            model=settings.default_model_name,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            max_tokens=1024,
            stream=False,
        )
        data = json.loads(_strip_json_fences(response.choices[0].message.content))

        risks = [
            ScoreRisk(
                title=r.get("title", ""),
                reason=r.get("reason", ""),
                evidence_refs=r.get("evidence_refs", []),
            )
            for r in data.get("risks", [])
        ]
        summary = data.get("summary", "")
        return risks, summary

    except Exception as e:
        logger.error("LLM risk analysis failed: %s", e, exc_info=True)
        return [], "Risk analysis unavailable due to LLM error."


_LOCALIZE_SCORE_PROMPT = """\
Translate the following reproducibility score payload to {target_language}.
Preserve technical meaning and output valid JSON only.

Input JSON:
{source_json}

Output schema:
{{
  "summary": "...",
  "risks": [
    {{"title": "...", "reason": "...", "evidence_refs": ["..."]}}
  ]
}}
"""


async def _localize_score_payload(
    source_payload: Dict[str, Any],
    target_language: str,
) -> Optional[Dict[str, Any]]:
    if not source_payload:
        return None
    client = get_client()
    if not client:
        return None

    try:
        prompt = _LOCALIZE_SCORE_PROMPT.format(
            target_language="Chinese" if target_language == "zh" else "English",
            source_json=json.dumps(source_payload, ensure_ascii=False)[:6000],
        )
        response = await client.chat.completions.create(
            model=settings.default_model_name,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            max_tokens=1024,
            stream=False,
        )
        data = json.loads(_strip_json_fences(response.choices[0].message.content))
        risks = data.get("risks", [])
        if not isinstance(risks, list):
            risks = []
        return {
            "summary": str(data.get("summary", "")),
            "risks": [
                {
                    "title": str(r.get("title", "")),
                    "reason": str(r.get("reason", "")),
                    "evidence_refs": list(r.get("evidence_refs", [])) if isinstance(r, dict) else [],
                }
                for r in risks
                if isinstance(r, dict)
            ],
        }
    except Exception as e:
        logger.warning("score localization failed: %s", e)
        return None


def _build_core_payload(
    dim_raw: DimensionScores,
    overall_raw: float,
    evidence_refs: List[str],
) -> Dict[str, Any]:
    return {
        "overall_score": round(overall_raw * 100),
        "overall_score_raw": overall_raw,
        "level": ReproScoreResult.compute_level(overall_raw),
        "quality_tier": ReproScoreResult.compute_tier(overall_raw),
        "dimension_scores_raw": {
            "code_structure": dim_raw.code_structure,
            "docs_quality": dim_raw.docs_quality,
            "env_readiness": dim_raw.env_readiness,
            "community_stability": dim_raw.community_stability,
        },
        "evidence_refs": evidence_refs,
    }


def _extract_evidence_refs(
    file_tree: str,
    risks: List[ScoreRisk],
    insight: Dict[str, Any],
) -> List[str]:
    evidence: List[str] = []
    for pattern in [
        r"README(\.\w+)?",
        r"requirements.*\.txt",
        r"Dockerfile",
        r"setup\.py|pyproject\.toml",
    ]:
        m = re.search(pattern, file_tree, re.IGNORECASE)
        if m:
            evidence.append(m.group(0))

    for r in risks:
        evidence.extend(r.evidence_refs)
    evidence.extend(_insight_evidence_refs(insight))
    return list(dict.fromkeys(evidence))


def _build_result_from_cached(
    core_data: Dict[str, Any],
    localized_data: Dict[str, Any],
    language: str,
    cache_hit: bool,
) -> ReproScoreResult:
    dim_src = core_data.get("dimension_scores_raw", {}) or {}
    dim_raw = DimensionScores(
        code_structure=float(dim_src.get("code_structure", 0.0) or 0.0),
        docs_quality=float(dim_src.get("docs_quality", 0.0) or 0.0),
        env_readiness=float(dim_src.get("env_readiness", 0.0) or 0.0),
        community_stability=float(dim_src.get("community_stability", 0.0) or 0.0),
    )

    risks: List[ScoreRisk] = []
    for item in (localized_data.get("risks") or []):
        if not isinstance(item, dict):
            continue
        risks.append(
            ScoreRisk(
                title=str(item.get("title", "")),
                reason=str(item.get("reason", "")),
                evidence_refs=list(item.get("evidence_refs", [])) if isinstance(item.get("evidence_refs", []), list) else [],
            )
        )

    overall_raw = float(core_data.get("overall_score_raw", _aggregate(dim_raw)))
    return ReproScoreResult(
        overall_score=int(core_data.get("overall_score", round(overall_raw * 100))),
        overall_score_raw=overall_raw,
        level=str(core_data.get("level", ReproScoreResult.compute_level(overall_raw))),
        quality_tier=str(core_data.get("quality_tier", ReproScoreResult.compute_tier(overall_raw))),
        dimension_scores=dim_raw,
        dimension_scores_raw=dim_raw,
        risks=risks,
        evidence_refs=list(core_data.get("evidence_refs", [])),
        summary=str(localized_data.get("summary", "")),
        language=language,
        cache_hit=cache_hit,
    )


# ------------------------------------------------------------------
# 公开 API
# ------------------------------------------------------------------

def _resolve_session(
    session_id: Optional[str],
    repo_url: Optional[str],
) -> str:
    """从 session_id 或 repo_url 推导出有效 session_id。"""
    if session_id:
        return session_id
    if repo_url:
        return generate_repo_session_id(repo_url)
    raise ValueError("session_id 和 repo_url 至少提供一个")


async def compute_repro_score(
    session_id: Optional[str] = None,
    repo_url: Optional[str] = None,
    *,
    language: str = "en",
    force: bool = False,
    insight_since_days: int = 90,
    insight_limit: int = 100,
) -> ReproScoreResult:
    """
    计算仓库可复现性评分 (§3.2)。

    Args:
        session_id: 仓库 session（优先）
        repo_url:   仓库 URL（备选，可用于推导 session_id）
        insight_since_days: 拉取 GitHub insight 的时间窗（天），默认 90，范围 1–365
        insight_limit: 单次拉取的 issue/commit 条数上限，默认 100，范围 1–500

    Returns:
        ReproScoreResult

    Raises:
        ValueError: session 无上下文（仓库未分析）
    """
    target_language = "zh" if language == "zh" else "en"
    sid = _resolve_session(session_id, repo_url)
    store = store_manager.get_store(sid)
    context = store.load_context()
    if not context or not context.get("repo_url"):
        raise ValueError(f"Session {sid} has no analyzed context. Run /analyze first.")

    core_cached_entry = store.get_score_core()
    localized_cached_entry = store.get_score_localized(target_language)
    core_cached = core_cached_entry.get("data", {}) if isinstance(core_cached_entry, dict) else {}
    localized_cached = localized_cached_entry.get("data", {}) if isinstance(localized_cached_entry, dict) else {}

    if not force and core_cached and localized_cached:
        return _build_result_from_cached(
            core_data=core_cached,
            localized_data=localized_cached,
            language=target_language,
            cache_hit=True,
        )

    # 语言缺失时优先从其他语言缓存重建（不重算核心分数）
    if not force and core_cached and not localized_cached:
        for source_lang in store.get_score_localized_languages():
            if source_lang == target_language:
                continue
            source_entry = store.get_score_localized(source_lang)
            source_payload = source_entry.get("data", {}) if isinstance(source_entry, dict) else {}
            if not source_payload:
                continue
            rebuilt = await _localize_score_payload(source_payload, target_language)
            if rebuilt:
                await store.save_score_localized(target_language, rebuilt)
                return _build_result_from_cached(
                    core_data=core_cached,
                    localized_data=rebuilt,
                    language=target_language,
                    cache_hit=True,
                )

    global_ctx = context.get("global_context", {})
    file_tree: str = global_ctx.get("file_tree", "")
    report: str = store.get_report("en") or store.get_report("zh") or ""
    effective_repo_url: str = context.get("repo_url") or ""

    # 0. GitHub insight（§3.1，与 §4「C 接入 insight」对齐）
    #    _upstream_error 存在时说明 GitHub 不可达，评分降级：仅用规则+报告，不惩罚维度。
    insight: Dict[str, Any] = {"issue_risks": [], "recent_feats": [], "stats": {}, "degraded": True}
    if effective_repo_url:
        insight = await fetch_issue_commit_insight(
            effective_repo_url,
            since_days=insight_since_days,
            limit=insight_limit,
        )
    insight_degraded = bool(insight.get("degraded") or insight.get("_upstream_error"))

    # 1. 规则打分 + insight 校正（降级时跳过 insight 惩罚）
    dim_raw = _rule_based_scores(file_tree)
    if not insight_degraded:
        dim_raw = _adjust_scores_for_insight(dim_raw, insight)
    overall_raw = _aggregate(dim_raw)

    # 2. LLM 生成 risks / summary（含 insight JSON）
    llm_risks, summary = await _llm_risks_and_summary(file_tree, report, insight, target_language)

    # 2b. 合并 issue 风险（置前，便于阅读）
    insight_risks = _risks_from_insight(insight, target_language)
    risks = insight_risks + llm_risks

    # 3. 收集 evidence_refs + 持久化 core/localized
    evidence_refs = _extract_evidence_refs(file_tree, risks, insight)
    core_payload = _build_core_payload(dim_raw, overall_raw, evidence_refs)
    localized_payload = {
        "summary": summary,
        "risks": [
            {
                "title": r.title,
                "reason": r.reason,
                "evidence_refs": r.evidence_refs,
            }
            for r in risks
        ],
    }
    await store.save_score_core(core_payload)
    await store.save_score_localized(target_language, localized_payload)

    result = _build_result_from_cached(
        core_data=core_payload,
        localized_data=localized_payload,
        language=target_language,
        cache_hit=False,
    )
    return result
