# 文件路径: app/services/agent_service.py
import json
import asyncio
import traceback
import re
import ast
from app.core.config import settings
from app.utils.llm_client import client
from app.services.github_service import get_repo_structure, get_file_content
from app.services.vector_service import store_manager
from app.services.chunking_service import PythonASTChunker

# === Helper: 鲁棒的 JSON 提取 ===
def extract_json_from_text(text):
    try:
        text = re.sub(r"^```(json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
        return json.loads(text)
    except:
        pass
    match = re.search(r"\[.*\]", text, re.DOTALL)
    if match:
        try: return json.loads(match.group(0))
        except: pass
    return []

# === 优化 1：基于 AST 的 Repo Map 生成 ===
def _extract_symbols(content):
    """
    从代码内容中提取 Class 和 Function 的签名，生成精简地图。
    """
    try:
        tree = ast.parse(content)
        symbols = []
        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                symbols.append(f"  [C] {node.name}")
                # 提取类里面的方法（可选，为了不占太多 Token，只提取 __init__ 或公共方法）
                for sub in node.body:
                    if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        if not sub.name.startswith("_") or sub.name == "__init__":
                            symbols.append(f"    - {sub.name}")
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                symbols.append(f"  [F] {node.name}")
        return symbols
    except:
        return []

async def generate_repo_map(repo_url, file_list, limit=20):
    """
    生成增强版仓库地图：
    1. 对 file_list 进行排序，优先看根目录和 core/app/src 目录。
    2. 异步并发下载前 limit 个文件的内容。
    3. 解析 AST，提取类/函数名。
    4. 组合成 Repo Map 字符串。
    """
    # 筛选高优先级的 Python 文件
    priority_files = [
        f for f in file_list 
        if f.endswith('.py') and 
        (f.count('/') <= 1 or any(k in f for k in ['main', 'app', 'core', 'api', 'service', 'utils']))
    ]
    # 截取前 N 个，避免下载太多
    targets = priority_files[:limit]
    remaining = [f for f in file_list if f not in targets]
    
    repo_map_lines = []
    
    # 异步下载并解析
    async def process_file(path):
        content = await asyncio.to_thread(get_file_content, repo_url, path)
        if not content: return f"{path} (Read Failed)"
        symbols = await asyncio.to_thread(_extract_symbols, content)
        if symbols:
            return f"{path}\n" + "\n".join(symbols)
        return path

    # 提示信息
    repo_map_lines.append(f"--- Key Files Structure (Top {len(targets)}) ---")
    
    # 并发执行 (加快速度)
    tasks = [process_file(f) for f in targets]
    results = await asyncio.gather(*tasks)
    repo_map_lines.extend(results)
    
    # 追加剩余文件（仅路径）
    if remaining:
        repo_map_lines.append("\n--- Other Files ---")
        # 如果剩余太多，做截断
        if len(remaining) > 300:
            repo_map_lines.extend(remaining[:300])
            repo_map_lines.append(f"... ({len(remaining)-300} more files)")
        else:
            repo_map_lines.extend(remaining)
            
    return "\n".join(repo_map_lines)


async def agent_stream(repo_url: str, session_id: str, language: str = "en"):
    short_id = session_id[-6:] if session_id else "unknown"
    yield json.dumps({"step": "init", "message": f"🚀 [Session: {short_id}] Connecting to GitHub..."})
    await asyncio.sleep(0.5)
    
    try:
        vector_db = store_manager.get_store(session_id)
        vector_db.reset_collection() 
        vector_db.repo_url = repo_url
        
        chunker = PythonASTChunker(min_chunk_size=50)

        file_list = await asyncio.to_thread(get_repo_structure, repo_url)
        if not file_list:
            yield json.dumps({"step": "error", "message": "❌ Failed to fetch file list. Check URL or Token."})
            return

        yield json.dumps({"step": "fetched", "message": f"📦 Found {len(file_list)} files. Building Repo Map (AST Parsing)..."})        
        # === 使用新的 Repo Map 生成逻辑 ===
        # 这会比之前稍慢一点点（因为要下载十几个文件），但对 Agent 智商提升巨大
        file_tree_str = await generate_repo_map(repo_url, file_list, limit=15)
        
        MAX_ROUNDS = 3
        visited_files = set()
        context_summary = ""
        readme_file = next((f for f in file_list if f.lower().endswith("readme.md")), None)

        for round_idx in range(MAX_ROUNDS):
            yield json.dumps({"step": "thinking", "message": f"🕵️ [Round {round_idx+1}/{MAX_ROUNDS}] DeepSeek is analyzing Repo Map..."})
            
            # === DeepSeek English Prompt Strategy ===
            system_prompt = "You are a Senior Software Architect. Your goal is to understand the codebase."
            user_content = f"""
            [Project Repo Map]
            (Contains file paths and key Class/Function signatures)
            {file_tree_str}
            
            [Files Already Read]
            {list(visited_files)}
            
            [Current Knowledge]
            {context_summary}
            
            [Task]
            Select 1-3 MOST CRITICAL files to read next to understand the core logic.
            Focus on files that seem to contain main logic based on the Repo Map symbols.
            
            [Constraint]
            Return ONLY a raw JSON list of strings. No markdown.
            Example: ["src/main.py", "app/auth.py"]
            """
            
            if not client:
                 yield json.dumps({"step": "error", "message": "❌ LLM Client Not Initialized."})
                 return
            
            response = await client.chat.completions.create(
                model=settings.MODEL_NAME,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_content}
                ],
                temperature=0.1 
            )
            
            raw_content = response.choices[0].message.content
            target_files = extract_json_from_text(raw_content)

            valid_files = [f for f in target_files if f in file_list and f not in visited_files]

            if round_idx == 0 and readme_file and readme_file not in visited_files and readme_file not in valid_files:
                valid_files.insert(0, readme_file)

            if not valid_files:
                yield json.dumps({"step": "plan", "message": f"🛑 [Round {round_idx+1}] Sufficient context gathered."})
                break
            
            yield json.dumps({"step": "plan", "message": f"👉 [Round {round_idx+1}] Selected: {valid_files}"})
            
            new_knowledge = ""
            for i, file_path in enumerate(valid_files):
                yield json.dumps({"step": "download", "message": f"📥 Reading: {file_path}..."})
                
                content = get_file_content(repo_url, file_path)
                if not content: continue
                visited_files.add(file_path)
                
                # Preview logic
                lines = content.split('\n')[:50]
                preview = "\n".join(lines)
                new_knowledge += f"\n--- File: {file_path} ---\n{preview}\n"

                chunks = await asyncio.to_thread(chunker.chunk_file, content, file_path)
                if not chunks: continue
                
                documents = [c["content"] for c in chunks]
                metadatas = []
                for c in chunks:
                    meta = c["metadata"]
                    metadatas.append({
                        "file": meta["file"],
                        "type": meta["type"],
                        "name": meta.get("name", ""),
                        "class": meta.get("class") or ""
                    })

                if documents:
                    await vector_db.add_documents(documents, metadatas)
            
            context_summary += new_knowledge
            
            # Save global context
            vector_db.global_context = {
                "file_tree": file_tree_str,
                "summary": context_summary[:8000] 
            }
            yield json.dumps({"step": "indexing", "message": f"🧠 [Round {round_idx+1}] Knowledge graph updated."})

        # Final Report
        yield json.dumps({"step": "generating", "message": "📝 Generating technical report..."})
        
        # === 根据语言选择 Prompt ===
        if language == "zh":
            # --- 中文 Prompt ---
            system_role = "你是一位务实的技术专家。目标是为开发者创建一个'3页纸'架构概览，让他们能在5分钟内看懂这个仓库。重点关注架构和数据流，不要纠结细节。"
            analysis_user_content = f"""
            [角色]
            你是一位务实的技术专家（Tech Lead）。
            
            [输入数据]
            分析的文件: {list(visited_files)}
            代码知识库: 
            {context_summary[:15000]}
            
            [严格限制]
            1. **不进行代码审查**: 不要列出 Bug、缺失功能或改进建议。
            2. **不评价**: 不要评价代码质量，只描述它**如何工作**。
            3. **语调**: 专业、结构化、描述性。使用中文回答。
            4. **不要废话**: 不要写"安全性"、"未来规划"等未请求的章节。

            [输出格式要求 (Markdown)]
            
            # 项目分析报告

            ## 1. 执行摘要 (Executive Summary)
            - **用途**: (这个项目具体解决什么问题？1-2句话)
            - **核心功能**: (列出Top 3功能点)
            - **技术栈**: (语言、框架、数据库、关键库)

            ## 2. 系统架构 (Mermaid)
            创建一个 `graph TD` 图。
            - 展示高层组件 (如 Client, API Server, Database, Worker, External Service)。
            - 在连线上标注数据流 (如 "HTTP/JSON", "SQL")。
            - **风格**: 保持概念清晰简单。

            ## 3. 核心逻辑分析 (Table)
            (总结关键模块，不要列出所有文件，只列最重要的)

            | 组件/文件 | 职责 (它做什么？) | 关键设计模式/逻辑 |
            | :--- | :--- | :--- |
            | 例如 `auth_service.py` | 处理JWT颁发与验证 | 单例模式, 路由装饰器 |
            | ... | ... | ... |

            ## 4. 🔬 核心方法深度解析
            (精选 3-5 个最关键的 `.py` 文件。针对每个文件，列出驱动逻辑的 Top 2-3 个方法)

            ### 4.1 `[文件名]`
            * **`[方法名]`**: [解释它做什么以及为什么重要，不要贴代码]
            * **`[方法名]`**: [解释...]

            ## 5. 主要工作流 (Mermaid)
            选择**一个最重要**的业务流程 (Happy Path)。
            创建一个 `sequenceDiagram`。
            - 参与者应该是高层概念 (如 User, API, DB)，不要用具体变量名。
            
            ## 6. 快速开始 (Quick Start)
            - **前置条件**: (如 Docker, Python 3.9+, .env 配置)
            - **入口**: (如何启动主逻辑？如 `python main.py`)
            """
        else:
            analysis_user_content = f"""
            [Role]
            You are a **Pragmatic Tech Lead**. Your goal is to create a **"3-Pages" Architecture Overview** for a developer who wants to understand this repo in 5 minutes.
            [Input Data]
            Files analyzed: {list(visited_files)}
            Code Knowledge: 
            {context_summary[:15000]}  # 稍微增加上下文长度，DeepSeek 处理得来
            
            [Strict Constraints]
            1. **NO Code Review**: Do NOT list bugs, issues, missing features, or recommendations.
            2. **NO Critique**: Do not judge the code quality. Focus on HOW it works.
            3. **Tone**: Professional, descriptive, and structural.
            4. **NO "FLUFF"**: Do NOT add unrequested sections like "Security", "Scalability", "Data Models", "Future Enhancements", etc.

            [Required Output Format (Markdown)]
            
            # Project Analysis Report

            ## 1. Executive Summary
            - **Purpose**: (What specific problem does this project solve? 1-2 sentences)
            - **Key Features**: (Bullet points of top 3 features)
            - **Tech Stack**: (List languages, frameworks, databases, and key libs)

            ## 2. System Architecture
            Create a `graph TD` diagram.
            - Show high-level components (e.g., Client, API Server, Database, Worker, External Service).
            - Label the edges with data flow (e.g., "HTTP/JSON", "SQL").
            - **Style**: Keep it simple and conceptual.

            ## 3. Core Logic Analysis
            (Create a Markdown Table to summarize key modules. Do not list every file, only the most important ones.)

            | Component/File | Responsibility (What does it do?) | Key Design Pattern / Logic |
            | :--- | :--- | :--- |
            | e.g. `auth_service.py` | Handles JWT issuance and verification | Singleton, Decorator for routes |
            | ... | ... | ... |

            ## 4. Core Methods Deep Dive
            (Select the 3-5 most critical `.py` files. For each, list the top 2-3 methods that drive the logic.)

            ### 4.1 `[Filename, e.g., agent_service.py]`
            * **`[Method Name]`**: [Explanation of what it does and why it matters. No code.]
            * **`[Method Name]`**: [Explanation...]

            ### 4.2 `[Filename, e.g., vector_service.py]`
            * **`[Method Name]`**: [Explanation...]
            * ...

            ## 5. Main Workflow (Mermaid)
            Select the **Single Most Important** business flow (The "Happy Path").
            Create a `sequenceDiagram`.
            - Participants should be high-level (e.g., User, API, DB), not specific variable names.
            
            ## 6. Quick Start Guide
            - **Prerequisites**: (e.g. Docker, Python 3.9+, .env file)
            - **Entry Point**: (How to run the main logic? e.g. `python main.py` or `uvicorn`)

            """
        
        # === FIX: 增加 timeout 防止长文本生成时断连 ===
        stream = await client.chat.completions.create(
            model=settings.MODEL_NAME,
            messages=[
                {"role": "system", "content": "You are a pragmatic Tech Lead. Focus on architecture and data flow, not implementation details."},
                {"role": "user", "content": analysis_user_content}
            ],
            stream=True,
            timeout=600  # <--- 核心修复：设置 600秒 (10分钟) 超时，解决 httpx.ReadError
        )
        
        # === FIX: 增加 try-except 捕获流式传输中断 ===
        try:
            async for chunk in stream:
                if chunk.choices[0].delta.content:
                    yield json.dumps({"step": "report_chunk", "chunk": chunk.choices[0].delta.content})
        except (httpx.ReadError, httpx.ConnectError) as e:
            yield json.dumps({"step": "error", "message": f"⚠️ Network Timeout during generation: {str(e)}"})
            return

        yield json.dumps({"step": "finish", "message": "✅ Analysis Complete!"})

    except Exception as e:
        traceback.print_exc()
        yield json.dumps({"step": "error", "message": f"💥 System Error: {str(e)}"})