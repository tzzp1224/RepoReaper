#!/bin/bash
# ============================================================
# RepoReaper - 自动化更新与部署脚本
# ============================================================

# 确保脚本在出错时立即停止
set -e

echo "🔄 [1/5] 正在从 GitHub 强制拉取代码..."
# 解决本地 frontend-dist 修改导致的冲突
git fetch --all
git reset --hard origin/main
git pull origin main

echo "🐍 [2/5] 正在更新 Python 依赖..."
# 检查并激活虚拟环境
if [ -d "venv" ]; then
    source venv/bin/activate
    pip install -r requirements.txt
else
    echo "⚠️ 未找到虚拟环境 venv，请确认路径"
    exit 1
fi

echo "🎨 [3/5] 正在同步前端静态文件..."
# 这里的路径完全匹配你当前的 Nginx 配置
sudo cp -r frontend-dist/* /var/www/realdexter/

echo "🔐 [4/5] 正在修正文件权限..."
sudo chown -R www-data:www-data /var/www/realdexter
sudo chmod -R 755 /var/www/realdexter

echo "⚙️ [5/5] 正在重启后端服务..."
# 优先尝试 Systemd 重启，如果没配则使用 pkill 模式
if systemctl is-active --quiet reaper; then
    sudo systemctl restart reaper
    echo "✅ Systemd 服务 (reaper) 已重启"
else
    echo "⚠️ 未检测到 Systemd 服务，正在执行手动重启 (nohup)..."
    pkill -9 -f gunicorn || true
    nohup ./venv/bin/gunicorn -c gunicorn_conf.py app.main:app > logs/app.log 2>&1 &
    echo "✅ Gunicorn 已在后台启动"
fi

echo "🌐 正在刷新 Nginx..."
sudo systemctl reload nginx

echo "=========================================="
echo "✨ RepoReaper 更新成功！"
echo "🚀 访问地址: https://realdexter.com"
echo "=========================================="