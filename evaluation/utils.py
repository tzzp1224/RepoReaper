# 文件路径: evaluation/utils.py
"""
评估模块公共工具函数和常量

将重复的逻辑抽取到这里，保持代码 DRY (Don't Repeat Yourself)
"""

from typing import List


# ============================================================================
# 闲聊/无效 Query 检测
# ============================================================================

CHATTY_PATTERNS: List[str] = [
    # 中文闲聊
    "你好", "您好", "嗨", "在吗", "在不在", "谢谢", "多谢", "再见", "拜拜",
    "什么是", "你是谁", "你叫什么", "帮帮我", "教教我",
    # 英文闲聊
    "hello", "hi", "hey", "thanks", "thank you", "bye", "goodbye",
    "what is", "who are you", "help me", "can you",
    # 单词/简短
    "test", "测试", "ok", "yes", "no",
]

# 代码语言指示符
CODE_INDICATORS: List[str] = [
    # Python
    "def ", "class ", "import ", "from ",
    # JavaScript/TypeScript  
    "function ", "const ", "let ", "var ",
    # Java/C#
    "public ", "private ", "void ",
    # Go
    "func ", "package ",
    # 通用
    "```",  # Markdown 代码块
]


def is_chatty_query(query: str, min_length: int = 5) -> bool:
    """
    检测是否为闲聊/无效 query
    
    Args:
        query: 用户查询
        min_length: 最小有效长度，低于此值视为无效
        
    Returns:
        True 如果是闲聊/无效查询
    """
    if not query:
        return True
    
    query_lower = query.lower().strip()
    
    # 长度检查
    if len(query_lower) < min_length:
        return True
    
    # 模式匹配
    for pattern in CHATTY_PATTERNS:
        if query_lower == pattern or query_lower.startswith(pattern + " "):
            return True
    
    return False


def has_code_indicators(text: str) -> bool:
    """
    检查文本是否包含代码指示符
    
    Args:
        text: 要检查的文本
        
    Returns:
        True 如果包含代码特征
    """
    if not text:
        return False
    
    for indicator in CODE_INDICATORS:
        if indicator in text:
            return True
    
    return False


# ============================================================================
# 文件操作工具
# ============================================================================

def append_jsonl(filepath: str, data: dict) -> None:
    """
    追加一行 JSON 到 JSONL 文件
    
    Args:
        filepath: 文件路径
        data: 要追加的数据字典
    """
    import json
    with open(filepath, 'a', encoding='utf-8') as f:
        f.write(json.dumps(data, ensure_ascii=False) + '\n')


def read_jsonl(filepath: str) -> list:
    """
    读取 JSONL 文件
    
    Args:
        filepath: 文件路径
        
    Returns:
        数据列表
    """
    import json
    import os
    
    if not os.path.exists(filepath):
        return []
    
    results = []
    with open(filepath, 'r', encoding='utf-8') as f:
        for line in f:
            try:
                results.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return results


def safe_truncate(text: str, max_length: int, suffix: str = "\n... [truncated]") -> str:
    """
    安全截断文本
    
    Args:
        text: 原始文本
        max_length: 最大长度
        suffix: 截断后缀
        
    Returns:
        截断后的文本
    """
    if not text or len(text) <= max_length:
        return text
    return text[:max_length] + suffix


def smart_truncate(text: str, max_length: int, keep_ratio: float = 0.7) -> str:
    """
    智能截断：保留开头大部分 + 结尾小部分，适合代码上下文
    
    Args:
        text: 原始文本
        max_length: 最大长度
        keep_ratio: 开头保留比例（默认 70% 开头，30% 结尾）
        
    Returns:
        截断后的文本，保留首尾关键内容
    """
    if not text or len(text) <= max_length:
        return text
    
    separator = "\n\n... [中间内容已省略] ...\n\n"
    available = max_length - len(separator)
    
    if available <= 0:
        return text[:max_length]
    
    head_len = int(available * keep_ratio)
    tail_len = available - head_len
    
    return text[:head_len] + separator + text[-tail_len:]


# ============================================================================
# SFT 数据长度配置
# ============================================================================

class SFTLengthConfig:
    """SFT 训练数据长度配置"""
    
    # Context 限制（检索到的代码上下文）
    MAX_CONTEXT_CHARS = 2500          # 最大字符数 (~800 tokens)
    
    # Answer 限制（模型生成的回答）
    MAX_ANSWER_CHARS = 3000           # 最大字符数 (~1000 tokens)
    
    # Query 限制
    MAX_QUERY_CHARS = 500             # 最大字符数
    
    # 总体限制
    MAX_TOTAL_CHARS = 6000            # 总字符数上限 (~2000 tokens)
    
    # Token 估算（中英文混合，保守估计）
    CHARS_PER_TOKEN = 3               # 平均每 token 的字符数
