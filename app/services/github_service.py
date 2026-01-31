# -*- coding: utf-8 -*-
"""
GitHub 服务层

职责:
- 提供业务级别的 GitHub 操作
- 封装底层客户端，提供简洁 API
- 保持向后兼容的函数签名
"""

import logging
from typing import List, Optional, Dict

from app.utils.github_client import (
    GitHubClient,
    GitHubRepo,
    GitHubFile,
    FileFilter,
    GitHubError,
    GitHubNotFoundError,
    get_github_client,
    parse_repo_url,
)

logger = logging.getLogger(__name__)


# ============================================================
# 服务类
# ============================================================

class GitHubService:
    """
    GitHub 服务
    
    提供高层业务操作，内部使用异步客户端。
    
    使用示例:
    ```python
    service = GitHubService()
    
    # 获取仓库文件列表
    files = await service.get_repo_structure("https://github.com/owner/repo")
    
    # 获取文件内容
    content = await service.get_file_content(
        "https://github.com/owner/repo",
        "src/main.py"
    )
    
    # 批量获取文件
    contents = await service.get_files_content(
        "https://github.com/owner/repo",
        ["README.md", "src/main.py", "requirements.txt"]
    )
    ```
    """
    
    def __init__(self, client: Optional[GitHubClient] = None):
        self._client = client
    
    @property
    def client(self) -> GitHubClient:
        """获取客户端 (延迟初始化)"""
        if self._client is None:
            self._client = get_github_client()
        return self._client
    
    async def _get_repo_from_url(self, repo_url: str) -> GitHubRepo:
        """从 URL 获取仓库对象"""
        parsed = parse_repo_url(repo_url)
        if not parsed:
            raise ValueError(f"无效的 GitHub URL: {repo_url}")
        
        owner, name = parsed
        return await self.client.get_repo(owner, name)
    
    async def get_repo_structure(
        self,
        repo_url: str,
        file_filter: Optional[FileFilter] = None
    ) -> List[str]:
        """
        获取仓库文件列表
        
        Args:
            repo_url: GitHub 仓库 URL
            file_filter: 自定义文件过滤器
            
        Returns:
            文件路径列表
        """
        repo = await self._get_repo_from_url(repo_url)
        files = await self.client.get_repo_tree(repo, file_filter)
        return [f.path for f in files]
    
    async def get_file_content(
        self,
        repo_url: str,
        file_path: str
    ) -> Optional[str]:
        """
        获取单个文件内容
        
        Args:
            repo_url: GitHub 仓库 URL
            file_path: 文件路径
            
        Returns:
            文件内容，失败返回 None
        """
        repo = await self._get_repo_from_url(repo_url)
        return await self.client.get_file_content(repo, file_path)
    
    async def get_files_content(
        self,
        repo_url: str,
        file_paths: List[str]
    ) -> Dict[str, Optional[str]]:
        """
        批量获取文件内容 (并发)
        
        Args:
            repo_url: GitHub 仓库 URL
            file_paths: 文件路径列表
            
        Returns:
            {path: content} 字典
        """
        repo = await self._get_repo_from_url(repo_url)
        return await self.client.get_files_content(repo, file_paths, show_progress=True)
    
    async def get_repo_info(self, repo_url: str) -> GitHubRepo:
        """
        获取仓库基本信息
        
        Args:
            repo_url: GitHub 仓库 URL
            
        Returns:
            GitHubRepo 对象
        """
        return await self._get_repo_from_url(repo_url)


# ============================================================
# 全局服务实例
# ============================================================

_github_service: Optional[GitHubService] = None


def get_github_service() -> GitHubService:
    """获取 GitHub 服务单例"""
    global _github_service
    if _github_service is None:
        _github_service = GitHubService()
    return _github_service


# ============================================================
# 兼容旧接口 (同步风格的函数签名，但返回协程)
# ============================================================

# 保留 parse_repo_url 的旧签名兼容
def parse_repo_url_compat(url: str) -> Optional[str]:
    """
    解析 GitHub URL (兼容旧接口)
    
    Returns:
        "owner/repo" 字符串，无效返回 None
    """
    result = parse_repo_url(url)
    if result:
        return f"{result[0]}/{result[1]}"
    return None


async def get_repo_structure(repo_url: str) -> List[str]:
    """
    获取仓库文件列表 (兼容旧接口)
    
    注意: 这是一个异步函数，需要 await 调用
    """
    service = get_github_service()
    return await service.get_repo_structure(repo_url)


async def get_file_content(repo_url: str, file_path: str) -> Optional[str]:
    """
    获取文件内容 (兼容旧接口)
    
    注意: 这是一个异步函数，需要 await 调用
    """
    service = get_github_service()
    return await service.get_file_content(repo_url, file_path)


# 导出
__all__ = [
    "GitHubService",
    "get_github_service",
    "get_repo_structure",
    "get_file_content",
    "parse_repo_url_compat",
    "GitHubError",
    "GitHubNotFoundError",
    "FileFilter",
    "GitHubRepo",
]