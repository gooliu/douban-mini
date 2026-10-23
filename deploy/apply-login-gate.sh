#!/usr/bin/env bash
# 一键把「登录门槛判定」这版更新应用到已运行的容器
# 用法（服务器上，root 或普通用户都行；docker 命令需要权限时前面加 sudo）：
#   cd ~ && curl -fsSL https://raw.githubusercontent.com/gooliu/douban-mini/main/deploy/apply-login-gate.sh -o apply.sh && bash apply.sh
#
# 做四件事：下载最新代码 -> 校验 -> 备份并覆盖容器内文件 -> 重启并自检
# 幂等：重复跑只覆盖同样三个文件，不会动 app-data 数据卷
set -u

CT=douban-mini
# 别写死 /root —— 阿里云 Workbench 的 shell 有时不是 root，会 Permission denied
WORK="${HOME:-/tmp}/dbm-apply"
URL_ZIP="https://codeload.github.com/gooliu/douban-mini/zip/refs/heads/main"
URL_PKG="https://raw.githubusercontent.com/gooliu/douban-mini/main/deploy/douban-mini-update-hhy.zip"
MD5_EXPECT="60f9f68c35876ae710018f3383d12c72"

die() { echo "!! $*"; exit 1; }

echo "== 0/5 环境检查 =="
command -v docker >/dev/null 2>&1 || die "没装 docker"

# docker.sock 经常只允许 root / docker 组访问。脚本自动判断要不要加 sudo：
# 先缓存一次 sudo 凭据（只提示一次密码），后面所有 docker 命令都带上同样的前缀。
DK=""
if ! docker info >/dev/null 2>&1; then
  echo "  当前用户（${USER:-$(id -un)}）不能直接访问 docker.sock，尝试 sudo"
  command -v sudo >/dev/null 2>&1 || die "无权访问 docker 且没有 sudo。请用 root 跑，或执行 sudo usermod -aG docker \$USER 后重新登录"
  sudo -v || die "sudo 认证失败。也可把自己加进 docker 组：sudo usermod -aG docker \$USER（需重新登录生效）"
  DK="sudo "
  echo "  已拿到 sudo 权限"
fi

${DK}docker ps --format '{{.Names}}' | grep -qx "$CT" || die "容器 $CT 没在运行，先 ${DK}docker start $CT"
command -v unzip >/dev/null 2>&1 || die "缺 unzip，先 sudo apt-get install -y unzip"
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
for f in backend/main.py backend/douban_client.py backend/data/hhy_groups.json web/index.html; do
  [ -f "$SRC/$f" ] || die "缺少 $f"
done
ls -l "$SRC/web/index.html" | awk '{print "  index.html:", $5, "字节"}'

echo "== 3/5 校验版本特征 =="
grep -q "login_required" "$SRC/backend/douban_client.py" || die "douban_client.py 不含 login_required，是旧包"
grep -q "DOUBAN_LIST_SCRAPE" "$SRC/backend/main.py" || die "main.py 不含 DOUBAN_LIST_SCRAPE 开关，是旧包"
grep -q "api/hhy/artists" "$SRC/backend/main.py" || die "main.py 不含艺人后花园接口，是旧包"
grep -q "TA 的小组只对登录用户可见" "$SRC/web/index.html" || die "index.html 不含登录门槛文案，是旧包"
grep -q "applyArtistToChk" "$SRC/web/index.html" || die "index.html 不含艺人套用入口，是旧包"
echo "  关键特征都在，是目标版本"

echo "== 4/5 备份并覆盖 =="
${DK}docker exec "$CT" sh -c 'cp /app/backend/main.py /app/backend/main.py.bak2 2>/dev/null; cp /app/web/index.html /app/web/index.html.bak2 2>/dev/null; echo "  旧文件已备份为 *.bak2"' || true
${DK}docker exec "$CT" mkdir -p /app/backend/data
${DK}docker cp "$SRC/backend/main.py"               "$CT:/app/backend/main.py"               >/dev/null || die "拷 main.py 失败"
${DK}docker cp "$SRC/backend/douban_client.py"      "$CT:/app/backend/douban_client.py"      >/dev/null || die "拷 douban_client.py 失败"
${DK}docker cp "$SRC/backend/data/hhy_groups.json"  "$CT:/app/backend/data/hhy_groups.json"  >/dev/null || die "拷 hhy_groups.json 失败"
${DK}docker cp "$SRC/web/index.html"                "$CT:/app/web/index.html"                >/dev/null || die "拷 index.html 失败"
echo "  四个文件已覆盖（含艺人后花园名单）"

echo "== 5/5 重启并自检 =="
${DK}docker restart "$CT" >/dev/null || die "重启失败"
for i in 1 2 3 4 5 6 7 8 9 10; do
  sleep 2
  curl -s -o /dev/null -m 3 http://127.0.0.1/ && break
done
${DK}docker ps --filter "name=$CT" --format '  状态: {{.Status}}'

echo "  --- 容器内版本特征 ---"
${DK}docker exec "$CT" sh -c 'echo "  DOUBAN_LIST_SCRAPE 开关: $(grep -c DOUBAN_LIST_SCRAPE /app/backend/main.py)"; echo "  艺人后花园接口: $(grep -c "api/hhy/artists" /app/backend/main.py)"; echo "  艺人名单文件: $(ls /app/backend/data/hhy_groups.json 2>/dev/null || echo 缺失)"; echo "  艺人套用入口: $(grep -c applyArtistToChk /app/web/index.html)"' || true

echo "  --- 接口实测 ---"
echo "  [艺人后花园] $(curl -s -m 10 http://127.0.0.1/api/hhy/artists | grep -o '"message": *"[^"]*"' | head -1)"
for uid in 63425139 207700707 1097726; do
  code=$(curl -s -o "$WORK/r.json" -w '%{http_code}' -m 25 "http://127.0.0.1/api/user/$uid/groups")
  dis=$(grep -o '"list_disabled": *[a-z]*' "$WORK/r.json" 2>/dev/null | head -1 | grep -o '[a-z]*$')
  clm=$(grep -o '"claimed_total": *[0-9]*' "$WORK/r.json" 2>/dev/null | head -1 | grep -o '[0-9]*$')
  tot=$(grep -o '"total": *[0-9]*' "$WORK/r.json" 2>/dev/null | head -1 | grep -o '[0-9]*$')
  echo "  uid $uid -> HTTP $code, list_disabled=${dis:-无} claimed=${clm:-0} total=${tot:-0}"
done

echo
echo "判定：三个接口都应是 HTTP 200。"
echo "  他人（207700707 / 1097726）：list_disabled=true、total=0、claimed>0  → 正确，不再抓取列表"
echo "  查自己（63425139 未登录时同样是他人视角）：同上；登录后应 total>0"
echo "  若 list_disabled 显示「无」，说明还是旧包，把上面输出发我。"
echo "然后浏览器 Ctrl+F5 强刷 http://43.108.119.94/ 即可看到新版。"
echo "回滚：${DK}docker exec $CT sh -c 'cp /app/backend/main.py.bak2 /app/backend/main.py; cp /app/web/index.html.bak2 /app/web/index.html' && ${DK}docker restart $CT"
