# 文件名: gunicorn_conf.py
import multiprocessing
import os

# 监听地址
bind = "0.0.0.0:8000"

# Worker 数量
# - Qdrant Local 模式: 必须设为 1 (文件锁限制)
# - Qdrant Server 模式: 可设为 CPU 核心数 * 2 + 1
qdrant_mode = os.getenv("QDRANT_MODE", "local")
if qdrant_mode == "local":
    workers = 1
else:
    # Server/Cloud 模式支持多 Worker
    workers = int(os.getenv("GUNICORN_WORKERS", multiprocessing.cpu_count() * 2 + 1))

# Worker 类：FastAPI 需要使用 uvicorn
worker_class = "uvicorn.workers.UvicornWorker"

# 超时时间：分析大库时可能需要较长时间，设大一点
timeout = 600
keepalive = 5

# 日志
accesslog = "-"
errorlog = "-"
loglevel = "info"

# 启动日志
def on_starting(server):
    print(f"🚀 Gunicorn 启动: workers={workers}, qdrant_mode={qdrant_mode}")
