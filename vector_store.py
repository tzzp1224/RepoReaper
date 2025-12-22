import chromadb
from chromadb.config import Settings
from google import genai
import os
from dotenv import load_dotenv

load_dotenv()

# 配置 Gemini
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
client = genai.Client(api_key=GEMINI_API_KEY)

class VectorStore:
    def __init__(self):
        # 初始化 ChromaDB
        self.chroma_client = chromadb.Client(Settings(anonymized_telemetry=False))
        
        # ⚡️ 修复点：防止 Collection already exists 错误
        # 策略：每次初始化时，先尝试删除旧的集合，确保是从零开始的干净状态
        try:
            self.chroma_client.delete_collection(name="repo_code")
            print("🧹 已清理旧的向量数据库集合 [repo_code]")
        except Exception:
            # 如果集合不存在 (第一次运行)，delete 会报错，我们直接忽略
            pass
            
        # 现在可以放心地创建新的了
        self.collection = self.chroma_client.create_collection(name="repo_code")

    def embed_text(self, text):
        """调用 Gemini 将文本转换为向量 (Embedding)"""
        try:
            # 这里的 model 也可以换成 'text-embedding-004' 或其他
            result = client.models.embed_content(
                model="text-embedding-004",
                contents=text
            )
            return result.embeddings[0].values
        except Exception as e:
            print(f"❌ Embedding failed: {e}")
            return []

    def add_documents(self, documents, metadatas):
        """
        将代码片段存入向量数据库
        """
        if not documents:
            return

        print(f"🧠 正在生成 {len(documents)} 个代码片段的向量...")
        
        embeddings = []
        ids = []
        for i, doc in enumerate(documents):
            emb = self.embed_text(doc)
            if emb:
                embeddings.append(emb)
                ids.append(f"{metadatas[i]['file']}_{i}")

        if embeddings:
            self.collection.add(
                documents=documents,
                embeddings=embeddings,
                metadatas=metadatas,
                ids=ids
            )
            print(f"✅ 已存入 {len(documents)} 个代码片段到向量库")

    def search(self, query, top_k=3):
        """
        检索：根据问题找最相关的代码片段
        """
        print(f"🔍 RAG 检索中: {query}")
        query_embedding = self.embed_text(query)
        if not query_embedding:
            return []

        results = self.collection.query(
            query_embeddings=[query_embedding],
            n_results=top_k
        )
        
        output = []
        if results['documents']:
            # 处理 Chroma 返回的嵌套列表
            for i in range(len(results['documents'][0])):
                doc = results['documents'][0][i]
                meta = results['metadatas'][0][i]
                output.append({
                    "content": doc,
                    "file": meta['file']
                })
        return output