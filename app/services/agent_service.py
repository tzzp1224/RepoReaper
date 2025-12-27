# 文件路径: app/services/agent_service.py
import json
import asyncio
from app.core.config import settings
from app.utils.llm_client import client
from app.services.github_service import get_repo_structure, get_file_content
from app.services.vector_service import vector_db  # 引用单例

async def agent_stream(repo_url: str):
    """
    Agent 核心工作流：感知 -> 规划 -> 执行 -> 报告
    """
    # Step 1: 初始化
    yield json.dumps({"step": "init", "message": f"🚀 正在连接 GitHub: {repo_url}..."})
    await asyncio.sleep(0.5)
    
    try:
        # 重置向量库
        vector_db.reset_collection()

        # 获取目录结构
        file_list = get_repo_structure(repo_url)
        if not file_list:
            yield json.dumps({"step": "error", "message": "❌ 无法获取文件列表，请检查 URL。"})
            return

        yield json.dumps({"step": "fetched", "message": f"📦 获取成功！共发现 {len(file_list)} 个文件。"})
        
        # 截取前 400 个文件，防止 Token 超出限制
        limit = 400
        file_list_str = "\n".join(file_list[:limit])

        # Step 2: 规划 (Gemini Thinking)
        yield json.dumps({"step": "thinking", "message": "🤖 Gemini 正在阅读目录，挑选核心代码..."})
        
        selection_prompt = f"""
        You are a Senior Software Architect.
        Repo Structure:
        {file_list_str}
        
        Identify top 3-5 critical files to understand the project architecture and logic.
        Return ONLY a raw JSON list of strings. 
        Example: ["README.md", "main.py", "app/core/config.py"]
        """
        
        if not client:
             yield json.dumps({"step": "error", "message": "❌ LLM Client 未初始化，请检查 API Key。"})
             return

        response = client.models.generate_content(
            model=settings.MODEL_NAME, 
            contents=selection_prompt
        )
        
        selected_files = ["README.md"]
        try:
            # 清洗 JSON
            clean_text = response.text.replace("```json", "").replace("```", "").strip()
            selected_files = json.loads(clean_text)
        except:
            print("⚠️ JSON 解析失败，回退到默认文件")

        yield json.dumps({"step": "plan", "message": f"🎯 决定深入阅读: {selected_files}"})
        
        # Step 3: 索引 (Indexing)
        code_context = ""
        documents = []
        metadatas = []

        for i, file_path in enumerate(selected_files):
            yield json.dumps({"step": "download", "message": f"📥 [{i+1}/{len(selected_files)}] 读取并向量化: {file_path}..."})
            content = get_file_content(repo_url, file_path)
            if content:
                # 简单切分：取前 8000 字符作为 Context
                snippet = content[:8000]
                documents.append(snippet)
                metadatas.append({"file": file_path})
                code_context += f"\n\n=== FILE: {file_path} ===\n{snippet}"
        
        yield json.dumps({"step": "indexing", "message": "🧠 正在构建 RAG 向量索引..."})
        vector_db.add_documents(documents, metadatas)

        # Step 4: 生成报告 (Reporting)
        yield json.dumps({"step": "generating", "message": "📝 正在撰写架构分析报告..."})
        
        analysis_prompt = f"""
        You are a Tech Lead.
        Based on the code context below:
        {code_context}
        
        Write a concise technical report (in Chinese). Use Markdown.
        Cover: Project Purpose, Tech Stack, and Key Architecture.
        """
        
        # 尝试流式生成
        try:
            stream = client.models.generate_content_stream(
                model=settings.MODEL_NAME, 
                contents=analysis_prompt
            )
            for chunk in stream:
                yield json.dumps({"step": "report_chunk", "chunk": chunk.text})
                await asyncio.sleep(0.02)
        except Exception as e:
            # 回退到非流式
            resp = client.models.generate_content(
                model=settings.MODEL_NAME, contents=analysis_prompt
            )
            yield json.dumps({"step": "report_chunk", "chunk": resp.text})

        yield json.dumps({"step": "finish", "message": "✅ 分析完成！现在可以提问了。"})

    except Exception as e:
        import traceback
        traceback.print_exc()
        yield json.dumps({"step": "error", "message": f"💥 系统错误: {str(e)}"})