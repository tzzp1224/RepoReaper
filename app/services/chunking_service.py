import ast
import re
import os
from dataclasses import dataclass

# --- 配置类 ---
@dataclass
class ChunkingConfig:
    """
    统一管理切分服务的配置参数
    """
    min_chunk_size: int = 50          # 最小分块阈值 (chars)
    max_chunk_size: int = 2000        # 最大分块阈值 (chars)
    fallback_line_size: int = 100     # 兜底策略的行数 (lines)
    max_context_chars: int = 500      # 允许注入到每个Chunk的上下文最大长度
                                      # 超过此长度则不再注入，避免冗余内容撑爆 Token

class UniversalChunker:
    def __init__(self, config: ChunkingConfig = None):
        # 如果未传入配置，使用默认配置
        self.config = config if config else ChunkingConfig()

    def chunk_file(self, content: str, file_path: str):
        if not content:
            return []
        
        ext = os.path.splitext(file_path)[1].lower()
        
        if ext == '.py':
            return self._chunk_python(content, file_path)
        
        # 2. C-Style 语言优化
        elif ext in ['.java', '.js', '.ts', '.jsx', '.tsx', '.go', '.cpp', '.c', '.h', '.cs', '.php', '.rs']:
            return self._chunk_c_style(content, file_path)
        
        else:
            return self._fallback_chunking(content, file_path)

    def _chunk_python(self, content, file_path):
        """
        分级注入策略
        """
        chunks = []
        try:
            tree = ast.parse(content)
        except SyntaxError:
            return self._fallback_chunking(content, file_path)

        import_nodes = []
        other_nodes = []
        function_class_chunks = []

        # A. 遍历与分类
        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                class_code = ast.get_source_segment(content, node)
                if not class_code: continue
                if len(class_code) <= self.config.max_chunk_size:
                    function_class_chunks.append(self._create_chunk(
                        class_code, file_path, "class", node.name, node.lineno, node.name
                    ))
                else:
                    # function_class_chunks 包含了从大类中拆分出的方法
                    function_class_chunks.extend(
                        self._chunk_large_python_class(node, content, file_path)
                    )

            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                func_code = ast.get_source_segment(content, node)
                if func_code and len(func_code) >= self.config.min_chunk_size:
                    function_class_chunks.append(self._create_chunk(
                        func_code, file_path, "function", node.name, node.lineno
                    ))

            else:
                segment = ast.get_source_segment(content, node)
                if segment and len(segment.strip()) > 0:
                    if isinstance(node, (ast.Import, ast.ImportFrom)):
                        import_nodes.append(segment)
                    else:
                        other_nodes.append(segment)

        # B. 决策准备
        has_core_code = len(function_class_chunks) > 0
        others_text = "\n".join(other_nodes).strip()
        should_inject_others = len(others_text) <= self.config.max_context_chars

        # C. 构建 Context Header
        context_parts = []
        # 1. Import 永远注入
        if import_nodes:
            context_parts.append("\n".join(import_nodes))
        # 2. Globals 按需注入
        if others_text and should_inject_others:
            context_parts.append(others_text)

        full_header = "\n".join(context_parts).strip()
        if full_header:
            full_header = f"# --- Context ---\n{full_header}\n# ---------------\n"

        # D. 注入 Header 到核心 Chunk (函数/类)
        # 此时 function_class_chunks 已经包含了大类拆分出来的方法
        # 这里的循环会给它们都加上 Import/Global Context
        for chunk in function_class_chunks:
            chunk["content"] = full_header + chunk["content"]

        # E. 处理溢出 (仅当有核心代码时，才独立存储溢出的 Globals)
        if has_core_code and others_text and not should_inject_others:
             chunks.append(self._create_chunk(
                others_text, file_path, "global_context", "globals", 1
            ))
            
        # F. 纯脚本兜底
        if not has_core_code:
             # 这是一个纯脚本文件 (只有 Import 和 顶层逻辑)
             full_script = (("\n".join(import_nodes) + "\n") if import_nodes else "") + others_text
             if full_script.strip():
                 # 如果脚本太长，不要硬切成一个大块，而是走 Fallback 按行切分
                 if len(full_script) > self.config.max_chunk_size * 1.5: # 1.5倍宽容度
                     return self._fallback_chunking(content, file_path)
                 else:
                     chunks.append(self._create_chunk(
                        full_script, file_path, "script", "main", 1
                    ))

        chunks.extend(function_class_chunks)
        
        if not chunks and len(content.strip()) > 0:
             return self._fallback_chunking(content, file_path)
             
        return chunks

    def _chunk_large_python_class(self, class_node, content, file_path):
        chunks = []
        class_name = class_node.name
        docstring = ast.get_docstring(class_node) or ""
        
        # === 尝试收集类级别的变量定义 ===
        class_vars = []
        for node in class_node.body:
            # 如果是赋值语句，且在方法定义之前 (通常 AST 是有序的)
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                seg = ast.get_source_segment(content, node)
                if seg: class_vars.append(seg)
            # 一旦遇到函数，就停止收集变量，避免把乱七八糟的逻辑也收进去
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                break
        
        vars_text = "\n    ".join(class_vars) 
        if vars_text:
            vars_text = "\n    " + vars_text # 缩进对齐

        # 将变量拼接到 Header 中
        context_header = f"class {class_name}:{vars_text}\n    \"\"\"{docstring}\"\"\"\n    # ... (Parent Context)\n"

        for node in class_node.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                method_code = ast.get_source_segment(content, node)
                if not method_code: continue
                
                full_chunk_content = context_header + "\n" + method_code
                chunks.append(self._create_chunk(
                    full_chunk_content, file_path, "method", node.name, node.lineno, class_name
                ))
        return chunks

    def _chunk_c_style(self, content, file_path):
        """
        解决宏干扰、全局变量丢失、跨行函数头问题
        """
        chunks = []
        if not content: return []

        # === 1. 定义正则 Token ===
        # 使用 Named Groups 避免 startswith 的模糊匹配
        # 顺序至关重要：长匹配优先
        token_pattern = re.compile(
            r'(?P<BLOCK_COMMENT>/\*.*?\*/)|'       # 块注释
            r'(?P<LINE_COMMENT>//[^\n]*)|'         # 行注释
            r'(?P<STRING>"(?:\\.|[^"\\])*")|'      # 双引号字符串
            r'(?P<CHAR>\'(?:\\.|[^\'\\])*\')|'     # 单引号字符
            r'(?P<TEMPLATE>`(?:\\.|[^`\\])*`)|'    # 反引号模板 (JS/Go)
            r'(?P<MACRO>^\s*#.*(?:\\\n.*)*)|'      # 宏定义 (支持跨行)
            r'(?P<BRACE_OPEN>\{)|'                 # 开括号
            r'(?P<BRACE_CLOSE>\})|'                # 闭括号
            r'(?P<SEMICOLON>;)',                   # 分号 (用于分割全局变量和函数头)
            re.DOTALL | re.MULTILINE
        )

        # 全局上下文收集器
        global_context_parts = []
        
        last_index = 0  # 上一个 Token 结束位置
        block_start_index = 0 # 当前 Block (函数/类) 的签名开始位置
        
        brace_balance = 0
        in_structural_block = False # 是否在最外层的类/函数块内
        
        # 暂存当前块的前置文本 (从上一个块结束 到 当前块开始)
        # 这段文本里可能混杂着：全局变量、Import、以及当前函数的签名
        pending_pre_text_start = 0

        # 扫描
        for match in token_pattern.finditer(content):
            kind = match.lastgroup
            start, end = match.span()
            
            # 跳过非结构化 Token (注释、字符串、宏)
            if kind in ('BLOCK_COMMENT', 'LINE_COMMENT', 'STRING', 'CHAR', 'TEMPLATE', 'MACRO'):
                continue
            
            # 忽略括号 () 和 []，只认 {}。
            # C-style 语言只有 {} 定义 Scope Body。忽略 () [] 是为了防止 if(a[i]){...} 误判。
            # 只要 regex 不匹配 () []，它们就被视为普通文本，不会影响 brace_balance。
            if kind == 'BRACE_OPEN':
                if brace_balance == 0:
                    # === 发现一个新的顶层 Block ===
                    in_structural_block = True
                    
                    # 1. 分析 "空隙文本" (从上一个块结束 到 这个 { 之前)
                    gap_text = content[pending_pre_text_start:start]
                    
                    # [策略] 拆分 Global Context 和 Signature
                    # 寻找最后一个分号 ';' 或 '}' (在 gap_text 内部的逻辑结束点)
                    # 倒序查找比较安全。
                    # 如果找不到，说明整段 gap 都是签名 (e.g. void foo() {)
                    # 如果找到，分号前是 Global，分号后是 Signature
                    split_idx = gap_text.rfind(';')
                    if split_idx != -1:
                        # 分号前：归入全局上下文
                        global_part = gap_text[:split_idx+1].strip()
                        if global_part:
                            global_context_parts.append(global_part)
                        # 分号后：是当前函数的签名
                        # 自动处理了跨行函数头，因为 gap_text 包含换行
                        block_signature_start = pending_pre_text_start + split_idx + 1
                    else:
                        # 没有分号，假设全是签名 (e.g. 紧接着上一个块，或者是文件开头)
                        # 但要小心 include/import 等没有分号的语句 (Python 思维在 C 里不适用，C 几乎都有分号)
                        # Go 语言除外 (Go 没分号)。这里做一个简单的 heuristic:
                        # 如果是 Go/JS/TS，可能没有分号。暂且全部视为 Signature，
                        # 除非它看起来像 import。
                        # 这是一个 trade-off。
                        block_signature_start = pending_pre_text_start

                    # 记录当前 Block 真正的“视觉开始点” (包含签名)
                    block_start_index = block_signature_start

                brace_balance += 1

            elif kind == 'BRACE_CLOSE':
                brace_balance -= 1
                
                if brace_balance == 0 and in_structural_block:
                    # === 顶层 Block 结束 ===
                    in_structural_block = False
                    
                    # 提取完整代码块 (Signature + Body)
                    # 范围：block_start_index -> end
                    full_block_text = content[block_start_index:end]
                    
                    # 小块合并策略
                    # 如果块太小 (e.g. Getter/Setter)，暂不生成 Chunk
                    # 架构决策：为了代码完整性，工业界 RAG 通常不建议丢弃小块，
                    # 尤其是 Getter/Setter 可能包含关键字段名。
                    # 这里我们生成 Chunk，但后续入库时可以由 Embedding 模型决定权重。
                    
                    # 提取元数据
                    meta = self._extract_c_style_metadata(full_block_text)
                    start_line = content.count('\n', 0, block_start_index) + 1
                    
                    chunks.append(self._create_chunk(
                        full_block_text, # 暂时不加 Global Header，最后统一加
                        file_path, meta["type"], meta["name"], start_line
                    ))
                    
                    # 更新游标：下一个块的前置文本从这里开始
                    pending_pre_text_start = end

        # === 循环结束后的收尾 ===
        # 处理文件末尾的剩余文本 (Tail)
        tail_text = content[pending_pre_text_start:].strip()
        if tail_text:
            global_context_parts.append(tail_text)

        # === Global Context 重排序 ===
        # 目标顺序: Includes > Macros (#define) > Others (Typedefs/Vars)
        # 简单策略：基于字符串内容的优先级排序
        
        def context_priority(text):
            text = text.strip()
            if text.startswith("#include") or text.startswith("import") or text.startswith("using"):
                return 0 # 最高优先级
            if text.startswith("#define") or text.startswith("#macro"):
                return 1 # 宏定义
            if text.startswith("typedef") or text.startswith("enum") or text.startswith("struct"):
                return 2 # 类型定义
            return 3 # 普通全局变量和其他

        # 稳定排序
        global_context_parts.sort(key=context_priority)

        # === 组装与注入 ===
        full_global_context = "\n".join(global_context_parts).strip()
        
        should_inject = len(full_global_context) <= self.config.max_context_chars
        
        context_header = ""
        if full_global_context and should_inject:
            context_header = f"/* --- Global Context --- */\n{full_global_context}\n/* ---------------------- */\n"
        
        for chunk in chunks:
            chunk["content"] = context_header + chunk["content"]
            
        if (full_global_context and not should_inject) or (not chunks and full_global_context):
            chunks.insert(0, self._create_chunk(
                full_global_context, file_path, "global_context", "header", 1
            ))

        if not chunks:
            return self._fallback_chunking(content, file_path)
            
        return chunks

    def _extract_c_style_metadata(self, code_block):
        """
        从包含签名的代码块中提取元数据 (支持多行签名)
        """
        # 截取到第一个 { 为止
        header_part = code_block.split('{')[0]
        # 压缩多余空白，变成单行以便正则匹配
        header_clean = " ".join(header_part.split())
        
        # 1. Class/Struct/Interface
        type_pattern = re.compile(r'\b(class|struct|interface|enum|record|type)\s+([a-zA-Z0-9_]+)')
        match = type_pattern.search(header_clean)
        if match:
            return {"type": "class", "name": match.group(2)}
            
        # 2. Function
        # 匹配: 单词 + (
        # 排除关键字: if, for, while, switch, catch, return
        func_pattern = re.compile(r'\b([a-zA-Z0-9_]+)\s*\(')
        for match in func_pattern.finditer(header_clean):
            name = match.group(1)
            if name not in {'if', 'for', 'while', 'switch', 'catch', 'return', 'sizeof'}:
                return {"type": "function", "name": name}

        return {"type": "code_block", "name": "anonymous"}

    def _fallback_chunking(self, content, file_path):
        """兜底策略：使用 Config 中的行数设置"""
        chunks = []
        lines = content.split('\n')
        chunk_size = self.config.fallback_line_size
        
        for i in range(0, len(lines), chunk_size):
            chunk_content = "\n".join(lines[i:i+chunk_size])
            chunks.append(self._create_chunk(chunk_content, file_path, "text_chunk", f"chunk_{i}", i+1))
        return chunks

    def _create_chunk(self, content, file_path, type_, name, start_line, class_name=""):
        return {
            "content": content,
            "metadata": {
                "file": file_path,
                "type": type_,
                "name": name,
                "start_line": start_line,
                "class": class_name
            }
        }