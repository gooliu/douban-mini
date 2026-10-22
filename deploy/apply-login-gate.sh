#!/usr/bin/env bash
# 一键把「登录门槛判定」这版更新应用到已运行的容器
# 用法（服务器上，root）：
#   cd /root && curl -fsSL https://raw.githubusercontent.com/gooliu/douban-mini/main/deploy/apply-login-gate.sh -o apply.sh && bash apply.sh
#
# 做四件事：下载最新代码 -> 校验 -> 备份并覆盖容器内文件 -> 重启并自检
# 幂等：重复跑只覆盖同样三个文件，不会动 app-data 数据卷
set -u

CT=douban-mini
WORK=/root/dbm-apply
URL_ZIP="https://codeload.github.com/gooliu/douban-mini/zip/refs/heads/main"
URL_PKG="https://raw.githubusercontent.com/gooliu/douban-mini/main/deploy/douban-mini-update-login-gate.zip"
MD5_EXPECT="ee8ca21db5adabd4cf50d737097d4682"

die() { echo "!! $*"; exit 1; }

echo "== 0/5 环境检查 =="
command -v docker >/dev/null 2>&1 || die "没装 docker"
docker ps --format '{{.Names}}' | grep -qx "$CT" || die "容器 $CT 没在运行，先 docker start $CT"
command -v unzip >/dev/null 2>&1 || die "缺 unzip，先 apt-get install -y unzip"
echo "  容器 $CT 运行中"

echo "== 1/5 下载最新代码 =="
[ "$WORK" = "/" ] && die "WORK 路径异常"
rm -rf "$WORK"; mkdir -p "$WORK"
cd "$WORK" || die "进不去 $WORK"

if curl -fsSL --connect-timeout 10 -m 180 -o repo.zip "$URL_ZIP"; then
  echo "  已下载整仓 zip ($(stat -c%s repo.zip) 字节)"
  unzip -q -o repo.zip || die "解压失败"
  PKG_MODE=repo
else
  echo "  整仓下载失败，改用离线包"
  curl -fsSL --connect-timeout 10 -m 180 -o pkg.zip "$URL_PKG" || die "两个源都下不动，检查网络"
  GOT=$(md5sum pkg.zip | cut -d' ' -f1)
  [ "$GOT" = "$MD5_EXPECT" ] || die "离线包 md5 不符（$GOT），不要继续"
  echo "  离线包 md5 校验通过"
  unzip -q -o pkg.zip || die "解压失败"
  PKG_MODE=pkg
fi

echo "== 2/5 定位源码 =="
MAIN=$(find "$WORK" -path '*/backend/main.py' -print | head -1)
[ -n "$MAIN" ] || die "没找到 backend/main.py（解压目录结构异常）"
SRC=$(dirname "$(dirname "$MAIN")")
echo "  源码目录: $SRC"
for f in backend/main.py backend/douban_client.py web/index.html; do
  [ -f "$SRC/$f" ] || die "缺少 $f"
done
ls -l "$SRC/web/index.html" | awk '{print "  index.html:", $5, "字节"}'

echo "== 3/5 校验版本特征 =="
grep -q "login_required" "$SRC/backend/douban_client.py" || die "douban_client.py 不含 login_required，是旧包"
grep -q "TA 的小组只对登录用户可见" "$SRC/web/index.html" || die "index.html 不含登录门槛文案，是旧包"
echo "  两个关键特征都在，是目标版本"

echo "== 4/5 备份并覆盖 =="
docker exec "$CT" sh -c 'cp /app/backend/main.py /app/backend/main.py.bak2 2>/dev/null; cp /app/web/index.html /app/web/index.html.bak2 2>/dev/null; echo "  旧文件已备份为 *.bak2"' || true
docker cp "$SRC/backend/main.py"          "$CT:/app/backend/main.py"          >/dev/null || die "拷 main.py 失败"
docker cp "$SRC/backend/douban_client.py" "$CT:/app/backend/douban_client.py" >/dev/null || die "拷 douban_client.py 失败"
docker cp "$SRC/web/index.html"           "$CT:/app/web/index.html"           >/dev/null || die "拷 index.html 失败"
echo "  三个文件已覆盖"

echo "== 5/5 重启并自检 =="
docker restart "$CT" >/dev/null || die "重启失败"
for i in 1 2 3 4 5 6 7 8 9 10; do
  sleep 2
  curl -s -o /dev/null -m 3 http://127.0.0.1/ && break
done
docker ps --filter "name=$CT" --format '  状态: {{.Status}}'

echo "  --- 容器内版本特征 ---"
docker exec "$CT" sh -c 'echo "  login_required 命中: $(grep -c login_required /app/backend/douban_client.py)"; echo "  登录门槛文案命中: $(grep -c "只对登录用户可见" /app/web/index.html)"' || true

echo "  --- 接口实测 ---"
for uid in 63425139 207700707 1097726; do
  code=$(curl -s -o "$WORK/r.json" -w '%{http_code}' -m 25 "http://127.0.0.1/api/user/$uid/joined-groups")
  st=$(grep -o '"status": *"[a-z_]*"' "$WORK/r.json" 2>/dev/null | head -1 | sed 's/.*: *"//; s/"//')
  echo "  uid $uid -> HTTP $code, status=${st:-无}"
done

echo
echo "判定：三个接口都应是 HTTP 200。"
echo "  ok             = 正常列出（63425139 应为这个）"
echo "  login_required = 对方设了「仅登录用户可见」（207700707 / 1097726 应为这个）"
echo "  blocked        = 服务器 IP 被豆瓣临时限流，不是用户隐私；等 20~30 分钟再刷。"
echo "                   代码已加冷却（600s 起、连续命中翻倍封顶 1 小时），期间不会反复打豆瓣。"
echo "然后浏览器 Ctrl+F5 强刷 http://43.108.119.94/ 即可看到新版。"
echo "回滚：docker exec $CT sh -c 'cp /app/backend/main.py.bak2 /app/backend/main.py; cp /app/web/index.html /app/web/index.html.bak2' && docker restart $CT"
