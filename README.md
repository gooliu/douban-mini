# 豆瓣小组查询小程序（douban-mini）

> 🧭 **要改东西先看 [`MAP.md`](MAP.md)** —— 一张表对着找「我想改 X → 改哪个文件 → 怎么上线」。
> 本文件是完整技术说明（原理、安装、接口、踩坑史）。

根据豆瓣用户 ID 查询 **TA 加入过的小组** 和 **历史发言记录** 的微信小程序 Demo。

> ⚠️ **合规提示**：本项目通过豆瓣 APP 内部接口（frodo.douban.com，社区逆向公开的签名算法）获取数据，
> 仅用于学习研究。请勿高频调用、勿用于商业用途。豆瓣随时可能调整接口导致失效。

## 功能（实测能力矩阵，2026-10）

| 功能 | 谁可以查 | 数据来源 | 验证结果 |
|------|---------|---------|---------|
| 用户公开信息 | 任意用户 | ✅ 实时真实 | 昵称/头像/注册时间/粉丝数 |
| 用户看过的影视 / 书 | 任意用户 | ✅ 实时真实 | 含评分、短评 |
| 小组详情 | 任意小组 | ✅ 实时真实 | 名称/成员数/简介 |
| 小组近期话题 | 任意小组 | ✅ 实时真实 | 含作者、评论数 |
| 小组成员列表 | 任意小组 | ✅ 实时真实 | 大组百万级，**不支持按 uid 过滤** |
| **搜索小组**（按名找 id） | 任意 | ✅ 实时真实 | 手机端友好 |
| **搜索用户**（按昵称找 uid） | 任意 | ✅ 实时真实 | 手机端友好 |
| **加入的小组**（本人） | **仅 token 本人** | ✅ 实时真实 | frodo `joined_groups`，字段更全 |
| **完整发言记录** | **仅 token 本人** | ✅ 实时真实 | 实测本人 58 条，可翻页 |
| **他人加入的小组** | — | ⛔ 已默认关闭 | 2026-10-23 起不再抓取：豆瓣网页端对机房 IP 限流很凶（403 后连累其他功能），且多数人设了「仅登录可见」。**查他人小组一律改用「隐藏小组排查」**；想恢复旧行为给容器加 `DOUBAN_LIST_SCRAPE=1` |
| **隐藏小组排查** | 任意（无需登录） | ✅ 实时真实 | 给候选小组，逐个翻成员名单核对某 uid 是否在组内 |
| **反查他人发帖** | 任意（无需 token） | ✅ 实时真实 | 给候选小组 id 扫描过滤 |
| **反查他人回帖** | **需登录**（任意账号） | ✅ 登录后可用 | 豆瓣已把评论接口改为必须登录；未登录会明确提示登录，不再误报「反爬」 |
| **手动补充记录** | 本人（跟着账号走） | ✅ 本地存储 | 帖子网址 / 截图 / 手写关键词，按 uid 记住，下次查同一 ID 自动一起显示 |

> 关键结论：豆瓣把 App 侧的「用户加入的小组」(`joined_groups`) 限制为**只有 token 本人可查**
> ——非本人 uid 一律 403 `need_permission`。但**桌面版网页还留着一个公开入口**：
> `https://www.douban.com/group/people/:uid/joins`，匿名可读、无需签名、一次性返回全部小组，
> 本项目已接进来，因此他人小组列表不再是死路（见下面三条实测结论）。
>
> ### 「他人加入的小组」现状（2026-10-23 变更）
>
> 桌面版 `www.douban.com/group/people/:uid/joins` 匿名可读、一次拿全量，但**豆瓣对机房 IP 限流很凶**：
> 连续抓几十次就 403，恢复要几十分钟，期间还连累其他功能。加上多数用户把小组设成「仅登录可见」
> （如 uid 207700707，匿名只能拿到「未登录无法查看更多内容」），列表功能实际收益很低。
>
> 因此**默认不再抓取他人小组列表**（`DOUBAN_LIST_SCRAPE` 未开启时直接返回 `list_disabled`），
> 页面只显示「TA 主页显示加入了 N 个小组」+ 直接展开「隐藏小组排查」。
> 需要临时恢复：容器加环境变量 `DOUBAN_LIST_SCRAPE=1` 并重启。
>
> ### 「隐藏小组排查」的判定口径（别把"没找到"当成"不在"）
>
> | 结果 | 含义 | 可信度 |
> |------|------|--------|
> | ✅ 命中 | 在成员名单里找到了 TA（告诉你是第几位） | 100% 确定**在**组内 |
> | ❌ 已翻完 | 成员数 ≤ 已扫描数，全量比对过 | 100% 确定**不在**组内 |
> | ⚠️ 未见其人 | 只翻了一部分（大组几十万人，覆盖率很低） | **不确定**，需调大扫描深度 |
>
> 成员名单按加入顺序排列，越早加入越靠前；每页最多 100 条，实测可深翻到 30 万位。
>
> ⚠️ **关于「反爬 / 机器人」提示**：豆瓣很多接口（评论/回帖、话题详情）匿名请求时
> 返回 HTTP 403 + `code=1000` + `msg="need_permission"`，`localized_message` 恰好是
> 「你访问豆瓣的方式有点像机器人程序，请登录」。只看 code=1000 会误判成风控反爬并触发
> 无意义冷却 —— 本项目已按 `msg` 字段区分：`need_permission` 归为「需要登录」，
> 只有真正的风控才进入冷却。**遇到回帖查不了，先登录再查即可。**

## 架构

```
微信小程序 (miniprogram/) ┐
                         ├─ HTTP (JSON) ─→ FastAPI 后端 (backend/) ←— 签名 + 代理 + 缓存
Web 网页 (web/index.html) ┘                       │
                                                  ▼
                                       豆瓣 frodo.douban.com/api/v2
```

- **后端**：Python 3.10+ / FastAPI，封装 frodo 内部 API（`_sig` 签名算法已实测可用），
  自带 10 分钟 TTL 内存缓存；同时挂载 Web 网页版。
- **Web 前端**：`web/index.html` 单文件 SPA（无构建依赖），复刻小程序三页流程。
  - 智能搜索：纯数字当 uid 直接查；其他文本自动并行搜用户和小组
  - 「反查 TA 的小组活动」面板支持「📝 发帖 / 💬 回帖」两种模式
- **小程序前端**：微信原生小程序，三个页面（输入 → 小组列表 → 发言记录）。

## 快速启动

### 1. 启动后端

```bash
cd backend
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

验证：浏览器打开 http://127.0.0.1:8000/docs 可看到接口文档（Swagger UI）。

### 2a. 网页版使用（推荐，最简单）

后端启动后，浏览器直接打开：

```
http://127.0.0.1:8000/
```

会自动跳转到 Web 版查询页面（手机/桌面浏览器均可，与后端同源无跨域问题）。
输入豆瓣用户 ID（示例 `55644233`）即可查询，交互流程与小程序一致。

### 2b. 小程序版使用

1. 打开「微信开发者工具」→ 导入项目 → 选择 `miniprogram/` 目录
   （测试号即可，AppID 用 `touristappid`）
2. 详情 → 本地设置 → 勾选 **「不校验合法域名…」**（因为后端是 http://127.0.0.1）
3. 编译运行，输入示例用户 ID `55644233` 体验

### 3.（可选）配置 DOUBAN_TOKEN —— 查你自己的真实小组与发言

「加入的小组」「发言记录」只有**登录态本人**能查（豆瓣限制，见上文能力矩阵）。获取方式：

1. 手机安装豆瓣 APP 并登录
2. 抓包（安卓 HttpCanary；iOS 用 Stream）→ 找 `frodo.douban.com` 的请求 → 复制请求头
   `Authorization: Bearer xxxxx...` 中 **Bearer 后面那一串**（不要带 "Bearer " 字样）
3. 后端启动时设置环境变量：

```bash
# Windows (PowerShell)
$env:DOUBAN_TOKEN="你的token"; uvicorn main:app --port 8000

# Linux / macOS
DOUBAN_TOKEN=你的token uvicorn main:app --port 8000
```

4. 验证：访问 `/api/health`，看到 `"token_configured": true` 且 `"self_uid": "你的uid"`；
   再访问 `/api/me` 可看到账号昵称、加入小组数、发言总数

配置后网页版会自动识别你的账号（首页出现账号卡片，「我加入的小组 / 我的发言记录」一键查看）。

> 说明：token 对应账号的 uid 由后端自动推断（取 `posted_topics` 首条话题的作者 id）。
> 若账号从未发过小组话题，可用 `DOUBAN_SELF_UID=你的uid` 手动指定。
> 注意 `/api/v2/user/me` 返回的是豆瓣**占位账号**（uid 字段是字符串 "me"），不可用于识别本人。

## API 一览

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/health` | 健康检查，显示 token 状态与识别到的本人 uid |
| GET | `/api/me` | 当前 token 对应账号（前端用它做"我"的入口） |
| GET | `/api/user/{user_id}` | 用户公开信息（真实） |
| GET | `/api/user/{user_id}/groups` | 加入的小组：本人走 frodo（字段全）/ **他人默认返回 `list_disabled`**（不再抓页面，见 `DOUBAN_LIST_SCRAPE`） |
| GET | `/api/user/{user_id}/joined-groups` | 他人加入的小组（纯公开页通道，额外返回「管理的小组」与隐私状态） |
| POST | `/api/check-membership` | **隐藏小组排查**：body `{user_id, groups[], max_members}`，逐个翻成员名单核对 |
| GET | `/api/user/{user_id}/topics?start=&count=` | 发言记录（本人真实可翻页 / 他人 restricted） |
| GET | `/api/user/{user_id}/comments` | 评论记录（豆瓣无公开接口，仅演示） |
| GET | `/api/group/{group_id}` | 小组详情（真实） |
| GET | `/api/group/{group_id}/topics` | 小组近期话题（真实） |
| GET | `/api/topic/{topic_id}` | 话题详情（补充记录里粘贴网址自动带标题用，需登录态） |
| GET/POST/DELETE | `/api/notes/{user_id}` | **手动补充记录**：按 uid 存帖子网址 / 截图 / 手写关键词 |
| POST | `/api/notes/{user_id}/pin` | 置顶 / 取消置顶一条补充记录 |
| GET/POST | `/api/notes/export/all`、`/api/notes/import/all` | 补充记录备份导出 / 导入 |

### 手动补充记录（notes）

豆瓣接口给不了、但你自己掌握的信息，可以补进来（发言记录页 / 反查页都有「➕ 补充记录」按钮）：

* **帖子网址** —— 粘贴链接自动抠出 topic_id 并尝试带出标题
* **截图** —— 选图后前端自动压缩（≤1400px、JPEG），存 base64
* **手写关键词** —— 例如「收了定金不发货」

下次再查同一个 uid，补充记录会自动一起显示（置顶的排最前）。
存储位置按模式自动选择：

| 运行模式 | 存哪里 | 说明 |
|---------|--------|------|
| 本机自用（`127.0.0.1` 访问） | `backend/data/notes.json` | 换浏览器也在；**该文件已列入 .gitignore，不会上传** |
| 共享 / 公网隧道 / 云端 | 访问者自己的浏览器 localStorage | 多人共用后端也不会互相看到；换设备用「导出 / 导入」迁移 |

统一响应体：`{ ok, source: "douban"(真实) | "mock"(演示) | "restricted"(豆瓣限制), data, message }`

## 部署到 GitHub（分享给别人访问）

GitHub Pages **只能托管静态页面**，而这个项目的后端是 Python/FastAPI，所以标准做法是：

```
GitHub Pages（静态前端）  ──fetch──▶  免费云上的 FastAPI 后端  ──▶  豆瓣
   https://xxx.github.io/...              https://xxx.onrender.com
```

好消息是：**后端自己也会托管前端**。所以最简单的做法是只部署后端一个服务，
直接把 `https://xxx.onrender.com/` 发给别人即可（打开就是完整网页，无需 GitHub Pages）。

### 最省事的三步（推荐）

**1. 把代码推到 GitHub**

```bash
cd douban-mini
git init
git add .
git commit -m "豆瓣小组查询"
# 在 github.com 新建一个空仓库（不要勾 README），然后：
git remote add origin https://github.com/<你的用户名>/douban-mini.git
git branch -M main
git push -u origin main
```

> `.gitignore` 已排除 `backend/douban_config.json`（里面有你的豆瓣凭证），不会误提交。

**2. 一键部署后端（Render 免费）**

1. 打开 <https://render.com> → 用 GitHub 登录
2. `New +` → `Blueprint` → 选刚推的仓库（它会读仓库里的 `render.yaml`）
3. 一路 `Apply`，等 3~5 分钟构建完成
4. 拿到地址：`https://douban-mini-xxxx.onrender.com`

Render 免费层 15 分钟无访问会休眠，第一次打开要等 30 秒左右唤醒，属正常现象。

**3. 发给别人**

直接把 `https://douban-mini-xxxx.onrender.com` 发过去即可（手机浏览器也能用，
还能「添加到主屏幕」当 App 用）。

### 想要 github.io 地址（可选）

1. 仓库 `Settings` → `Pages` → `Source` 选 **GitHub Actions**
2. `Settings` → `Secrets and variables` → `Actions` → `Variables` → `New repository variable`
   - 名字：`CLOUD_API`
   - 值：`https://douban-mini-xxxx.onrender.com`（第 2 步拿到的地址）
3. 推一次代码或手动跑一下 `Deploy web to GitHub Pages` 工作流

之后 `https://<用户名>.github.io/douban-mini/` 就是前端地址，工作流会自动把后端地址写进去，
访问者不用手填。没配 `CLOUD_API` 也没关系——页面会提示填一次后端地址，存在浏览器里。

### 云端模式的隐私保护（默认开启）

`render.yaml` 里设了 `DOUBAN_CLOUD=1`，此时：

- 后端**不保存任何人的豆瓣凭证**（不写 `douban_config.json`）
- 每个人登录时，凭证只存在**他自己的浏览器** localStorage 里
- 每次请求通过 `X-Douban-Token` / `X-Douban-Cookie` 头带过来，后端按凭证隔离缓存
- 结果：多人共用一个网址，**互相看不到对方的账号数据**

本机使用（不设该变量）仍是老行为：凭证写进 `backend/douban_config.json`。

### 云端部署：让网址 24/7 可用（推荐）

**为什么必须做**：GitHub Pages 只托管网页（静态文件），**后端仍然跑在这台电脑上**。
你关机 / 断网，别人打开网址就是「能看不能用」——页面正常，但查不了任何东西。
把后端部署到云服务器后，网址与本机彻底解耦，开关机都不影响。

> ⚠️ **别再写家宽公网 IP**。页面里曾经写死过 `http://211.91.184.194:8000`，
> 但运营商分配的 IP 会变——实测这台机器当前的出口 IP 已经是 `141.11.146.78`，
> 旧地址落到别的设备上直接返回 502。填云服务商给的固定 IP / 域名才靠谱。

#### 方式一：境外轻量云服务器（免备案 —— 推荐）

> **备案规则一句话**：只有**中国大陆节点**才需要 ICP 备案。
> 服务器在中国香港 / 新加坡 / 东京 / 硅谷，**一律不需要备案**，买完即用，
> 后面绑域名也不用备案。（腾讯云 / 阿里云账号要实名认证，那是开户要求，跟备案两回事。）

镜像里**已经带上了网页**（`/web`），所以一个地址同时提供页面和接口，**同源**，
不存在 HTTPS 页面调 HTTP 接口被浏览器拦截的问题。

##### 0. 买服务器（零基础手把手，2026-10 实测价）

| 厂商 | 活动页 | 免备案地域 | 参考价 |
| --- | --- | --- | --- |
| 腾讯云 | <https://www.qcloud.com/act/plan> | 中国香港 / 新加坡 / 硅谷 | 境外轻量 2核2G / 30M / 1T流量 约 **99 元/年**（新加坡、硅谷）；香港约 **199 元/年** |
| 阿里云 | <https://www.aliyun.com/benefit/select/ecs> | 中国香港 / 新加坡 / 美国 | 轻量海外通用版 香港 2核2G **约 39 元/月**（年付约 429 元）；老款 30M 款 24 元/月 |
| Vultr | <https://www.vultr.com> | 东京 / 新加坡 / 硅谷 | **$2.5/月** 起，按小时计费，不用随时销毁 |

流程：

1. **注册账号**（手机号）→ **实名认证**（腾讯云 / 阿里云开户都要，身份证或人脸，几分钟）
2. 进活动页 → 找「**轻量应用服务器**」→ 立即购买
3. 购买页这样勾（勾错会白花钱或后面装不上）：

   | 选项 | 勾这个 | 别勾这个 |
   | --- | --- | --- |
   | 机型 | **轻量应用服务器** | 云服务器 CVM / ECS（贵一倍，没必要） |
   | 地域 | **中国香港**（延迟最低）或 **新加坡**（性价比最高） | 中国大陆地域（那就得备案了） |
   | 镜像 | **系统镜像 → Ubuntu 22.04 LTS** | 宝塔 / WordPress 应用镜像（预装面板占端口） |
   | 时长 | **1 年**（新用户优惠只有一次） | 1 个月 |
   | 带宽 | 30M 足够（本项目只传 JSON） | —— |

4. 付款后等 1~3 分钟创建完成，控制台里能看到**公网 IP**（把它发我，后面的我来配）

> 💡 地域怎么选：**香港离大陆最近（30–80ms）**，但便宜档次的跨境线路质量不保证，
> 追求稳定选腾讯云「**锐驰型**」（200Mbps，约 65 元/月）；预算优先选新加坡（60–80ms，99 元/年）。

##### 1. 放行端口

境外服务器也要在控制台放行端口，否则 100% 打不开：
云厂商控制台 → 服务器详情 → **防火墙 / 安全组** → 添加规则：TCP、端口 **80**、来源 `0.0.0.0/0`。

##### 2. 一键部署

控制台里有 **WebShell / 一键登录**，不用装 SSH 客户端，进去粘贴：

```bash
curl -fsSL https://raw.githubusercontent.com/gooliu/douban-mini/main/deploy/一键部署.sh -o install.sh
bash install.sh
```

境外服务器默认走**官方源**（Docker 官方脚本 + PyPI + GitHub 直连），一般都能通；
如果卡在下载，换国内镜像再跑一次：

```bash
CN_MIRROR=1 bash install.sh
```

**服务器连不上 GitHub 时**（脚本卡在 `git clone`）：用离线代码包，全程不碰 GitHub。
把项目打包成 zip（`git archive --format=zip --prefix=douban-mini/ -o douban-mini.zip HEAD`），
在 WebShell 里用「文件上传」传到服务器根目录，然后执行：

```bash
bash ~/douban-mini/deploy/一键部署.sh
```

脚本会**自动发现并解压**根目录里的 zip；已经解压好的 `~/douban-mini` 目录也会直接拿来用，
不再联网下载代码。

脚本跑完会打印地址，浏览器打开 **`http://你的服务器IP/`** 即可——页面和接口同源，
不用再填后端地址。

> 服务器访问 GitHub 拉不动代码？在本机把整个项目打包成 zip，上传到服务器解压，
> 再 `cd douban-mini && bash deploy/一键部署.sh`。

##### 3. 日常维护

```bash
docker logs -f douban-mini                                     # 看日志
docker restart douban-mini                                     # 重启
bash /root/douban-mini/deploy/服务器更新.sh                     # ★ 更新到 GitHub 最新版
```

> `服务器更新.sh` 不依赖 git 和 compose 插件（服务器上的代码是 zip 解压的、镜像里也没有
> compose），会自动 下载最新 zip → 构建 → 换容器重启，补充记录不会丢。
> **只改了网页**（`web/index.html`）时更快：把新文件 `docker cp` 进容器即可，见 [`MAP.md`](MAP.md) 第三节。

补充记录存在 Docker 卷 `app-data` 里，**更新 / 重建容器都不会丢**。

##### 4. 想要 HTTPS（可选）

境外节点绑域名**不需要备案**：把域名 A 记录指向服务器 IP，然后

```bash
DOMAIN=your.domain docker compose -f deploy/docker-compose.yml --profile https up -d
```

Caddy 会自动申请并续期 Let's Encrypt 证书。有了 HTTPS 之后，把 `https://your.domain`
填进 `web/index.html` 的 `DEFAULT_CLOUD_API` 并推送，**GitHub Pages 那个 github.io 网址
也能用这台服务器了**。

#### 方式二：国内轻量云服务器（豆瓣风控最少）

国内服务器 + **纯 IP 访问**同样不用备案（备案只针对「域名 + 80/443」），
而且访问豆瓣时风控最少、延迟最低。国内地域 2核2G 新用户约 68 元/年（日常价 400+），
活动页：<https://www.qcloud.com/act/plan> 、<https://www.aliyun.com/benefit/select/ecs>。

步骤与方式一相同，只差两点：地域选**国内（上海 / 广州 / 北京）**；部署时走国内镜像，
不然装 Docker 和拉依赖会很慢：

```bash
CN_MIRROR=1 bash install.sh
```

> ⚠️ 续费会恢复原价（400+ 元/年），下单时留意「同价续费」标签。
> 国内节点用**域名** + 80/443 必须做个人 ICP 备案（免费，10~20 天）。

#### 方式三：Cloudflare Workers（免费、免备案、不休眠）

免费额度 10 万请求/天 + KV 1GB 存储、**无冷启动**、不用绑卡，个人自用绰绰有余。
代价是后端现在是 Python，需要改写成 JS Worker（HMAC 签名用 WebCrypto、请求用 fetch、
记录库存 KV）——属于改造项，要的话让我来做。

#### 方式四：其它云平台

`Dockerfile` 是通用的，Koyeb / Northflank / Fly.io / Render 都能直接用：

```bash
docker build -t douban-mini .
docker run -d -p 8000:8000 -e DOUBAN_CLOUD=1 \
  -v douban-data:/data douban-mini      # 挂卷才能保住补充记录

# 或者用现成的 compose（含持久卷 + 健康检查）
docker compose -f deploy/docker-compose.yml up -d --build
```

免费额度参考（2026-10）：**Northflank** 免费层常驻不休眠、不用信用卡；
**Koyeb** 多数账号免卡，但区域只有法兰克福 / 华盛顿；**Railway** 已无永久免费档；
**Fly.io** 新用户必须绑卡；**Render** 免费 Web 服务对部分新账号会弹「Add card」
（1 美元预授权），且 15 分钟休眠、无持久磁盘。

> ❌ **Zeabur 不要再选**：官方已公告停止运营，2026-12-01 起分批停服、
> **2026-12-31 全部关闭**（年内还发生过两起安全入侵事件）。已在用的请尽快迁到上面任一方式。
>
> ⚠️ 豆瓣对境外 IP 风控更严。部署后如果常出现「机器人程序」提示，优先用
> **自己的豆瓣 Cookie 登录**（登录弹窗 → 粘贴 Cookie，默认就是这一项），
> 匿名调用在境外 IP 上更容易被拦。
>
> ℹ️ **登录方式（界面上只保留这三条能用的）**：
> ① **粘贴 Cookie（推荐，默认项）** —— 走移动网页通道 `m.douban.com/rexxar`
>    （豆瓣 H5 靠 Cookie 认登录态；frodo App 接口只认 Bearer，
>    **所以 Cookie 必须走 rexxar 通道**）。浏览器登录 douban.com → F12 →
>    「应用 / Application」→ Cookie → 复制 `dbcl2` 和 `ck`，格式：
>    `dbcl2="123456789:AbCdEf"; ck=xxxx`（**英文分号**分隔，引号保留）。
> ② **粘贴 token** —— 抓包拿到的 Bearer token（手机 App 用 HTTPCanary / Stream）。
> ③ **手机接力** —— 电脑已登录后生成 6 位码，手机输入即可（token / Cookie 都能接力）。
>
> 🚫 **已从界面移除的两条路**（实测不可用，别再试）：
> - **豆瓣账号密码**：豆瓣收紧第三方客户端校验，密码正确也常返回
>   `invalid_device_id`（设备号已是 20 位十六进制、存在 `backend/data/device.json`、
>   失败会自动换端点 + 换 20/40 位重试，仍过不去；境外 IP 更容易触发）。
> - **「从本机浏览器读取」按钮 / 一键带回书签**：前者只在**自用模式**且请求来自
>   本机或局域网时可用，云端共享模式下必然失败（后端在服务器，读不到访客的浏览器，
>   这是安全设计）；后者因为豆瓣把 `dbcl2` 设成 **HttpOnly**，浏览器 JS
>   （含 `javascript:` 小书签、`document.cookie`）根本读不到它。
>   > 后端接口 `/api/login/password`、`/api/local-cookie` 仍保留（自用模式本地跑时有用），
>   > 只是前端不再展示入口。
>
> ⚠️ 实测记录（2026-10-21）：`accounts.douban.com/j/mobile/login/basic`
> 服务器直连一律返回 `captcha_required`（腾讯验证码），所以「账号密码换网页
> Cookie」这条路也堵死了；frodo apikey 走 OAuth 授权码也不行（任何 redirect_uri
> 都报 `redirect_uri_mismatch`）。能用的只有上面三条。

> 📚 **补充记录库**：主页入口可查看**所有人**手动补充过的 ID 记录，
> 支持按豆瓣 ID（纯数字）或关键词（正文/标题/网址/标签）搜索；
> 接口 `GET /api/notes-library?q=&limit=`，列表里不含截图 base64（只有标记）。

## 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `DOUBAN_TOKEN` | 空 | 豆瓣登录态 Bearer token（不含 "Bearer " 前缀） |
| `DOUBAN_CLOUD` | 空 | 设为 `1` 开启云端模式：凭证不落服务端，只存访问者浏览器 |
| `DOUBAN_SELF_UID` | 自动推断 | 手动指定 token 对应 uid |
| `DOUBAN_CACHE_TTL` | 600 | 缓存秒数 |
| `DOUBAN_SCAN_GROUPS` | 8 | 保留参数（扫描方案已因接口限制失效） |
| `DOUBAN_DEVICE_ID` | 自动生成 | 账号密码登录用的设备号（20 位十六进制）；一般用不到，仅调试用 |
| `DOUBAN_DATA_DIR` | `backend/data` | 补充记录 / 设备号的存放目录；云端部署指到挂载卷才能跨重启保留 |
| `PORT` | 8000 | 监听端口；Render / Koyeb 等平台会自动注入，容器已兼容 |

## 技术要点：frodo 签名算法（已实测）

```
_sig = base64( HMAC-SHA1( secret, message ) )
secret = "bf7dddc7c9cfe6f7"
message = "GET&{urlencode(path, safe='')}&{ts}"           # 匿名请求（3 段）
message = "GET&{urlencode(path, safe='')}&{token}&{ts}"   # 带登录态（4 段）

查询参数: apikey=0dad551ec0f84ed02907ff5c42e8ec70&_ts={秒级时间戳}&_sig={签名}&os_rom=android
请求头:   User-Agent 必须形如豆瓣安卓客户端
注意:     path 必须是完整路径（含 /api/v2 前缀），且 urlencode 时 / 也要编码为 %2F
```

## 目录结构

```
douban-mini/
├── backend/
│   ├── main.py            # FastAPI 路由（含 /web 静态挂载）
│   ├── douban_client.py   # frodo API 客户端（签名/缓存/扫描）
│   ├── mock_data.py       # 演示数据
│   ├── probe_frodo.py     # 接口探测脚本（调试用，不含任何凭据）
│   └── requirements.txt
├── web/
│   └── index.html         # Web 网页版（单文件，浏览器直接用）
├── deploy/                # 轻量服务器 / VPS 一键部署（境外、国内都能用）
│   ├── 一键部署.sh         #   装 Docker → 拉代码 → 构建 → 启动 → 自检
│   │                      #     境外走官方源；国内服务器加 CN_MIRROR=1
│   ├── 服务器更新.sh       #   ★ 推荐：已部署机器更新到 GitHub 最新版
│   │                      #     不用 git、不用 compose 插件，数据在卷里不丢
│   ├── 更新.sh            #   老脚本，依赖 docker compose（本机缺插件时用不了）
│   ├── docker-compose.yml #   后端 + 持久卷（--profile https 可加 Caddy 自动 HTTPS）
│   └── Caddyfile          #   自动 HTTPS 配置（境外节点绑域名不用备案）
├── Dockerfile
├── render.yaml
├── MAP.md                 # ★ 改哪里速查（一句话定位到要改的文件）
└── miniprogram/
    ├── app.js / app.json / app.wxss
    ├── utils/api.js       # 请求封装
    └── pages/
        ├── index/         # 输入用户 ID
        ├── groups/        # 加入的小组列表
        └── posts/         # 发言记录 / 小组话题
```

## 已知限制

1. 豆瓣 2017 年后不再开放官方 API，本项目走的是 APP 内部接口（社区逆向），
   **接口随时可能变化或失效**
2. **「加入的小组」「发言记录」只支持查 token 本人的账号**：豆瓣对 `joined_groups`
   做了权限收紧，非本人 uid 一律 403 `need_permission`；网页版对这一情况会显示蓝色
   提示条说明原因，而不是伪造数据
3. token 有有效期（通常数十天），过期后需重新抓包；token 等同账号登录态，注意保管
4. 小程序正式发布需将后端部署到 HTTPS 域名并在微信后台配置合法域名
5. 频繁请求可能触发风控，请勿高频调用（后端已内置 10 分钟 TTL 缓存）

## 部署与分享

### 一键脚本

| 脚本 | 作用 |
| --- | --- |
| `启动豆瓣查询.bat` | 启动后端（8000 端口） |
| `开通公网访问.bat` | 放行防火墙 + 引导路由器端口映射 + 自检 |
| `建Gitee仓库并推送.bat` | 自动建 Gitee 仓库并推送代码 |
| `分享给他人.bat` | 局域网分享（二维码） |
| `放到桌面.bat` | 创建桌面快捷方式 |

### 后端公网化（前后端同源，推荐）

后端本身提供网页（`http://<地址>:8000/web/`），所以只要把 8000 端口暴露到公网，
整个应用即可被他人访问，不存在跨域与混合内容问题。

1. 双击 `开通公网访问.bat`（会自动放行防火墙，并告诉你公网 IP / 路由器地址）
2. 按提示登录路由器，新增端口映射：`外部 8000 → 内网IP:8000 / TCP`
3. 把 `http://<公网IP>:8000/web/` 发给对方

> 家宽公网 IP 会在重拨后变化；若运营商封锁入站端口，可改外部端口为 8080/8888。
> 若路由器支持 UPnP，可尝试 `python tools/upnp_map.py`（无需路由器密码）。

### 前端静态托管（备用）

`web/` 是纯静态页面，可直接托管到任意静态服务。后端地址在
`web/index.html` 的 `DEFAULT_CLOUD_API` 中配置，也支持用 `?api=<后端地址>` 覆盖。

注意：静态页若是 HTTPS，而后端是 HTTP，浏览器会按"混合内容"拦截请求；
页面顶部会自动提示解决办法（允许不安全内容，或改用同源 HTTP 版）。

### 云端多用户模式

设置环境变量 `DOUBAN_CLOUD=1` 后，服务端不保存任何人的凭证：
登录凭证只存在访问者自己的浏览器（localStorage），通过请求头
`X-Douban-Token` / `X-Douban-Cookie` 传递，后端按凭证隔离缓存。
`backend/douban_config.json` 已被 `.gitignore` 排除，不会进仓库。

### 已上线的地址

| 用途 | 地址 |
| --- | --- |
| 代码仓库 | https://github.com/gooliu/douban-mini |
| 网页（GitHub Pages） | https://gooliu.github.io/douban-mini/ |
| 云端服务器（韩国首尔，免备案） | http://43.108.119.94/ |

网页由 `.github/workflows/pages.yml` 在每次推送 `main` 后自动部署，
**网页本身永远在线**（存在 GitHub 服务器上，与本机开关机无关）。

`DEFAULT_CLOUD_API` 保持**留空**是有意的：服务器镜像自带 `/web`，
打开 `http://43.108.119.94/` 时页面和接口**同源**，前端会自动走相对路径，
不需要填任何地址（填了反而会让 https 的 github.io 页面因「HTTPS 页面调 HTTP 接口」
被浏览器拦截）。想让 github.io 也用这台服务器，需要给服务器加 HTTPS：
境外节点绑域名不用备案，可用免费通配 DNS（如 `43.108.119.94.nip.io`）+ Caddy 自动证书，

```bash
DOMAIN=43.108.119.94.nip.io docker compose -f deploy/docker-compose.yml --profile https up -d
```

拿到 `https://43.108.119.94.nip.io` 后再填进 `DEFAULT_CLOUD_API` 即可。

### 公网隧道（推荐：免注册、免账号、自带 HTTPS）

双击 **`启动公网隧道.bat`**，几秒后得到一个 `https://xxx.lhr.life` 地址，直接发给别人。

- 原理是 SSH 反向隧道，用系统自带的 OpenSSH，**不用装软件、不用注册任何账号**
- 网页和后端同一个地址（同源），没有跨域、也没有「HTTPS 页面访问 HTTP 接口」的拦截
- 脚本会强制以后端 `DOUBAN_PUBLIC=1` 模式运行：**任何访客都必须登录自己的豆瓣账号**，
  看不到你的 uid（隧道转发后源 IP 会变成 127.0.0.1，不强制隔离就会泄露账号）
- 脚本会额外生成一条带 `cred=` 的专属链接，**只有你自己打开一次**，
  凭证写进你自己浏览器的 localStorage，之后用公开地址也是你自己的账号
- 缺点：运行隧道的窗口要一直开着，电脑关机或重启后地址会变，需要重新发

### 访问者与登录隔离

| 谁打开 | 看到什么 |
| --- | --- |
| 你自己（专属链接打开过一次后） | 你自己的账号，免登录 |
| 其他任何人 | 空白 + 提示「请用你自己的豆瓣账号登录」，凭证只进他自己的浏览器 |

相关环境变量：

| 变量 | 作用 |
| --- | --- |
| `DOUBAN_CLOUD=1` | 云端多用户模式，服务端不保存任何人的凭证 |
| `DOUBAN_PUBLIC=1` | 公网隧道模式，强制每个访客自带凭证 |
| `DOUBAN_TRUST_LAN=1` | 信任局域网，家里 WiFi 下的设备可免登录（默认关闭，更安全） |

`/api/local-credential` 是唯一会返回服务端凭证的接口，用于「本机浏览器一键继承」。
它要求源地址为回环**且不带任何转发头**（`X-Forwarded-For` 等），
一旦检测到转发头就拒绝，避免在隧道场景下把凭证泄露给外部访客。
