import sys
import io
import json
import time
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

from google import genai
from tools_github import get_repo_structure, get_file_content

# ==========================================
# 1. 配置 (Configuration)
# ==========================================
# ⚠️⚠️⚠️ 再次确认填入的是你的 Key ⚠️⚠️⚠️
GEMINI_API_KEY = ""

client = genai.Client(api_key=GEMINI_API_KEY)

# ⭐️ 关键修改：根据你的截图，使用 Gemini 3 Flash
# 它的 RPM 是 5，意味着我们每分钟只能发 5 次请求，必须小心限流
MODEL_NAME = "gemini-3-flash-preview" 

# ==========================================
# 2. 工具函数：带重试机制的 API 调用
# ==========================================
def call_gemini_with_retry(prompt, max_retries=3):
    for attempt in range(max_retries):
        try:
            response = client.models.generate_content(
                model=MODEL_NAME,
                contents=prompt
            )
            return response
        except Exception as e:
            error_str = str(e)
            print(f"⚠️ 尝试 {attempt+1}/{max_retries} 失败: {error_str[:100]}...")
            
            # 针对 429 限流进行指数退避
            if "429" in error_str or "RESOURCE_EXHAUSTED" in error_str:
                wait_time = (attempt + 1) * 20 # 因为 RPM 只有 5，我们等待时间加长到 20秒
                print(f"⏳ 触发限流，冷却 {wait_time} 秒...")
                time.sleep(wait_time)
            elif "404" in error_str:
                print(f"❌ 模型 {MODEL_NAME} 未找到 (404)。请检查名称是否完全匹配截图。")
                return None
            else:
                time.sleep(5)
    
    print("❌ 重试多次无效。")
    return None

# ==========================================
# 3. Agent 主逻辑
# ==========================================
def analyze_github_repo(repo_url):
    print(f"\n🚀 [Step 1] Initializing Agent for: {repo_url}")
    print(f"ℹ️  Model selected: {MODEL_NAME} (RPM: 5 - Strict Limit)")
    
    file_list = get_repo_structure(repo_url)
    if not file_list:
        print("❌ Failed to fetch repo structure.")
        return

    # 截取前 400 个文件，保持 Token 在 250K TPM 限制内
    limit = 400
    file_list_str = "\n".join(file_list[:limit])
    
    print(f"\n🤖 [Step 2] Gemini is thinking: Which files are important?")
    
    selection_prompt = f"""
    You are a Senior Software Architect.
    Below is the file structure of a GitHub repository:
    
    {file_list_str}
    
    Identify the top 3 most critical files to understand the project's architecture.
    Return the output strictly as a JSON list of strings.
    Example: ["README.md", "pyproject.toml"]
    """
    
    # === 第一次调用 API ===
    response = call_gemini_with_retry(selection_prompt)
    if not response: return

    selected_files = ["README.md"]
    try:
        clean_text = response.text.replace("```json", "").replace("```", "").strip()
        selected_files = json.loads(clean_text)
        print(f"🎯 Agent decided to read: {selected_files}")
    except:
        print(f"⚠️ JSON parsing failed, using default files.")

    # 🛑 强制休息：因为 RPM 只有 5，为了防止第二次调用直接 429，我们在这里强制睡 15 秒
    print("⏳ (Rate Limit Safety) Sleeping 15s before next request...")
    time.sleep(15)

    # --- 阶段 3: 执行 (Action) ---
    print(f"\n📥 [Step 3] Downloading file contents...")
    
    code_context = ""
    for file_path in selected_files:
        content = get_file_content(repo_url, file_path)
        if content:
            # 限制长度，防止超出 TPM 250K
            code_context += f"\n\n=== FILE: {file_path} ===\n{content[:12000]}"
            print(f"   ✅ Read: {file_path}")
        else:
            print(f"   ⚠️ Skipped: {file_path}")

    # --- 阶段 4: 综合分析 (Report) ---
    print(f"\n📝 [Step 4] Generating Final Report...")
    
    analysis_prompt = f"""
    You are a Tech Lead.
    Based on the code below from {repo_url}:
    
    {code_context}
    
    Please write a structured technical report (in Chinese) covering:
    1. **项目简介**: 一句话概括它是什么。
    2. **技术栈分析**: 用了什么语言、框架、关键库。
    3. **核心架构**: 代码是如何组织的？入口在哪里？
    4. **安装与运行**: 基于配置文件的推断。
    """
    
    # === 第二次调用 API ===
    final_response = call_gemini_with_retry(analysis_prompt)
    
    if final_response:
        print("\n" + "="*50)
        print("📋 FINAL AGENT REPORT")
        print("="*50)
        print(final_response.text)

if __name__ == "__main__":
    target_repo = "https://github.com/fastapi/fastapi"
    analyze_github_repo(target_repo)