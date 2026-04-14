# -*- coding: utf-8 -*-
"""
Chat Suggested Questions Service

职责:
- 基于已分析仓库上下文生成 3 条高相关推荐问题
- 三类固定: 宏观功能 / 具体实现 / 快速复现
- 复用 artifact 持久化缓存 (kind=chat_questions)
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from app.core.config import settings
from app.services.vector_service import store_manager
from app.utils.llm_client import get_client
from app.utils.session import generate_repo_session_id

logger = logging.getLogger(__name__)

_FENCE_RE = re.compile(r"^```(?:json)?\\s*|\\s*```$", re.MULTILINE)
_MAX_FILE_TREE_CHARS = 3000
_MAX_SUMMARY_CHARS = 2000
_MAX_REPORT_CHARS = 2500


def _strip_json_fences(raw: str) -> str:
    return _FENCE_RE.sub("", (raw or "").strip()).strip()


def _normalize_lang(language: str) -> str:
    return "zh" if language == "zh" else "en"


def _fallback_questions(language: str) -> List[str]:
    if language == "zh":
        return [
            "这个仓库最核心的功能模块和主流程是什么？",
            "实现主流程时最关键的入口函数和调用链在哪里？",
            "如果要快速复现这个项目，最短可执行步骤是什么？",
        ]
    return [
        "What are the core modules and primary workflow in this repository?",
        "Which entrypoints and call chain are most critical to the main implementation?",
        "What is the fastest end-to-end path to reproduce this project locally?",
    ]


def _normalize_questions(payload: Dict[str, Any], language: str) -> List[str]:
    fallback = _fallback_questions(language)

    macro = str(payload.get("macro", "") or "").strip()
    implementation = str(payload.get("implementation", "") or "").strip()
    reproduction = str(payload.get("reproduction", "") or "").strip()

    if not (macro and implementation and reproduction):
        questions = payload.get("questions", [])
        if isinstance(questions, list):
            items = [str(q).strip() for q in questions if str(q).strip()]
            if len(items) >= 3:
                macro, implementation, reproduction = items[:3]

    normalized = [
        macro or fallback[0],
        implementation or fallback[1],
        reproduction or fallback[2],
    ]

    # 去重：如有重复，用兜底补齐该位置
    seen = set()
    for idx, question in enumerate(normalized):
        key = question.lower()
        if key in seen:
            normalized[idx] = fallback[idx]
            key = normalized[idx].lower()
        seen.add(key)

    return normalized


def _resolve_session(session_id: Optional[str], repo_url: Optional[str]) -> str:
    if session_id:
        return session_id
    if repo_url:
        return generate_repo_session_id(repo_url)
    raise ValueError("session_id or repo_url is required")


def _build_prompt(
    *,
    language: str,
    repo_url: str,
    file_tree: str,
    summary: str,
    report: str,
) -> str:
    lang_instruction = "请用中文输出问题。" if language == "zh" else "Output questions in English."

    return f"""{lang_instruction}

You are a repository analysis assistant.
Generate exactly THREE highly repository-specific suggested user questions for follow-up chat.

The three questions must follow this fixed order and intent:
1) Macro functionality question (overall architecture/business capability)
2) Concrete implementation question (specific module/function/data flow)
3) Quick reproduction question (fastest way to run/verify)

Return VALID JSON ONLY (no markdown fences):
{{
  "macro": "...",
  "implementation": "...",
  "reproduction": "..."
}}

Rules:
- Output exactly one question string for each key.
- Keep each question concise and actionable.
- Every question MUST contain at least one concrete repository anchor from context:
  file path (e.g. app/services/chunking_service.py), function/class/symbol name,
  API/route name, config key, or executable command.
- Do NOT output generic questions with no anchor (forbidden examples:
  "How can this project be improved?" / "What should we optimize?").
- The second question (implementation) MUST explicitly focus on one of:
  algorithm choice, data structure, key branch conditions, or time/space complexity.
- The third question (reproduction) MUST be executable-minded and include at least one
  runnable clue, such as a concrete command, entry file, or minimum verification step.
- Do not output explanations or extra keys.

## Repository URL
{repo_url}

## Repo Summary
{summary}

## File Tree (truncated)
{file_tree}

## Analysis Report (truncated)
{report}
"""


async def get_suggested_questions(
    *,
    session_id: Optional[str] = None,
    repo_url: Optional[str] = None,
    language: str = "en",
    force: bool = False,
) -> Tuple[List[str], bool]:
    """
    返回 (questions, cache_hit)
    """
    target_language = _normalize_lang(language)
    sid = _resolve_session(session_id, repo_url)
    store = store_manager.get_store(sid)

    context = store.load_context()
    if not context or not context.get("repo_url"):
        raise ValueError(f"Session {sid} has no analyzed context. Run /analyze first.")

    if not force:
        cached = store.get_artifact("chat_questions", target_language)
        cached_data = cached.get("data", {}) if isinstance(cached, dict) else {}
        cached_questions = cached_data.get("questions", [])
        if isinstance(cached_questions, list):
            cleaned = [str(q).strip() for q in cached_questions if str(q).strip()]
            if len(cleaned) >= 3:
                return cleaned[:3], True

    repo_from_context = str(context.get("repo_url") or repo_url or "").strip()
    global_ctx = context.get("global_context", {}) or {}
    file_tree = str(global_ctx.get("file_tree", "") or "")[:_MAX_FILE_TREE_CHARS]
    summary = str(global_ctx.get("summary", "") or "")[:_MAX_SUMMARY_CHARS]

    report = (
        store.get_report(target_language)
        or store.get_report("en")
        or store.get_report("zh")
        or ""
    )
    report = str(report)[:_MAX_REPORT_CHARS]

    questions = _fallback_questions(target_language)
    client = get_client()

    if client:
        prompt = _build_prompt(
            language=target_language,
            repo_url=repo_from_context,
            file_tree=file_tree,
            summary=summary,
            report=report,
        )
        try:
            response = await client.chat.completions.create(
                model=settings.default_model_name,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.2,
                max_tokens=512,
                stream=False,
            )
            content = response.choices[0].message.content
            payload = json.loads(_strip_json_fences(content))
            if isinstance(payload, dict):
                questions = _normalize_questions(payload, target_language)
        except Exception as exc:
            logger.warning("suggested questions generation failed, fallback applied: %s", exc)

    await store.save_artifact(
        kind="chat_questions",
        language=target_language,
        payload={"questions": questions},
    )

    return questions, False
