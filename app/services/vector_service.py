# 文件路径: app/services/vector_service.py
import chromadb
from chromadb.config import Settings as ChromaSettings
from app.core.config import settings
from rank_bm25 import BM25Okapi
from openai import AsyncOpenAI  
import asyncio # 确保导入 asyncio
import re
import os
import json
import shutil

# 初始化本地 Embedding 模型
client = AsyncOpenAI(
    api_key=settings.SILICON_API_KEY, 
    base_url="https://api.siliconflow.cn/v1"
)
EMBEDDING_MODEL_NAME = "BAAI/bge-m3"

# === 核心修改：定义数据存储路径 ===
DATA_DIR = "data"
CHROMA_DIR = os.path.join(DATA_DIR, "chroma_db")
CONTEXT_DIR = os.path.join(DATA_DIR, "contexts")

# 确保目录存在
os.makedirs(CHROMA_DIR, exist_ok=True)
os.makedirs(CONTEXT_DIR, exist_ok=True)

# === 核心优化：在模块层级初始化 Client，而不是在类里 ===
# 这样每个 Worker 进程启动时只会创建一个 Client 实例，减少锁冲突
try:
    GLOBAL_CHROMA_CLIENT = chromadb.PersistentClient(path=CHROMA_DIR)
except Exception as e:
    print(f"⚠️ ChromaDB Init Error: {e}")
    GLOBAL_CHROMA_CLIENT = None

class VectorStore:
    def __init__(self, session_id: str):
        self.session_id = session_id
        
        # 使用全局 Client
        self.chroma_client = GLOBAL_CHROMA_CLIENT
        
        self.collection_name = f"repo_{session_id}"
        # 注意：get_or_create_collection 是轻量级操作
        self.collection = self.chroma_client.get_or_create_collection(name=self.collection_name)
        
        self.context_file = os.path.join(CONTEXT_DIR, f"{session_id}.json")
        
        self.repo_url = None
        self.indexed_files = set() 
        self.doc_store = [] 
        self.bm25 = None
        
        # 初始化时尝试加载已有数据
        self._load_local_state()

    def _load_local_state(self):
        """从磁盘加载上下文和 BM25 数据"""
        # 1. 加载 Global Context (JSON)
        if os.path.exists(self.context_file):
            try:
                with open(self.context_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self.repo_url = data.get("repo_url")
                    self.global_context = data.get("global_context", {})
            except Exception as e:
                print(f"⚠️ Load Context Error: {e}")
                self.global_context = {}
        else:
            self.global_context = {}

        # 2. 从 Chroma 恢复 indexed_files 和 doc_store (用于构建 BM25)
        # 注意：每次请求都全量拉取可能稍慢，但为了无状态化必须这样做，
        # 或者你可以选择仅在 search 时构建 BM25。
        existing_data = self.collection.get()
        if existing_data['ids']:
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
            # 重建 BM25
            tokenized_corpus = [self._tokenize(doc['content']) for doc in self.doc_store]
            if tokenized_corpus:
                self.bm25 = BM25Okapi(tokenized_corpus)

    def save_context(self, repo_url, context_data):
        """显式保存上下文到 JSON (供其他 Worker 读取)"""
        self.repo_url = repo_url
        self.global_context = context_data
        
        data = {
            "repo_url": repo_url,
            "global_context": context_data
        }
        with open(self.context_file, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def reset_collection(self):
        try:
            self.chroma_client.delete_collection(name=self.collection_name)
            # 删除对应的 JSON 上下文
            if os.path.exists(self.context_file):
                os.remove(self.context_file)
        except Exception:
            pass
        self.collection = self.chroma_client.get_or_create_collection(name=self.collection_name)
        self.bm25 = None
        self.doc_store = []
        self.repo_url = None
        self.indexed_files = set()
        self.global_context = {}

    # ... embed_text 方法保持不变 ...
    async def embed_text(self, text):
        try:
            text = text.replace("\n", " ")
            response = await client.embeddings.create(
                input=[text],
                model=EMBEDDING_MODEL_NAME
            )
            return response.data[0].embedding
        except Exception as e:
            print(f"❌ API Embedding Error: {e}")
            return []

    def _tokenize(self, text):
        return [t.lower() for t in re.split(r'[^a-zA-Z0-9]', text) if t.strip()]

    # ... add_documents 方法保持不变，但移除了 print ...
    async def add_documents(self, documents, metadatas):
        if not documents: return
        
        embeddings = []
        ids = []
        
        try:
            batch_size = 20  
            for i in range(0, len(documents), batch_size):
                batch_docs = documents[i : i + batch_size]
                batch_docs_clean = [d.replace("\n", " ") for d in batch_docs]
                
                response = await client.embeddings.create(
                    input=batch_docs_clean,
                    model=EMBEDDING_MODEL_NAME
                )
                batch_embeddings = [item.embedding for item in response.data]
                embeddings.extend(batch_embeddings)
                
        except Exception as e:
            print(f"❌ Batch API Embedding Error: {e}")
            return

        for i, doc in enumerate(documents):
            self.indexed_files.add(metadatas[i]['file'])
            doc_id = f"{metadatas[i]['file']}_{len(self.doc_store) + i}"
            
            self.doc_store.append({
                "id": doc_id,
                "content": doc,
                "metadata": metadatas[i]
            })
            ids.append(doc_id)

        if embeddings:
            self.collection.add(documents=documents, embeddings=embeddings, metadatas=metadatas, ids=ids)
        
        # 实时更新内存中的 BM25，下次其他 Worker 重新 init 时会重新加载
        tokenized_corpus = [self._tokenize(doc['content']) for doc in self.doc_store]
        self.bm25 = BM25Okapi(tokenized_corpus)

    # ... get_documents_by_file 保持不变 ...
    def get_documents_by_file(self, file_path):
        raw_docs = [doc for doc in self.doc_store if doc['metadata']['file'] == file_path]
        formatted_docs = []
        for d in raw_docs:
            formatted_docs.append({
                "id": d['id'],
                "content": d['content'],
                "file": d['metadata']['file'],
                "metadata": d['metadata'],
                "score": 1.0
            })
        return sorted(formatted_docs, key=lambda x: x['metadata'].get('start_line', 0))

    # ... search_hybrid 保持不变 ...
    async def search_hybrid(self, query, top_k=3):
        # (代码逻辑与之前一致，无需变动，因为 self.collection 已经是持久化的了)
        vector_results = []
        query_embedding = await self.embed_text(query)
        
        # ⚠️ 阻塞点 1：ChromaDB 查询是同步 I/O + 计算
        # 使用 asyncio.to_thread 将其扔到线程池执行
        def _chroma_query():
            if query_embedding:
                return self.collection.query(
                    query_embeddings=[query_embedding], n_results=top_k * 2
                )
            return None

        if query_embedding:
            # 修改：异步执行同步的 Chroma 查询
            chroma_res = await asyncio.to_thread(_chroma_query)
            
            if chroma_res and chroma_res['ids']:
                ids = chroma_res['ids'][0]
                docs = chroma_res['documents'][0]
                metas = chroma_res['metadatas'][0]
                for i in range(len(ids)):
                    vector_results.append({
                        "id": ids[i], 
                        "content": docs[i], 
                        "file": metas[i]['file'], 
                        "metadata": metas[i],
                        "score": 0
                    })

        bm25_results = []
        if self.bm25:
            tokenized_query = self._tokenize(query)
            
            # ⚠️ 阻塞点 2：BM25 计算是纯 CPU 密集型操作
            # 这是导致卡顿的元凶，必须放入线程池
            def _bm25_score():
                doc_scores = self.bm25.get_scores(tokenized_query)
                top_n = min(len(doc_scores), top_k * 2)
                # 使用 numpy 或 python 原生排序，这里也需要在线程中完成
                return sorted(range(len(doc_scores)), key=lambda i: doc_scores[i], reverse=True)[:top_n]

            top_indices = await asyncio.to_thread(_bm25_score)
            
            for idx in top_indices:
                # 获取分数需要重新访问 bm25 对象，简单起见在外面做
                # 或者把整个逻辑封装进函数。为安全起见，简单获取即可。
                # 注意：self.doc_store 访问通常很快，不一定要 wrap，但为了保险：
                score = self.bm25.get_scores(tokenized_query)[idx]
                if score > 0:
                    item = self.doc_store[idx]
                    bm25_results.append({
                        "id": item["id"], 
                        "content": item["content"], 
                        "file": item["metadata"]["file"], 
                        "metadata": item["metadata"],
                        "score": 0
                    })

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
    """
    无状态管理器。
    每次 get_store 都会实例化一个新的 VectorStore 对象，
    但因为底层使用的是 PersistentClient 和磁盘 JSON，
    所以数据在不同 Worker 之间是同步的。
    """
    def get_store(self, session_id: str) -> VectorStore:
        # 移除 print 以减少日志噪音
        return VectorStore(session_id)

store_manager = VectorStoreManager()