# 文件路径: app/services/vector_service.py
import chromadb
from chromadb.config import Settings as ChromaSettings
from app.utils.llm_client import client
from app.core.config import settings

# 新增依赖
from rank_bm25 import BM25Okapi
import jieba # 如果处理中文注释可能需要，纯代码分词可以用简单的 split
import re

class VectorStore:
    def __init__(self):
        # 初始化 ChromaDB (内存模式)
        self.chroma_client = chromadb.Client(ChromaSettings(anonymized_telemetry=False))
        self.collection_name = "repo_code"
        
        # === Hybrid Search 组件 ===
        self.bm25 = None
        self.doc_store = [] # 存储 {"id":..., "content":..., "file":...} 用于 BM25
        
        self.reset_collection()

    def reset_collection(self):
        """重置集合与内存索引"""
        try:
            self.chroma_client.delete_collection(name=self.collection_name)
        except Exception:
            pass
        self.collection = self.chroma_client.create_collection(name=self.collection_name)
        
        # 重置 BM25 相关数据
        self.bm25 = None
        self.doc_store = []
        print("🧹 [VectorDB] 数据库与 BM25 索引已重置")

    def embed_text(self, text):
        """调用 Gemini 生成 Embedding"""
        if not client:
            return []
        try:
            result = client.models.embed_content(
                model=settings.EMBEDDING_MODEL,
                contents=text
            )
            return result.embeddings[0].values
        except Exception as e:
            print(f"❌ Embedding 生成失败: {e}")
            return []

    def _tokenize(self, text):
        """简单的代码分词：按非字母数字字符切分"""
        # 将代码转为 token 列表，例如 "def my_func" -> ["def", "my", "func"]
        return [t.lower() for t in re.split(r'[^a-zA-Z0-9]', text) if t.strip()]

    def add_documents(self, documents, metadatas):
        """
        批量添加文档：
        1. 存入 Chroma (向量检索)
        2. 存入内存列表并构建 BM25 (关键词检索)
        """
        if not documents: return

        embeddings = []
        ids = []
        
        print(f"🧠 [VectorDB] 正在处理 {len(documents)} 个片段 (Vector + BM25)...")
        
        # 1. 准备向量数据
        for i, doc in enumerate(documents):
            # 生成唯一 ID
            doc_id = f"{metadatas[i]['file']}_{len(self.doc_store) + i}"
            
            # 存入 BM25 存储区
            self.doc_store.append({
                "id": doc_id,
                "content": doc,
                "metadata": metadatas[i]
            })
            
            # 生成向量
            emb = self.embed_text(doc)
            if emb:
                embeddings.append(emb)
                ids.append(doc_id)

        # 2. 写入 Chroma
        if embeddings:
            self.collection.add(
                documents=documents,
                embeddings=embeddings,
                metadatas=metadatas,
                ids=ids
            )
        
        # 3. 重建 BM25 索引 (注意：每次添加都会全量重建，生产环境需优化，但在 Demo 中可接受)
        tokenized_corpus = [self._tokenize(doc['content']) for doc in self.doc_store]
        self.bm25 = BM25Okapi(tokenized_corpus)
        
        print(f"✅ [VectorDB] 已索引 {len(documents)} 个片段")

    def search_hybrid(self, query, top_k=3):
        """
        混合检索：Vector + BM25 + RRF Fusion
        """
        # 1. 向量检索结果
        vector_results = []
        query_embedding = self.embed_text(query)
        if query_embedding:
            chroma_res = self.collection.query(
                query_embeddings=[query_embedding],
                n_results=top_k * 2 # 取多一点用于融合
            )
            if chroma_res['ids']:
                # 整理格式
                ids = chroma_res['ids'][0]
                docs = chroma_res['documents'][0]
                metas = chroma_res['metadatas'][0]
                for i in range(len(ids)):
                    vector_results.append({
                        "id": ids[i],
                        "content": docs[i],
                        "file": metas[i]['file'],
                        "score": 0 # RRF 中分数由排名决定
                    })

        # 2. BM25 检索结果
        bm25_results = []
        if self.bm25:
            tokenized_query = self._tokenize(query)
            # 获取所有文档的分数
            doc_scores = self.bm25.get_scores(tokenized_query)
            # 获取 top_k * 2 的索引
            top_n = min(len(doc_scores), top_k * 2)
            top_indices = sorted(range(len(doc_scores)), key=lambda i: doc_scores[i], reverse=True)[:top_n]
            
            for idx in top_indices:
                if doc_scores[idx] > 0: # 只保留有匹配的
                    doc_item = self.doc_store[idx]
                    bm25_results.append({
                        "id": doc_item["id"],
                        "content": doc_item["content"],
                        "file": doc_item["metadata"]["file"],
                        "score": 0
                    })

        # 3. RRF (Reciprocal Rank Fusion) 融合
        # 算法：Score = 1 / (k + rank)
        k = 60
        fused_scores = {}
        
        # === 核心调整：设置权重 ===
        # 向量搜索通常更准，给高权重 (1.0)
        # BM25 主要用于捕捉专有名词，但在通用句子里噪音大，给低权重 (0.3 - 0.5)
        weight_vector = 1.0
        weight_bm25 = 0.3  # <--- 降低 BM25 的话语权

        # 处理向量结果 (加权)
        for rank, item in enumerate(vector_results):
            doc_id = item['id']
            if doc_id not in fused_scores:
                fused_scores[doc_id] = {"item": item, "score": 0}
            # 公式：Weight * (1 / (k + rank))
            fused_scores[doc_id]["score"] += weight_vector * (1 / (k + rank + 1))
            
        # 处理 BM25 结果 (加权)
        for rank, item in enumerate(bm25_results):
            doc_id = item['id']
            if doc_id not in fused_scores:
                fused_scores[doc_id] = {"item": item, "score": 0}
            # 公式：Weight * (1 / (k + rank))
            fused_scores[doc_id]["score"] += weight_bm25 * (1 / (k + rank + 1))
            
        # 4. 排序并取 Top K
        sorted_results = sorted(fused_scores.values(), key=lambda x: x['score'], reverse=True)
        final_output = [res['item'] for res in sorted_results[:top_k]]
        
        return final_output

    def search(self, query, top_k=3):
        """保留原接口，指向混合搜索"""
        return self.search_hybrid(query, top_k)

# 创建全局单例
vector_db = VectorStore()