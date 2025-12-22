import sys
from dotenv import load_dotenv
import io
# ==========================================
# ⚡️ 修复 Windows 终端中文乱码/报错的关键代码
# ==========================================
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

from google import genai
from google.genai import types
import os

# ==========================================
# 1. 配置 (Configuration)
# ==========================================

load_dotenv()

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

if not GEMINI_API_KEY:
    raise ValueError("❌ 未找到 GEMINI_API_KEY，请检查 .env 文件")

# ==========================================
# 2. 初始化客户端 (Client Instantiation)
# ==========================================
# 新版 SDK 使用 Client 模式，更加工程化
client = genai.Client(aapi_key=GEMINI_API_KEY)

# ==========================================
# 3. 准备输入 & 调用 (Call)
# ==========================================
prompt = "你好 Gemini，我正在学习开发 AI Agent。请用一句话鼓励我，并用 Python 写一个打印 'Hello Agent' 的函数。"

print("正在发送请求给 Gemini (新版 SDK)...")

try:
    response = client.models.generate_content(
        model="gemini-3-flash-preview",
        contents=prompt
    )
    
    # ==========================================
    # 4. 处理响应 (Response)
    # ==========================================
    print("-" * 30)
    print("Gemini 回复:")
    print("-" * 30)
    print(response.text)

except Exception as e:
    print(f"请求失败: {e}")