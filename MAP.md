# 🧭 改哪里速查（先看这个）

> 面向「我要加个功能 / 改句话 / 换个样式」的场景，按表找文件即可。
> 部署原理、安装步骤等完整说明在 [`README.md`](README.md)。

---

## 一、当前线上环境（2026-10-22）

| 项目 | 值 |
| --- | --- |
| 服务器 | 阿里云轻量 · 韩国首尔 |
| 公网 IP | `43.108.119.94` |
| 访问地址 | **<http://43.108.119.94/>**（`http`，不是 https） |
| 是否备案 | 不需要（境外节点） |
| 容器内位置 | 代码 `/app`，网页 `/app/web`，数据 `/app/data` |
| 容器名 | `douban-mini`（开机自启，策略 `unless-stopped`） |
| 端口映射 | 服务器 80 → 容器 8000 |
| 数据卷 | `app-data`（存补充记录，**重建容器不会丢**） |
| 运行模式 | 云端共享模式 `DOUBAN_CLOUD=1`（每个访客登录自己的账号） |
| 服务器上代码 | `/root/douban-mini`（由 GitHub zip 解压而来，**没有 .git**，不能用 git pull） |

⚠️ GitHub 仓库更新后**不会自动同步到服务器**，必须手动执行一次上线操作（见第三节）。

---

## 二、我想改 → 改哪个文件

| 我想…… | 改这个文件 | 备注 |
| --- | --- | --- |
| 改**界面文字 / 按钮 / 颜色 / 布局** | `web/index.html` | 网页是**单文件**（HTML+CSS+JS 全在里面） |
| 新增 / 删除一个**功能页面**（视图） | `web/index.html` 搜 `view-notes` 仿照写一个 | 同时改顶部导航（`nav-` 开头的按钮） |
| 改**登录弹窗**里的说明或入口 | `web/index.html` 搜 `tokenModal` | 现在只有三个可用入口：粘贴 Cookie / 粘贴 token / 手机接力 |
| 改**首页那张未登录引导卡** | `web/index.html` 搜 `loginPrompt` | |
| 改**接口返回值 / 新增后端接口** | `backend/main.py` | FastAPI，路由都在这里 |
| 改**调豆瓣的方式**（签名、设备号、请求头） | `backend/douban_client.py` | 含 `_sig` 签名、设备号持久化、桌面版 HTML 抓取 |
| 改「**他人加入的小组**」抓取逻辑 | `backend/douban_client.py` 搜 `fetch_joined_groups_www` | 抓 `www.douban.com/group/people/:uid/joins`，匿名可读 |
| 改「**隐藏小组排查**」判定口径 | `backend/douban_client.py` 搜 `check_uid_in_group` | 翻成员名单比对 uid |
| 改排查面板的 UI | `web/index.html` 搜 `hiddenCheckPanelHtml` | |
| 改**记录库（手动补充）**逻辑 | `backend/notes.py` | 数据落盘在 `/app/data`（卷 `app-data`） |
| 改**批量扫描**逻辑 | `backend/scanner.py` | |
| 读**本机浏览器 Cookie**（仅自用模式） | `backend/browser_cookie.py` | 云端模式用不到 |
| 加 / 换 **Python 依赖** | `backend/requirements.txt` | 改了必须重新构建镜像 |
| 改**机器人 / 冷却 / 缓存策略** | `backend/main.py`（冷却）+ `backend/douban_client.py`（缓存） | |
| 改**小程序** | `miniprogram/` 各页面 | 与网页版相互独立，改它不影响网站 |
| 改**部署方式**（换端口、HTTPS、多站点） | `deploy/` | `docker-compose.yml`、`Caddyfile`、各 `.sh` |
| 改 **GitHub Pages 自动发布** | `.github/workflows/pages.yml` | Pages 只发布网页壳，**没有后端**，目前不是主用入口 |

**自检脚本**（改完本地跑一下，能提前发现 JS 报错 / DOM 缺失）：

| 脚本 | 检查什么 |
| --- | --- |
| `tools/login_ui_check.js` | 登录弹窗：入口数量、按钮接线、DOM 完整性、JS 语法 |
| `tools/ui_check.js` | 主界面各视图与导航 |
| `tools/notes_check.js` | 记录库增删查 |
| `tools/server_selfhost_check.js` | 自己跑服务时的同源 / 接口自检 |
| `tools/cloud_deploy_check.js` | 云端部署就绪检查（Dockerfile、环境变量等） |
| `tools/hidden_group_ui_check.py` | 隐藏小组排查：面板函数 / CSS / DOM id / JS 语法是否齐全 |

后端改动也可以本地起服务实测（前端静态页由后端同源提供，无需另起 web 服务）：

```bash
cd backend && python -m uvicorn main:app --host 127.0.0.1 --port 8011
# 浏览器打开 http://127.0.0.1:8011/web/ 即可看到完整页面
```

---

## 三、改完之后，怎么让它在线上生效

### 0. 前端 + 后端都改了（本机推不动 GitHub 时用它）—— 上传更新包

容器里跑的是 **Python 源码**，所以改后端**不用重建镜像**，`docker cp` 进去再重启即可：

```
桌面(zip) → Workbench「上传文件」→ 服务器执行：
cd ~/douban-mini-update && bash 应用更新-隐藏小组排查.sh
```

脚本做的事：复制文件进容器 → `docker restart` → 语法自检 →
顺带确认这台海外机器能不能抓到豆瓣桌面版页面。
更新包模板见 `deploy/应用更新-隐藏小组排查.sh`（换文件名即可复用）。

### A. 只改了网页（`web/index.html`）—— 不用重建，几秒生效

服务器上执行（root 身份，不是 root 先 `sudo -i`）：

```bash
cd /root && rm -rf dm dm.zip && curl -fsSL -m 120 -o dm.zip \
  https://codeload.github.com/gooliu/douban-mini/zip/refs/heads/main \
  && unzip -q -o dm.zip -d /root \
  && docker cp /root/douban-mini-main/web/index.html douban-mini:/app/web/index.html \
  && echo "页面已更新"
```

然后浏览器按 **Ctrl + F5** 强制刷新（普通刷新会看到旧页面）。

### B. 改了后端 Python / 依赖 / Dockerfile —— 要重建镜像

```bash
bash /root/douban-mini/deploy/服务器更新.sh
```

（这个脚本会自己下载最新代码 → 构建 → 换容器重启；数据在卷里不会丢。
拿不到脚本时用手动版：构建 + 重启两条，见 `README.md` 的「更新」小节。）

### C. 把改动传到 GitHub —— 本机三个办法

本项目在本机**无法自动推送**（环境拿不到 GitHub 登录态），任选其一：

1. **网页拖拽**（最省事）：打开 <https://github.com/gooliu/douban-mini/upload/main>
   → 登录 → 把改好的文件拖进去 → 拉到底 **Commit changes**
   ⚠️ 一定要停在**仓库根目录**再拖单个文件。若停在 `web/` 里拖文件夹，会多出一层目录。
2. **双击 `推送更新.bat`**：直接回车走 GitHub 登录窗 / 或粘贴 Token
3. **自己 `git push origin main`**

---

## 四、运维常用命令（服务器 root 下）

| 想干什么 | 命令 |
| --- | --- |
| 看容器状态 | `docker ps` |
| 看日志（报错先看这个） | `docker logs -n 50 douban-mini` |
| 实时跟日志 | `docker logs -f douban-mini` |
| 重启服务 | `docker restart douban-mini` |
| 停 / 起 | `docker stop douban-mini` / `docker start douban-mini` |
| 本机探活（**307 就是正常**，代表 `/` 跳 `/web/`） | `curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1/` |
| 看接口模式 | `curl -s http://127.0.0.1/api/health` |
| 进容器看文件 | `docker exec -it douban-mini sh` |
| 备份补充记录 | `docker run --rm -v app-data:/data -v $PWD:/bak alpine tar czf /bak/notes.tgz /data` |

排障口诀：
- 本机 `curl 127.0.0.1` 返回 **000** → 服务没起来，看 `docker logs`
- 返回 **307 / 200** → 正常
- 外部浏览器打不开，但服务器上正常 → 九成是**防火墙没放行 80**
- 改了没变化 → 浏览器缓存，Ctrl+F5

---

## 五、登录机制速记（别再走弯路）

| 方式 | 现状 |
| --- | --- |
| 粘贴 Cookie（含 `dbcl2`） | ✅ **唯一推荐**。存在访客自己浏览器的 localStorage，服务器不保存 |
| 粘贴 token | ✅ 可用（需自己抓包） |
| 手机接力码 | ✅ 电脑登录后在弹窗里生成 6 位码给手机 |
| 账号密码登录 | ❌ 豆瓣返回 `invalid_device_id`，接口保留但入口已移除 |
| 「从本机浏览器读取」/ 一键书签 | ❌ 云端模式下必然失败（后端在韩国读不到你电脑；且 `dbcl2` 是 HttpOnly） |

取 Cookie：F12 →「应用」→ Cookie → `https://www.douban.com` → 复制 `dbcl2` 和 `ck`，
粘成 `dbcl2="xxx:yyy"; ck=zzz`（**分号必须是英文半角**，dbcl2 的引号要保留）。

> 本机关机 / 重启 **不影响** 网站访问和登录态；只有服务器停机才会。

---

## 六、目录速览

```
web/index.html            网页全部内容（唯一前端文件）
backend/main.py           后端路由（所有 /api/* 在这里）
backend/douban_client.py  豆瓣接口封装：签名、设备号、重试、缓存
backend/notes.py          手动补充记录（持久化到 /app/data）
backend/scanner.py        批量扫描小组/发帖/回帖
backend/browser_cookie.py 读本机浏览器 Cookie（仅自用模式）
backend/mock_data.py      演示用假数据
deploy/                   部署相关（见下）
tools/                    自检脚本（开发用）
miniprogram/              微信小程序（次要，可忽略）
.github/workflows/        GitHub Pages 自动发布
MAP.md                    本文件
README.md                 完整说明：原理、安装、接口、踩坑史
```

`deploy/`：
```
一键部署.sh      新服务器从零部署（境内外通用，CN_MIRROR 开关）
服务器更新.sh    已部署机器更新到 GitHub 最新版（不依赖 git / compose）
应用更新-隐藏小组排查.sh  离线小更新：docker cp 三个文件 + 重启 + 自检（不用重建镜像）
更新.sh          老脚本，依赖 docker compose（本机没装插件时用不了）
docker-compose.yml  编排（含 --profile https 加 Caddy）
Caddyfile        自动 HTTPS 模板（境外节点免备案）
```

---

## 七、待办 / 小尾巴

- [ ] 在 GitHub 网页删掉误传的目录 `web/上传GitHub拖进去/`（不影响使用）
- [ ] 想用 `gooliu.github.io` 那个网址访问 → 需要给服务器配 HTTPS
      （`43.108.119.94.nip.io` + Caddy，境外免备案），配好后 https 页面才能调这台服务器
- [ ] 以后要在同一台服务器放**第二个网站** → 用 Caddy 按域名分流到不同容器端口即可
