# -*- coding: utf-8 -*-
import asyncio
from types import SimpleNamespace

import pytest

from app.utils.github_client import GitHubCommit


class _FakeStore:
    def __init__(self):
        self._context = {
            "repo_url": "https://github.com/acme/demo",
            "global_context": {
                "file_tree": "app/main.py\napp/services/chunking_service.py",
                "summary": "This repository analyzes code repositories and provides insights.",
            },
        }
        self._reports = {"en": "Project report content"}
        self._artifacts = {}

    def load_context(self):
        return self._context

    def get_report(self, language="en"):
        return self._reports.get(language)

    def get_artifact(self, kind, language):
        return self._artifacts.get(kind, {}).get(language)

    async def save_artifact(self, kind, language, payload, generated_at=None):
        by_kind = self._artifacts.setdefault(kind, {})
        by_kind[language] = {
            "data": payload,
            "generated_at": generated_at or "2026-04-14T00:00:00Z",
        }


class _FakeStoreManager:
    def __init__(self, store):
        self._store = store

    def get_store(self, _session_id):
        return self._store


class _FakeCompletions:
    def __init__(self, contents):
        self._contents = list(contents)
        self.calls = 0

    async def create(self, **_kwargs):
        idx = min(self.calls, len(self._contents) - 1)
        self.calls += 1
        content = self._contents[idx]
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
        )


class _FakeLLMClient:
    def __init__(self, contents):
        self.chat = SimpleNamespace(completions=_FakeCompletions(contents))


class TestSuggestedQuestions:
    def test_generate_then_cache_hit(self, monkeypatch):
        import app.services.chat_questions_service as mod

        store = _FakeStore()
        fake_llm = _FakeLLMClient([
            """{
              "macro": "这个仓库的核心分析链路是什么？",
              "implementation": "chunking_service 在主流程里如何被调用？",
              "reproduction": "最快如何本地跑通一次完整分析？"
            }"""
        ])

        monkeypatch.setattr(mod, "store_manager", _FakeStoreManager(store))
        monkeypatch.setattr(mod, "get_client", lambda: fake_llm)

        questions_1, cache_hit_1 = asyncio.get_event_loop().run_until_complete(
            mod.get_suggested_questions(session_id="demo", language="zh", force=False)
        )
        assert cache_hit_1 is False
        assert len(questions_1) == 3
        assert store.get_artifact("chat_questions", "zh") is not None
        assert fake_llm.chat.completions.calls == 1

        questions_2, cache_hit_2 = asyncio.get_event_loop().run_until_complete(
            mod.get_suggested_questions(session_id="demo", language="zh", force=False)
        )
        assert cache_hit_2 is True
        assert questions_2 == questions_1
        assert fake_llm.chat.completions.calls == 1

    def test_fallback_when_llm_returns_invalid_json(self, monkeypatch):
        import app.services.chat_questions_service as mod

        store = _FakeStore()
        fake_llm = _FakeLLMClient(["not-json-response"])

        monkeypatch.setattr(mod, "store_manager", _FakeStoreManager(store))
        monkeypatch.setattr(mod, "get_client", lambda: fake_llm)

        questions, cache_hit = asyncio.get_event_loop().run_until_complete(
            mod.get_suggested_questions(session_id="demo", language="en", force=True)
        )
        assert cache_hit is False
        assert len(questions) == 3
        assert "core modules" in questions[0].lower()


class TestRoadmapPrompt:
    def test_roadmap_prompt_uses_latest_ten_commits(self):
        import app.services.insights_service as mod

        commits = [
            GitHubCommit(
                sha=f"{idx:07d}",
                message=f"feat: change {idx}",
                author="dev",
                date=f"2026-04-{idx:02d}T00:00:00Z",
            )
            for idx in range(12, 0, -1)  # newest -> oldest
        ]

        prompt = mod._build_roadmap_prompt(commits, "en")
        commit_section = prompt.split("## Commits", 1)[1]
        commit_lines = [line for line in commit_section.splitlines() if line.strip().startswith("- ")]

        assert "latest 10, max 10" in prompt
        assert len(commit_lines) == 10
        assert "change 12" in prompt
        assert "change 3" in prompt
        assert "change 2" not in prompt
        assert "change 1" not in prompt

