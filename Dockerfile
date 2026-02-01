# 1. 基础镜像：选择 Python 3.10 的轻量版 (Slim)
FROM python:3.10-slim

# 2. 设置环境变量
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    # 默认 LLM 供应商 (可通过 docker run -e 覆盖)
    LLM_PROVIDER=deepseek

# 3. 设置工作目录
WORKDIR /app

# 4. 安装系统级依赖
# build-essential: ChromaDB 编译需要
# curl: 健康检查
# git: 某些 pip 包可能需要
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    git \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean

# 5. 复制依赖文件并安装 (利用 Docker 层缓存)
COPY requirements.txt .

# 6. 安装 Python 依赖
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# 7. 复制项目代码
COPY . .

# 8. 创建数据目录 (Qdrant 本地存储 + 上下文缓存)
RUN mkdir -p /app/data/qdrant_db /app/data/contexts

# 9. 暴露端口
EXPOSE 8000

# 10. 健康检查
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# 11. 启动命令
CMD ["gunicorn", "-c", "gunicorn_conf.py", "app.main:app"]