# 文件路径: app/core/config.py
import os
from dotenv import load_dotenv

# 加载 .env 文件
load_dotenv()

class Settings:
    # --- API Keys ---
    GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
    
    # [新增] SiliconFlow API Key (用于 BGE-M3 Embedding)
    SILICON_API_KEY = os.getenv("SILICON_API_KEY")
    
    # --- DeepSeek 配置 ---
    DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
    DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
    
    # 模型名称
    MODEL_NAME = os.getenv("MODEL_NAME", "deepseek-chat")
    
    # --- 服务配置 ---
    HOST = os.getenv("HOST", "127.0.0.1")
    PORT = int(os.getenv("PORT", 8000))

    def validate(self):
        """启动时检查必要的 Key 是否存在"""
        missing_keys = []

        # 1. 检查 DeepSeek Key (LLM 必需)
        if not self.DEEPSEEK_API_KEY:
            missing_keys.append("DEEPSEEK_API_KEY")

        # 2. 检查 SiliconCloud Key (Embedding 必需)
        # 如果你现在的代码强制依赖它，这里最好报错
        if not self.SILICON_API_KEY:
             # 为了避免再次报错 AttributeError，这里只是打印警告，或者你可以选择 raise ValueError
             print("⚠️ 警告: 未找到 SILICON_API_KEY，向量检索功能可能无法工作。")
            
        if missing_keys:
            raise ValueError(f"❌ 错误: 缺少必要的环境变量: {', '.join(missing_keys)}。请检查 .env 文件。")
            
        # 3. 检查 GitHub Token (可选但建议)
        if not self.GITHUB_TOKEN:
            print("⚠️ 警告: 未找到 GITHUB_TOKEN，GitHub API 请求将受到每小时 60 次的严格限制。建议配置 Token。")

settings = Settings()
# 立即执行验证，确保启动时就暴露问题
settings.validate()