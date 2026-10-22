# -*- coding: utf-8 -*-
"""在服务器的容器里应用本次更新（隐藏小组排查）"""
#!/usr/bin/env bash
set -e

SRC="$(cd "$(dirname "$0")" && pwd)"
CT="douban-mini"

if [ "$(id -u)" != "0" ]; then
  echo "非 root，自动提权重跑…"
  exec sudo bash "$0" "$@"
fi

echo "== 1/4 检查容器 =="
docker ps --format '{{.Names}}\t{{.Status}}' | grep -q "^$CT" \
  || { echo "找不到运行中的容器 $CT，先确认服务是否已部署"; exit 1; }

echo "== 2/4 复制文件进容器 =="
docker cp "$SRC/backend/main.py"          "$CT:/app/backend/main.py"
docker cp "$SRC/backend/douban_client.py" "$CT:/app/backend/douban_client.py"
docker cp "$SRC/web/index.html"           "$CT:/app/web/index.html"

echo "== 3/4 重启容器 =="
docker restart "$CT" >/dev/null
sleep 6

echo "== 4/4 自检 =="
# 容器里可能是 python 也可能是 python3，先探测；-i 必须加，否则 heredoc 传不进去
PYBIN="$(docker exec "$CT" sh -c 'command -v python3 || command -v python' 2>/dev/null | head -1)"
PYBIN="${PYBIN:-python}"
echo "  容器内的 python: $PYBIN"
docker exec -i "$CT" "$PYBIN" - <<'PY'
import ast, urllib.request, re, ssl
for p in ("/app/backend/main.py", "/app/backend/douban_client.py"):
    ast.parse(open(p, encoding="utf-8").read())
print("✅ Python 语法 OK")
try:
    idx = open("/app/web/index.html", encoding="utf-8").read()
    print("✅ 页面已含隐藏小组排查:", ("check-membership" in idx))
except Exception as e:
    print("⚠️ 页面检查失败:", e)
# 韩国节点能否抓到豆瓣桌面版页面（本功能的关键前提）
req = urllib.request.Request(
    "https://www.douban.com/group/people/63425139/joins",
    headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                           "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"})
try:
    r = urllib.request.urlopen(req, timeout=20)
    body = r.read().decode("utf-8", "ignore")
    ids = set(re.findall(r"douban\.com/group/(\d+)/", body))
    print(f"✅ 豆瓣桌面版页面可达: HTTP {r.status}, 解析到 {len(ids)} 个小组（期望 21）")
except Exception as e:
    print("❌ 豆瓣桌面版页面不可达:", repr(e)[:160])
    print("   若这里失败，说明海外 IP 被豆瓣拦了，页面会提示「反爬」，需要换国内节点")
PY

echo
echo "全部完成 → 浏览器打开 http://43.108.119.94/ 后按 Ctrl+F5 强制刷新"
