#!/usr/bin/env bash
# 应用更新：修正「他人加入的小组」判定（仅登录可见 / 仅自己可见）
# 用法：把 douban-mini-update 整个目录放到服务器 ~ 下，然后
#   cd ~/douban-mini-update && bash 应用更新-登录门槛判定.sh
set -u
CT=douban-mini
DIR="$(cd "$(dirname "$0")" && pwd)"

echo "== 0/4 环境检查 =="
command -v docker >/dev/null || { echo "!! 没装 docker"; exit 1; }
docker ps --format '{{.Names}}' | grep -qx "$CT" || { echo "!! 容器 $CT 没在跑"; exit 1; }
echo "容器 $CT 正常"

echo "== 1/4 备份容器内旧文件 =="
docker exec "$CT" sh -c 'cp /app/backend/main.py /app/backend/main.py.bak 2>/dev/null; cp /app/web/index.html /app/web/index.html.bak 2>/dev/null; echo ok'

echo "== 2/4 覆盖文件 =="
for f in backend/main.py backend/douban_client.py web/index.html; do
  src="$DIR/$f"
  [ -f "$src" ] || { echo "!! 缺少 $f"; exit 1; }
  case "$f" in
    backend/*) dst="/app/$f" ;;
    web/*)     dst="/app/$f" ;;
  esac
  docker cp "$src" "$CT:$dst" >/dev/null && echo "  $f -> $dst ($(stat -c%s "$src") 字节)"
done

echo "== 3/4 重启容器 =="
docker restart "$CT" >/dev/null && sleep 8
docker ps --filter "name=$CT" --format '  状态: {{.Status}}'

echo "== 4/4 自检 =="
docker exec "$CT" sh -c 'grep -c "login_required" /app/backend/douban_client.py; grep -c "TA 的小组只对登录用户可见" /app/web/index.html' || true
curl -s -o /dev/null -w "  加入的小组(公开用户) -> %{http_code}\n" http://127.0.0.1/api/user/63425139/joined-groups
curl -s -o /dev/null -w "  加入的小组(登录门槛) -> %{http_code}\n" http://127.0.0.1/api/user/207700707/joined-groups
echo
echo "两行命中数都 >=1、两个接口都是 200 就成功了；浏览器 Ctrl+F5 强刷。"
