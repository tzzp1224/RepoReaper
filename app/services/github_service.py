# -*- coding: utf-8 -*-
"""
GitHub 服务层

职责:
- 提供业务级别的 GitHub 操作
- 封装底层客户端，提供简洁 API
- 保持向后兼容的函数签名
"""

import asyncio
import logging
import re
from typing import List, Optional, Dict
from urllib.parse import urlparse
from app.services.repository_source import (
    BlobEntry, GitSnapshotSource, RepositorySource, bound_source, decode_text, discovery_paths,
)

from app.storage.repo_mirror_store import RepoMirrorStore, RepoMirrorUnavailable
from app.utils.github_client import (
    GitHubClient,
    GitHubRepo,
    GitHubFile,
    GitHubIssue,
    GitHubCommit,
    FileFilter,
    GitHubError,
    GitHubNotFoundError,
    get_github_client,
    parse_repo_url,
)

logger = logging.getLogger(__name__)


class GitHubSnapshotSource:
    def __init__(self, client: GitHubClient, repo: GitHubRepo, revision: str, entries: dict[str, BlobEntry]):
        self._client = client
        self._repo = repo
        self.repo = repo.full_name
        self.revision = revision
        self._entries = entries

    async def manifest(self) -> dict[str, BlobEntry]:
        return self._entries

    async def list_files(self) -> list[str]:
        return discovery_paths(self._entries)

    async def read_file(self, path: str) -> str | None:
        entry = self._entries.get(path)
        if entry is None or entry.size > FileFilter.max_file_size:
            return None
        data = await self._client.get_blob(self._repo, entry.sha)
        return decode_text(data) if len(data) <= FileFilter.max_file_size else None


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
    
    def __init__(
        self,
        client: Optional[GitHubClient] = None,
        mirror_store: Optional[RepoMirrorStore] = None,
    ):
        self._client = client
        self._mirror_store = mirror_store if mirror_store is not None else RepoMirrorStore()
    
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

    async def open_snapshot(self, repo_url: str, revision: str | None = None) -> RepositorySource:
        source = bound_source(repo_url)
        if source is not None:
            if revision and revision != source.revision:
                raise ValueError("Snapshot revision mismatch")
            return source
        if revision is not None and not re.fullmatch(r"[0-9a-f]{40}", revision):
            raise ValueError("Invalid commit SHA")

        parsed = parse_repo_url(repo_url)
        if not parsed or not all(re.fullmatch(r"[A-Za-z0-9_.-]+", part) and part not in {".", ".."} for part in parsed):
            raise ValueError("Invalid GitHub repository URL")
        repo = GitHubRepo(*parsed) if revision else await self._get_repo_from_url(repo_url)
        try:
            if revision is None:
                snapshot = await self._mirror_store.resolve_snapshot(repo)
                root, revision = snapshot.mirror_git_dir, snapshot.commit_sha
            else:
                root = self._mirror_store.local_git_dir(repo)
            source = await asyncio.to_thread(GitSnapshotSource, root, repo.full_name, revision)
            await source.manifest()
            return source
        except Exception as exc:
            logger.info("Git snapshot unavailable in mirror for %s: %s", repo.full_name, type(exc).__name__)

        resolved, tree = await self.client.get_revision_tree(repo, revision or repo.default_branch)
        if revision and resolved != revision:
            raise ValueError("GitHub returned a different revision")
        entries = {
            item["path"]: BlobEntry(item["sha"], item.get("size", 0))
            for item in await self.client.get_tree_entries(repo, tree)
            if item["type"] == "blob" and item["mode"] in {"100644", "100755"}
        }
        return GitHubSnapshotSource(self.client, repo, resolved, entries)
    
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
        source = bound_source(repo_url)
        if source is not None:
            return await source.list_files()
        repo = await self._get_repo_from_url(repo_url)
        if self._mirror_store is not None:
            try:
                files = await self._mirror_store.get_repo_tree(repo, file_filter)
                logger.debug(f"仓库镜像命中: {repo.full_name}@{repo.default_branch}")
                return [f.path for f in files]
            except RepoMirrorUnavailable as e:
                logger.info(f"仓库镜像不可用，回退 GitHub API: {repo.full_name} ({e})")
            except Exception as e:
                logger.warning(f"仓库镜像读取异常，回退 GitHub API: {repo.full_name} ({e})")

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
        source = bound_source(repo_url)
        if source is not None:
            return await source.read_file(file_path)
        repo = await self._get_repo_from_url(repo_url)
        if self._mirror_store is not None:
            try:
                content = await self._mirror_store.get_file_content(repo, file_path)
                if content is not None:
                    logger.debug(f"镜像文件命中: {repo.full_name}:{file_path}")
                    return content
                logger.debug(f"镜像文件未命中，回退 GitHub API: {repo.full_name}:{file_path}")
            except RepoMirrorUnavailable as e:
                logger.info(f"仓库镜像不可用，回退 GitHub API: {repo.full_name}:{file_path} ({e})")
            except Exception as e:
                logger.warning(f"仓库镜像读取异常，回退 GitHub API: {repo.full_name}:{file_path} ({e})")

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
        source = bound_source(repo_url)
        if source is not None:
            return {path: await source.read_file(path) for path in file_paths}
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

    async def get_repo_issues(
        self,
        repo_url: str,
        state: str = "all",
        per_page: int = 30,
        max_pages: int = 3,
    ) -> List[GitHubIssue]:
        """
        获取仓库 Issues
        
        Args:
            repo_url: GitHub 仓库 URL
            state: "open" | "closed" | "all"
            per_page: 每页数量
            max_pages: 最大页数
        """
        repo = await self._get_repo_from_url(repo_url)
        return await self.client.get_repo_issues(repo, state, per_page, max_pages)

    async def get_repo_commits(
        self,
        repo_url: str,
        per_page: int = 30,
        max_pages: int = 3,
    ) -> List[GitHubCommit]:
        """
        获取仓库最近 Commits
        
        Args:
            repo_url: GitHub 仓库 URL
            per_page: 每页数量
            max_pages: 最大页数
        """
        repo = await self._get_repo_from_url(repo_url)
        return await self.client.get_repo_commits(repo, per_page, max_pages)

    async def extract_paper_urls_from_readme(self, repo_url: str) -> List[Dict[str, str]]:
        """
        Fetch the repo README and extract paper-related PDF/abstract URLs.

        Returns a list of dicts: [{"url": "...", "title": "...", "source": "arxiv|openreview|pdf|..."}]
        """
        repo = await self._get_repo_from_url(repo_url)

        readme_path = None
        try:
            files = await self.client.get_repo_tree(repo)
            for f in files:
                if f.path.lower() in ("readme.md", "readme.rst", "readme.txt", "readme"):
                    readme_path = f.path
                    break
        except Exception:
            readme_path = "README.md"

        if not readme_path:
            readme_path = "README.md"

        try:
            content = await self.client.get_file_content(repo, readme_path)
        except Exception:
            return []

        if not content:
            return []

        return _extract_paper_links(content)


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


async def get_repo_issues(
    repo_url: str,
    state: str = "all",
    per_page: int = 30,
    max_pages: int = 3,
) -> List[GitHubIssue]:
    """获取仓库 Issues"""
    service = get_github_service()
    return await service.get_repo_issues(repo_url, state, per_page, max_pages)


async def get_repo_commits(
    repo_url: str,
    per_page: int = 30,
    max_pages: int = 3,
) -> List[GitHubCommit]:
    """获取仓库最近 Commits"""
    service = get_github_service()
    return await service.get_repo_commits(repo_url, per_page, max_pages)


async def extract_paper_urls_from_readme(repo_url: str) -> List[Dict[str, str]]:
    """Extract paper PDF/abstract URLs from a repo's README."""
    service = get_github_service()
    return await service.extract_paper_urls_from_readme(repo_url)


# ============================================================
# README paper-link extraction helpers
# ============================================================

_ARXIV_ABS_RE = re.compile(r'https?://arxiv\.org/abs/[\w.]+', re.IGNORECASE)
_ARXIV_PDF_RE = re.compile(r'https?://arxiv\.org/pdf/[\w.]+(?:\.pdf)?', re.IGNORECASE)
_OPENREVIEW_RE = re.compile(r'https?://openreview\.net/(?:forum|pdf)\?id=[\w-]+', re.IGNORECASE)
_GENERIC_PDF_RE = re.compile(r'https?://[^\s)>\]"\']+\.pdf(?:\?[^\s)>\]"\']*)?', re.IGNORECASE)

_MD_LINK_RE = re.compile(r'\[([^\]]*)\]\((https?://[^\s)]+)\)')


def _extract_paper_links(readme_text: str) -> List[Dict[str, str]]:
    """Parse README markdown and return deduplicated paper links."""
    seen_urls: set = set()
    results: List[Dict[str, str]] = []

    md_links = {url: title for title, url in _MD_LINK_RE.findall(readme_text)}

    def _add(url: str, source: str, fallback_title: str = ""):
        canon = url.rstrip("/")
        if canon in seen_urls:
            return
        seen_urls.add(canon)
        title = md_links.get(url, "") or fallback_title or source
        results.append({"url": canon, "title": title.strip(), "source": source})

    for m in _ARXIV_PDF_RE.finditer(readme_text):
        url = m.group(0)
        if not url.endswith(".pdf"):
            url += ".pdf"
        _add(url, "arxiv", "arXiv PDF")

    for m in _ARXIV_ABS_RE.finditer(readme_text):
        abs_url = m.group(0)
        pdf_url = abs_url.replace("/abs/", "/pdf/")
        if not pdf_url.endswith(".pdf"):
            pdf_url += ".pdf"
        _add(pdf_url, "arxiv", "arXiv PDF")

    for m in _OPENREVIEW_RE.finditer(readme_text):
        url = m.group(0)
        pdf_url = url.replace("forum?", "pdf?") if "forum?" in url else url
        _add(pdf_url, "openreview", "OpenReview PDF")

    for m in _GENERIC_PDF_RE.finditer(readme_text):
        url = m.group(0)
        parsed = urlparse(url)
        if "arxiv.org" in parsed.netloc or "openreview.net" in parsed.netloc:
            continue
        _add(url, "pdf", "Paper PDF")

    return results


# 导出
__all__ = [
    "GitHubService",
    "get_github_service",
    "get_repo_structure",
    "get_file_content",
    "get_repo_issues",
    "get_repo_commits",
    "extract_paper_urls_from_readme",
    "parse_repo_url_compat",
    "GitHubError",
    "GitHubNotFoundError",
    "FileFilter",
    "GitHubRepo",
    "GitHubIssue",
    "GitHubCommit",
]
