#!/usr/bin/env bash
# 国内轻量云服务器一键部署（Ubuntu / Debian / CentOS 通用）
#
# 用法（在服务器上执行两行）：
#   curl -fsSL https://raw.githubusercontent.com/gooliu/douban-mini/main/deploy/一键部署.sh -o install.sh
#   bash install.sh
#
# 或者先在电脑上下载好这个脚本，上传到服务器再 bash install.sh
#
# 可选环境变量：
#   PORT=80            对外端口（默认 80）
#   PIP_INDEX_URL=...  pip 镜像源（默认清华源，国内服务器建议保留）

set -e

GREEN='\033[32m'; YELLOW='\033[33m'; RED='\033[31m'; NC='\033[0m'
say()  { echo -e "${GREEN}[OK]${NC} $*"; }
warn() { echo -e "${YELLOW}[!]${NC}  $*"; }
err()  { echo -e "${RED}[X]${NC} $*"; }

PORT="${PORT:-80}"
PIP_INDEX_URL="${PIP_INDEX_URL:-https://pypi.tuna.tsinghua.edu.cn/simple}"
REPO="${REPO:-https://github.com/gooliu/douban-mini.git}"
DIR="${DIR:-$HOME/douban-mini}"

echo "=============================================="
echo " 豆瓣小组查询 · 服务器一键部署"
echo "=============================================="

# ---------- 1. 装 Docker ----------
if command -v docker >/dev/null 2>&1; then
  say "Docker 已安装：$(docker --version)"
else
  warn "未检测到 Docker，开始安装（约 1-3 分钟）…"
  if command -v apt-get >/dev/null 2>&1; then
    apt-get update -qq
    apt-get install -y -qq ca-certificates curl git >/dev/null
    # 用阿里云镜像装，国内快
    curl -fsSL https://get.docker.com | bash -s docker --mirror Aliyun
  elif command -v yum >/dev/null 2>&1; then
    yum install -y -q curl git >/dev/null
    curl -fsSL https://get.docker.com | bash -s docker --mirror Aliyun
  else
    err "无法识别的系统，请手动安装 Docker 后重跑本脚本"; exit 1
  fi
  systemctl enable --now docker >/dev/null 2>&1 || true
  say "Docker 安装完成"
fi

# ---------- 2. 装 docker compose 插件 ----------
if docker compose version >/dev/null 2>&1; then
  say "docker compose 可用：$(docker compose version --short)"
else
  warn "安装 docker compose 插件…"
  if command -v apt-get >/dev/null 2>&1; then
    apt-get install -y -qq docker-compose-plugin >/dev/null
  else
    mkdir -p /usr/local/lib/docker/cli-plugins
    curl -fsSL https://ghfast.top/https://github.com/docker/compose/releases/latest/download/docker-compose-linux-x86_64 \
      -o /usr/local/lib/docker/cli-plugins/docker-compose
    chmod +x /usr/local/lib/docker/cli-plugins/docker-compose
  fi
  docker compose version >/dev/null 2>&1 || { err "docker compose 安装失败，请手动安装"; exit 1; }
  say "docker compose 安装完成"
fi

# ---------- 3. 拉代码 ----------
if [ -d "$DIR/.git" ]; then
  say "目录已存在，更新代码…"
  cd "$DIR" && git pull --ff-only || warn "更新失败，用现有代码继续"
else
  warn "下载代码到 $DIR …"
  git clone --depth 1 "$REPO" "$DIR" || {
    err "git clone 失败（国内服务器访问 GitHub 有时不通）。"
    echo "    变通办法：在本机打包整个项目为 zip，上传到服务器解压后再跑本脚本。"
    exit 1
  }
  say "代码下载完成"
fi
cd "$DIR"

# ---------- 4. 构建并启动 ----------
warn "构建并启动服务（首次约 2-5 分钟）…"
PORT="$PORT" PIP_INDEX_URL="$PIP_INDEX_URL" \
  docker compose -f deploy/docker-compose.yml up -d --build

# ---------- 5. 自检 ----------
echo
warn "等待服务就绪…"
for i in $(seq 1 30); do
  if curl -fsS -m 3 "http://127.0.0.1:${PORT}/api/health" >/dev/null 2>&1; then
    break
  fi
  sleep 2
done

IP="$(curl -fsS -m 5 ifconfig.me 2>/dev/null || curl -fsS -m 5 ip.sb 2>/dev/null || echo '服务器公网IP')"

echo
if curl -fsS -m 5 "http://127.0.0.1:${PORT}/api/health" >/dev/null 2>&1; then
  say "部署成功！"
  echo
  echo -e "  请在浏览器打开： ${GREEN}http://${IP}${PORT:+:}${PORT}${NC}"
  echo "  （页面和接口在同一台服务器上，不需要再配置后端地址）"
  echo
  echo "  常用命令："
  echo "    看日志： docker logs -f douban-mini"
  echo "    重启：   cd $DIR && docker compose -f deploy/docker-compose.yml restart"
  echo "    更新：   cd $DIR && bash deploy/更新.sh"
  echo
  warn "如果打不开，99% 是云服务器【安全组/防火墙】没放行 ${PORT} 端口——"
  echo "     去云厂商控制台 → 服务器详情 → 防火墙/安全组 → 添加规则：TCP ${PORT} 允许 0.0.0.0/0"
else
  err "服务没起来，看日志： docker logs douban-mini"
  exit 1
fi
