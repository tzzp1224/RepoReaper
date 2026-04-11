# -*- coding: utf-8 -*-
"""
论文-代码对齐服务  (Owner: 成员 C)

职责:
- 从 paper_text 拆分可验证 claims
- 利用向量检索 (search_hybrid) 在已索引仓库中查找代码证据
- LLM 判定 aligned / partial / missing
- 输出 PaperAlignResult (与 §3.3 对齐)

不负责:
- GitHub Issues/Commits 抽取 (成员 B)
- 路由注册 (成员 A)
"""

from __future__ import annotations

import json
import logging
import re
from typing import Dict, List, Optional

from app.core.config import settings
from app.schemas.repro import (
    AlignmentItem,
    MissingClaim,
    PaperAlignResult,
)
from app.services.vector_service import store_manager
from app.utils.llm_client import get_client
from app.utils.session import generate_repo_session_id

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------
# Step 1: LLM 拆 claim
# ------------------------------------------------------------------

_EXTRACT_CLAIMS_PROMPT = """\
You are a research paper analyst. Given a paper excerpt, extract a list of \
**verifiable technical claims** that could be checked against a code repository.

Focus on claims about:
- Algorithms, models, architectures
- Training/evaluation pipelines
- Data processing steps
- Specific techniques or optimizations

## Paper Text
{paper_text}

## Instructions
Return **valid JSON only** (no markdown fences). Schema:
{{
  "claims": [
    "claim text 1",
    "claim text 2"
  ]
}}
Extract at most 10 claims. Each claim should be a single concise sentence.
"""


async def _extract_claims(paper_text: str) -> List[str]:
    client = get_client()
    if not client:
        logger.warning("LLM client unavailable; cannot extract claims")
        return []

    prompt = _EXTRACT_CLAIMS_PROMPT.format(paper_text=paper_text[:6000])

    try:
        response = await client.chat.completions.create(
            model=settings.default_model_name,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
            max_tokens=1024,
            stream=False,
        )
        raw = response.choices[0].message.content.strip()
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
        data = json.loads(raw)
        claims: List[str] = data.get("claims", [])
        return [c for c in claims if isinstance(c, str) and c.strip()]
    except Exception as e:
        logger.error("Claim extraction failed: %s", e, exc_info=True)
        return []


# ------------------------------------------------------------------
# Step 2: 检索 + LLM 对齐判定 (per claim)
# ------------------------------------------------------------------

_ALIGN_JUDGE_PROMPT = """\
You are a code-paper alignment judge. Determine whether the following code \
evidence supports the claim from a research paper.

## Claim
{claim}

## Code Evidence (top snippets from the repository)
{evidence}

## Instructions
Return **valid JSON only** (no markdown fences). Schema:
{{
  "status": "aligned" | "partial" | "missing",
  "matched_files": ["file1.py", ...],
  "matched_symbols": ["function_or_class_name", ...],
  "evidence_excerpt": "key code line(s) that support the claim (max 200 chars)",
  "reason": "brief justification"
}}
- "aligned": code clearly implements the claim
- "partial": some evidence but incomplete or indirect
- "missing": no supporting code found
"""


async def _judge_claim(
    claim: str,
    evidence_snippets: List[Dict],
) -> AlignmentItem | MissingClaim:
    """Use LLM to judge whether evidence supports a single claim."""
    client = get_client()
    if not client:
        return MissingClaim(claim=claim, reason="LLM unavailable")

    evidence_text = "\n---\n".join(
        f"File: {s.get('file', '?')}\n{s.get('content', '')[:800]}"
        for s in evidence_snippets[:5]
    )
    if not evidence_text.strip():
        return MissingClaim(claim=claim, reason="no code indexed or search returned nothing")

    prompt = _ALIGN_JUDGE_PROMPT.format(
        claim=claim,
        evidence=evidence_text[:4000],
    )

    try:
        response = await client.chat.completions.create(
            model=settings.default_model_name,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
            max_tokens=512,
            stream=False,
        )
        raw = response.choices[0].message.content.strip()
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
        data = json.loads(raw)

        _VALID_STATUSES = {"aligned", "partial", "missing"}
        raw_status = data.get("status", "missing")
        status = raw_status if raw_status in _VALID_STATUSES else "missing"

        if status == "missing":
            return MissingClaim(
                claim=claim,
                reason=data.get("reason", "no direct implementation evidence found"),
            )
        return AlignmentItem(
            claim=claim,
            status=status,
            matched_files=data.get("matched_files", []),
            matched_symbols=data.get("matched_symbols", []),
            evidence_excerpt=data.get("evidence_excerpt", "")[:300],
        )
    except Exception as e:
        logger.error("Alignment judge failed for claim '%s': %s", claim[:60], e)
        return MissingClaim(claim=claim, reason=f"LLM judge error: {e}")


# ------------------------------------------------------------------
# 公开 API
# ------------------------------------------------------------------

def _resolve_session(
    session_id: Optional[str],
    repo_url: Optional[str],
) -> str:
    if session_id:
        return session_id
    if repo_url:
        return generate_repo_session_id(repo_url)
    raise ValueError("session_id 或 repo_url 至少提供一个")


async def compute_paper_alignment(
    paper_text: str,
    session_id: Optional[str] = None,
    repo_url: Optional[str] = None,
    top_k: int = 5,
) -> PaperAlignResult:
    """
    论文-代码对齐 (§3.3)。

    Args:
        paper_text: 论文 / 方法描述文本
        session_id: 仓库 session
        repo_url:   备选，推导 session_id
        top_k:      每条 claim 检索的代码片段数

    Returns:
        PaperAlignResult
    """
    if not paper_text or not paper_text.strip():
        raise ValueError("paper_text is required")
    top_k = max(1, min(top_k, 20))

    sid = _resolve_session(session_id, repo_url)
    store = store_manager.get_store(sid)
    context = store.load_context()
    if not context or not context.get("repo_url"):
        raise ValueError(f"Session {sid} has no analyzed context. Run /analyze first.")

    # Step 1: 拆 claim
    claims = await _extract_claims(paper_text)
    if not claims:
        return PaperAlignResult(
            missing_claims=[MissingClaim(claim="(entire paper)", reason="failed to extract claims")],
            confidence=0.0,
        )

    # Step 2 & 3: 逐条检索 + 判定
    alignment_items: List[AlignmentItem] = []
    missing_claims: List[MissingClaim] = []

    for claim in claims:
        snippets = await store.search_hybrid(claim, top_k=top_k)
        result = await _judge_claim(claim, snippets)
        if isinstance(result, AlignmentItem):
            alignment_items.append(result)
        else:
            missing_claims.append(result)

    # confidence = aligned 占比 (aligned=1, partial=0.5, missing=0)
    total = len(claims) or 1
    aligned_score = sum(
        1.0 if a.status == "aligned" else 0.5
        for a in alignment_items
    )
    confidence = round(aligned_score / total, 4)

    return PaperAlignResult(
        alignment_items=alignment_items,
        missing_claims=missing_claims,
        confidence=confidence,
    )
