# -*- coding: utf-8 -*-
"""
Issues + Commits 洞察（契约 §3.1 data 形状）

供：
- POST /api/repo/insight/issues-commits 直接返回
- repro_score_service 融合可复现评分

HTTP 调用走 GitHubClient（成员 B 维护的客户端扩展）。
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

from app.utils.github_client import get_github_client, parse_repo_url, GitHubError

logger = logging.getLogger(__name__)

_EMPTY_STATS = {
    "issues_total_scanned": 0,
    "commits_total_scanned": 0,
    "risk_issue_count": 0,
    "recent_feat_count": 0,
}

_REPRO_PAT = re.compile(
    r"reproduc|cannot\s+repro|can\'?t\s+repro|repro\s|cuda|docker|"
    r"dependenc|pip\s|conda|environment|venv|build\s+fail|install\s+fail|"
    r"wheel|version\s+mismatch|requirements",
    re.IGNORECASE,
)
_FEAT_MSG = re.compile(r"^(feat|feature)(\(|!|:|\b)", re.IGNORECASE)


def _utc_iso_z(dt: Optional[str]) -> str:
    if not dt:
        return ""
    s = dt.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(s)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        else:
            parsed = parsed.astimezone(timezone.utc)
        return parsed.strftime("%Y-%m-%dT%H:%M:%SZ")
    except Exception:
        return dt if dt.endswith("Z") else f"{dt}Z"


def _issue_risk_entry(issue: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    title = (issue.get("title") or "")[:500]
    body = (issue.get("body") or "")[:2000]
    labels = [str(l.get("name", "")).lower() for l in issue.get("labels", []) if isinstance(l, dict)]
    text = f"{title}\n{body}\n{' '.join(labels)}"

    if not _REPRO_PAT.search(text) and not any(
        x in labels for x in ("bug", "reproducibility", "dependencies", "installation")
    ):
        return None

    if _REPRO_PAT.search(text) or "reproducibility" in labels:
        risk_type = "repro_env"
        severity = "high" if re.search(r"cuda|docker|dependenc|reproduc", text, re.I) else "medium"
    else:
        risk_type = "stability"
        severity = "high" if "critical" in labels or "blocker" in labels else "medium"

    return {
        "id": issue.get("number", 0),
        "title": title,
        "url": issue.get("html_url", ""),
        "labels": [l.get("name", "") for l in issue.get("labels", []) if isinstance(l, dict)],
        "risk_type": risk_type,
        "severity": severity,
    }


def _commit_feat_entry(c: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    commit = c.get("commit") or {}
    msg = (commit.get("message") or "").strip()
    first = msg.split("\n", 1)[0].strip()
    if not _FEAT_MSG.match(first):
        return None
    author = commit.get("author") or {}
    sha = (c.get("sha") or "")[:7]
    return {
        "sha": sha,
        "message": first[:500],
        "author": (author.get("name") or "unknown")[:120],
        "date": _utc_iso_z(author.get("date")),
        "category": "feature",
    }


def build_insight_payload(
    raw_issues: List[Dict[str, Any]],
    raw_commits: List[Dict[str, Any]],
    *,
    limit_issues: int,
    limit_commits: int,
) -> Dict[str, Any]:
    """将 GitHub 原始 JSON 转为 §3.1 的 data 对象。"""
    issue_risks: List[Dict[str, Any]] = []
    for issue in raw_issues[:limit_issues]:
        row = _issue_risk_entry(issue)
        if row:
            issue_risks.append(row)

    recent_feats: List[Dict[str, Any]] = []
    for c in raw_commits[:limit_commits]:
        row = _commit_feat_entry(c)
        if row:
            recent_feats.append(row)

    return {
        "issue_risks": issue_risks,
        "recent_feats": recent_feats,
        "stats": {
            "issues_total_scanned": len(raw_issues),
            "commits_total_scanned": len(raw_commits),
            "risk_issue_count": len(issue_risks),
            "recent_feat_count": len(recent_feats),
        },
    }


def _empty_payload(*, degraded: bool = False, upstream_error: Optional[str] = None) -> Dict[str, Any]:
    """构造空结构，附带失败语义标记。"""
    out: Dict[str, Any] = {
        "issue_risks": [],
        "recent_feats": [],
        "stats": dict(_EMPTY_STATS),
        "degraded": degraded,
    }
    if upstream_error is not None:
        out["_upstream_error"] = upstream_error
    return out


async def fetch_issue_commit_insight(
    repo_url: str,
    since_days: int = 90,
    limit: int = 100,
) -> Dict[str, Any]:
    """
    拉取并结构化 Issues + Commits 洞察。

    Returns:
        与 §3.1 `data` 字段同结构的 dict。
        额外字段:
        - ``degraded`` (bool): True 表示上游部分/完全不可用，数据可能不完整。
        - ``_upstream_error`` (str, 仅失败时存在): 内部标记，路由层据此返回
          ``UPSTREAM_UNAVAILABLE`` 错误而非 ``success``；正常路径不包含此字段。
    """
    parsed = parse_repo_url(repo_url)
    if not parsed:
        logger.warning("issue_commit_insight: invalid repo_url")
        return _empty_payload(upstream_error="invalid repo_url")

    since_days = max(1, min(since_days, 365))
    limit = max(1, min(limit, 500))

    since_dt = datetime.now(timezone.utc) - timedelta(days=since_days)
    since_str = since_dt.strftime("%Y-%m-%dT%H:%M:%SZ")

    try:
        client = get_github_client()
        owner, name = parsed
        repo = await client.get_repo(owner, name)
        raw_issues = await client.list_repo_issues_open(
            repo, since=since_str, per_page=100, max_items=limit
        )
        raw_commits = await client.list_repo_commits(
            repo, since=since_str, per_page=100, max_items=limit
        )
        result = build_insight_payload(
            raw_issues,
            raw_commits,
            limit_issues=limit,
            limit_commits=limit,
        )
        result["degraded"] = False
        return result
    except GitHubError as e:
        logger.warning("issue_commit_insight GitHubError: %s", e)
        return _empty_payload(upstream_error=f"GitHub API error: {e.message}")
    except Exception as e:
        logger.warning("issue_commit_insight failed: %s", e, exc_info=True)
        return _empty_payload(upstream_error=f"Unexpected error: {e}")
