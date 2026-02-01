# -*- coding: utf-8 -*-
"""
Session 工具模块

提供基于仓库 URL 的 Session ID 生成和管理
"""

import hashlib
import re
from typing import Optional, Tuple, Dict
from urllib.parse import urlparse

from app.core.config import conversation_config


def normalize_repo_url(url: str) -> str:
    """
    标准化 GitHub 仓库 URL
    
    支持格式:
    - https://github.com/owner/repo
    - https://github.com/owner/repo.git
    - https://github.com/owner/repo/tree/main
    - git@github.com:owner/repo.git
    
    Returns:
        标准化的 URL: https://github.com/owner/repo
    """
    url = url.strip()
    
    # 处理 SSH 格式
    if url.startswith('git@'):
        # git@github.com:owner/repo.git -> https://github.com/owner/repo
        match = re.match(r'git@github\.com:(.+?)(?:\.git)?$', url)
        if match:
            return f"https://github.com/{match.group(1)}"
    
    # 处理 HTTPS 格式
    parsed = urlparse(url)
    path = parsed.path.strip('/')
    
    # 移除 .git 后缀
    if path.endswith('.git'):
        path = path[:-4]
    
    # 只保留 owner/repo 部分
    parts = path.split('/')
    if len(parts) >= 2:
        path = f"{parts[0]}/{parts[1]}"
    
    return f"https://github.com/{path}"


def extract_repo_info(url: str) -> Tuple[str, str]:
    """
    从 URL 提取仓库信息
    
    Returns:
        (owner, repo) 元组
    """
    normalized = normalize_repo_url(url)
    path = urlparse(normalized).path.strip('/')
    parts = path.split('/')
    
    if len(parts) >= 2:
        return parts[0], parts[1]
    return "", ""


def generate_repo_session_id(repo_url: str) -> str:
    """
    基于仓库 URL 生成稳定的 Session ID
    
    同一仓库 URL -> 同一 Session ID
    
    格式: repo_{short_hash}_{owner}_{repo}
    """
    normalized = normalize_repo_url(repo_url)
    owner, repo = extract_repo_info(repo_url)
    
    # 生成短 hash (8 字符)
    url_hash = hashlib.sha256(normalized.encode()).hexdigest()[:8]
    
    # 清理 owner 和 repo 名称
    clean_owner = re.sub(r'[^a-zA-Z0-9]', '', owner)[:10]
    clean_repo = re.sub(r'[^a-zA-Z0-9]', '', repo)[:15]
    
    return f"repo_{url_hash}_{clean_owner}_{clean_repo}"


def is_repo_session_id(session_id: str) -> bool:
    """判断是否为仓库级 Session ID"""
    return session_id.startswith("repo_")


# === 对话历史管理 ===

class ConversationMemory:
    """
    对话记忆管理 - 滑动窗口 + 摘要压缩
    
    特性:
    1. 保留最近 N 轮完整对话
    2. 早期对话自动压缩为摘要
    3. 支持 token 估算
    """
    
    def __init__(
        self,
        max_recent_turns: int = None,
        max_context_tokens: int = None,
        summary_threshold: int = None,
    ):
        # 使用统一配置
        self.max_recent_turns = max_recent_turns or conversation_config.max_recent_turns
        self.max_context_tokens = max_context_tokens or conversation_config.max_context_tokens
        self.summary_threshold = summary_threshold or conversation_config.summary_threshold
        
        self._messages: list = []            # 完整消息历史
        self._summary: Optional[str] = None  # 早期对话摘要
        self._summary_up_to: int = 0         # 摘要覆盖到第 N 条消息
    
    def add_message(self, role: str, content: str) -> None:
        """添加消息"""
        self._messages.append({
            "role": role,
            "content": content
        })
    
    def add_user_message(self, content: str) -> None:
        """添加用户消息"""
        self.add_message("user", content)
    
    def add_assistant_message(self, content: str) -> None:
        """添加助手消息"""
        self.add_message("assistant", content)
    
    def get_context_messages(self) -> list:
        """
        获取用于 LLM 的上下文消息
        
        策略:
        1. 如果消息数 <= max_recent_turns * 2，返回全部
        2. 否则返回: [摘要] + 最近 N 轮
        """
        total_messages = len(self._messages)
        max_messages = self.max_recent_turns * 2  # user + assistant = 1 轮
        
        if total_messages <= max_messages:
            return list(self._messages)
        
        # 需要截断
        recent_messages = self._messages[-max_messages:]
        
        # 如果有摘要，加在前面
        if self._summary:
            return [
                {"role": "system", "content": f"[Earlier conversation summary]\n{self._summary}"}
            ] + recent_messages
        
        return recent_messages
    
    def needs_summarization(self) -> bool:
        """检查是否需要生成摘要"""
        unsummarized = len(self._messages) - self._summary_up_to
        return unsummarized > self.summary_threshold * 2
    
    def get_messages_to_summarize(self) -> list:
        """获取需要摘要的消息"""
        if not self.needs_summarization():
            return []
        
        # 保留最近的，摘要早期的
        end_idx = len(self._messages) - self.max_recent_turns * 2
        return self._messages[self._summary_up_to:end_idx]
    
    def set_summary(self, summary: str, up_to_index: int) -> None:
        """设置摘要"""
        if self._summary:
            # 合并旧摘要
            self._summary = f"{self._summary}\n\n{summary}"
        else:
            self._summary = summary
        self._summary_up_to = up_to_index
    
    def clear(self) -> None:
        """清空对话历史"""
        self._messages = []
        self._summary = None
        self._summary_up_to = 0
    
    def get_turn_count(self) -> int:
        """获取对话轮数"""
        return len(self._messages) // 2
    
    def get_stats(self) -> dict:
        """获取统计信息"""
        return {
            "total_messages": len(self._messages),
            "turn_count": self.get_turn_count(),
            "has_summary": self._summary is not None,
            "summary_covers": self._summary_up_to,
        }


# === 全局对话记忆存储 ===
# key: session_id, value: ConversationMemory
# 纯内存存储，服务重启自动清空
_conversation_memories: Dict[str, ConversationMemory] = {}


def get_conversation_memory(session_id: str) -> ConversationMemory:
    """获取或创建对话记忆"""
    if session_id not in _conversation_memories:
        _conversation_memories[session_id] = ConversationMemory()
    return _conversation_memories[session_id]


def clear_conversation_memory(session_id: str) -> None:
    """清除对话记忆"""
    if session_id in _conversation_memories:
        del _conversation_memories[session_id]


def get_memory_stats() -> dict:
    """获取对话记忆统计"""
    return {
        "total_memories": len(_conversation_memories),
        "sessions": list(_conversation_memories.keys()),
    }
