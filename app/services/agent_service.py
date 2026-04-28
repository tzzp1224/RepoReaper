# 文件路径: app/services/agent_service.py
import json
import asyncio
import traceback
import re
import ast
import httpx
import time
from typing import Set, Tuple, List
from datetime import datetime
from app.core.config import settings, agent_config
from app.utils.llm_client import client
from app.utils.repo_lock import RepoLock
from app.services.github_service import get_repo_structure, get_file_content
from app.services.vector_service import store_manager
from app.services.chunking_service import UniversalChunker, ChunkingConfig
from app.services.tracing_service import tracing_service
from app.utils.session import generate_repo_lock_key

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
        if count > agent_config.max_symbols_per_file: break

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

async def generate_repo_map(repo_url, file_list, limit=agent_config.initial_map_limit) -> Tuple[str, Set[str]]:
    """
    生成增强版仓库地图 (多语言版)
    Returns:
        str: 地图字符串
        set: 已包含在地图中的文件路径集合 (用于增量更新查重)
    """
    # === 扩展高优先级文件列表 (使用配置) ===
    priority_files = [
        f for f in file_list 
        if f.endswith(agent_config.priority_exts) and 
        (f.count('/') <= 2 or any(k in f.lower() for k in agent_config.priority_keywords))
    ]
    
    # 去重并截取
    targets = sorted(list(set(priority_files)))[:limit]
    remaining = [f for f in file_list if f not in targets]
    
    repo_map_lines = []
    mapped_files_set = set(targets) # === 记录已映射的文件 ===
    
    async def process_file(path):
        content = await get_file_content(repo_url, path)
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


async def agent_stream(repo_url: str, session_id: str, language: str = "en", regenerate_only: bool = False):
    """
    主分析流程。
    
    Args:
        repo_url: GitHub 仓库 URL
        session_id: 会话 ID
        language: 报告语言 (zh/en)
        regenerate_only: 如果为 True，跳过索引步骤，直接使用已有数据生成新语言报告
    """
    short_id = session_id[-6:] if session_id else "unknown"
    lock_key = _resolve_repo_lock_key(repo_url, session_id)
    
    # === 追踪初始化 ===
    trace_id = tracing_service.start_trace(
        trace_name="agent_analysis",
        session_id=session_id,
        metadata={
            "repo_url": repo_url,
            "language": language,
            "regenerate_only": regenerate_only,
            "lock_key": lock_key,
        }
    )
    start_time = time.time()
    
    # === 检查是否有其他用户正在分析同一仓库 ===
    if not regenerate_only:
        if await RepoLock.is_locked(lock_key):
            waiting_event = json.dumps({
                "step": "waiting", 
                "message": f"⏳ Another user is analyzing this repository. Please wait..."
            })
            tracing_service.record_step(
                step_name="waiting",
                status="info",
                message="Another user is analyzing this repository",
                payload={"session_id": session_id, "lock_key": lock_key},
            )
            yield waiting_event

    def _record_step_from_stream_event(raw_event: str) -> None:
        try:
            payload = json.loads(raw_event)
        except Exception:
            return
        if not isinstance(payload, dict):
            return
        step_name = str(payload.get("step") or "stream_event")
        message = payload.get("message")
        if step_name == "error":
            status = "error"
        elif step_name in {"finish", "complete", "completed"}:
            status = "completed"
        else:
            status = "info"
        tracing_service.record_step(
            step_name=step_name,
            status=status,
            message=str(message) if message is not None else None,
            payload=payload,
        )
    
    # === 获取仓库锁 (仅写操作需要) ===
    try:
        async with RepoLock.acquire(lock_key):
            async for event in _agent_stream_inner(
                repo_url, session_id, language, regenerate_only, 
                short_id, trace_id, start_time
            ):
                _record_step_from_stream_event(event)
                yield event
    except TimeoutError as e:
        timeout_event = json.dumps({
            "step": "error",
            "message": f"❌ {str(e)}. The repository is being analyzed by another user."
        })
        _record_step_from_stream_event(timeout_event)
        yield timeout_event
    finally:
        tracing_service.end_trace(
            {
                "session_id": session_id,
                "repo_url": repo_url,
                "language": language,
                "lock_key": lock_key,
                "trace_id": trace_id,
            }
        )


def _resolve_repo_lock_key(repo_url: str, session_id: str) -> str:
    """
    解析仓库级锁 key。

    优先使用 repo_url 归一化 key；异常时回退 session_id，确保不阻断主流程。
    """
    try:
        if repo_url and repo_url.strip():
            return generate_repo_lock_key(repo_url)
    except Exception:
        pass
    return session_id or "unknown_repo_lock"


async def _agent_stream_inner(
    repo_url: str, session_id: str, language: str, regenerate_only: bool,
    short_id: str, trace_id: str, start_time: float
):
    """
    实际的分析流程 (在锁保护下执行)
    """
    try:
        vector_db = store_manager.get_store(session_id)
        
        # 调试日志：确认 session 隔离
        print(f"🔍 [DEBUG] session_id: {session_id}, collection: {vector_db.collection_name}, context_file: {vector_db._context_file}")
        
        # === regenerate_only 模式：跳过索引，直接生成报告 ===
        if regenerate_only:
            yield json.dumps({"step": "init", "message": f"🔄 [Session: {short_id}] Regenerating report in {language}..."})
            await asyncio.sleep(0.3)
            
            # 从已有索引加载上下文
            context = vector_db.load_context()
            if not context:
                yield json.dumps({"step": "error", "message": "❌ No existing index found. Please analyze the repository first."})
                return
            
            # 正确读取 global_context 内的字段
            global_ctx = context.get("global_context", {})
            file_tree_str = global_ctx.get("file_tree", "")
            context_summary = global_ctx.get("summary", "")
            visited_files = set()  # regenerate 模式不需要这个，但报告生成需要引用
            
            # 验证上下文与请求的仓库匹配
            stored_repo_url = context.get("repo_url", "")
            if stored_repo_url and repo_url not in stored_repo_url and stored_repo_url not in repo_url:
                print(f"⚠️ [WARNING] repo_url mismatch! Request: {repo_url}, Stored: {stored_repo_url}")
            
            yield json.dumps({"step": "generating", "message": f"📝 Generating report in {'Chinese' if language == 'zh' else 'English'}..."})
        else:
            # === 正常分析模式 ===
            yield json.dumps({"step": "init", "message": f"🚀 [Session: {short_id}] Connecting to GitHub..."})
            await asyncio.sleep(0.5)
            
            await vector_db.reset()  # 使用异步方法
            
            chunker = UniversalChunker(config=ChunkingConfig(min_chunk_size=50))

            file_list = await get_repo_structure(repo_url)
            if not file_list:
                raise Exception("Repository is empty or unreadable.")

            yield json.dumps({"step": "fetched", "message": f"📦 Found {len(file_list)} files. Building Repo Map (AST Parsing)..."})        
            
            # === 接收 mapped_files 用于后续查重 + 计时 ===
            map_start = time.time()
            file_tree_str, mapped_files = await generate_repo_map(repo_url, file_list, limit=agent_config.initial_map_limit)
            map_latency_ms = (time.time() - map_start) * 1000
            tracing_service.add_event("repo_map_generated", {"latency_ms": map_latency_ms, "files_mapped": len(mapped_files)})
            
            visited_files = set()
            context_summary = ""
            readme_file = next((f for f in file_list if f.lower().endswith("readme.md")), None)

            for round_idx in range(agent_config.max_rounds):
                yield json.dumps({"step": "thinking", "message": f"🕵️ [Round {round_idx+1}/{agent_config.max_rounds}] DeepSeek is analyzing Repo Map..."})
                
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
                Select 1-{agent_config.files_per_round} MOST CRITICAL files to read next to understand the core logic.
                Focus on files that seem to contain main logic based on the Repo Map symbols.
                
                [Constraint]
                Return ONLY a raw JSON list of strings. No markdown.
                Example: ["src/main.py", "app/auth.py"]
                """
                
                if not client:
                     yield json.dumps({"step": "error", "message": "❌ LLM Client Not Initialized."})
                     return
                
                # === Token & Latency Tracing ===
                llm_start_time = time.time()
                plan_messages = [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_content}
                ]
                
                response = await client.chat.completions.create(
                    model=settings.default_model_name,
                    messages=plan_messages,
                    temperature=0.1,
                    timeout=settings.LLM_TIMEOUT 
                )
                
                llm_latency_ms = (time.time() - llm_start_time) * 1000
                raw_content = response.choices[0].message.content
                
                # 记录 Token 使用量
                usage = getattr(response, 'usage', None)
                tracing_service.record_llm_generation(
                    model=settings.default_model_name,
                    prompt_messages=plan_messages,
                    generated_text=raw_content,
                    total_latency_ms=llm_latency_ms,
                    prompt_tokens=usage.prompt_tokens if usage else None,
                    completion_tokens=usage.completion_tokens if usage else None,
                    total_tokens=usage.total_tokens if usage else None,
                    is_streaming=False,
                    metadata={"step": "file_selection", "round": round_idx + 1}
                )
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
                    try:
                        file_start = time.time()
                        
                        # 🔧 异步 GitHub API (已优化为非阻塞)
                        tool_start = time.time()
                        content = await get_file_content(repo_url, file_path)
                        tracing_service.record_tool_call(
                            tool_name="github.get_file_content",
                            parameters={"repo_url": repo_url, "file_path": file_path},
                            result={
                                "has_content": bool(content),
                                "content_chars": len(content or ""),
                            },
                            latency_ms=(time.time() - tool_start) * 1000,
                            success=bool(content),
                            error=None if content else "empty_content",
                        )
                        if not content: 
                            tracing_service.add_event("file_read_failed", {"file": file_path})
                            return None

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
                                    "class": meta.get("class") or "",
                                    "start_line": meta.get("start_line"),
                                    "end_line": meta.get("end_line"),
                                })
                            if documents:
                                try:
                                    add_start = time.time()
                                    await vector_db.add_documents(documents, metadatas)
                                    tracing_service.record_tool_call(
                                        tool_name="vector.add_documents",
                                        parameters={
                                            "file_path": file_path,
                                            "documents": len(documents),
                                        },
                                        result={"indexed_documents": len(documents)},
                                        latency_ms=(time.time() - add_start) * 1000,
                                        success=True,
                                    )
                                except Exception as e:
                                    tracing_service.record_tool_call(
                                        tool_name="vector.add_documents",
                                        parameters={
                                            "file_path": file_path,
                                            "documents": len(documents),
                                        },
                                        result=None,
                                        latency_ms=0.0,
                                        success=False,
                                        error=str(e),
                                    )
                                    print(f"❌ 索引错误 {file_path}: {e}")
                                    # 不中断，继续处理其他文件
                                    return None
                        
                        file_latency_ms = (time.time() - file_start) * 1000
                        tracing_service.add_event("file_processed", {
                            "file": file_path,
                            "latency_ms": file_latency_ms,
                            "chunks_count": len(chunks) if chunks else 0
                        })

                        return {
                            "path": file_path,
                            "knowledge": file_knowledge,
                            "map_entry": new_map_entry
                        }
                    except Exception as e:
                        print(f"❌ 处理文件错误 {file_path}: {e}")
                        return None

                # 提示开始并发下载
                yield json.dumps({"step": "download", "message": f"📥 Starting parallel download for {len(valid_files)} files..."})

                # 启动并发任务 (return_exceptions=True 防止单个失败导致整个中断)
                tasks = [process_single_file(f) for f in valid_files]
                results = await asyncio.gather(*tasks, return_exceptions=True)

                # 聚合结果
                download_count = 0
                for res in results:
                    if not res or isinstance(res, Exception): 
                        if isinstance(res, Exception):
                            print(f"❌ Task 异常: {res}")
                        continue
                    download_count += 1
                    visited_files.add(res["path"])
                    context_summary += res["knowledge"]
                    
                    # 增量更新 Map
                    if res["map_entry"]:
                        file_tree_str = f"{res['map_entry']}\n\n{file_tree_str}"
                        mapped_files.add(res["path"])
                
                # === 硬编码截断解耦 ===
                context_summary = context_summary[:agent_config.max_context_length]
                
                global_context_data = {
                    "file_tree": file_tree_str,
                    "summary": context_summary[:8000]
                }
                await vector_db.save_context(repo_url, global_context_data)
                
                yield json.dumps({"step": "indexing", "message": f"🧠 [Round {round_idx+1}] Processed {download_count} files. Knowledge graph updated."})

            # Final Report (正常分析模式下的提示)
            yield json.dumps({"step": "generating", "message": "📝 Generating technical report..."})
        
        # === 报告生成 (两种模式共用) ===
        
        # === P0: 向量检索补充关键代码片段 ===
        yield json.dumps({"step": "enriching", "message": "🔍 Retrieving key code snippets..."})
        
        key_queries = [
            "main entry point initialization startup",
            "core business logic handler processor",
            "API routes endpoints controllers",
            "database models schema ORM",
            "authentication authorization middleware"
        ]
        
        retrieved_snippets = []
        try:
            await vector_db.initialize()
            for query in key_queries:
                results = await vector_db.search_hybrid(query, top_k=2)
                for r in results:
                    snippet = r.get("content", "")[:400]
                    file_path = r.get("file", "unknown")
                    if snippet and snippet not in [s.split("]")[1] if "]" in s else s for s in retrieved_snippets]:
                        retrieved_snippets.append(f"[{file_path}]\n{snippet}")
        except Exception as e:
            print(f"⚠️ 向量检索失败: {e}")
        
        code_snippets_section = "\n\n".join(retrieved_snippets[:8]) if retrieved_snippets else ""
        
        # === P1: 依赖文件解析 ===
        dep_files = ["requirements.txt", "pyproject.toml", "package.json", "go.mod", "Cargo.toml", "pom.xml", "build.gradle"]
        dependencies_info = ""
        
        # 获取 file_list（regenerate_only 模式下需要重新获取）
        if regenerate_only:
            try:
                temp_file_list = await get_repo_structure(repo_url)
            except:
                temp_file_list = []
        else:
            temp_file_list = file_list if 'file_list' in dir() else []
        
        for dep_file in dep_files:
            matching = [f for f in temp_file_list if f.endswith(dep_file)]
            for f in matching[:1]:  # 只取第一个匹配
                try:
                    content = await get_file_content(repo_url, f)
                    if content:
                        dependencies_info += f"\n[{f}]\n{content[:800]}\n"
                except:
                    pass
        
        # 构建增强的上下文
        enhanced_context = f"""
        {context_summary[:12000]}
        
        [Key Code Snippets (Retrieved by Semantic Search)]
        {code_snippets_section}
        
        [Project Dependencies]
        {dependencies_info if dependencies_info else "No dependency file found."}
        """

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
            {repo_map_injection}

            分析的文件: {list(visited_files)}
            
            [代码知识库与关键片段]
            {enhanced_context}
            
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
            - 在连线上标注数据流 (如 "HTTP", "SQL")。
            - **风格**: 保持概念清晰简单，节点数量控制在 8 个以内。
            
            **⚠️ Mermaid 语法严格要求 (v10.x)**:
            1. **所有节点文本必须用双引号包裹**: `A["用户界面"]` ✓, `A[用户界面]` ✗
            2. **所有连线标签必须用双引号包裹**: `-->|"HTTP请求"|` ✓, `-->|HTTP请求|` ✗
            3. **禁止使用特殊字符**: 不要在文本中使用 `<br/>`, `/`, `(`, `)`, `&`, `<`, `>` 等
            4. **使用简短英文ID**: 节点ID用简短英文如 `A`, `B`, `Client`, `API`
            5. **subgraph 标题也需引号**: `subgraph "核心服务"` ✓
            6. **数据库节点**: 使用 `[("数据库")]` 格式
            
            - **正确示例**:
            ```mermaid
            graph TD
                Client["客户端"] -->|"HTTP请求"| API["API网关"]
                API --> Service["业务服务"]
                Service --> DB[("数据库")]
                Service -->|"调用"| External["外部服务"]
            ```

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
            
            **⚠️ sequenceDiagram 语法要求**:
            1. **participant 别名格式**: `participant API as "API服务"` ✓
            2. **消息文本用双引号**: `User->>API: "发起请求"` ✓
            3. **避免特殊字符**: 不要在消息中使用 `/`, `&`, `<`, `>` 等
            
            - **正确示例**:
            ```mermaid
            sequenceDiagram
                participant User as "用户"
                participant API as "API服务"
                participant DB as "数据库"
                User->>API: "发起请求"
                API->>DB: "查询数据"
                DB-->>API: "返回结果"
                API-->>User: "响应数据"
            ```
            
            ## 6. 快速开始 (Quick Start)
            - **前置条件**: (如 Docker, Python 3.9+, .env 配置)
            - **入口**: (如何启动主逻辑？如 `python main.py`)
            """
        else:
            analysis_user_content = f"""
            [Role]
            You are a **Pragmatic Tech Lead**. Your goal is to create a **"3-Pages" Architecture Overview** for a developer who wants to understand this repo in 5 minutes.
            [Input Data]
            {repo_map_injection}

            Files analyzed: {list(visited_files)}
            
            [Code Knowledge & Key Snippets]
            {enhanced_context}
            
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
            - Label the edges with data flow (e.g., "HTTP", "SQL").
            - **Style**: Keep it simple and conceptual. Limit to 8 nodes max.
            
            **⚠️ Mermaid Syntax Rules (v10.x - MUST FOLLOW)**:
            1. **Wrap ALL node text in double quotes**: `A["User Client"]` ✓, `A[User Client]` ✗
            2. **Wrap ALL edge labels in double quotes**: `-->|"HTTP Request"|` ✓, `-->|HTTP Request|` ✗
            3. **NO special characters in text**: Avoid `/`, `()`, `&`, `<>`, `<br/>` in labels
            4. **Use short alphanumeric IDs**: e.g., `A`, `B`, `Client`, `API`, `DB`
            5. **Subgraph titles need quotes**: `subgraph "Core Services"` ✓
            6. **Database node format**: Use `[("Database")]` for cylinder shape
            
            - **Correct Example**:
            ```mermaid
            graph TD
                Client["User Client"] -->|"HTTP Request"| API["API Gateway"]
                API --> Service["Business Service"]
                Service --> DB[("Database")]
                Service -->|"Calls"| External["External API"]
            ```

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
            
            **⚠️ sequenceDiagram Syntax Rules**:
            1. **Wrap participant aliases in quotes**: `participant API as "API Server"` ✓
            2. **Wrap message text in quotes**: `User->>API: "Send Request"` ✓
            3. **NO special characters**: Avoid `/`, `&`, `<`, `>` in messages
            
            - **Correct Example**:
            ```mermaid
            sequenceDiagram
                participant User as "User"
                participant API as "API Server"
                participant DB as "Database"
                User->>API: "Send Request"
                API->>DB: "Query Data"
                DB-->>API: "Return Result"
                API-->>User: "Send Response"
            ```
            
            ## 6. Quick Start Guide
            - **Prerequisites**: (e.g. Docker, Python 3.9+, .env file)
            - **Entry Point**: (How to run the main logic? e.g. `python main.py` or `uvicorn`)

            """
        
        # === 增加 timeout 防止长文本生成时断连 ===
        report_messages = [
            {"role": "system", "content": "You are a pragmatic Tech Lead. Focus on architecture and data flow, not implementation details."},
            {"role": "user", "content": analysis_user_content}
        ]
        
        stream_start_time = time.time()
        stream = await client.chat.completions.create(
            model=settings.default_model_name,
            messages=report_messages,
            stream=True,
            timeout=settings.LLM_TIMEOUT  # 使用统一配置
        )
        
        # === TTFT & Token Tracking ===
        first_token_received = False
        ttft_ms = None
        generated_text = ""
        completion_tokens_estimate = 0
        
        # === 增加 try-except 捕获流式传输中断 ===
        try:
            async for chunk in stream:
                if chunk.choices[0].delta.content:
                    content = chunk.choices[0].delta.content
                    
                    # 记录 TTFT (首 Token 时间)
                    if not first_token_received:
                        ttft_ms = (time.time() - stream_start_time) * 1000
                        tracing_service.record_ttft(
                            ttft_ms=ttft_ms,
                            model=settings.default_model_name,
                            metadata={"step": "report_generation"}
                        )
                        first_token_received = True
                    
                    generated_text += content
                    completion_tokens_estimate += 1  # 粗略估计每个 chunk 约 1 token
                    yield json.dumps({"step": "report_chunk", "chunk": content})
        except (httpx.ReadError, httpx.ConnectError) as e:
            yield json.dumps({"step": "error", "message": f"⚠️ Network Timeout during generation: {str(e)}"})
            return
        
        # 流结束后记录完整的 LLM 生成信息
        total_latency_ms = (time.time() - stream_start_time) * 1000
        tracing_service.record_llm_generation(
            model=settings.default_model_name,
            prompt_messages=report_messages,
            generated_text=generated_text,
            ttft_ms=ttft_ms,
            total_latency_ms=total_latency_ms,
            completion_tokens=completion_tokens_estimate,
            is_streaming=True,
            metadata={"step": "report_generation", "generated_chars": len(generated_text)}
        )
        
        # === 保存报告 (按语言存储，异步避免阻塞) ===
        await vector_db.save_report(generated_text, language)

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
