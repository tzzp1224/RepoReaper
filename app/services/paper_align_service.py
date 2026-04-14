# -*- coding: utf-8 -*-
"""
论文-代码对齐服务  (Owner: 成员 C)

职责:
- 从 paper_text 拆分可验证 claims
- 利用向量检索 (search_hybrid) 在已索引仓库中查找代码证据
- LLM 判定 aligned / partial / missing
- 输出 PaperAlignResult (与 §3.3 对齐)
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime, timezone
from typing import Any, AsyncGenerator, Awaitable, Callable, Dict, List, Optional, Set, Tuple

from app.core.config import settings
from app.schemas.repro import (
    AlignmentItem,
    MissingClaim,
    PaperAlignResult,
)
from app.services.chunking_service import ChunkingConfig, UniversalChunker
from app.services.github_service import get_file_content
from app.services.vector_service import store_manager
from app.utils.llm_client import get_client
from app.utils.session import generate_repo_session_id

logger = logging.getLogger(__name__)

EventCallback = Optional[Callable[[Dict[str, Any]], Awaitable[None]]]


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _event_payload(event: Dict[str, Any]) -> Dict[str, Any]:
    payload = dict(event or {})
    payload.setdefault("timestamp", _now_iso())
    return payload


async def _emit(event_cb: EventCallback, event: Dict[str, Any]) -> None:
    if event_cb:
        await event_cb(_event_payload(event))


def _strip_json_fences(raw: str) -> str:
    text = (raw or "").strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    return text


def _int_or_none(value: Any) -> Optional[int]:
    try:
        if value is None:
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _build_evidence_spans(evidence_snippets: List[Dict[str, Any]], top_k: int) -> List[Dict[str, Any]]:
    spans: List[Dict[str, Any]] = []
    for item in evidence_snippets[:top_k]:
        content = str(item.get("content", "") or "")
        if not content.strip():
            continue
        metadata = item.get("metadata") or {}
        start_line = _int_or_none(metadata.get("start_line"))
        end_line = _int_or_none(metadata.get("end_line"))
        if start_line is not None and end_line is None:
            end_line = max(start_line, start_line + content.count("\n"))

        spans.append(
            {
                "file": str(item.get("file", "") or ""),
                "start_line": start_line,
                "end_line": end_line,
                "snippet": content[:500],
                "score": round(float(item.get("score", 0.0) or 0.0), 6),
                "source_query": None,
            }
        )
    return spans


_TOKEN_RE = re.compile(r"[a-zA-Z0-9_]+|[\u4e00-\u9fff]{1,8}")


def _claim_tokens(text: str) -> List[str]:
    tokens = [t.lower() for t in _TOKEN_RE.findall(text or "")]
    stop = {
        "the", "and", "for", "with", "that", "this", "from", "into", "using", "use",
        "在", "对", "的", "与", "和", "并", "进行", "用于", "通过", "实现", "其他",
    }
    return [t for t in tokens if len(t) > 1 and t not in stop]


def _semantic_equiv_bonus(claim: str, path_lower: str) -> float:
    claim_lower = (claim or "").lower()
    bonus = 0.0

    if any(k in claim_lower for k in ["regex", "正则", "pattern"]):
        if any(k in path_lower for k in ["regex", "parser", "chunk", "token", "lex"]):
            bonus += 2.0
    if any(k in claim_lower for k in ["split", "chunk", "tokenize", "切分", "分块", "分词"]):
        if any(k in path_lower for k in ["chunk", "split", "token", "parser", "segment"]):
            bonus += 1.8
    if any(k in claim_lower for k in ["retrieval", "召回", "search", "检索"]):
        if any(k in path_lower for k in ["retriev", "search", "vector", "bm25"]):
            bonus += 1.5

    return bonus


_PATH_RE = re.compile(
    r"([A-Za-z0-9_./-]+\.(?:py|js|ts|tsx|jsx|java|go|rs|cpp|c|h|cs|php|rb|kt|scala|swift))"
)


def _extract_paths_from_file_tree(file_tree: str) -> List[str]:
    paths: List[str] = []
    seen: Set[str] = set()
    for line in (file_tree or "").splitlines():
        for match in _PATH_RE.findall(line):
            path = match.strip()
            if not path or path in seen:
                continue
            seen.add(path)
            paths.append(path)
    return paths


def _select_jit_candidates(
    file_tree: str,
    claim: str,
    excluded_paths: Set[str],
    limit: int = 3,
) -> List[str]:
    claim_toks = _claim_tokens(claim)
    candidates = _extract_paths_from_file_tree(file_tree)
    scored: List[Tuple[float, str]] = []

    for path in candidates:
        if path in excluded_paths:
            continue
        path_lower = path.lower()
        score = 0.0
        for tok in claim_toks:
            if tok in path_lower:
                score += 1.0
        score += _semantic_equiv_bonus(claim, path_lower)
        if score <= 0:
            continue
        scored.append((score, path))

    scored.sort(key=lambda x: (x[0], -len(x[1])), reverse=True)
    return [p for _, p in scored[:limit]]


async def _jit_fetch_and_index_files(store, file_paths: List[str]) -> Dict[str, Any]:
    if not file_paths:
        return {
            "requested": [],
            "indexed": [],
            "failed": [],
            "skipped": [],
            "indexed_count": 0,
        }

    chunker = UniversalChunker(config=ChunkingConfig(min_chunk_size=50))
    indexed: List[str] = []
    failed: List[str] = []
    skipped: List[str] = []

    for file_path in file_paths:
        if file_path in store.indexed_files:
            skipped.append(file_path)
            continue

        try:
            content = await get_file_content(store.repo_url, file_path)
            if not content:
                failed.append(file_path)
                continue

            chunks = await asyncio.to_thread(chunker.chunk_file, content, file_path)
            if not chunks:
                chunks = [{
                    "content": content,
                    "metadata": {
                        "file": file_path,
                        "type": "text",
                        "name": "root",
                        "class": "",
                        "start_line": 1,
                        "end_line": max(1, content.count("\n") + 1),
                    },
                }]

            documents = [c["content"] for c in chunks]
            metadatas: List[Dict[str, Any]] = []
            for c in chunks:
                meta = c.get("metadata") or {}
                metadatas.append(
                    {
                        "file": meta.get("file", file_path),
                        "type": meta.get("type", "text"),
                        "name": meta.get("name", ""),
                        "class": meta.get("class") or "",
                        "start_line": meta.get("start_line"),
                        "end_line": meta.get("end_line"),
                    }
                )

            if documents:
                await store.add_documents(documents, metadatas)
                indexed.append(file_path)
            else:
                failed.append(file_path)
        except Exception:
            logger.exception("JIT fetch/index failed for file %s", file_path)
            failed.append(file_path)

    return {
        "requested": file_paths,
        "indexed": indexed,
        "failed": failed,
        "skipped": skipped,
        "indexed_count": len(indexed),
    }


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
        data = json.loads(_strip_json_fences(response.choices[0].message.content))
        claims: List[str] = data.get("claims", [])
        return [c for c in claims if isinstance(c, str) and c.strip()]
    except Exception as e:
        logger.error("Claim extraction failed: %s", e, exc_info=True)
        return []


# ------------------------------------------------------------------
# Step 2: 检索 + LLM 对齐判定 (per claim)
# ------------------------------------------------------------------

_ALIGN_JUDGE_PROMPT = """\
You are a code-paper alignment judge. Determine whether the following code evidence supports the claim.

## Claim
{claim}

## Code Evidence (Top-{top_k} snippets from the repository)
{evidence}

## Instructions
Return **valid JSON only** (no markdown fences). Schema:
{{
  "status": "aligned" | "partial" | "missing" | "insufficient_evidence",
  "matched_files": ["file1.py", ...],
  "matched_symbols": ["function_or_class_name", ...],
  "evidence_excerpt": "key code line(s) that support the claim (max 200 chars)",
  "reason": "brief justification"
}}

Judging rules:
- "aligned": code clearly implements the claim.
- "partial": evidence is related but incomplete/indirect.
- "missing": no credible supporting implementation found.
- "insufficient_evidence": retrieval evidence is too weak/ambiguous to decide.

Semantic equivalence policy:
- Do NOT over-penalize wording or translation differences.
- If mechanism is semantically close, prefer "partial" over "missing".
- Treat terms like split/chunk/tokenize/segment and regex/pattern-matching as potentially equivalent depending on context.
"""


_REWRITE_QUERY_PROMPT = """\
You are a retrieval specialist. Rewrite one technical claim into multiple code-search-friendly queries.
Return valid JSON only.

Claim:
{claim}

JSON schema:
{{
  "queries": {{
    "keyword_compact": "short noun-phrase query",
    "implementation_view": "query asking where/how implemented in code",
    "synonym_expansion": "query using close technical synonyms"
  }}
}}
"""


async def _rewrite_claim_queries(claim: str) -> List[Tuple[str, float]]:
    """
    返回多路查询（query, weight）:
    - 原始 claim（最高权重）
    - 关键词压缩
    - 实现导向
    - 同义词扩展
    """
    routes: List[Tuple[str, float]] = [(claim.strip(), 1.0)]
    client = get_client()
    if not client:
        return routes

    try:
        prompt = _REWRITE_QUERY_PROMPT.format(claim=claim[:800])
        response = await client.chat.completions.create(
            model=settings.default_model_name,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            max_tokens=512,
            stream=False,
        )
        data = json.loads(_strip_json_fences(response.choices[0].message.content))
        q = data.get("queries", {})
        candidates: List[Tuple[str, float]] = [
            (str(q.get("keyword_compact", "")).strip(), 0.82),
            (str(q.get("implementation_view", "")).strip(), 0.78),
            (str(q.get("synonym_expansion", "")).strip(), 0.72),
        ]
        seen = {claim.strip().lower()}
        for text, weight in candidates:
            if not text:
                continue
            key = text.lower()
            if key in seen:
                continue
            seen.add(key)
            routes.append((text, weight))
    except Exception as e:
        logger.warning("claim query rewrite failed: %s", e)

    return routes


async def _retrieve_claim_evidence(
    store,
    claim: str,
    top_k: int,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """
    多路检索 + 融合:
    1) claim 原句
    2) 关键词压缩
    3) 实现导向
    4) 同义词扩展
    """
    routes = await _rewrite_claim_queries(claim)
    route_top_k = max(3, min(12, top_k))
    score_map: Dict[str, Dict[str, Any]] = {}
    hits_per_query: Dict[str, int] = {}

    for query, weight in routes:
        results = await store.search_hybrid(query, top_k=route_top_k)
        hits_per_query[query] = len(results)
        for rank, item in enumerate(results):
            doc_id = str(item.get("id") or f"{item.get('file')}::{rank}")
            bonus = weight / (rank + 1)
            if doc_id not in score_map:
                score_map[doc_id] = {"score": 0.0, "item": item}
            score_map[doc_id]["score"] += bonus

    # 二次扩召：首轮结果很弱时提升候选池
    if len(score_map) < max(2, top_k // 2):
        expanded_top_k = min(20, top_k * 2 + 4)
        for query, weight in routes[:2]:
            results = await store.search_hybrid(query, top_k=expanded_top_k)
            hits_per_query[f"{query} (expanded)"] = len(results)
            for rank, item in enumerate(results):
                doc_id = str(item.get("id") or f"{item.get('file')}::expanded::{rank}")
                bonus = (weight * 0.9) / (rank + 1)
                if doc_id not in score_map:
                    score_map[doc_id] = {"score": 0.0, "item": item}
                score_map[doc_id]["score"] += bonus

    ranked = sorted(score_map.values(), key=lambda x: x["score"], reverse=True)
    merged = [r["item"] for r in ranked[:top_k]]
    debug = {
        "queries": [q for q, _ in routes],
        "hits_per_query": hits_per_query,
        "final_hits": len(merged),
        "top_files": [item.get("file", "") for item in merged[:5]],
    }
    return merged, debug


async def _judge_claim(
    claim: str,
    evidence_snippets: List[Dict[str, Any]],
    top_k: int,
    debug_info: Optional[Dict[str, Any]] = None,
) -> AlignmentItem | MissingClaim:
    """Use LLM to judge whether evidence supports a single claim."""
    client = get_client()
    if not client:
        return MissingClaim(claim=claim, reason="LLM unavailable")

    evidence_limit = max(1, min(top_k, 10))
    selected = evidence_snippets[:evidence_limit]
    evidence_parts = []
    for s in selected:
        meta = s.get("metadata") or {}
        start_line = _int_or_none(meta.get("start_line"))
        end_line = _int_or_none(meta.get("end_line"))
        line_info = ""
        if start_line is not None:
            line_info = f" (lines {start_line}-{end_line or start_line})"
        evidence_parts.append(
            f"File: {s.get('file', '?')}{line_info}\n{s.get('content', '')[:800]}"
        )

    evidence_text = "\n---\n".join(evidence_parts)
    if not evidence_text.strip():
        return MissingClaim(
            claim=claim,
            status="insufficient_evidence",
            reason="retrieval produced no useful evidence",
            debug_info=debug_info,
        )

    prompt = _ALIGN_JUDGE_PROMPT.format(
        claim=claim,
        top_k=evidence_limit,
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
        data = json.loads(_strip_json_fences(response.choices[0].message.content))

        valid_statuses = {"aligned", "partial", "missing", "insufficient_evidence"}
        raw_status = data.get("status", "missing")
        status = raw_status if raw_status in valid_statuses else "missing"
        reason = str(data.get("reason", "")).strip()

        evidence_spans = _build_evidence_spans(selected, evidence_limit)
        judge_debug = dict(debug_info or {})
        if reason:
            judge_debug["judge_reason"] = reason
        judge_debug["judge_top_k"] = evidence_limit

        matched_files = data.get("matched_files", [])
        if not isinstance(matched_files, list):
            matched_files = []
        if not matched_files:
            matched_files = [s.get("file") for s in selected if s.get("file")][:5]

        matched_symbols = data.get("matched_symbols", [])
        if not isinstance(matched_symbols, list):
            matched_symbols = []

        if status in {"missing", "insufficient_evidence"}:
            return MissingClaim(
                claim=claim,
                status=status,
                reason=reason or "no direct implementation evidence found",
                debug_info=judge_debug,
            )

        return AlignmentItem(
            claim=claim,
            status=status,
            matched_files=matched_files,
            matched_symbols=matched_symbols,
            evidence_excerpt=str(data.get("evidence_excerpt", ""))[:300],
            evidence_spans=evidence_spans,
            debug_info=judge_debug,
        )
    except Exception as e:
        logger.error("Alignment judge failed for claim '%s': %s", claim[:60], e)
        return MissingClaim(
            claim=claim,
            status="insufficient_evidence",
            reason=f"LLM judge error: {e}",
            debug_info=debug_info,
        )


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


async def _compute_paper_alignment_internal(
    paper_text: str,
    session_id: Optional[str] = None,
    repo_url: Optional[str] = None,
    top_k: int = 5,
    event_cb: EventCallback = None,
) -> PaperAlignResult:
    if not paper_text or not paper_text.strip():
        raise ValueError("paper_text is required")

    top_k = max(1, min(int(top_k), 10))

    sid = _resolve_session(session_id, repo_url)
    store = store_manager.get_store(sid)
    context = store.load_context()
    if not context or not context.get("repo_url"):
        raise ValueError(f"Session {sid} has no analyzed context. Run /analyze first.")

    await _emit(event_cb, {
        "type": "stage",
        "stage": "init",
        "message": f"Session resolved ({sid}), Top-K={top_k}",
        "top_k": top_k,
    })

    claims = await _extract_claims(paper_text)
    await _emit(event_cb, {
        "type": "stage",
        "stage": "claim_extraction",
        "message": f"Extracted {len(claims)} claims",
        "claims_count": len(claims),
    })

    if not claims:
        result = PaperAlignResult(
            missing_claims=[MissingClaim(claim="(entire paper)", reason="failed to extract claims")],
            confidence=0.0,
        )
        await _emit(event_cb, {
            "type": "stage",
            "stage": "complete",
            "message": "No claims extracted",
        })
        return result

    alignment_items: List[AlignmentItem] = []
    missing_claims: List[MissingClaim] = []

    file_tree = ((context.get("global_context") or {}).get("file_tree") or "")

    for idx, claim in enumerate(claims, start=1):
        await _emit(event_cb, {
            "type": "claim_progress",
            "index": idx,
            "total": len(claims),
            "claim": claim,
            "message": f"Processing claim {idx}/{len(claims)}",
        })

        snippets, debug_info = await _retrieve_claim_evidence(store, claim, top_k=top_k)
        await _emit(event_cb, {
            "type": "retrieval",
            "index": idx,
            "total": len(claims),
            "claim": claim,
            "hits": len(snippets),
            "queries": debug_info.get("queries", []),
            "top_files": debug_info.get("top_files", []),
            "message": f"Retrieved {len(snippets)} snippets",
        })

        result = await _judge_claim(claim, snippets, top_k=top_k, debug_info=debug_info)

        # 轻量 JIT：缺证据时尝试单轮补召
        if isinstance(result, MissingClaim) and result.status in {"missing", "insufficient_evidence"}:
            excluded_paths: Set[str] = set(store.indexed_files)
            excluded_paths.update([f for f in debug_info.get("top_files", []) if f])
            jit_candidates = _select_jit_candidates(file_tree, claim, excluded_paths, limit=3)

            if jit_candidates:
                await _emit(event_cb, {
                    "type": "fallback_jit",
                    "index": idx,
                    "total": len(claims),
                    "claim": claim,
                    "phase": "start",
                    "candidates": jit_candidates,
                    "message": f"JIT fetching {len(jit_candidates)} files",
                })

                jit_summary = await _jit_fetch_and_index_files(store, jit_candidates)
                await _emit(event_cb, {
                    "type": "fallback_jit",
                    "index": idx,
                    "total": len(claims),
                    "claim": claim,
                    "phase": "done",
                    "jit": jit_summary,
                    "message": f"JIT indexed {jit_summary.get('indexed_count', 0)} files",
                })

                if jit_summary.get("indexed_count", 0) > 0:
                    snippets_post, debug_post = await _retrieve_claim_evidence(store, claim, top_k=top_k)
                    combined_debug = dict(debug_info or {})
                    combined_debug["jit"] = jit_summary
                    combined_debug["post_jit_retrieval"] = debug_post
                    result = await _judge_claim(
                        claim,
                        snippets_post,
                        top_k=top_k,
                        debug_info=combined_debug,
                    )

                    await _emit(event_cb, {
                        "type": "retrieval",
                        "index": idx,
                        "total": len(claims),
                        "claim": claim,
                        "phase": "post_jit",
                        "hits": len(snippets_post),
                        "queries": debug_post.get("queries", []),
                        "top_files": debug_post.get("top_files", []),
                        "message": f"Post-JIT retrieved {len(snippets_post)} snippets",
                    })

        if isinstance(result, AlignmentItem):
            alignment_items.append(result)
            await _emit(event_cb, {
                "type": "judge",
                "index": idx,
                "total": len(claims),
                "claim": claim,
                "status": result.status,
                "display_status": result.status,
                "reason": (result.debug_info or {}).get("judge_reason", ""),
                "message": f"Judged as {result.status}",
            })
        else:
            missing_claims.append(result)
            await _emit(event_cb, {
                "type": "judge",
                "index": idx,
                "total": len(claims),
                "claim": claim,
                "status": result.status,
                "display_status": "missing",
                "reason": result.reason,
                "message": f"Judged as missing ({result.status})",
            })

    total = len(claims) or 1
    aligned_score = sum(
        1.0 if a.status == "aligned" else 0.5
        for a in alignment_items
    )
    confidence = round(aligned_score / total, 4)

    await _emit(event_cb, {
        "type": "stage",
        "stage": "complete",
        "message": "Alignment completed",
        "confidence": confidence,
    })

    return PaperAlignResult(
        alignment_items=alignment_items,
        missing_claims=missing_claims,
        confidence=confidence,
    )


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
        top_k:      每条 claim 检索并送审的代码片段数（范围 1-10）

    Returns:
        PaperAlignResult
    """
    return await _compute_paper_alignment_internal(
        paper_text=paper_text,
        session_id=session_id,
        repo_url=repo_url,
        top_k=top_k,
        event_cb=None,
    )


async def compute_paper_alignment_stream(
    paper_text: str,
    session_id: Optional[str] = None,
    repo_url: Optional[str] = None,
    top_k: int = 5,
) -> AsyncGenerator[Dict[str, Any], None]:
    """流式返回对齐过程诊断事件与最终结果。"""
    queue: asyncio.Queue[Dict[str, Any]] = asyncio.Queue()

    async def _push(event: Dict[str, Any]) -> None:
        await queue.put(_event_payload(event))

    async def _runner() -> None:
        try:
            result = await _compute_paper_alignment_internal(
                paper_text=paper_text,
                session_id=session_id,
                repo_url=repo_url,
                top_k=top_k,
                event_cb=_push,
            )
            await queue.put(_event_payload({"type": "final", "data": result.to_dict()}))
        except Exception as e:
            logger.exception("paper alignment stream failed")
            await queue.put(_event_payload({"type": "error", "message": str(e)}))
        finally:
            await queue.put({"type": "_end"})

    runner = asyncio.create_task(_runner())

    try:
        while True:
            event = await queue.get()
            if event.get("type") == "_end":
                break
            yield event
    finally:
        await runner
