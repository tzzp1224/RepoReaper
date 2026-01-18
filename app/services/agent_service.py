# 文件路径: app/services/agent_service.py
import json
import asyncio
import traceback
import re
import ast
import httpx
from typing import Set, Tuple, List
from app.core.config import settings
from app.utils.llm_client import client
from app.services.github_service import get_repo_structure, get_file_content
from app.services.vector_service import store_manager
from app.services.chunking_service import UniversalChunker

# === 硬编码配置解耦 ===
class AgentConfig:
    INITIAL_MAP_LIMIT = 15
    MAX_ROUNDS = 3
    MAX_CONTEXT_LENGTH = 15000
    LLM_TIMEOUT = 600
    FILES_PER_ROUND = 3
    # 扩展的优先级列表
    PRIORITY_EXTS = ('.py', '.java', '.go', '.js', '.ts', '.tsx', '.cpp', '.cs', '.rs')
    PRIORITY_KEYWORDS = ['main', 'app', 'core', 'api', 'service', 'utils', 'controller', 'model', 'config']

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

# === 多语言符号提取 ===
def _extract_symbols(content, file_path):
    """
    根据文件类型，智能提取 Class 和 Function 签名生成地图。
    """
    ext = file_path.split('.')[-1].lower() if '.' in file_path else ""
    
    # 1. Python 使用 AST (最准)
    if ext == 'py':
        return _extract_symbols_python(content)
    
    # 2. 其他语言使用正则 (Java, TS, JS, Go, C++)
    elif ext in ['java', 'ts', 'tsx', 'js', 'jsx', 'go', 'cpp', 'cs', 'rs']:
        return _extract_symbols_regex(content, ext)
        
    return []

def _extract_symbols_python(content):
    try:
        tree = ast.parse(content)
        symbols = []
        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                symbols.append(f"  [C] {node.name}")
                for sub in node.body:
                    if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        if not sub.name.startswith("_") or sub.name == "__init__":
                            symbols.append(f"    - {sub.name}")
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                symbols.append(f"  [F] {node.name}")
        return symbols
    except:
        return []
    
def _extract_symbols_regex(content, ext):
    """
    针对类 C 语言的通用正则提取。
    """
    symbols = []
    lines = content.split('\n')
    
    # 定义各语言的正则模式
    patterns = {
        'java': {
            'class': re.compile(r'(?:public|protected|private)?\s*(?:static|abstract)?\s*(?:class|interface|enum)\s+([a-zA-Z0-9_]+)'),
            'func': re.compile(r'(?:public|protected|private)\s+(?:static\s+)?[\w<>[\]]+\s+([a-zA-Z0-9_]+)\s*\(')
        },
        'ts': { 
            'class': re.compile(r'class\s+([a-zA-Z0-9_]+)'),
            'func': re.compile(r'(?:function\s+([a-zA-Z0-9_]+)|const\s+([a-zA-Z0-9_]+)\s*=\s*(?:async\s*)?\(|([a-zA-Z0-9_]+)\s*\([^)]*\)\s*[:\{])') 
        },
        'go': {
            'class': re.compile(r'type\s+([a-zA-Z0-9_]+)\s+(?:struct|interface)'),
            'func': re.compile(r'func\s+(?:(?:\(.*\)\s+)?([a-zA-Z0-9_]+)|([a-zA-Z0-9_]+)\()')
        }
    }
    
    lang_key = 'java' if ext in ['java', 'cs', 'cpp', 'rs'] else 'go' if ext == 'go' else 'ts'
    rules = patterns.get(lang_key, patterns['java'])
    
    count = 0 
    for line in lines:
        line = line.strip()
        # === 正则解析优化 (过滤更多干扰项) ===
        if not line or line.startswith(("//", "/*", "*", "#", "print", "console.")): continue
        if count > 30: break # 单文件限制

        # 匹配类
        c_match = rules['class'].search(line)
        if c_match:
            name = next((g for g in c_match.groups() if g), "Unknown")
            symbols.append(f"  [C] {name}")
            count += 1
            continue
            
        # 匹配方法
        if line.endswith('{') or "=>" in line: 
            f_match = rules['func'].search(line)
            if f_match:
                name = next((g for g in f_match.groups() if g), None)
                # 增强过滤
                if name and len(name) > 2 and name not in ['if', 'for', 'switch', 'while', 'catch', 'return']:
                    symbols.append(f"    - {name}")
                    count += 1

    return symbols

async def generate_repo_map(repo_url, file_list, limit=AgentConfig.INITIAL_MAP_LIMIT) -> Tuple[str, Set[str]]:
    """
    生成增强版仓库地图 (多语言版)
    Returns:
        str: 地图字符串
        set: 已包含在地图中的文件路径集合 (用于增量更新查重)
    """
    # === 扩展高优先级文件列表 (使用配置) ===
    priority_files = [
        f for f in file_list 
        if f.endswith(AgentConfig.PRIORITY_EXTS) and 
        (f.count('/') <= 2 or any(k in f.lower() for k in AgentConfig.PRIORITY_KEYWORDS))
    ]
    
    # 去重并截取
    targets = sorted(list(set(priority_files)))[:limit]
    remaining = [f for f in file_list if f not in targets]
    
    repo_map_lines = []
    mapped_files_set = set(targets) # === 记录已映射的文件 ===
    
    async def process_file(path):
        content = await asyncio.to_thread(get_file_content, repo_url, path)
        if not content: return f"{path} (Read Failed)"
        
        symbols = await asyncio.to_thread(_extract_symbols, content, path)
        
        if symbols:
            return f"{path}\n" + "\n".join(symbols)
        return path

    repo_map_lines.append(f"--- Key Files Structure (Top {len(targets)}) ---")
    
    tasks = [process_file(f) for f in targets]
    results = await asyncio.gather(*tasks)
    repo_map_lines.extend(results)
    
    if remaining:
        repo_map_lines.append("\n--- Other Files ---")
        if len(remaining) > 300:
            repo_map_lines.extend(remaining[:300])
            repo_map_lines.append(f"... ({len(remaining)-300} more files)")
        else:
            repo_map_lines.extend(remaining)
            
    return "\n".join(repo_map_lines), mapped_files_set


async def agent_stream(repo_url: str, session_id: str, language: str = "en"):
    short_id = session_id[-6:] if session_id else "unknown"
    yield json.dumps({"step": "init", "message": f"🚀 [Session: {short_id}] Connecting to GitHub..."})
    await asyncio.sleep(0.5)
    
    try:
        vector_db = store_manager.get_store(session_id)
        vector_db.reset_collection() 
        
        chunker = UniversalChunker(min_chunk_size=50)

        file_list = await asyncio.to_thread(get_repo_structure, repo_url)
        if not file_list:
            raise Exception("Repository is empty or unreadable.")

        yield json.dumps({"step": "fetched", "message": f"📦 Found {len(file_list)} files. Building Repo Map (AST Parsing)..."})        
        
        # === 接收 mapped_files 用于后续查重 ===
        file_tree_str, mapped_files = await generate_repo_map(repo_url, file_list, limit=AgentConfig.INITIAL_MAP_LIMIT)
        
        visited_files = set()
        context_summary = ""
        readme_file = next((f for f in file_list if f.lower().endswith("readme.md")), None)

        for round_idx in range(AgentConfig.MAX_ROUNDS):
            yield json.dumps({"step": "thinking", "message": f"🕵️ [Round {round_idx+1}/{AgentConfig.MAX_ROUNDS}] DeepSeek is analyzing Repo Map..."})
            
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
            Select 1-{AgentConfig.FILES_PER_ROUND} MOST CRITICAL files to read next to understand the core logic.
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
                temperature=0.1,
                timeout=AgentConfig.LLM_TIMEOUT 
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
            
            # === 并发模型缺陷优化 (并行下载处理) ===
            async def process_single_file(file_path):

                # 如果非常需要在UI显示下载进度，只能在外部模拟，或者引入Queue，但在 gather 中最简单的办法是去掉它
                content = get_file_content(repo_url, file_path)
                if not content: return None

                # 1. 摘要与 Context
                lines = content.split('\n')[:50]
                preview = "\n".join(lines)
                file_knowledge = f"\n--- File: {file_path} ---\n{preview}\n"
                
                # 2. Repo Map 增量更新与查重
                new_map_entry = None
                if file_path not in mapped_files:
                    symbols = await asyncio.to_thread(_extract_symbols, content, file_path)
                    if symbols:
                        new_map_entry = f"{file_path}\n" + "\n".join(symbols)

                # 3. 切片与入库
                chunks = await asyncio.to_thread(chunker.chunk_file, content, file_path)
                if chunks:
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

                return {
                    "path": file_path,
                    "knowledge": file_knowledge,
                    "map_entry": new_map_entry
                }

            # 提示开始并发下载
            yield json.dumps({"step": "download", "message": f"📥 Starting parallel download for {len(valid_files)} files..."})

            # 启动并发任务
            tasks = [process_single_file(f) for f in valid_files]
            results = await asyncio.gather(*tasks)

            # 聚合结果
            download_count = 0
            for res in results:
                if not res: continue
                download_count += 1
                visited_files.add(res["path"])
                context_summary += res["knowledge"]
                
                # 增量更新 Map
                if res["map_entry"]:
                    file_tree_str = f"{res['map_entry']}\n\n{file_tree_str}"
                    mapped_files.add(res["path"])
            
            # === 硬编码截断解耦 ===
            context_summary = context_summary[:AgentConfig.MAX_CONTEXT_LENGTH]
            
            global_context_data = {
                "file_tree": file_tree_str,
                "summary": context_summary[:8000]
            }
            vector_db.save_context(repo_url, global_context_data)
            
            yield json.dumps({"step": "indexing", "message": f"🧠 [Round {round_idx+1}] Processed {download_count} files. Knowledge graph updated."})

        # Final Report
        yield json.dumps({"step": "generating", "message": "📝 Generating technical report..."})
        

        repo_map_injection = f"""
        [Project Repo Map (Structure)]
        {file_tree_str}
        """

        # === 根据语言选择 Prompt ===
        if language == "zh":
            # --- 中文 Prompt ---
            system_role = "你是一位务实的技术专家。目标是为开发者创建一个'3页纸'架构概览，让他们能在5分钟内看懂这个仓库。重点关注架构和数据流，不要纠结细节。"
            analysis_user_content = f"""
            [角色]
            你是一位务实的技术专家（Tech Lead）。
            
            [输入数据]
            {repo_map_injection}  <-- 插入 Repo Map

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
            {repo_map_injection}  <-- Injecting Repo Map

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
        
        # === 增加 timeout 防止长文本生成时断连 ===
        stream = await client.chat.completions.create(
            model=settings.MODEL_NAME,
            messages=[
                {"role": "system", "content": "You are a pragmatic Tech Lead. Focus on architecture and data flow, not implementation details."},
                {"role": "user", "content": analysis_user_content}
            ],
            stream=True,
            timeout=AgentConfig.LLM_TIMEOUT  # 使用 Config
        )
        
        # === 增加 try-except 捕获流式传输中断 ===
        try:
            async for chunk in stream:
                if chunk.choices[0].delta.content:
                    yield json.dumps({"step": "report_chunk", "chunk": chunk.choices[0].delta.content})
        except (httpx.ReadError, httpx.ConnectError) as e:
            yield json.dumps({"step": "error", "message": f"⚠️ Network Timeout during generation: {str(e)}"})
            return

        yield json.dumps({"step": "finish", "message": "✅ Analysis Complete!"})

    except Exception as e:
        # === 全局异常捕获 ===
        import traceback
        traceback.print_exc()
        
        # 提取友好的错误信息
        error_msg = str(e)
        if "401" in error_msg:
            ui_msg = "❌ GitHub Token Invalid. Please check your settings."
        elif "403" in error_msg:
            ui_msg = "❌ GitHub API Rate Limit Exceeded. Try again later or add a Token."
        elif "404" in error_msg:
            ui_msg = "❌ Repository Not Found. Check the URL."
        elif "Timeout" in error_msg or "ConnectError" in error_msg:
            ui_msg = "❌ Network Timeout. LLM or GitHub is not responding."
        else:
            ui_msg = f"💥 System Error: {error_msg}"
            
        yield json.dumps({"step": "error", "message": ui_msg})
        return # 终止流