# -*- coding: utf-8 -*-
"""
Part C 单元测试 —— 可复现评分 & 论文-代码对齐

所有外部依赖 (store_manager、LLM client、settings) 全部 mock，
不需要网络、数据库或 API Key 即可运行。

运行方式:
    pytest tests/part_c/test_part_c.py -v
"""

from __future__ import annotations

import json
import asyncio
import re
import pytest
from dataclasses import dataclass
from typing import Any, Dict, List, Optional


# ============================================================
# Mock 数据 —— 模拟一个 "已分析" 的仓库 session
# ============================================================

MOCK_FILE_TREE = """\
README.md
LICENSE
requirements.txt
setup.py
Dockerfile
.env.example
.github/workflows/ci.yml
.github/ISSUE_TEMPLATE/bug_report.md
CONTRIBUTING.md
CHANGELOG.md
src/
  main.py
  model.py
  train.py
  data_loader.py
tests/
  test_model.py
  test_train.py
docs/
  guide.md
examples/
  demo.py
"""

MOCK_REPORT = (
    "This repository implements a two-stage retrieval pipeline for code analysis. "
    "It includes a training script, a model definition, and evaluation utilities. "
    "Dependencies are pinned in requirements.txt. Docker support is provided. "
    "Some users report CUDA version conflicts in open issues."
)

MOCK_SESSION_CONTEXT = {
    "repo_url": "https://github.com/mock-owner/mock-repo",
    "global_context": {
        "file_tree": MOCK_FILE_TREE,
        "summary": "A ML research repo with training, inference and evaluation.",
    },
    "reports": {
        "en": MOCK_REPORT,
    },
}

MOCK_SEARCH_RESULTS: List[Dict[str, Any]] = [
    {
        "id": "src/model.py_0",
        "content": (
            "class TwoStageRetriever:\n"
            "    def __init__(self, encoder, reranker):\n"
            "        self.encoder = encoder\n"
            "        self.reranker = reranker\n"
            "\n"
            "    def search_hybrid(self, query, top_k=10):\n"
            "        candidates = self.encoder.encode(query)\n"
            "        return self.reranker.rerank(candidates, top_k)\n"
        ),
        "file": "src/model.py",
        "metadata": {"file": "src/model.py", "type": "class", "name": "TwoStageRetriever"},
        "score": 0.92,
    },
    {
        "id": "src/train.py_0",
        "content": (
            "def train_epoch(model, dataloader, optimizer):\n"
            "    model.train()\n"
            "    for batch in dataloader:\n"
            "        loss = model(batch)\n"
            "        loss.backward()\n"
            "        optimizer.step()\n"
        ),
        "file": "src/train.py",
        "metadata": {"file": "src/train.py", "type": "function", "name": "train_epoch"},
        "score": 0.85,
    },
    {
        "id": "src/data_loader.py_0",
        "content": (
            "class DataPipeline:\n"
            "    def preprocess(self, raw):\n"
            "        return tokenize(clean(raw))\n"
        ),
        "file": "src/data_loader.py",
        "metadata": {"file": "src/data_loader.py", "type": "class", "name": "DataPipeline"},
        "score": 0.78,
    },
]

# ---- LLM 预设回复 ----

LLM_RISK_RESPONSE = json.dumps({
    "risks": [
        {
            "title": "CUDA version conflict",
            "reason": "Multiple open issues about CUDA 12 incompatibility",
            "evidence_refs": ["issue#42", "issue#58"],
        },
        {
            "title": "Missing pinned CUDA in Dockerfile",
            "reason": "Dockerfile does not pin CUDA toolkit version",
            "evidence_refs": ["Dockerfile"],
        },
    ],
    "summary": "Repository is partially reproducible; environment setup has moderate risk due to CUDA conflicts.",
})

LLM_CLAIMS_RESPONSE = json.dumps({
    "claims": [
        "The system uses a two-stage retrieval pipeline.",
        "Training uses a standard epoch-based loop with backpropagation.",
        "The model performs ablation study on encoder variants.",
    ],
})

LLM_JUDGE_ALIGNED = json.dumps({
    "status": "aligned",
    "matched_files": ["src/model.py"],
    "matched_symbols": ["TwoStageRetriever", "search_hybrid"],
    "evidence_excerpt": "class TwoStageRetriever … def search_hybrid",
    "reason": "Code clearly implements two-stage retrieval.",
})

LLM_JUDGE_PARTIAL = json.dumps({
    "status": "partial",
    "matched_files": ["src/train.py"],
    "matched_symbols": ["train_epoch"],
    "evidence_excerpt": "def train_epoch(model, dataloader, optimizer)",
    "reason": "Standard training loop present but no explicit backprop details.",
})

LLM_JUDGE_MISSING = json.dumps({
    "status": "missing",
    "matched_files": [],
    "matched_symbols": [],
    "evidence_excerpt": "",
    "reason": "No ablation study implementation found in the repository.",
})

# 按调用顺序排列：score 用 1 次 LLM，align 用 1(拆claim) + 3(judge) 次
async def _empty_issue_commit_insight(*args, **kwargs):
    """避免单测打真实 GitHub API。返回 degraded=False 的空结构。"""
    return {
        "issue_risks": [],
        "recent_feats": [],
        "stats": {
            "issues_total_scanned": 0,
            "commits_total_scanned": 0,
            "risk_issue_count": 0,
            "recent_feat_count": 0,
        },
        "degraded": False,
    }


LLM_RESPONSES_QUEUE = [
    LLM_RISK_RESPONSE,     # repro score → risks
    LLM_CLAIMS_RESPONSE,   # paper align → extract claims
    LLM_JUDGE_ALIGNED,     # claim 1 judge
    LLM_JUDGE_PARTIAL,     # claim 2 judge
    LLM_JUDGE_MISSING,     # claim 3 judge
]


async def _collect_stream_events(stream):
    events = []
    async for event in stream:
        events.append(event)
    return events


# ============================================================
# Mock 对象
# ============================================================

@dataclass
class _FakeMessage:
    content: str

@dataclass
class _FakeChoice:
    message: _FakeMessage

@dataclass
class _FakeCompletion:
    choices: List[_FakeChoice]


class _FakeLLMCompletions:
    """模拟 client.chat.completions，按顺序返回预设回复。"""

    def __init__(self, responses: List[str]):
        self._responses = list(responses)
        self._idx = 0

    async def create(self, **kwargs):
        if self._idx >= len(self._responses):
            raise RuntimeError("LLM mock: no more responses in queue")
        text = self._responses[self._idx]
        self._idx += 1
        return _FakeCompletion(choices=[_FakeChoice(message=_FakeMessage(content=text))])


class _FakeLLMChat:
    def __init__(self, completions: _FakeLLMCompletions):
        self.completions = completions


class FakeLLMClient:
    """完整的假 LLM 客户端，兼容 client.chat.completions.create(...)"""

    def __init__(self, responses: List[str]):
        self.chat = _FakeLLMChat(_FakeLLMCompletions(responses))


class FakeVectorStore:
    """模拟 VectorStore，返回固定的 context 和搜索结果。"""

    def __init__(self, context: Optional[Dict], search_results: List[Dict]):
        self._context = context
        self._search_results = search_results
        self.repo_url = (context or {}).get("repo_url")
        self._score_core_entry: Optional[Dict[str, Any]] = None
        self._score_localized_entries: Dict[str, Dict[str, Any]] = {}
        self._indexed_files = {
            item.get("file")
            for item in search_results
            if item.get("file")
        }
        file_tree = ((context or {}).get("global_context") or {}).get("file_tree", "")
        for path in re.findall(
            r"([A-Za-z0-9_./-]+\.(?:py|js|ts|tsx|jsx|java|go|rs|cpp|c|h|cs|php|rb|kt|scala|swift))",
            file_tree,
        ):
            self._indexed_files.add(path)

    def load_context(self):
        return self._context

    def get_report(self, language: str) -> Optional[str]:
        if not self._context:
            return None
        reports = self._context.get("reports", {})
        return reports.get(language)

    async def search_hybrid(self, query: str, top_k: int = 5) -> List[Dict]:
        return self._search_results[:top_k]

    @property
    def indexed_files(self):
        return self._indexed_files

    async def add_documents(self, documents: List[str], metadatas: List[Dict[str, Any]]) -> int:
        for meta in metadatas:
            file_path = meta.get("file")
            if file_path:
                self._indexed_files.add(file_path)
        return len(documents)

    def get_score_core(self) -> Optional[Dict[str, Any]]:
        return self._score_core_entry

    async def save_score_core(self, payload: Dict[str, Any]) -> None:
        self._score_core_entry = {"data": payload, "generated_at": "2026-04-26T00:00:00Z"}

    def get_score_localized(self, language: str) -> Optional[Dict[str, Any]]:
        return self._score_localized_entries.get(language)

    async def save_score_localized(self, language: str, payload: Dict[str, Any]) -> None:
        self._score_localized_entries[language] = {
            "data": payload,
            "generated_at": "2026-04-26T00:00:00Z",
        }

    def get_score_localized_languages(self) -> List[str]:
        return sorted(self._score_localized_entries.keys())


class FakeStoreManager:
    def __init__(self, store: FakeVectorStore):
        self._store = store

    def get_store(self, session_id: str) -> FakeVectorStore:
        return self._store


# ============================================================
# 测试：Schema to_dict
# ============================================================

class TestSchemaToDict:
    def test_repro_score_result_to_dict_has_all_keys(self):
        from app.schemas.repro import ReproScoreResult, DimensionScores, ScoreRisk

        dim = DimensionScores(0.8, 0.68, 0.7, 0.66)
        result = ReproScoreResult(
            overall_score=71,
            overall_score_raw=0.71,
            level="medium",
            quality_tier="silver",
            dimension_scores=dim,
            dimension_scores_raw=dim,
            risks=[ScoreRisk("risk1", "reason1", ["ref1"])],
            evidence_refs=["README.md"],
            summary="test summary",
        )
        d = result.to_dict()
        assert d["overall_score"] == 71
        assert d["level"] == "medium"
        assert d["quality_tier"] == "silver"
        assert "code_structure" in d["dimension_scores"]
        assert "code_structure" in d["dimension_scores_raw"]
        assert len(d["risks"]) == 1
        assert d["risks"][0]["title"] == "risk1"

    def test_paper_align_result_to_dict_has_all_keys(self):
        from app.schemas.repro import PaperAlignResult, AlignmentItem, MissingClaim

        result = PaperAlignResult(
            alignment_items=[
                AlignmentItem("claim1", "aligned", ["f.py"], ["func"], "excerpt"),
            ],
            missing_claims=[
                MissingClaim("claim2", "not found"),
            ],
            confidence=0.75,
        )
        d = result.to_dict()
        assert len(d["alignment_items"]) == 1
        assert d["alignment_items"][0]["status"] == "aligned"
        assert len(d["missing_claims"]) == 1
        assert d["confidence"] == 0.75

    def test_compute_level_boundaries(self):
        from app.schemas.repro import ReproScoreResult
        assert ReproScoreResult.compute_level(0.80) == "high"
        assert ReproScoreResult.compute_level(0.79) == "medium"
        assert ReproScoreResult.compute_level(0.60) == "medium"
        assert ReproScoreResult.compute_level(0.59) == "low"

    def test_compute_tier_boundaries(self):
        from app.schemas.repro import ReproScoreResult
        assert ReproScoreResult.compute_tier(0.90) == "gold"
        assert ReproScoreResult.compute_tier(0.89) == "silver"
        assert ReproScoreResult.compute_tier(0.70) == "silver"
        assert ReproScoreResult.compute_tier(0.69) == "bronze"
        assert ReproScoreResult.compute_tier(0.50) == "bronze"
        assert ReproScoreResult.compute_tier(0.49) == "rejected"


# ============================================================
# 测试：规则层打分（纯函数，无需 mock）
# ============================================================

class TestRuleBasedScoring:
    def test_full_repo_scores_high(self):
        from app.services.repro_score_service import _rule_based_scores, _aggregate

        dim = _rule_based_scores(MOCK_FILE_TREE)
        assert dim.code_structure > 0.5, f"code_structure={dim.code_structure}"
        assert dim.docs_quality > 0.5, f"docs_quality={dim.docs_quality}"
        assert dim.env_readiness > 0.5, f"env_readiness={dim.env_readiness}"

        overall = _aggregate(dim)
        assert overall >= 0.5, f"overall={overall}"

    def test_empty_tree_scores_zero(self):
        from app.services.repro_score_service import _rule_based_scores, _aggregate

        dim = _rule_based_scores("")
        assert dim.code_structure == 0.0
        assert dim.docs_quality == 0.0
        assert dim.env_readiness == 0.0
        assert dim.community_stability == 0.0
        assert _aggregate(dim) == 0.0

    def test_minimal_repo(self):
        from app.services.repro_score_service import _rule_based_scores

        tree = "README.md\nmain.py\n"
        dim = _rule_based_scores(tree)
        assert dim.docs_quality > 0, "README should contribute to docs_quality"
        assert dim.code_structure > 0, "main.py should contribute to code_structure"
        assert dim.env_readiness == 0, "no deps/docker in minimal repo"

    def test_insight_penalty_lowers_env_and_community(self):
        from app.services.repro_score_service import _adjust_scores_for_insight, DimensionScores

        base = DimensionScores(0.8, 0.8, 0.8, 0.8)
        insight = {
            "issue_risks": [
                {"id": 1, "severity": "high", "risk_type": "repro_env", "title": "cuda"},
            ]
            * 4,
            "stats": {"risk_issue_count": 4, "issues_total_scanned": 8},
        }
        adj = _adjust_scores_for_insight(base, insight)
        assert adj.community_stability < base.community_stability
        assert adj.env_readiness < base.env_readiness
        assert adj.code_structure == base.code_structure


# ============================================================
# 测试：compute_repro_score（mock store + LLM）
# ============================================================

class TestComputeReproScore:
    @pytest.fixture(autouse=True)
    def _patch(self, monkeypatch):
        import app.services.repro_score_service as mod

        fake_store = FakeVectorStore(MOCK_SESSION_CONTEXT, [])
        fake_mgr = FakeStoreManager(fake_store)
        monkeypatch.setattr(mod, "store_manager", fake_mgr)

        fake_llm = FakeLLMClient([LLM_RISK_RESPONSE])
        monkeypatch.setattr(mod, "get_client", lambda: fake_llm)

        class FakeSettings:
            default_model_name = "mock-model"
        monkeypatch.setattr(mod, "settings", FakeSettings())
        monkeypatch.setattr(mod, "fetch_issue_commit_insight", _empty_issue_commit_insight)

    def test_returns_valid_result(self):
        from app.services.repro_score_service import compute_repro_score

        result = asyncio.run(
            compute_repro_score(session_id="test_session")
        )

        assert 0 <= result.overall_score <= 100
        assert 0 <= result.overall_score_raw <= 1
        assert result.level in ("high", "medium", "low")
        assert result.quality_tier in ("gold", "silver", "bronze", "rejected")
        assert len(result.risks) == 2
        assert result.risks[0].title == "CUDA version conflict"
        assert "README" in result.evidence_refs or "README.md" in result.evidence_refs
        assert result.summary != ""

    def test_to_dict_matches_contract(self):
        from app.services.repro_score_service import compute_repro_score

        result = asyncio.run(
            compute_repro_score(session_id="test_session")
        )
        d = result.to_dict()

        contract_keys = {
            "overall_score", "overall_score_raw", "level", "quality_tier",
            "dimension_scores", "dimension_scores_raw",
            "risks", "evidence_refs", "summary", "language", "cache_hit",
        }
        assert set(d.keys()) == contract_keys

        dim_keys = {"code_structure", "docs_quality", "env_readiness", "community_stability"}
        assert set(d["dimension_scores"].keys()) == dim_keys
        assert set(d["dimension_scores_raw"].keys()) == dim_keys

    def test_no_context_raises(self, monkeypatch):
        import app.services.repro_score_service as mod

        empty_store = FakeVectorStore(None, [])
        monkeypatch.setattr(mod, "store_manager", FakeStoreManager(empty_store))
        monkeypatch.setattr(mod, "fetch_issue_commit_insight", _empty_issue_commit_insight)

        from app.services.repro_score_service import compute_repro_score

        with pytest.raises(ValueError, match="no analyzed context"):
            asyncio.run(
                compute_repro_score(session_id="empty")
            )

    def test_repo_url_resolves_session(self):
        from app.services.repro_score_service import compute_repro_score

        result = asyncio.run(
            compute_repro_score(repo_url="https://github.com/mock-owner/mock-repo")
        )
        assert result.overall_score > 0

    def test_missing_both_raises(self):
        from app.services.repro_score_service import compute_repro_score

        with pytest.raises(ValueError, match="至少提供一个"):
            asyncio.run(
                compute_repro_score()
            )

    def test_insight_since_days_and_limit_forwarded(self, monkeypatch):
        import app.services.repro_score_service as mod

        captured: dict = {}

        async def capture_fetch(rurl, since_days=90, limit=100):
            captured["since_days"] = since_days
            captured["limit"] = limit
            return {
                "issue_risks": [],
                "recent_feats": [],
                "stats": {
                    "issues_total_scanned": 0,
                    "commits_total_scanned": 0,
                    "risk_issue_count": 0,
                    "recent_feat_count": 0,
                },
                "degraded": False,
            }

        monkeypatch.setattr(mod, "fetch_issue_commit_insight", capture_fetch)

        from app.services.repro_score_service import compute_repro_score

        asyncio.run(
            compute_repro_score(
                session_id="test_session",
                insight_since_days=30,
                insight_limit=50,
            )
        )
        assert captured["since_days"] == 30
        assert captured["limit"] == 50


# ============================================================
# 测试：compute_paper_alignment（mock store + LLM）
# ============================================================

class TestComputePaperAlignment:
    @pytest.fixture(autouse=True)
    def _patch(self, monkeypatch):
        import app.services.paper_align_service as mod

        fake_store = FakeVectorStore(MOCK_SESSION_CONTEXT, MOCK_SEARCH_RESULTS)
        fake_mgr = FakeStoreManager(fake_store)
        monkeypatch.setattr(mod, "store_manager", fake_mgr)

        fake_llm = FakeLLMClient([
            LLM_CLAIMS_RESPONSE,
            LLM_JUDGE_ALIGNED,
            LLM_JUDGE_PARTIAL,
            LLM_JUDGE_MISSING,
        ])
        monkeypatch.setattr(mod, "get_client", lambda: fake_llm)

        class FakeSettings:
            default_model_name = "mock-model"
        monkeypatch.setattr(mod, "settings", FakeSettings())
        async def _no_rewrite(claim: str):
            return [(claim, 1.0)]
        monkeypatch.setattr(mod, "_rewrite_claim_queries", _no_rewrite)

    def test_returns_valid_result(self):
        from app.services.paper_align_service import compute_paper_alignment

        result = asyncio.run(
            compute_paper_alignment(
                paper_text="We propose a two-stage retrieval pipeline with ablation.",
                session_id="test_session",
            )
        )

        assert len(result.alignment_items) == 2
        assert result.alignment_items[0].status == "aligned"
        assert result.alignment_items[1].status == "partial"
        assert len(result.missing_claims) == 1
        assert 0 < result.confidence < 1

    def test_to_dict_matches_contract(self):
        from app.services.paper_align_service import compute_paper_alignment

        result = asyncio.run(
            compute_paper_alignment(
                paper_text="Some method claims",
                session_id="test_session",
            )
        )
        d = result.to_dict()

        assert "alignment_items" in d
        assert "missing_claims" in d
        assert "confidence" in d

        if d["alignment_items"]:
            item = d["alignment_items"][0]
            assert "claim" in item
            assert "status" in item
            assert "matched_files" in item
            assert "matched_symbols" in item
            assert "evidence_excerpt" in item
            assert "evidence_spans" in item
            assert "debug_info" in item

        if d["missing_claims"]:
            mc = d["missing_claims"][0]
            assert "claim" in mc
            assert "reason" in mc
            assert "status" in mc
            assert "debug_info" in mc

    def test_confidence_calculation(self):
        from app.services.paper_align_service import compute_paper_alignment

        result = asyncio.run(
            compute_paper_alignment(
                paper_text="method claims",
                session_id="test_session",
            )
        )
        # 3 claims: aligned(1.0) + partial(0.5) + missing(0) = 1.5 / 3 = 0.5
        assert result.confidence == 0.5

    def test_empty_paper_text_raises(self):
        from app.services.paper_align_service import compute_paper_alignment

        with pytest.raises(ValueError, match="paper_text is required"):
            asyncio.run(
                compute_paper_alignment(paper_text="", session_id="test")
            )

    def test_no_context_raises(self, monkeypatch):
        import app.services.paper_align_service as mod

        empty_store = FakeVectorStore(None, [])
        monkeypatch.setattr(mod, "store_manager", FakeStoreManager(empty_store))

        from app.services.paper_align_service import compute_paper_alignment

        with pytest.raises(ValueError, match="no analyzed context"):
            asyncio.run(
                compute_paper_alignment(paper_text="claims", session_id="empty")
            )

    def test_top_k_clamp(self):
        from app.services.paper_align_service import compute_paper_alignment

        result = asyncio.run(
            compute_paper_alignment(
                paper_text="claims",
                session_id="test_session",
                top_k=999,
            )
        )
        assert result is not None

    def test_stream_reports_input_and_effective_chars(self):
        from app.services.paper_align_service import compute_paper_alignment_stream

        source_text = "a" * 7000
        events = asyncio.run(
            _collect_stream_events(
                compute_paper_alignment_stream(
                    paper_text=source_text,
                    session_id="test_session",
                    top_k=5,
                )
            )
        )

        init_event = next(
            event for event in events
            if event.get("type") == "stage" and event.get("stage") == "init"
        )
        assert init_event["input_chars"] == 7000
        assert init_event["effective_chars"] == 6000
        assert any(event.get("type") == "final" for event in events)


# ============================================================
# 测试：LLM 不可用时的降级
# ============================================================

class TestLLMFallback:
    def test_score_without_llm(self, monkeypatch):
        import app.services.repro_score_service as mod

        fake_store = FakeVectorStore(MOCK_SESSION_CONTEXT, [])
        monkeypatch.setattr(mod, "store_manager", FakeStoreManager(fake_store))
        monkeypatch.setattr(mod, "get_client", lambda: None)
        monkeypatch.setattr(mod, "fetch_issue_commit_insight", _empty_issue_commit_insight)

        class FakeSettings:
            default_model_name = "mock"
        monkeypatch.setattr(mod, "settings", FakeSettings())

        from app.services.repro_score_service import compute_repro_score

        result = asyncio.run(
            compute_repro_score(session_id="test")
        )
        assert result.overall_score > 0
        assert result.risks == []
        assert "unavailable" in result.summary.lower()

    def test_align_without_llm(self, monkeypatch):
        import app.services.paper_align_service as mod

        fake_store = FakeVectorStore(MOCK_SESSION_CONTEXT, MOCK_SEARCH_RESULTS)
        monkeypatch.setattr(mod, "store_manager", FakeStoreManager(fake_store))
        monkeypatch.setattr(mod, "get_client", lambda: None)

        class FakeSettings:
            default_model_name = "mock"
        monkeypatch.setattr(mod, "settings", FakeSettings())

        from app.services.paper_align_service import compute_paper_alignment

        result = asyncio.run(
            compute_paper_alignment(paper_text="claims", session_id="test")
        )
        assert result.confidence == 0.0
        assert len(result.missing_claims) > 0
