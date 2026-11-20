"""Read and stage repository indexes without mutating a published snapshot."""

import asyncio
import hashlib
import json
from pathlib import PurePosixPath

from app.core.config import vector_config
from app.services.repository_evidence import source_chunks
from app.services.repository_source import BlobEntry
from app.services.vector_service import VectorStore, get_embedding
from app.storage.base import Document
from app.storage.qdrant_store import get_qdrant_factory


def index_namespace(repo: str) -> str:
    identity = (repo.lower(), "source-spans-v1", vector_config.embedding_api_url,
                vector_config.embedding_model, vector_config.embedding_dimensions,
                vector_config.embedding_max_length)
    key = hashlib.sha256(json.dumps(identity).encode()).hexdigest()[:40]
    return f"snapshot_{key}"


def incremental_paths(previous: dict, entries: dict[str, BlobEntry], paths: list[str]):
    """Keep prior coverage, carry it across renames, and seed a few added paths."""
    indexed = previous["files"]
    old_tree = previous["tree"]
    discoverable = set(paths)
    selected = {path: path for path in indexed if path in discoverable}
    removed = {path for path in indexed if path not in entries}
    by_blob = {}
    for path in sorted(removed):
        key = (indexed[path]["blob"], PurePosixPath(path).suffix.lower())
        by_blob.setdefault(key, path)

    additions = []
    for path in paths:
        if path in old_tree:
            continue
        key = (entries[path].sha, PurePosixPath(path).suffix.lower())
        if key in by_blob:
            selected[path] = by_blob[key]
        else:
            additions.append(path)
    additions.sort(key=lambda path: (not PurePosixPath(path).name.lower().startswith("readme"),
                                     path.count("/"), path))
    for path in additions[:5]:
        selected[path] = None
    return selected


class SnapshotIndex:
    def __init__(self, store: VectorStore, manifest: dict):
        self.store = store
        self.manifest = manifest
        self.backend = get_qdrant_factory().create(manifest["namespace"])
        self.ids = list(dict.fromkeys(doc_id for entry in manifest["files"].values() for doc_id in entry["docs"]))
        self._lexical = None
        self._lexical_lock = asyncio.Lock()

    async def _load_lexical(self):
        async with self._lexical_lock:
            if self._lexical is None:
                records = await self.backend.retrieve_documents(self.ids)
                docs = [records[doc_id][0] for doc_id in self.ids if doc_id in records]
                self._lexical = await asyncio.to_thread(self.store.build_lexical, docs)
            return self._lexical

    async def search(self, query: str, top_k: int = 6) -> list[dict]:
        if not self.ids:
            return []

        async def embed_query():
            try:
                return await asyncio.wait_for(self.store.embed_text(query), 1.8)
            except asyncio.TimeoutError:
                return []

        lexical, query_vector = await asyncio.gather(self._load_lexical(), embed_query())
        return await self.store.search_index(query, top_k, lexical, self.backend,
                                             query_vector=query_vector, document_ids=self.ids)

    async def stage_file(self, path: str, content: str, previous: dict | None) -> list[str]:
        chunks = await asyncio.to_thread(source_chunks, path, content)
        docs = []
        for text, metadata in chunks:
            doc_id = hashlib.sha256(json.dumps([text, metadata], sort_keys=True).encode()).hexdigest()
            docs.append(Document(doc_id, text, metadata))
        if not docs:
            return []

        doc_ids = [doc.id for doc in docs]
        existing = await self.backend.retrieve_documents(doc_ids)
        missing = [doc for doc in docs if doc.id not in existing]
        if not missing:
            return doc_ids

        embedding = get_embedding()
        previous_records = await self.backend.retrieve_documents(previous["docs"], with_vectors=True) if previous else {}
        vectors = {embedding.embedding_input(doc.content): vector
                   for doc, vector in previous_records.values() if isinstance(vector, list)}
        inputs = [embedding.embedding_input(doc.content) for doc in missing]
        fresh = {key: doc.content for key, doc in zip(inputs, missing) if key not in vectors}
        if fresh:
            computed = await embedding.embed_batch(list(fresh.values()))
            if len(computed) != len(fresh) or any(not vector or len(vector) != vector_config.embedding_dimensions
                                                   for vector in computed):
                raise RuntimeError(f"Incomplete embeddings for {path}")
            vectors.update(zip(fresh, computed))
        added = await self.backend.add_documents(missing, [vectors[key] for key in inputs])
        if added != len(missing):
            raise RuntimeError(f"Incomplete vector write for {path}")
        return doc_ids
