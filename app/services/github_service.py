# 文件路径: app/services/github_service.py
from github import Github, Auth
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
        return None

    print(f"🔍 [GitHub] 连接中: {repo_name} ...")
    
    try:
        # 使用 settings 中的 Token
        g = Github(auth=Auth.Token(settings.GITHUB_TOKEN)) if settings.GITHUB_TOKEN else Github()
        repo = g.get_repo(repo_name)
        
        # 自动获取默认分支
        default_branch = repo.default_branch
        
        # 获取文件树 (递归)
        contents = repo.get_git_tree(default_branch, recursive=True).tree
        
        file_list = []
        
        # --- 过滤器配置 (保留原代码逻辑) ---
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
            
            # 检查目录过滤
            if any(part in IGNORED_DIRS for part in path.split("/")): continue
            
            # 检查后缀过滤
            ext = os.path.splitext(path)[1]
            if ext in IGNORED_EXTS: continue
                
            file_list.append(path)

        return file_list

    except Exception as e:
        print(f"❌ [GitHub Error] 获取结构失败: {e}")
        return []

def get_file_content(repo_url, file_path):
    """下载单个文件内容"""
    repo_name = parse_repo_url(repo_url)
    if not repo_name: return None
    
    try:
        g = Github(auth=Auth.Token(settings.GITHUB_TOKEN)) if settings.GITHUB_TOKEN else Github()
        repo = g.get_repo(repo_name)
        # 获取文件内容并解码
        content_file = repo.get_contents(file_path, ref=repo.default_branch)
        return content_file.decoded_content.decode('utf-8')
    except Exception as e:
        print(f"❌ [GitHub Error] 读取文件 {file_path} 失败: {e}")
        return None