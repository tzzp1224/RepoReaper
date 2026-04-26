# -*- coding: utf-8 -*-
import asyncio
from typing import List

from app.services import vector_service
from app.services.vector_service import VectorStore


class _FakeEmbeddingService:
    async def embed_batch(self, documents, show_progress=False):
        await asyncio.sleep(0.01)
        return [[0.01] * 1024 for _ in documents]


class _FakeQdrant:
    def __init__(self):
        self.added_ids: List[str] = []

    async def add_documents(self, documents, embeddings):
        self.added_ids.extend(doc.id for doc in documents)
        # 故意让并发路径交错，验证索引锁是否生效
        await asyncio.sleep(0.05)
        return len(documents)


def test_concurrent_context_writes_do_not_overwrite_each_other(tmp_path):
    async def _run():
        store = VectorStore("concurrent_context")
        store._context_file = str(tmp_path / "concurrent_context.json")
        store._cache_file = str(tmp_path / "concurrent_context_bm25.pkl")
        store._context_lock_file = f"{store._context_file}.lock"

        await store.save_context(
            "https://github.com/example/repo",
            {"file_tree": "a.py\nb.py", "summary": "seed context"},
        )
        await store.save_score_core({"overall": 0.88})

        async def _write_lang(i: int):
            language = f"l{i:02d}"
            await asyncio.gather(
                store.save_report(f"report-{language}", language),
                store.save_artifact("issues", language, {"lang": language}),
                store.save_score_localized(language, {"summary": f"score-{language}"}),
            )

        await asyncio.gather(*(_write_lang(i) for i in range(20)))

        context = store.load_context()
        assert context is not None
        assert context["repo_url"] == "https://github.com/example/repo"
        assert context["global_context"]["summary"] == "seed context"

        reports = context.get("reports", {})
        issues = context.get("artifacts", {}).get("issues", {})
        localized = context.get("artifacts", {}).get("score", {}).get("localized", {})

        assert len(reports) == 20
        assert len(issues) == 20
        assert len(localized) == 20
        assert context["artifacts"]["score"]["core"]["data"]["overall"] == 0.88

    asyncio.run(_run())


def test_add_documents_keeps_unique_doc_ids_under_concurrency(monkeypatch, tmp_path):
    async def _run():
        fake_qdrant = _FakeQdrant()
        fake_embedding = _FakeEmbeddingService()

        store = VectorStore("concurrent_docs")
        store._context_file = str(tmp_path / "concurrent_docs.json")
        store._cache_file = str(tmp_path / "concurrent_docs_bm25.pkl")
        store._context_lock_file = f"{store._context_file}.lock"
        store._qdrant = fake_qdrant
        store._initialized = True
        store._rebuild_bm25_sync = lambda: None

        async def _noop_initialize():
            return None

        monkeypatch.setattr(store, "initialize", _noop_initialize)
        monkeypatch.setattr(vector_service, "get_embedding", lambda: fake_embedding)

        docs_a = ["alpha", "beta", "gamma"]
        metas_a = [{"file": "a.py"} for _ in docs_a]
        docs_b = ["delta", "epsilon", "zeta"]
        metas_b = [{"file": "b.py"} for _ in docs_b]

        await asyncio.gather(
            store.add_documents(docs_a, metas_a),
            store.add_documents(docs_b, metas_b),
        )

        ids = [doc.id for doc in store._doc_store]
        assert len(ids) == 6
        assert len(ids) == len(set(ids))
        assert len(fake_qdrant.added_ids) == 6
        assert "a.py" in store.indexed_files
        assert "b.py" in store.indexed_files

    asyncio.run(_run())
