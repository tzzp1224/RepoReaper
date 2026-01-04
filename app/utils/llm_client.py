# 文件路径: app/utils/llm_client.py
from openai import AsyncOpenAI
from app.core.config import settings
import os

# 初始化 DeepSeek 客户端 (兼容 OpenAI SDK)
try:
    # 确保 settings 中有 DEEPSEEK_API_KEY
    # DeepSeek V3 的 Base URL 通常为 https://api.deepseek.com
    api_key = getattr(settings, "DEEPSEEK_API_KEY", os.getenv("DEEPSEEK_API_KEY"))
    base_url = getattr(settings, "DEEPSEEK_BASE_URL", "https://api.deepseek.com")
    
    if api_key:
        client = AsyncOpenAI(  # <--- 初始化异步客户端
            api_key=api_key,
            base_url=base_url
        )
        print(f"✅ DeepSeek Client 初始化成功 (Model: {settings.MODEL_NAME})")
    else:
        print("❌ 未找到 DEEPSEEK_API_KEY")
        client = None
except Exception as e:
    print(f"❌ DeepSeek Client 初始化失败: {e}")
    client = None