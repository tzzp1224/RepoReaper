import asyncio

from app.services.github_service import GitHubService
from app.storage.repo_mirror_store import RepoMirrorUnavailable
from app.utils.github_client import GitHubFile, GitHubRepo


class FakeGitHubClient:
    def __init__(self, *, tree_files=None, file_content=None):
        self.tree_files = tree_files if tree_files is not None else []
        self.file_content = file_content
        self.get_repo_tree_calls = 0
        self.get_file_content_calls = 0

    async def get_repo(self, owner: str, name: str) -> GitHubRepo:
        return GitHubRepo(owner=owner, name=name, default_branch="main")

    async def get_repo_tree(self, repo: GitHubRepo, file_filter=None):
        self.get_repo_tree_calls += 1
        return self.tree_files

    async def get_file_content(self, repo: GitHubRepo, path: str):
        self.get_file_content_calls += 1
        return self.file_content


class FakeMirrorStore:
    def __init__(self, *, tree_files=None, tree_error=None, file_content=None, file_error=None):
        self.tree_files = tree_files
        self.tree_error = tree_error
        self.file_content = file_content
        self.file_error = file_error

    async def get_repo_tree(self, repo: GitHubRepo, file_filter=None):
        if self.tree_error:
            raise self.tree_error
        return self.tree_files if self.tree_files is not None else []

    async def get_file_content(self, repo: GitHubRepo, path: str):
        if self.file_error:
            raise self.file_error
        return self.file_content


def test_get_repo_structure_prefers_mirror():
    client = FakeGitHubClient(
        tree_files=[GitHubFile(path="api.py", type="blob", size=1, sha="1")]
    )
    mirror = FakeMirrorStore(
        tree_files=[GitHubFile(path="mirror.py", type="blob", size=1, sha="2")]
    )
    service = GitHubService(client=client, mirror_store=mirror)

    paths = asyncio.run(service.get_repo_structure("https://github.com/acme/demo"))

    assert paths == ["mirror.py"]
    assert client.get_repo_tree_calls == 0


def test_get_repo_structure_falls_back_when_mirror_unavailable():
    client = FakeGitHubClient(
        tree_files=[GitHubFile(path="fallback.py", type="blob", size=1, sha="1")]
    )
    mirror = FakeMirrorStore(tree_error=RepoMirrorUnavailable("mirror not ready"))
    service = GitHubService(client=client, mirror_store=mirror)

    paths = asyncio.run(service.get_repo_structure("https://github.com/acme/demo"))

    assert paths == ["fallback.py"]
    assert client.get_repo_tree_calls == 1


def test_get_file_content_prefers_mirror():
    client = FakeGitHubClient(file_content="api-content")
    mirror = FakeMirrorStore(file_content="mirror-content")
    service = GitHubService(client=client, mirror_store=mirror)

    content = asyncio.run(
        service.get_file_content("https://github.com/acme/demo", "README.md")
    )

    assert content == "mirror-content"
    assert client.get_file_content_calls == 0


def test_get_file_content_falls_back_when_mirror_miss():
    client = FakeGitHubClient(file_content="api-content")
    mirror = FakeMirrorStore(file_content=None)
    service = GitHubService(client=client, mirror_store=mirror)

    content = asyncio.run(
        service.get_file_content("https://github.com/acme/demo", "README.md")
    )

    assert content == "api-content"
    assert client.get_file_content_calls == 1
