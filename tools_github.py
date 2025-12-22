from github import Github, Auth
from dotenv import load_dotenv
import os

# ==========================================
# 1. 配置
# ==========================================
load_dotenv()

GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
if not GITHUB_TOKEN:
    print("❌ 错误：未找到 GITHUB_TOKEN，请检查 .env 文件")
    
# ==========================================
# 2. 工具函数
# ==========================================

def parse_repo_url(url):
    if url.endswith(".git"):
        url = url[:-4]
    parts = url.split("/")
    if "github.com" in parts:
        index = parts.index("github.com")
        if len(parts) > index + 2:
            return f"{parts[index+1]}/{parts[index+2]}"
    return None

def get_repo_structure(repo_url):
    repo_name = parse_repo_url(repo_url)
    if not repo_name:
        print("[ERROR] Invalid URL format")
        return None

    print(f"[INFO] Connecting to GitHub API: {repo_name} ...")
    
    try:
        auth = Auth.Token(GITHUB_TOKEN)
        g = Github(auth=auth)
        repo = g.get_repo(repo_name)
        
        # 自动获取默认分支
        default_branch = repo.default_branch
        print(f"[INFO] Default branch detected: {default_branch}")
        
        # 获取文件树
        contents = repo.get_git_tree(default_branch, recursive=True).tree
        print(f"[INFO] Total files fetched: {len(contents)}")
        
        file_list = []
        
        # 过滤器 (Filter)
        ignored_extensions = {
            '.png', '.jpg', '.jpeg', '.gif', '.svg', '.ico',
            '.pyc', '.lock', '.zip', '.tar', '.gz',
            '.DS_Store', '.gitignore', '.gitattributes'
        }
        
        ignored_dirs = {
            '.git', '.github', '.vscode', '.idea', '__pycache__', 
            'node_modules', 'venv', 'env', 'build', 'dist', 'site-packages'
        }

        for content in contents:
            path = content.path
            if content.type != "blob": continue
            if any(part in ignored_dirs for part in path.split("/")): continue
            ext = os.path.splitext(path)[1]
            if ext in ignored_extensions: continue
                
            file_list.append(path)

        print(f"[SUCCESS] Filtered files count: {len(file_list)}")
        return file_list

    except Exception as e:
        print(f"[ERROR] Failed to fetch repo: {e}")
        return []

def get_file_content(repo_url, file_path):
    """
    下载特定文件的具体代码内容
    """
    repo_name = parse_repo_url(repo_url)
    if not repo_name: return None
    
    try:
        auth = Auth.Token(GITHUB_TOKEN)
        g = Github(auth=auth)
        repo = g.get_repo(repo_name)
        
        # 获取文件对象
        # ref=repo.default_branch 确保我们从默认分支下载
        content_file = repo.get_contents(file_path, ref=repo.default_branch)
        
        # decoded_content 返回的是字节(bytes)，需要解码成字符串
        return content_file.decoded_content.decode('utf-8')
        
    except Exception as e:
        print(f"[ERROR] Reading file {file_path} failed: {e}")
        return None

if __name__ == "__main__":
    test_url = "https://github.com/fastapi/fastapi"
    files = get_repo_structure(test_url)
    
    if files:
        print("\n[PREVIEW] First 20 files:")
        print("-" * 30)
        for f in files[:20]:
            print(f)