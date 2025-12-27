# 文件路径: app/services/vector_service.py
import chromadb
from chromadb.config import Settings as ChromaSettings
from app.utils.llm_client import client
from app.core.config import settings

class VectorStore:
    def __init__(self):
        # 初始化 ChromaDB (内存模式)
        self.chroma_client = chromadb.Client(ChromaSettings(anonymized_telemetry=False))
        self.collection_name = "repo_code"
        self.reset_collection()

    def reset_collection(self):
        """重置集合，确保每次分析都是新的"""
        try:
            # 尝试删除旧集合，如果不存在会报错，直接忽略
            self.chroma_client.delete_collection(name=self.collection_name)
        except Exception:
            pass
        # 创建新集合
        self.collection = self.chroma_client.create_collection(name=self.collection_name)
        print("🧹 [VectorDB] 数据库已重置")

    def embed_text(self, text):
        """调用 Gemini 生成 Embedding"""
        if not client:
            print("❌ Embedding 失败: Client 未初始化")
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

    def add_documents(self, documents, metadatas):
        """批量添加文档到向量库"""
        if not documents: return

        embeddings = []
        ids = []
        valid_docs = []
        valid_metas = []

        print(f"🧠 [VectorDB] 正在为 {len(documents)} 个片段生成向量...")
        
        for i, doc in enumerate(documents):
            emb = self.embed_text(doc)
            if emb:
                embeddings.append(emb)
                valid_docs.append(doc)
                valid_metas.append(metadatas[i])
                # 生成唯一ID: 文件名_索引
                ids.append(f"{metadatas[i]['file']}_{i}")

        if embeddings:
            self.collection.add(
                documents=valid_docs,
                embeddings=embeddings,
                metadatas=valid_metas,
                ids=ids
            )
            print(f"✅ [VectorDB] 已存入 {len(valid_docs)} 个片段")

    def search(self, query, top_k=3):
        """语义检索"""
        query_embedding = self.embed_text(query)
        if not query_embedding: return []

        results = self.collection.query(
            query_embeddings=[query_embedding],
            n_results=top_k
        )
        
        output = []
        # 处理 Chroma 返回结果
        if results['documents']:
            for i in range(len(results['documents'][0])):
                output.append({
                    "content": results['documents'][0][i],
                    "file": results['metadatas'][0][i]['file']
                })
        return output

# 创建全局单例
vector_db = VectorStore()