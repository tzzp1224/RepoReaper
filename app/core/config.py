# 文件路径: app/core/config.py
import os
from dotenv import load_dotenv

# 加载 .env 文件
load_dotenv()

class Settings:
    # --- API Keys ---
    GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
    GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
    
    # --- 模型配置 ---
    # 原代码使用的是 gemini-3-flash-preview，请根据实际可用模型调整
    MODEL_NAME = "gemini-3-flash-preview"  
    EMBEDDING_MODEL = "text-embedding-004"
    
    # --- 服务配置 ---
    HOST = "127.0.0.1"
    PORT = 8000

    def validate(self):
        """启动时检查必要的 Key 是否存在"""
        if not self.GEMINI_API_KEY:
            raise ValueError("❌ 错误: 未找到 GEMINI_API_KEY，请检查 .env 文件。")
        if not self.GITHUB_TOKEN:
            print("⚠️ 警告: 未找到 GITHUB_TOKEN，GitHub API 请求将受到每小时 60 次的严格限制。建议配置 Token。")

settings = Settings()