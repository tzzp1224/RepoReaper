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


def _risks_from_insight(insight: Dict[str, Any], max_items: int = 8) -> List[ScoreRisk]:
    out: List[ScoreRisk] = []
    for it in (insight.get("issue_risks") or [])[:max_items]:
        if not isinstance(it, dict):
            continue
        iid = it.get("id")
        url = it.get("url") or ""
        ref = f"issue#{iid}" if iid is not None else url
        out.append(
            ScoreRisk(
                title=str(it.get("title", "open issue"))[:200],
                reason=f"GitHub {it.get('risk_type', 'risk')} ({it.get('severity', 'unknown')}): see issue tracker",
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


async def _llm_risks_and_summary(
    file_tree: str,
    report: str,
    insight: Dict[str, Any],
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
        raw = response.choices[0].message.content.strip()
        # strip markdown fences if present
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
        data = json.loads(raw)

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
    sid = _resolve_session(session_id, repo_url)
    store = store_manager.get_store(sid)
    context = store.load_context()
    if not context or not context.get("repo_url"):
        raise ValueError(f"Session {sid} has no analyzed context. Run /analyze first.")

    global_ctx = context.get("global_context", {})
    file_tree: str = global_ctx.get("file_tree", "")
    report: str = store.get_report("en") or store.get_report("zh") or ""
    effective_repo_url: str = context.get("repo_url") or ""

    # 0. GitHub insight（§3.1，与 §4「C 接入 insight」对齐）
    insight: Dict[str, Any] = {"issue_risks": [], "recent_feats": [], "stats": {}}
    if effective_repo_url:
        insight = await fetch_issue_commit_insight(
            effective_repo_url,
            since_days=insight_since_days,
            limit=insight_limit,
        )

    # 1. 规则打分 + insight 校正
    dim_raw = _rule_based_scores(file_tree)
    dim_raw = _adjust_scores_for_insight(dim_raw, insight)
    overall_raw = _aggregate(dim_raw)

    # 2. LLM 生成 risks / summary（含 insight JSON）
    risks, summary = await _llm_risks_and_summary(file_tree, report, insight)

    # 2b. 合并 issue 风险（置前，便于阅读）
    insight_risks = _risks_from_insight(insight)
    risks = insight_risks + risks

    # 3. 收集 evidence_refs（来自 file_tree 中检测到的关键文件）
    evidence: List[str] = []
    for pattern, label in [
        (r"README(\.\w+)?", "README"),
        (r"requirements.*\.txt", "requirements.txt"),
        (r"Dockerfile", "Dockerfile"),
        (r"setup\.py|pyproject\.toml", "setup/pyproject"),
    ]:
        m = re.search(pattern, file_tree, re.IGNORECASE)
        if m:
            evidence.append(m.group(0))

    for r in risks:
        evidence.extend(r.evidence_refs)
    evidence.extend(_insight_evidence_refs(insight))
    evidence = list(dict.fromkeys(evidence))  # deduplicate, preserve order

    result = ReproScoreResult(
        overall_score=round(overall_raw * 100),
        overall_score_raw=overall_raw,
        level=ReproScoreResult.compute_level(overall_raw),
        quality_tier=ReproScoreResult.compute_tier(overall_raw),
        dimension_scores=DimensionScores(
            code_structure=dim_raw.code_structure,
            docs_quality=dim_raw.docs_quality,
            env_readiness=dim_raw.env_readiness,
            community_stability=dim_raw.community_stability,
        ),
        dimension_scores_raw=dim_raw,
        risks=risks,
        evidence_refs=evidence,
        summary=summary,
    )
    return result
