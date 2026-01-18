# -*- coding: utf-8 -*-
# 文件路径: app/services/vector_service.py

import chromadb
from chromadb.config import Settings as ChromaSettings
from app.core.config import settings
from rank_bm25 import BM25Okapi
from openai import AsyncOpenAI  
from filelock import FileLock, Timeout

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

# === 初始化配置 ===
client = AsyncOpenAI(
    api_key=settings.SILICON_API_KEY, 
    base_url="https://api.siliconflow.cn/v1"
)
EMBEDDING_MODEL_NAME = "BAAI/bge-m3"

DATA_DIR = "data"
CHROMA_DIR = os.path.join(DATA_DIR, "chroma_db")
CONTEXT_DIR = os.path.join(DATA_DIR, "contexts")
# 全局文件锁
LOCK_FILE = os.path.join(DATA_DIR, "vector_store.lock")

os.makedirs(CHROMA_DIR, exist_ok=True)
os.makedirs(CONTEXT_DIR, exist_ok=True)

# === 全局 Client ===
try:
    GLOBAL_CHROMA_CLIENT = chromadb.PersistentClient(path=CHROMA_DIR)
except Exception as e:
    logger.critical(f"ChromaDB Init Error: {e}", exc_info=True)
    GLOBAL_CHROMA_CLIENT = None

# 缓存版本号：如果更改了数据结构，修改此版本号强制重建缓存
CACHE_VERSION = "1.0"

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
                    if isinstance(cache_data, dict) and cache_data.get('version') == CACHE_VERSION:
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
                    'version': CACHE_VERSION,
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
        lock = FileLock(LOCK_FILE, timeout=10)
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
        try:
            text = text.replace("\n", " ")
            response = await client.embeddings.create(input=[text], model=EMBEDDING_MODEL_NAME)
            return response.data[0].embedding
        except Exception:
            return []

    def _tokenize(self, text):
        return [t.lower() for t in re.split(r'[^a-zA-Z0-9_\.@]+', text) if t.strip()]

    async def add_documents(self, documents, metadatas):
        if not documents: return
        
        embeddings = []
        ids = []
        
        # 1. 批量 Embedding (不需要锁，因为只是 API 请求)
        try:
            batch_size = 50 
            for i in range(0, len(documents), batch_size):
                batch_docs = documents[i : i + batch_size]
                batch_docs_clean = [d.replace("\n", " ")[:8000] for d in batch_docs]
                
                response = await client.embeddings.create(
                    input=batch_docs_clean,
                    model=EMBEDDING_MODEL_NAME
                )
                embeddings.extend([item.embedding for item in response.data])
        except Exception as e:
            logger.error(f"Embedding API Error: {e}")
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

        # 3. 临界区：写入 DB 和 Cache
        lock = FileLock(LOCK_FILE, timeout=30)
        try:
            with lock:
                # Bug Fix: 使用局部变量，防止写入部分失败导致内存脏数据
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
            # 这里可以考虑是否重新 reload _load_local_state 以恢复一致性
            raise

    def get_documents_by_file(self, file_path):
        raw_docs = [doc for doc in self.doc_store if doc['metadata']['file'] == file_path]
        formatted_docs = []
        for d in raw_docs:
            formatted_docs.append({
                "id": d['id'], "content": d['content'],
                "file": d['metadata']['file'], "metadata": d['metadata'], "score": 1.0
            })
        return sorted(formatted_docs, key=lambda x: x['metadata'].get('start_line', 0))

    # === Bug Fix: 还原 Search 逻辑 ===
    async def search_hybrid(self, query: str, top_k: int = 3) -> list:
        vector_results = []
        query_embedding = await self.embed_text(query)
        
        # 1. 向量搜索 (读磁盘，通常无需锁，或者 Chroma 内部有读锁)
        if query_embedding:
            try:
                chroma_res = self.collection.query(
                    query_embeddings=[query_embedding], n_results=top_k * 2
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
                top_n = min(len(doc_scores), top_k * 2)
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
        k = 60
        weight_vector = 1.0
        weight_bm25 = 0.3
        fused_scores = {}

        for rank, item in enumerate(vector_results):
            doc_id = item['id']
            if doc_id not in fused_scores: fused_scores[doc_id] = {"item": item, "score": 0}
            fused_scores[doc_id]["score"] += weight_vector * (1 / (k + rank + 1))
            
        for rank, item in enumerate(bm25_results):
            doc_id = item['id']
            if doc_id not in fused_scores: fused_scores[doc_id] = {"item": item, "score": 0}
            fused_scores[doc_id]["score"] += weight_bm25 * (1 / (k + rank + 1))

        sorted_results = sorted(fused_scores.values(), key=lambda x: x['score'], reverse=True)
        return [res['item'] for res in sorted_results[:top_k]]

class VectorStoreManager:
    def get_store(self, session_id: str) -> VectorStore:
        return VectorStore(session_id)

store_manager = VectorStoreManager()