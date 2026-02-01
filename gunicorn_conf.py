# 文件名: gunicorn_conf.py
import multiprocessing

# 监听地址
bind = "0.0.0.0:8000"

# Worker 数量：Qdrant 本地模式不支持多进程并发访问，必须设为 1
# 如需多 worker，请使用 Qdrant Server 模式
workers = 1

# Worker 类：FastAPI 需要使用 uvicorn
worker_class = "uvicorn.workers.UvicornWorker"

# 超时时间：分析大库时可能需要较长时间，设大一点
timeout = 600
keepalive = 5


# 日志
accesslog = "-"
errorlog = "-"
loglevel = "info"
