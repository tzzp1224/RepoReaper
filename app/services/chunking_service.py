# 文件路径: app/services/chunking_service.py
import ast
import re
import os

class UniversalChunker:
    def __init__(self, min_chunk_size=50, max_chunk_size=2000):
        self.min_chunk_size = min_chunk_size
        self.max_chunk_size = max_chunk_size

    def chunk_file(self, content: str, file_path: str):
        if not content:
            return []
        
        ext = os.path.splitext(file_path)[1].lower()
        
        # 1. Python 专用优化 (AST)
        if ext == '.py':
            return self._chunk_python(content, file_path)
        
        # 2. C-Style 语言优化 (Java, JS, TS, Go, C++, C#)
        # 通过花括号 {} 层级识别代码块
        elif ext in ['.java', '.js', '.ts', '.jsx', '.tsx', '.go', '.cpp', '.c', '.h', '.cs', '.php', '.rs']:
            return self._chunk_c_style(content, file_path)
        
        # 3. 其他文件 (Markdown, 配置等) -> 兜底按行切分
        else:
            return self._fallback_chunking(content, file_path)

    def _chunk_python(self, content, file_path):
        """原有的 Python AST 逻辑"""
        chunks = []
        try:
            tree = ast.parse(content)
        except SyntaxError:
            return self._fallback_chunking(content, file_path)

        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                class_code = ast.get_source_segment(content, node)
                if not class_code: continue
                if len(class_code) <= self.max_chunk_size:
                    chunks.append(self._create_chunk(class_code, file_path, "class", node.name, node.lineno, node.name))
                else:
                    chunks.extend(self._chunk_large_python_class(node, content, file_path))
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                func_code = ast.get_source_segment(content, node)
                if func_code and len(func_code) >= self.min_chunk_size:
                    chunks.append(self._create_chunk(func_code, file_path, "function", node.name, node.lineno))
        
        if not chunks and len(content) > 0:
             return self._fallback_chunking(content, file_path)
        return chunks

    def _chunk_large_python_class(self, class_node, content, file_path):
        chunks = []
        class_name = class_node.name
        docstring = ast.get_docstring(class_node) or ""
        context_header = f"class {class_name}:\n    \"\"\"{docstring}\"\"\"\n    # ... (Parent Context)\n"

        for node in class_node.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                method_code = ast.get_source_segment(content, node)
                if not method_code: continue
                full_chunk_content = context_header + "\n" + method_code
                chunks.append(self._create_chunk(full_chunk_content, file_path, "method", node.name, node.lineno, class_name))
        return chunks

    def _chunk_c_style(self, content, file_path):
        """
        针对使用 {} 的语言（Java/JS/Go等）的启发式切分。
        逻辑：扫描顶级的大括号块，将其视为一个 Chunk。
        """
        chunks = []
        lines = content.split('\n')
        current_chunk_lines = []
        brace_balance = 0
        in_block = False
        start_line = 0
        
        # 简单的正则用于提取类名或方法名 (仅用于 Metadata，不影响切分)
        # 匹配: class X, function X, public void X, const X = () =>
        name_pattern = re.compile(r'(class|function|interface|func|const|var|let|public|private|protected)\s+([a-zA-Z0-9_]+)')

        for i, line in enumerate(lines):
            # 简单的括号计数 (忽略字符串/注释内的括号，这里做简化处理)
            open_braces = line.count('{')
            close_braces = line.count('}')
            
            if not in_block and open_braces > 0:
                # 发现新块开始
                if current_chunk_lines: # 保存之前的零散代码（如 import）
                    # 如果之前积累的太少，就跟当前块合并，否则单独存
                    pass 
                in_block = True
                start_line = i + 1
            
            current_chunk_lines.append(line)
            brace_balance += (open_braces - close_braces)

            # 当层级回归 0 且处于块内时，说明顶层块结束
            if in_block and brace_balance <= 0:
                block_content = "\n".join(current_chunk_lines)
                if len(block_content) >= self.min_chunk_size:
                    # 尝试提取名称
                    match = name_pattern.search(block_content)
                    name = match.group(2) if match else "block"
                    type_ = "class" if "class " in block_content[:100] else "function"
                    
                    chunks.append(self._create_chunk(block_content, file_path, type_, name, start_line))
                    current_chunk_lines = []
                    in_block = False
                    brace_balance = 0
                else:
                    # 块太小，继续积累（可能是内部小闭包）
                    pass

        # 处理文件末尾剩余内容
        if current_chunk_lines:
            content_left = "\n".join(current_chunk_lines)
            if len(content_left) > self.min_chunk_size:
                chunks.append(self._create_chunk(content_left, file_path, "text_chunk", "tail", start_line))

        if not chunks:
            return self._fallback_chunking(content, file_path)
            
        return chunks

    def _fallback_chunking(self, content, file_path):
        """兜底策略：固定行数切分"""
        chunks = []
        lines = content.split('\n')
        chunk_size = 100
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