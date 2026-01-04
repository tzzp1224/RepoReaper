# 文件路径: app/services/vector_service.py
import chromadb
from chromadb.config import Settings as ChromaSettings
from app.core.config import settings
from rank_bm25 import BM25Okapi
# from sentence_transformers import SentenceTransformer  <-- 删除这行，太占内存
from openai import AsyncOpenAI  
import re
import time
import os

# 初始化本地 Embedding 模型 (单例)
client = AsyncOpenAI(
    api_key=settings.SILICON_API_KEY, 
    base_url="https://api.siliconflow.cn/v1"
)
EMBEDDING_MODEL_NAME = "BAAI/bge-m3"

print(f"✅ Embedding Model: {EMBEDDING_MODEL_NAME}")

class VectorStore:
    def __init__(self, session_id: str):
        self.session_id = session_id
        self.chroma_client = chromadb.Client(ChromaSettings(anonymized_telemetry=False))
        self.collection_name = f"repo_{session_id}"
        
        self.repo_url = None
        self.indexed_files = set() 
        self.global_context = {}
        
        self.bm25 = None
        self.doc_store = [] 
        
        self.reset_collection()

    def reset_collection(self):
        try:
            self.chroma_client.delete_collection(name=self.collection_name)
        except Exception:
            pass
        self.collection = self.chroma_client.create_collection(name=self.collection_name)
        self.bm25 = None
        self.doc_store = []
        self.repo_url = None
        self.indexed_files = set()
        self.global_context = {} # 重置时清空
        print(f"🧹 [Session: {self.session_id}] 数据库已重置")

    async def embed_text(self, text):  # <--- 改为 async
        """异步调用 API 生成向量"""
        try:
            text = text.replace("\n", " ")
            response = await client.embeddings.create( # <--- await
                input=[text],
                model=EMBEDDING_MODEL_NAME
            )
            return response.data[0].embedding
        except Exception as e:
            print(f"❌ API Embedding Error: {e}")
            return []

    def _tokenize(self, text):
        return [t.lower() for t in re.split(r'[^a-zA-Z0-9]', text) if t.strip()]

    async def add_documents(self, documents, metadatas): # <--- 改为 async
        if not documents: return
        
        embeddings = []
        ids = []
        
        # === 批量生成 Embedding (API 优化) ===
        try:
            # 注意：大部分 API 单次请求限制 batch size (如 100 或 2048)
            # 如果 documents 很大，建议分批调用
            batch_size = 20  
            for i in range(0, len(documents), batch_size):
                batch_docs = documents[i : i + batch_size]
                # 清洗换行符
                batch_docs_clean = [d.replace("\n", " ") for d in batch_docs]
                
                response = await client.embeddings.create(
                    input=batch_docs_clean,
                    model=EMBEDDING_MODEL_NAME
                )
                # 按顺序提取 embedding
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
        
        tokenized_corpus = [self._tokenize(doc['content']) for doc in self.doc_store]
        self.bm25 = BM25Okapi(tokenized_corpus)
        
        print(f"✅ [Session: {self.session_id}] 增量索引完成，当前文档数: {len(self.doc_store)}")

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
    
    async def search_hybrid(self, query, top_k=3):
        vector_results = []
        query_embedding = await self.embed_text(query)
        
        if query_embedding:
            chroma_res = self.collection.query(
                query_embeddings=[query_embedding], n_results=top_k * 2
            )
            if chroma_res['ids']:
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
            doc_scores = self.bm25.get_scores(tokenized_query)
            top_n = min(len(doc_scores), top_k * 2)
            top_indices = sorted(range(len(doc_scores)), key=lambda i: doc_scores[i], reverse=True)[:top_n]
            for idx in top_indices:
                if doc_scores[idx] > 0:
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
    def __init__(self):
        self.stores = {} 
        self.last_access = {} 

    def get_store(self, session_id: str) -> VectorStore:
        if session_id not in self.stores:
            print(f"🆕 创建新会话: {session_id}")
            self.stores[session_id] = VectorStore(session_id)
        self.last_access[session_id] = time.time()
        return self.stores[session_id]

store_manager = VectorStoreManager()