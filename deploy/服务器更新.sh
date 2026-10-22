#!/usr/bin/env bash
# 服务器上把站点更新到 GitHub 最新版
#
# 适用：本站点在服务器上是用 GitHub zip（codeload）拿的，没有 .git，
#       且镜像里没装 docker compose 插件 —— 所以这个脚本不用 git、不用 compose。
#
# 用法（服务器上执行）：
#   bash /root/douban-mini/deploy/服务器更新.sh
# 覆盖端口：
#   PORT=8080 bash /root/douban-mini/deploy/服务器更新.sh
#
# 会做的事：下载最新代码 → 构建镜像 → 换掉旧容器
# 不会做的事：不动数据卷 app-data（补充记录安全）

# 不是 root 就自动用 sudo 重跑一次
if [ "$(id -u)" != "0" ]; then
  echo "需要管理员权限，正在用 sudo 重新执行…"
  exec sudo bash "$0" "$@"
fi

set -e

PORT="${PORT:-80}"
PIP_INDEX_URL="${PIP_INDEX_URL:-https://pypi.org/simple}"
DIR="${DIR:-/root/douban-mini}"

echo "==> 1/4 下载最新代码"
rm -rf /tmp/dm /tmp/mini.zip
for i in 1 2 3; do
  curl -fsSL -m 180 -o /tmp/mini.zip https://codeload.github.com/gooliu/douban-mini/zip/refs/heads/main && break
  echo "    第 $i 次下载失败，重试…"
  [ "$i" = "3" ] && { echo "下载失败：服务器连不上 GitHub"; exit 1; }
  sleep 2
done
unzip -q -o /tmp/mini.zip -d /tmp
rm -rf "$DIR"
mv -f /tmp/douban-mini-main "$DIR"

echo "==> 2/4 构建镜像（约 1-3 分钟）"
cd "$DIR"
docker build --build-arg PIP_INDEX_URL="$PIP_INDEX_URL" -t douban-mini:latest .

echo "==> 3/4 换掉旧容器（数据在卷里，不会丢）"
docker rm -f douban-mini 2>/dev/null || true
docker run -d --name douban-mini --restart unless-stopped \
  -p "${PORT}:8000" \
  -e DOUBAN_CLOUD=1 \
  -e DOUBAN_DATA_DIR=/data \
  -e TZ=Asia/Shanghai \
  -v app-data:/data \
  douban-mini:latest >/dev/null

echo "==> 4/4 自检"
sleep 5
if curl -fsS -m 8 "http://127.0.0.1:${PORT}/api/health" >/dev/null; then
  echo "✅ 更新完成，服务正常。"
  echo "   浏览器打开 http://服务器IP${PORT:+:$PORT}/ 然后按 Ctrl+F5 强制刷新"
else
  echo "❌ 服务没起来，看日志：docker logs -n 50 douban-mini"
  exit 1
fi

docker image prune -f >/dev/null 2>&1 || true
