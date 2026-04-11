# -*- coding: utf-8 -*-
"""
GitHub 异步客户端

设计原则:
1. 异步非阻塞 - 使用 httpx.AsyncClient
2. 连接池复用 - 单例模式管理客户端生命周期
3. 自动重试 - 集成 tenacity 处理瞬时错误
4. 类型安全 - 完整的类型注解
5. 可扩展 - 易于添加新的 API 端点
"""

import asyncio
import base64
import logging
import os
import re
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any, Set
from contextlib import asynccontextmanager
from urllib.parse import urlparse

import httpx

from app.core.config import settings
from app.utils.retry import llm_retry  # 复用已有的重试装饰器

logger = logging.getLogger(__name__)


# ============================================================
# 数据模型
# ============================================================

@dataclass
class GitHubFile:
    """GitHub 文件信息"""
    path: str
    type: str  # "blob" | "tree"
    size: int = 0
    sha: str = ""
    
    @property
    def is_file(self) -> bool:
        return self.type == "blob"
    
    @property
    def is_directory(self) -> bool:
        return self.type == "tree"


@dataclass
class GitHubRepo:
    """GitHub 仓库信息"""
    owner: str
    name: str
    default_branch: str = "main"
    description: str = ""
    stars: int = 0
    
    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.name}"


@dataclass
class GitHubIssue:
    """GitHub Issue 信息"""
    number: int
    title: str
    state: str  # "open" | "closed"
    labels: List[str]
    created_at: str
    updated_at: str
    body: str = ""
    comments_count: int = 0
    user: str = ""
    is_pull_request: bool = False


@dataclass
class GitHubCommit:
    """GitHub Commit 信息"""
    sha: str
    message: str
    author: str
    date: str
    additions: int = 0
    deletions: int = 0
    files_changed: int = 0


@dataclass
class FileFilter:
    """文件过滤配置"""
    ignored_extensions: Set[str] = field(default_factory=lambda: {
        '.png', '.jpg', '.jpeg', '.gif', '.svg', '.ico', '.mp4', '.webp',
        '.pyc', '.pyo', '.lock', '.zip', '.tar', '.gz', '.pdf', '.woff', '.woff2',
        '.DS_Store', '.gitignore', '.gitattributes', '.editorconfig'
    })
    
    ignored_directories: Set[str] = field(default_factory=lambda: {
        '.git', '.github', '.vscode', '.idea', '__pycache__',
        'node_modules', 'venv', 'env', '.env', 'build', 'dist',
        'site-packages', 'migrations', '.next', '.nuxt', 'coverage',
        'vendor', 'target', 'out', 'bin', 'obj'
    })
    
    max_file_size: int = 500_000  # 500KB
    
    def should_include(self, file: GitHubFile) -> bool:
        """判断文件是否应该被包含"""
        if not file.is_file:
            return False
        
        # 检查目录
        path_parts = file.path.split("/")
        if any(part in self.ignored_directories for part in path_parts):
            return False
        
        # 检查扩展名
        ext = os.path.splitext(file.path)[1].lower()
        if ext in self.ignored_extensions:
            return False
        
        # 检查文件大小
        if file.size > self.max_file_size:
            return False
        
        return True


# ============================================================
# 异常定义
# ============================================================

class GitHubError(Exception):
    """GitHub API 错误基类"""
    def __init__(self, message: str, status_code: int = 0):
        self.message = message
        self.status_code = status_code
        super().__init__(message)


class GitHubAuthError(GitHubError):
    """认证错误 (401)"""
    pass


class GitHubRateLimitError(GitHubError):
    """速率限制错误 (403)"""
    pass


class GitHubNotFoundError(GitHubError):
    """资源不存在 (404)"""
    pass


# ============================================================
# GitHub 异步客户端
# ============================================================

class GitHubClient:
    """
    GitHub 异步 API 客户端
    
    使用示例:
    ```python
    async with GitHubClient() as client:
        repo = await client.get_repo("owner", "repo")
        files = await client.get_repo_tree(repo)
        content = await client.get_file_content(repo, "README.md")
    ```
    """
    
    BASE_URL = "https://api.github.com"
    
    def __init__(
        self,
        token: Optional[str] = None,
        timeout: float = 30.0,
        max_concurrent_requests: int = 10
    ):
        self.token = token or settings.GITHUB_TOKEN
        self.timeout = timeout
        self._client: Optional[httpx.AsyncClient] = None
        self._semaphore = asyncio.Semaphore(max_concurrent_requests)
    
    @property
    def _headers(self) -> Dict[str, str]:
        """构建请求头"""
        headers = {
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "GitHub-Agent-Demo/1.0"
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers
    
    async def _ensure_client(self) -> httpx.AsyncClient:
        """确保客户端已初始化"""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self.BASE_URL,
                headers=self._headers,
                timeout=httpx.Timeout(self.timeout),
                follow_redirects=True,
                limits=httpx.Limits(
                    max_keepalive_connections=20,
                    max_connections=50
                )
            )
        return self._client
    
    async def close(self):
        """关闭客户端连接"""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None
    
    async def __aenter__(self):
        await self._ensure_client()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()
    
    def _handle_error(self, response: httpx.Response, context: str = ""):
        """统一错误处理"""
        status = response.status_code
        
        try:
            data = response.json()
            message = data.get("message", response.text)
        except Exception:
            message = response.text
        
        error_msg = f"{context}: {message}" if context else message
        
        if status == 401:
            raise GitHubAuthError(
                "GitHub Token 无效或已过期，请检查 .env 配置",
                status
            )
        elif status == 403:
            if "rate limit" in message.lower():
                raise GitHubRateLimitError(
                    "GitHub API 请求已达上限，请稍后重试或添加 Token",
                    status
                )
            raise GitHubError(error_msg, status)
        elif status == 404:
            raise GitHubNotFoundError(error_msg, status)
        else:
            raise GitHubError(error_msg, status)
    
    @llm_retry
    async def _request(
        self,
        method: str,
        endpoint: str,
        **kwargs
    ) -> Dict[str, Any]:
        """
        发送 API 请求 (带重试)
        
        Args:
            method: HTTP 方法
            endpoint: API 端点 (如 /repos/{owner}/{repo})
            **kwargs: 传递给 httpx 的参数
            
        Returns:
            JSON 响应
        """
        async with self._semaphore:
            client = await self._ensure_client()
            response = await client.request(method, endpoint, **kwargs)
            
            if response.status_code >= 400:
                self._handle_error(response, endpoint)
            
            return response.json()
    
    async def _request_raw(
        self,
        method: str,
        endpoint: str,
        **kwargs
    ) -> httpx.Response:
        """发送请求并返回原始响应"""
        async with self._semaphore:
            client = await self._ensure_client()
            return await client.request(method, endpoint, **kwargs)
    
    # --------------------------------------------------------
    # 仓库相关 API
    # --------------------------------------------------------
    
    async def get_repo(self, owner: str, name: str) -> GitHubRepo:
        """获取仓库信息"""
        data = await self._request("GET", f"/repos/{owner}/{name}")
        
        return GitHubRepo(
            owner=owner,
            name=name,
            default_branch=data.get("default_branch", "main"),
            description=data.get("description", ""),
            stars=data.get("stargazers_count", 0)
        )
    
    async def get_repo_tree(
        self,
        repo: GitHubRepo,
        file_filter: Optional[FileFilter] = None
    ) -> List[GitHubFile]:
        """
        获取仓库文件树
        
        Args:
            repo: 仓库信息
            file_filter: 文件过滤器 (默认使用标准过滤)
            
        Returns:
            过滤后的文件列表
        """
        filter_config = file_filter or FileFilter()
        
        data = await self._request(
            "GET",
            f"/repos/{repo.owner}/{repo.name}/git/trees/{repo.default_branch}",
            params={"recursive": "1"}
        )
        
        files = []
        for item in data.get("tree", []):
            file = GitHubFile(
                path=item["path"],
                type=item["type"],
                size=item.get("size", 0),
                sha=item.get("sha", "")
            )
            
            if filter_config.should_include(file):
                files.append(file)
        
        logger.info(f"📂 仓库 {repo.full_name}: 共 {len(data.get('tree', []))} 项, 过滤后 {len(files)} 文件")
        return files
    
    # --------------------------------------------------------
    # Issues API
    # --------------------------------------------------------

    async def get_repo_issues(
        self,
        repo: GitHubRepo,
        state: str = "all",
        per_page: int = 30,
        max_pages: int = 3,
    ) -> List[GitHubIssue]:
        """
        获取仓库 Issues (自动分页, 排除 Pull Request)

        Args:
            repo: 仓库信息
            state: "open" | "closed" | "all"
            per_page: 每页数量
            max_pages: 最大页数

        Returns:
            GitHubIssue 列表
        """
        issues: List[GitHubIssue] = []

        for page in range(1, max_pages + 1):
            data = await self._request(
                "GET",
                f"/repos/{repo.owner}/{repo.name}/issues",
                params={
                    "state": state,
                    "per_page": per_page,
                    "page": page,
                    "sort": "updated",
                    "direction": "desc",
                },
            )

            if not data:
                break

            for item in data:
                if item.get("pull_request"):
                    continue
                body_raw = item.get("body") or ""
                issues.append(
                    GitHubIssue(
                        number=item["number"],
                        title=item["title"],
                        state=item["state"],
                        labels=[lb["name"] for lb in item.get("labels", [])],
                        created_at=item.get("created_at", ""),
                        updated_at=item.get("updated_at", ""),
                        body=body_raw[:500],
                        comments_count=item.get("comments", 0),
                        user=item.get("user", {}).get("login", ""),
                        is_pull_request=False,
                    )
                )

            if len(data) < per_page:
                break

        logger.info(
            f"📋 仓库 {repo.full_name}: 获取到 {len(issues)} 个 Issues"
        )
        return issues

    # --------------------------------------------------------
    # Commits API
    # --------------------------------------------------------

    async def get_repo_commits(
        self,
        repo: GitHubRepo,
        per_page: int = 30,
        max_pages: int = 3,
    ) -> List[GitHubCommit]:
        """
        获取仓库最近 Commits (自动分页)

        Args:
            repo: 仓库信息
            per_page: 每页数量
            max_pages: 最大页数

        Returns:
            GitHubCommit 列表 (按时间倒序)
        """
        commits: List[GitHubCommit] = []

        for page in range(1, max_pages + 1):
            data = await self._request(
                "GET",
                f"/repos/{repo.owner}/{repo.name}/commits",
                params={
                    "per_page": per_page,
                    "page": page,
                    "sha": repo.default_branch,
                },
            )

            if not data:
                break

            for item in data:
                commit_data = item.get("commit", {})
                author_info = commit_data.get("author", {})
                commits.append(
                    GitHubCommit(
                        sha=item.get("sha", "")[:7],
                        message=commit_data.get("message", ""),
                        author=author_info.get("name", "unknown"),
                        date=author_info.get("date", ""),
                    )
                )

            if len(data) < per_page:
                break

        logger.info(
            f"📝 仓库 {repo.full_name}: 获取到 {len(commits)} 个 Commits"
        )
        return commits

    # --------------------------------------------------------
    # 文件内容 API
    # --------------------------------------------------------
    
    async def get_file_content(
        self,
        repo: GitHubRepo,
        path: str
    ) -> Optional[str]:
        """
        获取单个文件内容
        
        Args:
            repo: 仓库信息
            path: 文件路径
            
        Returns:
            文件内容 (UTF-8 解码)，失败返回 None
        """
        try:
            data = await self._request(
                "GET",
                f"/repos/{repo.owner}/{repo.name}/contents/{path}",
                params={"ref": repo.default_branch}
            )
            
            # 处理目录情况
            if isinstance(data, list):
                file_names = [f["name"] for f in data]
                return f"Directory '{path}' contains:\n" + "\n".join(
                    f"- {name}" for name in file_names
                )
            
            # 解码文件内容
            content = data.get("content", "")
            encoding = data.get("encoding", "base64")
            
            if encoding == "base64":
                return base64.b64decode(content).decode("utf-8")
            
            return content
            
        except GitHubNotFoundError:
            logger.warning(f"文件不存在: {path}")
            return None
        except UnicodeDecodeError:
            logger.warning(f"文件无法解码为 UTF-8: {path}")
            return None
        except Exception as e:
            logger.error(f"获取文件失败 {path}: {e}")
            return None
    
    async def get_files_content(
        self,
        repo: GitHubRepo,
        paths: List[str],
        show_progress: bool = False
    ) -> Dict[str, Optional[str]]:
        """
        批量获取文件内容 (并发优化)
        
        Args:
            repo: 仓库信息
            paths: 文件路径列表
            show_progress: 是否显示进度
            
        Returns:
            {path: content} 字典
        """
        if not paths:
            return {}
        
        if show_progress:
            logger.info(f"📥 开始下载 {len(paths)} 个文件 (并发: {self._semaphore._value})")
        
        # 并发获取所有文件
        tasks = [
            self.get_file_content(repo, path)
            for path in paths
        ]
        
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # 组装结果
        content_map = {}
        success_count = 0
        
        for path, result in zip(paths, results):
            if isinstance(result, Exception):
                logger.error(f"下载失败 {path}: {result}")
                content_map[path] = None
            else:
                content_map[path] = result
                if result is not None:
                    success_count += 1
        
        if show_progress:
            logger.info(f"✅ 文件下载完成: {success_count}/{len(paths)} 成功")
        
        return content_map


# ============================================================
# 全局单例管理
# ============================================================

_github_client: Optional[GitHubClient] = None


def get_github_client() -> GitHubClient:
    """获取 GitHub 客户端单例"""
    global _github_client
    if _github_client is None:
        _github_client = GitHubClient()
    return _github_client


async def close_github_client():
    """关闭全局客户端 (应用关闭时调用)"""
    global _github_client
    if _github_client:
        await _github_client.close()
        _github_client = None


# ============================================================
# 便捷函数 (兼容旧接口)
# ============================================================

def parse_repo_url(url: str) -> Optional[tuple[str, str]]:
    """
    解析 GitHub URL
    
    Args:
        url: GitHub 仓库 URL
        
    Returns:
        (owner, repo) 元组，无效返回 None
    """
    if not url:
        return None

    raw = url.strip()

    # 支持 SSH 格式: git@github.com:owner/repo(.git)
    ssh_match = re.match(r"^git@github\.com:([^/]+)/([^/]+?)(?:\.git)?/?$", raw, re.IGNORECASE)
    if ssh_match:
        owner = ssh_match.group(1).strip()
        repo = ssh_match.group(2).strip()
        return (owner, repo) if owner and repo else None

    # 支持 owner/repo 直接输入
    if "://" not in raw and raw.count("/") == 1 and not raw.lower().startswith("github.com/"):
        owner, repo = raw.split("/", 1)
        owner = owner.strip()
        repo = repo.strip()
        if repo.endswith(".git"):
            repo = repo[:-4]
        return (owner, repo) if owner and repo else None

    # 兼容 github.com/owner/repo（无协议）
    normalized = raw
    if "://" not in normalized and normalized.lower().startswith("github.com/"):
        normalized = f"https://{normalized}"

    parsed = urlparse(normalized)
    host = (parsed.netloc or "").lower()
    if host.startswith("www."):
        host = host[4:]

    if host != "github.com":
        return None

    # urlparse 会自动剥离 query/fragment
    path_parts = [part for part in parsed.path.split("/") if part]
    if len(path_parts) < 2:
        return None

    owner = path_parts[0].strip()
    repo = path_parts[1].strip()

    if repo.endswith(".git"):
        repo = repo[:-4]

    if not owner or not repo:
        return None

    return (owner, repo)
