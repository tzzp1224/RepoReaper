#!/bin/bash
# ============================================================
# GitHub RAG Agent - 生产环境部署脚本 (2核2G服务器优化版)
# ============================================================
# 
# 使用方法:
#   chmod +x deploy.sh
#   ./deploy.sh
#
# 前置要求:
#   - Python 3.10+
#   - Docker (用于运行 Qdrant)
#
# ============================================================

set -e

echo "🚀 GitHub RAG Agent 部署脚本"
echo "=========================================="

# 检查是否在项目目录
if [ ! -f "requirements.txt" ]; then
    echo "❌ 请在项目根目录运行此脚本"
    exit 1
fi

# 检查 .env 文件
if [ ! -f ".env" ]; then
    echo "❌ 未找到 .env 文件，请先复制 .env.example 并配置"
    echo "   cp .env.example .env"
    echo "   vim .env"
    exit 1
fi

# ============================================================
# 1. 启动 Qdrant Server (Docker)
# ============================================================
echo ""
echo "📦 步骤 1: 启动 Qdrant Server..."

# 检查 Docker 是否运行
if ! docker info > /dev/null 2>&1; then
    echo "❌ Docker 未运行，请先启动 Docker"
    exit 1
fi

# 检查 Qdrant 容器是否已存在
if docker ps -a --format '{{.Names}}' | grep -q "^qdrant-server$"; then
    echo "   Qdrant 容器已存在，检查状态..."
    if docker ps --format '{{.Names}}' | grep -q "^qdrant-server$"; then
        echo "   ✅ Qdrant 已在运行"
    else
        echo "   🔄 启动已有的 Qdrant 容器..."
        docker start qdrant-server
    fi
else
    echo "   🆕 创建并启动 Qdrant 容器 (内存限制 512MB)..."
    docker run -d \
        --name qdrant-server \
        --restart unless-stopped \
        -p 6333:6333 \
        -p 6334:6334 \
        -v qdrant_data:/qdrant/storage \
        -m 512m \
        -e QDRANT__STORAGE__ON_DISK_PAYLOAD=true \
        qdrant/qdrant:latest
fi

# 等待 Qdrant 就绪
echo "   ⏳ 等待 Qdrant 就绪..."
for i in {1..30}; do
    if curl -s http://localhost:6333/health > /dev/null 2>&1; then
        echo "   ✅ Qdrant 已就绪"
        break
    fi
    sleep 1
done

# ============================================================
# 2. 创建 Python 虚拟环境
# ============================================================
echo ""
echo "🐍 步骤 2: 配置 Python 环境..."

if [ ! -d "venv" ]; then
    echo "   创建虚拟环境..."
    python3 -m venv venv
fi

echo "   激活虚拟环境..."
source venv/bin/activate

echo "   安装依赖..."
pip install -q --upgrade pip
pip install -q -r requirements.txt

# ============================================================
# 3. 创建必要目录
# ============================================================
echo ""
echo "📁 步骤 3: 创建数据目录..."
mkdir -p data/locks
mkdir -p data/contexts
mkdir -p logs

# ============================================================
# 4. 设置环境变量
# ============================================================
echo ""
echo "⚙️ 步骤 4: 配置环境变量..."

# 从 .env 加载
set -a
source .env
set +a

# 设置 Server 模式
export QDRANT_MODE=server
export QDRANT_URL=http://localhost:6333
export LOCK_BACKEND=file
export LOCK_DIR=data/locks
export GUNICORN_WORKERS=2

echo "   QDRANT_MODE=$QDRANT_MODE"
echo "   QDRANT_URL=$QDRANT_URL"
echo "   GUNICORN_WORKERS=$GUNICORN_WORKERS"

# ============================================================
# 5. 启动应用
# ============================================================
echo ""
echo "🌐 步骤 5: 启动 FastAPI 应用..."
echo "=========================================="
echo "   Workers: 2 (优化2核CPU)"
echo "   监听地址: 0.0.0.0:8000"
echo "   Qdrant: http://localhost:6333"
echo "=========================================="
echo ""
echo "   按 Ctrl+C 停止服务"
echo ""

# 使用 Gunicorn 启动 (2 Workers)
gunicorn app.main:app -c gunicorn_conf.py
