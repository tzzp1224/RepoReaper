# 文件路径: app/services/github_service.py
from github import Github, Auth, GithubException
from app.core.config import settings
import os

def parse_repo_url(url):
    """解析 GitHub URL 提取 owner/repo"""
    if url.endswith(".git"):
        url = url[:-4]
    parts = url.split("/")
    if "github.com" in parts:
        index = parts.index("github.com")
        if len(parts) > index + 2:
            return f"{parts[index+1]}/{parts[index+2]}"
    return None

def get_repo_structure(repo_url):
    """获取仓库文件树，包含过滤逻辑"""
    repo_name = parse_repo_url(repo_url)
    if not repo_name:
        raise ValueError("Invalid GitHub URL format") # 抛出异常
    
    try:
        g = Github(auth=Auth.Token(settings.GITHUB_TOKEN)) if settings.GITHUB_TOKEN else Github()
        repo = g.get_repo(repo_name)
        default_branch = repo.default_branch
        contents = repo.get_git_tree(default_branch, recursive=True).tree
        
        file_list = []
        
        # ... (过滤器配置 IGNORED_EXTS, IGNORED_DIRS 保持不变) ...
        IGNORED_EXTS = {
            '.png', '.jpg', '.jpeg', '.gif', '.svg', '.ico', '.mp4',
            '.pyc', '.lock', '.zip', '.tar', '.gz', '.pdf',
            '.DS_Store', '.gitignore', '.gitattributes'
        }
        IGNORED_DIRS = {
            '.git', '.github', '.vscode', '.idea', '__pycache__', 
            'node_modules', 'venv', 'env', 'build', 'dist', 'site-packages',
            'migrations'
        }

        for content in contents:
            path = content.path
            if content.type != "blob": continue
            if any(part in IGNORED_DIRS for part in path.split("/")): continue
            ext = os.path.splitext(path)[1]
            if ext in IGNORED_EXTS: continue
            file_list.append(path)

        return file_list

    except GithubException as e:
        # === 核心修改：不再吞掉异常，而是根据状态码抛出更友好的错误 ===
        if e.status == 401:
            raise Exception("GitHub Token 无效或过期 (401 Unauthorized)。请检查 .env 配置。")
        elif e.status == 403:
            raise Exception("GitHub API 请求受限 (403 Rate Limit)。建议添加 Token。")
        elif e.status == 404:
            raise Exception(f"找不到仓库 {repo_name} (404 Not Found)。请检查 URL 或私有权限。")
        else:
            raise Exception(f"GitHub API Error: {e.data.get('message', str(e))}")
    except Exception as e:
        raise e

def get_file_content(repo_url, file_path):
    """
    下载单个文件内容。
    ✨ 修复：增加对文件夹(Directory)的处理逻辑。
    """
    repo_name = parse_repo_url(repo_url)
    if not repo_name: return None
    
    try:
        g = Github(auth=Auth.Token(settings.GITHUB_TOKEN)) if settings.GITHUB_TOKEN else Github()
        repo = g.get_repo(repo_name)
        
        # PyGithub get_contents 可能返回 ContentFile 或 List[ContentFile]
        content = repo.get_contents(file_path, ref=repo.default_branch)
        
        # 情况 A: 这是一个文件夹 (返回列表)
        if isinstance(content, list):
            file_names = [f.name for f in content]
            # 返回文件夹清单，让 LLM 知道里面有什么，从而决定下一步读哪个具体文件
            return f"Directory '{file_path}' contains:\n" + "\n".join([f"- {name}" for name in file_names])
            
        # 情况 B: 这是一个文件
        return content.decoded_content.decode('utf-8')
        
    except Exception as e:
        # 如果是因为路径不存在等原因，打印错误
        print(f"❌ [GitHub Error] 读取路径 {file_path} 失败: {e}")
        return None