# -*- coding: utf-8 -*-
# 文件路径: app/services/vector_service.py

import asyncio
import chromadb
from chromadb.config import Settings as ChromaSettings
from app.core.config import settings
from app.utils.embedding import get_embedding_service, EmbeddingConfig
from rank_bm25 import BM25Okapi
from filelock import FileLock, Timeout
from dataclasses import dataclass

import re
import os
import json
import shutil
import pickle
import logging
import tempfile
import time

# === 日志配置 ===
logging.basicConfig(
    level=logging.INFO, 
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

@dataclass
class VectorServiceConfig:
    # --- 基础配置 ---
    DATA_DIR: str = "data"
    CACHE_VERSION: str = "1.0"
    
    # --- 模型配置 ---
    API_BASE_URL: str = "https://api.siliconflow.cn/v1"
    EMBEDDING_MODEL_NAME: str = "BAAI/bge-m3"
    EMBEDDING_BATCH_SIZE: int = 50       # 批量 Embedding 的大小
    MAX_TEXT_LENGTH: int = 8000          # 单个文档最大字符数 (防止 Token 超限)

    # --- 文本处理配置 ---
    # 支持中文的正则示例：r'[^a-zA-Z0-9_\.@\u4e00-\u9fa5]+'
    TOKENIZE_REGEX: str = r'[^a-zA-Z0-9_\.@]+'

    # --- 并发控制 ---
    LOCK_TIMEOUT_RESET: int = 10         # 重置操作的锁等待时间 (秒)
    LOCK_TIMEOUT_WRITE: int = 30         # 写入操作的锁等待时间 (秒)

    # --- 混合检索 (RRF) 参数 ---
    RRF_K: int = 60                      # RRF 算法中的平滑常数 k
    RRF_WEIGHT_VECTOR: float = 1.0       # 向量检索权重
    RRF_WEIGHT_BM25: float = 0.3         # 关键字检索权重
    SEARCH_OVERSAMPLE_FACTOR: int = 2    # 初筛倍率 (TopK * N)
    DEFAULT_TOP_K: int = 3               # 默认搜索数量

# 实例化配置 (后续代码统一使用这个实例)
vector_config = VectorServiceConfig()

# === 初始化 Embedding 服务 (并发优化版) ===
embedding_config = EmbeddingConfig(
    api_base_url=vector_config.API_BASE_URL,
    model_name=vector_config.EMBEDDING_MODEL_NAME,
    batch_size=vector_config.EMBEDDING_BATCH_SIZE,
    max_text_length=vector_config.MAX_TEXT_LENGTH,
    max_concurrent_batches=5  # 最大 5 个并发批次
)
embedding_service = get_embedding_service(embedding_config)

CHROMA_DIR = os.path.join(vector_config.DATA_DIR, "chroma_db")
CONTEXT_DIR = os.path.join(vector_config.DATA_DIR, "contexts")
# 全局文件锁
LOCK_FILE = os.path.join(vector_config.DATA_DIR, "vector_store.lock")

os.makedirs(CHROMA_DIR, exist_ok=True)
os.makedirs(CONTEXT_DIR, exist_ok=True)

# === 全局 Client ===
try:
    GLOBAL_CHROMA_CLIENT = chromadb.PersistentClient(path=CHROMA_DIR)
except Exception as e:
    logger.critical(f"ChromaDB Init Error: {e}", exc_info=True)
    GLOBAL_CHROMA_CLIENT = None


class VectorStore:
    def __init__(self, session_id: str):
        self.session_id = self._sanitize_session_id(session_id)
        
        self.chroma_client = GLOBAL_CHROMA_CLIENT
        self.collection_name = f"repo_{self.session_id}"
        
        # 读操作通常不需要强锁，Chroma 内部有处理
        self.collection = self.chroma_client.get_or_create_collection(name=self.collection_name)
        
        self.context_file = os.path.join(CONTEXT_DIR, f"{self.session_id}.json")
        self.bm25_cache_file = os.path.join(CONTEXT_DIR, f"{self.session_id}_bm25.pkl")
        
        self.repo_url = None
        self.indexed_files = set() 
        self.doc_store = [] 
        self.bm25 = None
        
        self._load_local_state()

    def _sanitize_session_id(self, session_id: str) -> str:
        """防止路径注入"""
        clean_id = re.sub(r'[^a-zA-Z0-9_-]', '', session_id)
        if not clean_id: raise ValueError("Invalid session_id")
        return clean_id

    def _load_local_state(self):
        """加载状态 (Pickle Cache 优先)"""
        # 加载 Context JSON
        if os.path.exists(self.context_file):
            try:
                with open(self.context_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self.repo_url = data.get("repo_url")
                    self.global_context = data.get("global_context", {})
            except Exception as e:
                logger.error(f"Context Load Error: {e}")
                self.global_context = {}
        else:
            self.global_context = {}

        # 尝试加载 Pickle 缓存
        cache_loaded = False
        if os.path.exists(self.bm25_cache_file):
            try:
                with open(self.bm25_cache_file, 'rb') as f:
                    cache_data = pickle.load(f)
                    # Bug Fix: 增加版本校验
                    if isinstance(cache_data, dict) and cache_data.get('version') == vector_config.CACHE_VERSION:
                        self.bm25 = cache_data.get('bm25')
                        self.doc_store = cache_data.get('doc_store', [])
                        self.indexed_files = cache_data.get('indexed_files', set())
                        cache_loaded = True
                    else:
                        logger.warning(f"Cache version mismatch or invalid for {self.session_id}")
            except Exception as e:
                logger.warning(f"Cache corrupted ({e}), rebuilding...")
                os.remove(self.bm25_cache_file)

        # 缓存未命中：从 DB 重建 (Slow Path)
        if not cache_loaded:
            logger.info(f"Rebuilding index from DB for {self.session_id}...")
            try:
                existing_data = self.collection.get()
                if existing_data and existing_data['ids']:
                    self.doc_store = []
                    self.indexed_files = set()
                    for i, doc_id in enumerate(existing_data['ids']):
                        content = existing_data['documents'][i]
                        meta = existing_data['metadatas'][i]
                        self.indexed_files.add(meta['file'])
                        self.doc_store.append({
                            "id": doc_id,
                            "content": content,
                            "metadata": meta
                        })
                    
                    tokenized_corpus = [self._tokenize(doc['content']) for doc in self.doc_store]
                    if tokenized_corpus:
                        self.bm25 = BM25Okapi(tokenized_corpus)
                    
                    self._save_bm25_cache()
            except Exception as e:
                logger.error(f"DB Rebuild Error: {e}")

    def _save_bm25_cache(self):
        """原子写入缓存"""
        if not self.doc_store: return
        try:
            fd, tmp_path = tempfile.mkstemp(dir=CONTEXT_DIR)
            with os.fdopen(fd, 'wb') as f:
                pickle.dump({
                    'version': vector_config.CACHE_VERSION,
                    'bm25': self.bm25, 
                    'doc_store': self.doc_store,
                    'indexed_files': self.indexed_files
                }, f)
            
            if os.path.exists(self.bm25_cache_file):
                os.remove(self.bm25_cache_file)
            os.rename(tmp_path, self.bm25_cache_file)
        except Exception as e:
            logger.error(f"Save Cache Error: {e}")
            if os.path.exists(tmp_path): os.remove(tmp_path)

    def save_context(self, repo_url, context_data):
        self.repo_url = repo_url
        self.global_context = context_data
        data = {"repo_url": repo_url, "global_context": context_data}
        try:
            with open(self.context_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"Save Context Json Error: {e}")

    def reset_collection(self):
        # 临界区：写操作必须加锁
        lock = FileLock(LOCK_FILE, timeout=vector_config.LOCK_TIMEOUT_RESET)
        try:
            with lock:
                try:
                    self.chroma_client.delete_collection(name=self.collection_name)
                except ValueError: pass
                
                if os.path.exists(self.context_file): os.remove(self.context_file)
                if os.path.exists(self.bm25_cache_file): os.remove(self.bm25_cache_file)
                
                self.collection = self.chroma_client.get_or_create_collection(name=self.collection_name)
                self.bm25 = None
                self.doc_store = []
                self.repo_url = None
                self.indexed_files = set()
                self.global_context = {}
        except Timeout:
            logger.error("Reset Lock Timeout")
            raise

    async def embed_text(self, text):
        """获取单个文本的 Embedding (使用优化后的服务)"""
        return await embedding_service.embed_text(text)

    def _tokenize(self, text):
        return [t.lower() for t in re.split(vector_config.TOKENIZE_REGEX, text) if t.strip()]

    async def add_documents(self, documents, metadatas):
        if not documents: return
        
        ids = []
        
        # 1. 批量 Embedding (并发优化版 - 自动分批、并发、重试)
        logger.info(f"📊 开始 Embedding: {len(documents)} 个文档")
        embeddings = await embedding_service.embed_batch(documents, show_progress=True)
        
        # 检查是否有有效的 embeddings
        valid_embeddings = [e for e in embeddings if e]
        if not valid_embeddings:
            logger.error("Embedding 全部失败，跳过文档添加")
            return

        # 2. 准备数据
        new_doc_entries = []
        for i, doc in enumerate(documents):
            self.indexed_files.add(metadatas[i]['file'])
            doc_id = f"{metadatas[i]['file']}_{len(self.doc_store) + i}"
            ids.append(doc_id)
            new_doc_entries.append({
                "id": doc_id, "content": doc, "metadata": metadatas[i]
            })

        # 3. 临界区：在线程中执行写入，避免阻塞事件循环
        def _write_to_db():
            lock = FileLock(LOCK_FILE, timeout=vector_config.LOCK_TIMEOUT_WRITE)
            try:
                with lock:
                    # 使用局部变量，防止写入部分失败导致内存脏数据
                    # 先写 DB
                    if embeddings:
                        self.collection.add(
                            documents=documents, embeddings=embeddings, 
                            metadatas=metadatas, ids=ids
                        )
                    
                    # 再更新内存
                    self.doc_store.extend(new_doc_entries)
                    tokenized_corpus = [self._tokenize(d['content']) for d in self.doc_store]
                    self.bm25 = BM25Okapi(tokenized_corpus)
                    
                    # 最后写缓存
                    self._save_bm25_cache()
                    
            except Timeout:
                logger.error("Add Docs Lock Timeout")
                raise Exception("System busy, please try again.")
            except Exception as e:
                logger.critical(f"Critical Write Error: {e}")
                raise
        
        # 🔧 使用 asyncio.to_thread 避免阻塞事件循环
        await asyncio.to_thread(_write_to_db)

    def get_documents_by_file(self, file_path):
        raw_docs = [doc for doc in self.doc_store if doc['metadata']['file'] == file_path]
        formatted_docs = []
        for d in raw_docs:
            formatted_docs.append({
                "id": d['id'], "content": d['content'],
                "file": d['metadata']['file'], "metadata": d['metadata'], "score": 1.0
            })
        return sorted(formatted_docs, key=lambda x: x['metadata'].get('start_line', 0))

    # === Search 逻辑 ===
    async def search_hybrid(self, query: str, top_k: int = vector_config.DEFAULT_TOP_K) -> list:
        vector_results = []
        query_embedding = await self.embed_text(query)
        
        candidate_k = top_k * vector_config.SEARCH_OVERSAMPLE_FACTOR

        # 1. 向量搜索 (读磁盘，通常无需锁，或者 Chroma 内部有读锁)
        if query_embedding:
            try:
                chroma_res = self.collection.query(
                    query_embeddings=[query_embedding], n_results=candidate_k
                )
                if chroma_res['ids']:
                    ids = chroma_res['ids'][0]
                    docs = chroma_res['documents'][0]
                    metas = chroma_res['metadatas'][0]
                    for i in range(len(ids)):
                        vector_results.append({
                            "id": ids[i], "content": docs[i], 
                            "file": metas[i]['file'], "metadata": metas[i], "score": 0
                        })
            except Exception as e:
                logger.error(f"Chroma Search Error: {e}")

        # 2. BM25 搜索 (读内存)
        bm25_results = []
        if self.bm25:
            tokenized_query = self._tokenize(query)
            # 简单的防错
            if not tokenized_query: tokenized_query = [""]
            
            try:
                doc_scores = self.bm25.get_scores(tokenized_query)
                top_n = min(len(doc_scores), candidate_k)
                # 获取前 N 个最高分的索引
                top_indices = sorted(range(len(doc_scores)), key=lambda i: doc_scores[i], reverse=True)[:top_n]
                
                for idx in top_indices:
                    if doc_scores[idx] > 0:
                        item = self.doc_store[idx]
                        bm25_results.append({
                            "id": item["id"], "content": item["content"], 
                            "file": item["metadata"]["file"], "metadata": item["metadata"], "score": 0
                        })
            except Exception as e:
                logger.error(f"BM25 Search Error: {e}")

        # 3. RRF 融合 (Reciprocal Rank Fusion)
        k = vector_config.RRF_K
        fused_scores = {}

        for rank, item in enumerate(vector_results):
            doc_id = item['id']
            if doc_id not in fused_scores: fused_scores[doc_id] = {"item": item, "score": 0}
            # 使用配置权重
            fused_scores[doc_id]["score"] += vector_config.RRF_WEIGHT_VECTOR * (1 / (k + rank + 1))
            
        for rank, item in enumerate(bm25_results):
            doc_id = item['id']
            if doc_id not in fused_scores: fused_scores[doc_id] = {"item": item, "score": 0}
            # 使用配置权重
            fused_scores[doc_id]["score"] += vector_config.RRF_WEIGHT_BM25 * (1 / (k + rank + 1))

        sorted_results = sorted(fused_scores.values(), key=lambda x: x['score'], reverse=True)
        return [res['item'] for res in sorted_results[:top_k]]

class VectorStoreManager:
    def get_store(self, session_id: str) -> VectorStore:
        return VectorStore(session_id)

store_manager = VectorStoreManager()