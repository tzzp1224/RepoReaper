# -*- coding: utf-8 -*-
"""
向量服务层 - Qdrant 版

特性:
1. 混合搜索 - Qdrant 向量 + BM25 关键词，RRF 融合
2. 异步原生 - 全链路异步
3. 会话隔离 - 每个 session 独立集合
4. 状态持久化 - 仓库信息、BM25 索引缓存
"""

import asyncio
import json
import logging
import os
import pickle
import re
import tempfile
import time
from datetime import datetime, timezone
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional, Set

from rank_bm25 import BM25Okapi

from app.core.config import settings
from app.storage.base import Document, SearchResult, CollectionStats
from app.storage.qdrant_store import QdrantVectorStore, QdrantConfig, get_qdrant_factory
from app.utils.embedding import get_embedding_service, EmbeddingConfig

logger = logging.getLogger(__name__)


# ============================================================
# 使用统一配置
# ============================================================

from app.core.config import vector_config as config

# 确保目录存在
os.makedirs(config.context_dir, exist_ok=True)

# === 向后兼容导出 (供 main.py 使用) ===
vector_config = config  # 兼容旧名称
CONTEXT_DIR = config.context_dir
QDRANT_DIR = config.data_dir  # Qdrant 数据目录


# ============================================================
# Embedding 服务
# ============================================================

_embedding_service = None

def get_embedding():
    """获取 Embedding 服务单例"""
    global _embedding_service
    if _embedding_service is None:
        emb_config = EmbeddingConfig(
            api_base_url=config.embedding_api_url,
            model_name=config.embedding_model,
            batch_size=config.embedding_batch_size,
            max_text_length=config.embedding_max_length,
            max_concurrent_batches=config.embedding_concurrency,
        )
        _embedding_service = get_embedding_service(emb_config)
    return _embedding_service


# ============================================================
# 向量存储服务
# ============================================================

class VectorStore:
    """
    向量存储服务
    
    整合 Qdrant 向量搜索和 BM25 关键词搜索
    
    使用示例:
    ```python
    store = VectorStore("session_123")
    await store.initialize()
    
    # 重置 (分析新仓库时)
    await store.reset()
    
    # 添加文档
    await store.add_documents(documents, metadatas)
    
    # 混合搜索
    results = await store.search_hybrid("how does auth work?")
    
    await store.close()
    ```
    """
    
    def __init__(self, session_id: str):
        self.session_id = self._sanitize_id(session_id)
        self.collection_name = f"repo_{self.session_id}"
        
        # Qdrant 存储
        self._qdrant: Optional[QdrantVectorStore] = None
        
        # BM25 索引 (内存)
        self._bm25: Optional[BM25Okapi] = None
        self._doc_store: List[Document] = []
        self._indexed_files: Set[str] = set()
        
        # 上下文
        self.repo_url: Optional[str] = None
        self.global_context: Dict[str, Any] = {}
        
        # 文件路径
        self._context_file = os.path.join(config.context_dir, f"{self.session_id}.json")
        self._cache_file = os.path.join(config.context_dir, f"{self.session_id}_bm25.pkl")
        
        self._initialized = False
    
    @staticmethod
    def _sanitize_id(session_id: str) -> str:
        """清理 session ID"""
        clean = re.sub(r'[^a-zA-Z0-9_-]', '', session_id)
        if not clean:
            raise ValueError("Invalid session_id")
        return clean
    
    async def initialize(self) -> None:
        """初始化存储"""
        if self._initialized:
            return
        
        # 初始化 Qdrant
        factory = get_qdrant_factory()
        self._qdrant = factory.create(self.collection_name)
        await self._qdrant.initialize()
        
        # 加载本地状态
        await self._load_state()
        
        self._initialized = True
        logger.debug(f"✅ VectorStore 初始化: {self.session_id}")
    
    async def close(self) -> None:
        """关闭连接"""
        if self._qdrant:
            await self._qdrant.close()
            self._qdrant = None
        self._initialized = False
    
    async def _load_state(self) -> None:
        """加载状态"""
        # 1. 加载上下文 JSON
        if os.path.exists(self._context_file):
            try:
                with open(self._context_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self.repo_url = data.get("repo_url")
                    self.global_context = data.get("global_context", {})
            except Exception as e:
                logger.warning(f"加载上下文失败: {e}")
        
        # 2. 尝试加载 BM25 缓存
        cache_loaded = False
        if os.path.exists(self._cache_file):
            try:
                with open(self._cache_file, 'rb') as f:
                    cache = pickle.load(f)
                    if isinstance(cache, dict) and cache.get("version") == config.cache_version:
                        self._bm25 = cache.get("bm25")
                        self._doc_store = cache.get("doc_store", [])
                        self._indexed_files = cache.get("indexed_files", set())
                        cache_loaded = True
                        logger.debug(f"📦 BM25 缓存命中: {len(self._doc_store)} 文档")
            except Exception as e:
                logger.warning(f"BM25 缓存损坏: {e}")
                os.remove(self._cache_file)
        
        # 3. 缓存未命中: 从 Qdrant 重建
        if not cache_loaded and self._qdrant:
            await self._rebuild_bm25_index()
    
    async def _rebuild_bm25_index(self) -> None:
        """从 Qdrant 重建 BM25 索引"""
        logger.info(f"🔄 重建 BM25 索引: {self.session_id}")
        
        documents = await self._qdrant.get_all_documents()
        
        if documents:
            self._doc_store = documents
            self._indexed_files = {doc.file_path for doc in documents if doc.file_path}
            
            tokenized = [self._tokenize(doc.content) for doc in documents]
            if tokenized:
                self._bm25 = BM25Okapi(tokenized)
            
            self._save_bm25_cache()
            logger.info(f"✅ BM25 索引重建完成: {len(documents)} 文档")
    
    def _save_bm25_cache(self) -> None:
        """保存 BM25 缓存 (原子写入)"""
        if not self._doc_store:
            return
        
        try:
            fd, tmp_path = tempfile.mkstemp(dir=config.context_dir)
            with os.fdopen(fd, 'wb') as f:
                pickle.dump({
                    "version": config.cache_version,
                    "bm25": self._bm25,
                    "doc_store": self._doc_store,
                    "indexed_files": self._indexed_files,
                }, f)
            
            if os.path.exists(self._cache_file):
                os.remove(self._cache_file)
            os.rename(tmp_path, self._cache_file)
            
        except Exception as e:
            logger.error(f"保存 BM25 缓存失败: {e}")
    
    def _tokenize(self, text: str) -> List[str]:
        """分词"""
        return [
            t.lower() for t in re.split(config.tokenize_regex, text)
            if t.strip()
        ]
    
    async def save_context(self, repo_url: str, context_data: Dict[str, Any]) -> None:
        """保存仓库上下文 (异步，不阻塞事件循环)"""
        self.repo_url = repo_url
        self.global_context = context_data
        await asyncio.to_thread(self._write_context_file, {
            "repo_url": repo_url,
            "global_context": context_data,
        })
    
    def _write_context_file(self, updates: Dict[str, Any]) -> None:
        """写入上下文文件 (同步，供线程池调用)"""
        try:
            existing = {}
            if os.path.exists(self._context_file):
                with open(self._context_file, 'r', encoding='utf-8') as f:
                    existing = json.load(f)
            existing.update(updates)
            with open(self._context_file, 'w', encoding='utf-8') as f:
                json.dump(existing, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"写入上下文失败: {e}")
    
    async def save_report(self, report: str, language: str = "en") -> None:
        """保存技术报告 (异步，不阻塞事件循环)"""
        await asyncio.to_thread(self._write_report, report, language)
    
    def _write_report(self, report: str, language: str) -> None:
        """写入报告 (同步，供线程池调用)"""
        try:
            existing = {}
            if os.path.exists(self._context_file):
                with open(self._context_file, 'r', encoding='utf-8') as f:
                    existing = json.load(f)
            
            if "reports" not in existing:
                existing["reports"] = {}
            existing["reports"][language] = report
            existing["report"] = report
            existing["report_language"] = language
            
            with open(self._context_file, 'w', encoding='utf-8') as f:
                json.dump(existing, f, ensure_ascii=False, indent=2)
            logger.info(f"📝 报告已保存: {self.session_id} ({language})")
        except Exception as e:
            logger.error(f"保存报告失败: {e}")
    
    def get_report(self, language: str = "en") -> Optional[str]:
        """
        获取指定语言的报告
        
        Args:
            language: 语言代码 ('en', 'zh')
            
        Returns:
            报告内容，不存在返回 None
        """
        context = self.load_context()
        if not context:
            return None
        
        # 优先从 reports 字典获取
        reports = context.get("reports", {})
        if language in reports:
            return reports[language]
        
        # 兼容旧格式：如果只有 report 字段且语言匹配
        if "report" in context:
            stored_lang = context.get("report_language", "en")
            if stored_lang == language:
                return context["report"]
        
        return None
    
    def get_available_languages(self) -> List[str]:
        """获取已有报告的语言列表"""
        context = self.load_context()
        if not context:
            return []
        
        reports = context.get("reports", {})
        return list(reports.keys())

    # ------------------------------------------------------------------
    # Artifact 持久化（issues / roadmap / score）
    # ------------------------------------------------------------------

    def _now_iso(self) -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    async def save_artifact(
        self,
        kind: str,
        language: str,
        payload: Dict[str, Any],
        generated_at: Optional[str] = None,
    ) -> None:
        """保存通用 artifact 快照（按 kind + language）。"""
        await asyncio.to_thread(
            self._write_artifact,
            kind,
            language,
            payload,
            generated_at or self._now_iso(),
        )

    def _write_artifact(
        self,
        kind: str,
        language: str,
        payload: Dict[str, Any],
        generated_at: str,
    ) -> None:
        try:
            existing: Dict[str, Any] = {}
            if os.path.exists(self._context_file):
                with open(self._context_file, 'r', encoding='utf-8') as f:
                    existing = json.load(f)

            artifacts = existing.setdefault("artifacts", {})
            by_kind = artifacts.setdefault(kind, {})
            by_kind[language] = {
                "data": payload or {},
                "generated_at": generated_at,
            }

            with open(self._context_file, 'w', encoding='utf-8') as f:
                json.dump(existing, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error("保存 artifact 失败 (%s/%s): %s", kind, language, e)

    def get_artifact(self, kind: str, language: str) -> Optional[Dict[str, Any]]:
        """读取通用 artifact 快照。"""
        context = self.load_context()
        if not context:
            return None
        artifacts = context.get("artifacts", {})
        by_kind = artifacts.get(kind, {})
        item = by_kind.get(language)
        if not isinstance(item, dict):
            return None
        return item

    def get_artifact_languages(self, kind: str) -> List[str]:
        """读取某类 artifact 的可用语言列表。"""
        context = self.load_context()
        if not context:
            return []
        artifacts = context.get("artifacts", {})
        by_kind = artifacts.get(kind, {})
        if not isinstance(by_kind, dict):
            return []
        return list(by_kind.keys())

    async def save_score_core(
        self,
        core_payload: Dict[str, Any],
        generated_at: Optional[str] = None,
    ) -> None:
        """保存 score 核心结果（语言无关）。"""
        await asyncio.to_thread(
            self._write_score_core,
            core_payload,
            generated_at or self._now_iso(),
        )

    def _write_score_core(self, core_payload: Dict[str, Any], generated_at: str) -> None:
        try:
            existing: Dict[str, Any] = {}
            if os.path.exists(self._context_file):
                with open(self._context_file, 'r', encoding='utf-8') as f:
                    existing = json.load(f)

            artifacts = existing.setdefault("artifacts", {})
            score_artifact = artifacts.setdefault("score", {})
            score_artifact["core"] = {
                "data": core_payload or {},
                "generated_at": generated_at,
            }

            with open(self._context_file, 'w', encoding='utf-8') as f:
                json.dump(existing, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error("保存 score core 失败: %s", e)

    def get_score_core(self) -> Optional[Dict[str, Any]]:
        """读取 score 核心结果。"""
        context = self.load_context()
        if not context:
            return None
        artifacts = context.get("artifacts", {})
        score_artifact = artifacts.get("score", {})
        core = score_artifact.get("core")
        if not isinstance(core, dict):
            return None
        return core

    async def save_score_localized(
        self,
        language: str,
        localized_payload: Dict[str, Any],
        generated_at: Optional[str] = None,
    ) -> None:
        """保存 score 多语言文本快照。"""
        await asyncio.to_thread(
            self._write_score_localized,
            language,
            localized_payload,
            generated_at or self._now_iso(),
        )

    def _write_score_localized(
        self,
        language: str,
        localized_payload: Dict[str, Any],
        generated_at: str,
    ) -> None:
        try:
            existing: Dict[str, Any] = {}
            if os.path.exists(self._context_file):
                with open(self._context_file, 'r', encoding='utf-8') as f:
                    existing = json.load(f)

            artifacts = existing.setdefault("artifacts", {})
            score_artifact = artifacts.setdefault("score", {})
            localized = score_artifact.setdefault("localized", {})
            localized[language] = {
                "data": localized_payload or {},
                "generated_at": generated_at,
            }

            with open(self._context_file, 'w', encoding='utf-8') as f:
                json.dump(existing, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error("保存 score localized 失败 (%s): %s", language, e)

    def get_score_localized(self, language: str) -> Optional[Dict[str, Any]]:
        """读取 score 指定语言文本快照。"""
        context = self.load_context()
        if not context:
            return None
        artifacts = context.get("artifacts", {})
        score_artifact = artifacts.get("score", {})
        localized = score_artifact.get("localized", {})
        item = localized.get(language)
        if not isinstance(item, dict):
            return None
        return item

    def get_score_localized_languages(self) -> List[str]:
        """读取 score 已有文本语言列表。"""
        context = self.load_context()
        if not context:
            return []
        artifacts = context.get("artifacts", {})
        score_artifact = artifacts.get("score", {})
        localized = score_artifact.get("localized", {})
        if not isinstance(localized, dict):
            return []
        return list(localized.keys())
    
    def load_context(self) -> Optional[Dict[str, Any]]:
        """
        加载仓库上下文
        
        Returns:
            包含 repo_url, global_context, report 等的字典，不存在返回 None
        """
        if not os.path.exists(self._context_file):
            return None
        
        try:
            with open(self._context_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            # 恢复内存状态
            self.repo_url = data.get("repo_url")
            self.global_context = data.get("global_context", {})
            
            return data
        except Exception as e:
            logger.error(f"加载上下文失败: {e}")
            return None
    
    def has_index(self) -> bool:
        """检查是否已有索引"""
        context = self.load_context()
        return context is not None and context.get("repo_url") is not None
    
    async def reset(self) -> None:
        """重置存储 (分析新仓库时调用)"""
        await self.initialize()
        
        # 删除 Qdrant 集合
        if self._qdrant:
            await self._qdrant.delete_collection()
            await self._qdrant.initialize()
        
        # 清理本地文件
        for f in [self._context_file, self._cache_file]:
            if os.path.exists(f):
                os.remove(f)
        
        # 重置内存状态
        self._bm25 = None
        self._doc_store = []
        self._indexed_files = set()
        self.repo_url = None
        self.global_context = {}
        
        logger.info(f"🗑️ 重置存储: {self.session_id}")
    
    # 兼容旧接口
    def reset_collection(self) -> None:
        """同步重置 (兼容旧代码)"""
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            try:
                asyncio.set_event_loop(loop)
                loop.run_until_complete(self.reset())
            finally:
                asyncio.set_event_loop(None)
                loop.close()
            return
        
        if loop.is_running():
            raise RuntimeError(
                "reset_collection cannot run inside an active event loop; use `await reset()` instead"
            )
        
        loop.run_until_complete(self.reset())
    
    async def add_documents(
        self,
        documents: List[str],
        metadatas: List[Dict[str, Any]]
    ) -> int:
        """
        添加文档
        
        Args:
            documents: 文档内容列表
            metadatas: 元数据列表
            
        Returns:
            成功添加的数量
        """
        if not documents:
            return 0
        
        await self.initialize()
        
        # 1. 批量获取 Embedding
        logger.info(f"📊 Embedding: {len(documents)} 个文档")
        embedding_service = get_embedding()
        embeddings = await embedding_service.embed_batch(documents, show_progress=True)
        
        # 过滤无效的
        valid_indices = [i for i, emb in enumerate(embeddings) if emb]
        if not valid_indices:
            logger.error("所有 Embedding 都失败了")
            return 0
        
        # 2. 构建 Document 对象
        docs = []
        for i in valid_indices:
            doc_id = f"{metadatas[i].get('file', 'unknown')}_{len(self._doc_store) + len(docs)}"
            doc = Document(
                id=doc_id,
                content=documents[i],
                metadata=metadatas[i],
            )
            docs.append(doc)
        
        valid_embeddings = [embeddings[i] for i in valid_indices]
        
        # 3. 写入 Qdrant
        added = await self._qdrant.add_documents(docs, valid_embeddings)
        
        # 4. 更新 BM25 索引 (放入线程池，避免阻塞)
        self._doc_store.extend(docs)
        self._indexed_files.update(doc.file_path for doc in docs)
        
        await asyncio.to_thread(self._rebuild_bm25_sync)
        
        return added
    
    def _rebuild_bm25_sync(self) -> None:
        """重建 BM25 索引 (同步，用于线程池)"""
        tokenized = [self._tokenize(doc.content) for doc in self._doc_store]
        self._bm25 = BM25Okapi(tokenized)
        self._save_bm25_cache()
    
    async def embed_text(self, text: str) -> List[float]:
        """获取文本 Embedding"""
        embedding_service = get_embedding()
        return await embedding_service.embed_text(text)
    
    async def search_hybrid(
        self,
        query: str,
        top_k: int = None
    ) -> List[Dict[str, Any]]:
        """
        混合搜索 (向量 + BM25，RRF 融合)
        
        Args:
            query: 查询文本
            top_k: 返回数量
            
        Returns:
            搜索结果列表
        """
        await self.initialize()
        
        top_k = top_k or config.default_top_k
        candidate_k = top_k * config.search_oversample
        
        # 1. 向量搜索
        vector_results: List[SearchResult] = []
        query_embedding = await self.embed_text(query)
        
        if query_embedding and self._qdrant:
            vector_results = await self._qdrant.search(
                query_embedding,
                top_k=candidate_k
            )
        
        # 2. BM25 搜索
        bm25_results: List[SearchResult] = []
        if self._bm25 and self._doc_store:
            tokens = self._tokenize(query)
            if not tokens:
                tokens = [""]
            
            try:
                scores = self._bm25.get_scores(tokens)
                top_indices = sorted(
                    range(len(scores)),
                    key=lambda i: scores[i],
                    reverse=True
                )[:candidate_k]
                
                for idx in top_indices:
                    if scores[idx] > 0:
                        doc = self._doc_store[idx]
                        bm25_results.append(SearchResult(
                            document=doc,
                            score=scores[idx],
                            source="bm25",
                        ))
            except Exception as e:
                logger.error(f"BM25 搜索失败: {e}")
        
        # 3. RRF 融合
        fused = self._rrf_fusion(vector_results, bm25_results)
        
        # 4. 格式化输出 (兼容旧接口)
        results = []
        for item in fused[:top_k]:
            doc = item.document
            results.append({
                "id": doc.id,
                "content": doc.content,
                "file": doc.file_path,
                "metadata": doc.metadata,
                "score": item.score,
            })
        
        return results
    
    def _rrf_fusion(
        self,
        vector_results: List[SearchResult],
        bm25_results: List[SearchResult]
    ) -> List[SearchResult]:
        """RRF (Reciprocal Rank Fusion) 融合"""
        k = config.rrf_k
        fused: Dict[str, Dict] = {}
        
        # 向量结果
        for rank, result in enumerate(vector_results):
            doc_id = result.document.id
            if doc_id not in fused:
                fused[doc_id] = {"result": result, "score": 0}
            fused[doc_id]["score"] += config.rrf_weight_vector / (k + rank + 1)
        
        # BM25 结果
        for rank, result in enumerate(bm25_results):
            doc_id = result.document.id
            if doc_id not in fused:
                fused[doc_id] = {"result": result, "score": 0}
            fused[doc_id]["score"] += config.rrf_weight_bm25 / (k + rank + 1)
        
        # 排序
        sorted_items = sorted(
            fused.values(),
            key=lambda x: x["score"],
            reverse=True
        )
        
        return [
            SearchResult(
                document=item["result"].document,
                score=item["score"],
                source="hybrid",
            )
            for item in sorted_items
        ]
    
    def get_documents_by_file(self, file_path: str) -> List[Dict[str, Any]]:
        """根据文件路径获取文档 (兼容旧接口)"""
        docs = [
            doc for doc in self._doc_store
            if doc.file_path == file_path
        ]
        
        result = []
        for doc in sorted(docs, key=lambda d: d.metadata.get("start_line", 0)):
            result.append({
                "id": doc.id,
                "content": doc.content,
                "file": doc.file_path,
                "metadata": doc.metadata,
                "score": 1.0,
            })
        
        return result
    
    @property
    def indexed_files(self) -> Set[str]:
        """已索引的文件"""
        return self._indexed_files


# ============================================================
# 管理器 - LRU Cache + 过期清理
# ============================================================

class SessionEntry:
    """Session 条目 - 包含存储实例和访问时间"""
    __slots__ = ('store', 'last_access', 'created_at')
    
    def __init__(self, store: VectorStore):
        self.store = store
        self.last_access = time.time()
        self.created_at = time.time()
    
    def touch(self) -> None:
        """更新访问时间"""
        self.last_access = time.time()


class VectorStoreManager:
    """
    向量存储管理器 - LRU Cache 实现
    
    特性:
    1. LRU 淘汰 - 超过 max_count 时淘汰最久未访问的内存中的 session
    2. 仓库数据永久存储 - 不清理仓库索引和报告
    3. 线程安全 - 使用 asyncio.Lock
    """
    
    def __init__(self, max_count: int = None):
        self._max_count = max_count or config.session_max_count
        self._sessions: Dict[str, SessionEntry] = {}
        self._lock = asyncio.Lock()
    
    def get_store(self, session_id: str) -> VectorStore:
        """
        获取或创建存储实例 (同步接口，兼容现有代码)
        
        会触发 LRU 淘汰检查
        """
        if session_id in self._sessions:
            entry = self._sessions[session_id]
            entry.touch()
            # 移动到最后（模拟 LRU）
            self._sessions.pop(session_id)
            self._sessions[session_id] = entry
            return entry.store
        
        # 创建新 session
        store = VectorStore(session_id)
        entry = SessionEntry(store)
        self._sessions[session_id] = entry
        
        # 检查是否需要 LRU 淘汰（异步执行）
        if len(self._sessions) > self._max_count:
            asyncio.create_task(self._evict_lru())
        
        logger.info(f"📦 Session 创建: {session_id} (总数: {len(self._sessions)})")
        return store
    
    async def _evict_lru(self) -> None:
        """淘汰最久未访问的 session"""
        async with self._lock:
            while len(self._sessions) > self._max_count:
                # 找到最久未访问的
                oldest_id = min(
                    self._sessions.keys(),
                    key=lambda k: self._sessions[k].last_access
                )
                entry = self._sessions.pop(oldest_id)
                await entry.store.close()
                logger.info(f"🗑️ LRU 淘汰: {oldest_id}")
    
    async def close_session(self, session_id: str) -> None:
        """关闭指定 session"""
        async with self._lock:
            if session_id in self._sessions:
                entry = self._sessions.pop(session_id)
                await entry.store.close()
                logger.info(f"🔒 Session 关闭: {session_id}")
    
    async def close_all(self) -> None:
        """关闭所有连接"""
        async with self._lock:
            for session_id, entry in list(self._sessions.items()):
                await entry.store.close()
            self._sessions.clear()
            logger.info("🔒 所有 Session 已关闭")
    
    async def cleanup_expired_files(self, max_idle_minutes: int = 60) -> Dict[str, Any]:
        """
        手动清理长时间未访问的会话。
        
        仅释放内存中的 session 资源，不删除磁盘上的索引和报告文件。
        """
        now = time.time()
        idle_threshold_seconds = max_idle_minutes * 60
        closed_session_ids: List[str] = []
        
        async with self._lock:
            stale_sessions = [
                session_id
                for session_id, entry in self._sessions.items()
                if (now - entry.last_access) >= idle_threshold_seconds
            ]
            
            for session_id in stale_sessions:
                entry = self._sessions.pop(session_id)
                await entry.store.close()
                closed_session_ids.append(session_id)
        
        if closed_session_ids:
            logger.info(
                "🧹 手动清理空闲 Session: %s",
                ", ".join(closed_session_ids),
            )
        
        return {
            "closed_sessions": len(closed_session_ids),
            "closed_session_ids": closed_session_ids,
            "max_idle_minutes": max_idle_minutes,
            "active_sessions": len(self._sessions),
        }
    
    def get_stats(self) -> Dict[str, Any]:
        """获取管理器统计信息"""
        now = time.time()
        sessions_info = []
        for sid, entry in self._sessions.items():
            sessions_info.append({
                "session_id": sid,
                "age_hours": round((now - entry.created_at) / 3600, 2),
                "idle_minutes": round((now - entry.last_access) / 60, 2),
            })
        
        return {
            "total_sessions": len(self._sessions),
            "max_sessions": self._max_count,
            "sessions": sorted(sessions_info, key=lambda x: x["idle_minutes"], reverse=True)
        }


# 全局管理器
store_manager = VectorStoreManager()
