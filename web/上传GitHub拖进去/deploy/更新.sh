#!/usr/bin/env bash
# 拉取最新代码并重建服务（补充记录存在卷里，不会丢）
set -e
DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$DIR"

echo "==> 拉取最新代码"
git pull --ff-only || { echo "git pull 失败，用现有代码继续"; }

echo "==> 重建并重启"
docker compose -f deploy/docker-compose.yml up -d --build

echo "==> 清理旧镜像"
docker image prune -f >/dev/null 2>&1 || true

echo "==> 自检"
sleep 3
PORT="$(docker port douban-mini 8000/tcp 2>/dev/null | head -1 | awk -F: '{print $NF}')"
PORT="${PORT:-80}"
curl -fsS -m 5 "http://127.0.0.1:${PORT}/api/health" >/dev/null \
  && echo "服务正常：http://$(curl -fsS -m 5 ifconfig.me 2>/dev/null || echo '服务器IP')$([ "$PORT" = 80 ] && echo '' || echo ":$PORT")" \
  || echo "服务未就绪，看日志：docker logs douban-mini"
