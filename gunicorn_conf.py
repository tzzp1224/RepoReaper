# 文件名: gunicorn_conf.py
import multiprocessing

# 监听地址
bind = "0.0.0.0:8000"

# Worker 数量：推荐为 CPU 核心数 * 2 + 1
# 对于 Demo 或小机器，设置 2-4 个即可，太多了 ChromaDB 并发写可能有锁竞争
# 部署到轻量级云服务器时设为1
workers = 1

# Worker 类：FastAPI 需要使用 uvicorn
worker_class = "uvicorn.workers.UvicornWorker"

# 超时时间：分析大库时可能需要较长时间，设大一点
timeout = 600
keepalive = 5
# 建议开启 preload，节省内存（利用 Linux Copy-on-Write 机制）
preload_app = True

# 日志
accesslog = "-"
errorlog = "-"
loglevel = "info"
