# 文件路径: app/utils/llm_client.py
from google import genai
from app.core.config import settings

# 初始化 Gemini 客户端
# 单例模式：其他模块直接 import 这个 client 即可
try:
    if settings.GEMINI_API_KEY:
        client = genai.Client(
            api_key=settings.GEMINI_API_KEY,
            http_options={'api_version': 'v1beta'}
        )
    else:
        client = None
except Exception as e:
    print(f"❌ Gemini Client 初始化失败: {e}")
    client = None