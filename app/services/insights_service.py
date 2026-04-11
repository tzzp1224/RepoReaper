# -*- coding: utf-8 -*-
"""
Insights 服务 — Issue 摘要 & Commit Roadmap

提供两个独立的 SSE async generator:
  - issue_summary_stream   : 抓取 Issues → LLM 生成结构化笔记
  - commit_roadmap_stream  : 抓取 Commits → LLM 生成 Mermaid Timeline + 叙述
"""

import json
import logging
import httpx
from typing import List

from app.core.config import settings
from app.utils.llm_client import client
from app.services.github_service import get_repo_issues, get_repo_commits
from app.utils.github_client import GitHubIssue, GitHubCommit

logger = logging.getLogger(__name__)


# ============================================================
# Issue Summary
# ============================================================

def _build_issue_prompt(issues: List[GitHubIssue], language: str) -> str:
    """将 Issue 列表格式化成 LLM prompt 输入"""
    issue_lines: list[str] = []
    for iss in issues:
        labels_str = ", ".join(iss.labels) if iss.labels else "none"
        body_preview = iss.body.replace("\n", " ").strip()
        issue_lines.append(
            f"- #{iss.number} [{iss.state}] {iss.title}  "
            f"(labels: {labels_str}, comments: {iss.comments_count}, "
            f"by @{iss.user}, created: {iss.created_at[:10]})\n"
            f"  {body_preview}"
        )
    issues_text = "\n".join(issue_lines)

    lang_instruction = (
        "请用中文回答。" if language == "zh"
        else "Please respond in English."
    )

    return f"""{lang_instruction}

You are a senior software analyst. Given the following GitHub Issues, produce a structured **Issue Summary Note**.

## Requirements
1. Group issues by theme (e.g. Bug / Feature Request / Enhancement / Discussion / Question).
2. For each group, list key takeaways: what the community is asking for, most‐discussed pain points, and any consensus.
3. Highlight high‐priority or most‐commented items.
4. End with a short "Overall Health" paragraph: is the issue tracker healthy, are maintainers responsive, any red flags?

## Issues
{issues_text}
"""


async def issue_summary_stream(repo_url: str, session_id: str, language: str = "en"):
    """
    SSE generator: 抓取 Issues → LLM 流式生成结构化笔记

    Yields JSON strings with:
      step = fetching | summarizing | content_chunk | finish | error
    """
    try:
        yield json.dumps({
            "step": "fetching",
            "message": "Fetching issues from GitHub..."
        })

        issues = await get_repo_issues(repo_url, state="all", per_page=30, max_pages=3)

        if not issues:
            yield json.dumps({
                "step": "finish",
                "message": "No issues found in this repository."
            })
            return

        yield json.dumps({
            "step": "fetching",
            "message": f"Fetched {len(issues)} issues. Generating summary..."
        })

        prompt = _build_issue_prompt(issues, language)
        messages = [
            {"role": "system", "content": "You are a helpful code analyst."},
            {"role": "user", "content": prompt},
        ]

        yield json.dumps({"step": "summarizing", "message": "LLM is analyzing issues..."})

        stream = await client.chat.completions.create(
            model=settings.default_model_name,
            messages=messages,
            stream=True,
            timeout=settings.LLM_TIMEOUT,
        )

        async for chunk in stream:
            if chunk.choices[0].delta.content:
                yield json.dumps({
                    "step": "content_chunk",
                    "chunk": chunk.choices[0].delta.content,
                })

        yield json.dumps({"step": "finish", "message": "Issue summary complete!"})

    except (httpx.ReadError, httpx.ConnectError) as e:
        yield json.dumps({"step": "error", "message": f"Network error: {e}"})
    except Exception as e:
        logger.error(f"issue_summary_stream error: {e}", exc_info=True)
        yield json.dumps({"step": "error", "message": f"Error: {e}"})


# ============================================================
# Commit Roadmap
# ============================================================

def _build_roadmap_prompt(commits: List[GitHubCommit], language: str) -> str:
    """将 Commit 列表格式化成 LLM prompt 输入（按时间正序）"""
    sorted_commits = list(reversed(commits))
    commit_lines: list[str] = []
    for c in sorted_commits:
        first_line = c.message.split("\n", 1)[0]
        commit_lines.append(f"- {c.date[:10]} ({c.sha}) by {c.author}: {first_line}")
    commits_text = "\n".join(commit_lines)

    lang_instruction = (
        "请用中文回答。" if language == "zh"
        else "Please respond in English."
    )

    return f"""{lang_instruction}

You are a senior software analyst. Given the following recent Git commits (oldest first, chronological order), produce a **Feature Roadmap** document.

## Requirements
1. Cluster commits into logical groups: **Feature**, **Bug Fix**, **Refactor**, **Docs**, **Chore/CI**, etc.
2. Generate a Mermaid `timeline` diagram. IMPORTANT: each entry label must be a **unique, descriptive event name** (NOT a generic category like "Feature"). Put the category tag after the colon. Example:

```mermaid
timeline
    title Recent Development Roadmap
    section 2024-03
        Add user auth module : Feature
        Fix login crash on iOS : Bug Fix
        Refactor DB connection pool : Refactor
    section 2024-04
        Add payment gateway : Feature
        Update API docs : Docs
```

Group sections by month (or by week if all commits fall in the same month). Sections MUST be in chronological order (oldest month first, newest month last) so the most recent activity appears on the right side of the timeline. Keep each entry label short (under 8 words). Merge similar commits into one entry instead of listing every commit.

3. Below the diagram, write a brief narrative (3-5 sentences) summarizing:
   - What major features were shipped
   - The overall development velocity and focus areas
   - Any notable trends (e.g. heavy refactoring, bug-fix sprint, new module)

## Commits
{commits_text}
"""


async def commit_roadmap_stream(repo_url: str, session_id: str, language: str = "en"):
    """
    SSE generator: 抓取 Commits → LLM 流式生成 Mermaid Timeline roadmap

    Yields JSON strings with:
      step = fetching | analyzing | content_chunk | finish | error
    """
    try:
        yield json.dumps({
            "step": "fetching",
            "message": "Fetching commits from GitHub..."
        })

        commits = await get_repo_commits(repo_url, per_page=30, max_pages=3)

        if not commits:
            yield json.dumps({
                "step": "finish",
                "message": "No commits found in this repository."
            })
            return

        yield json.dumps({
            "step": "fetching",
            "message": f"Fetched {len(commits)} commits. Generating roadmap..."
        })

        prompt = _build_roadmap_prompt(commits, language)
        messages = [
            {"role": "system", "content": "You are a helpful code analyst."},
            {"role": "user", "content": prompt},
        ]

        yield json.dumps({"step": "analyzing", "message": "LLM is building roadmap..."})

        stream = await client.chat.completions.create(
            model=settings.default_model_name,
            messages=messages,
            stream=True,
            timeout=settings.LLM_TIMEOUT,
        )

        async for chunk in stream:
            if chunk.choices[0].delta.content:
                yield json.dumps({
                    "step": "content_chunk",
                    "chunk": chunk.choices[0].delta.content,
                })

        yield json.dumps({"step": "finish", "message": "Roadmap generation complete!"})

    except (httpx.ReadError, httpx.ConnectError) as e:
        yield json.dumps({"step": "error", "message": f"Network error: {e}"})
    except Exception as e:
        logger.error(f"commit_roadmap_stream error: {e}", exc_info=True)
        yield json.dumps({"step": "error", "message": f"Error: {e}"})
