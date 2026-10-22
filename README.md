# 豆瓣小组查询小程序（douban-mini）

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
| **加入的小组** | **仅 token 本人** | ✅ 实时真实 | 实测本人 19 个小组 |
| **完整发言记录** | **仅 token 本人** | ✅ 实时真实 | 实测本人 58 条，可翻页 |
| 他人加入的小组 | 他人 | ⛔ 豆瓣已关闭 | 无任何可用接口 |
| **反查他人发帖** | 任意（无需 token） | ✅ 实时真实 | 给候选小组 id 扫描过滤 |
| **反查他人回帖** | **需登录**（任意账号） | ✅ 登录后可用 | 豆瓣已把评论接口改为必须登录；未登录会明确提示登录，不再误报「反爬」 |
| **手动补充记录** | 本人（跟着账号走） | ✅ 本地存储 | 帖子网址 / 截图 / 手写关键词，按 uid 记住，下次查同一 ID 自动一起显示 |

> 关键结论：豆瓣把「用户加入的小组」(`joined_groups`) 和「用户发言」(`posted_topics`)
> 限制为**只有 token 本人可查**——非本人 uid 一律 403 `need_permission`。
> 他人的「加入的小组」无任何可用接口；他人的「发帖/回帖」可用「反查」变通：给定候选小组扫描过滤。
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
| GET | `/api/user/{user_id}/groups` | 加入的小组（本人真实 / 他人 restricted） |
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
把后端部署到云平台后，网址与本机彻底解耦，开关机都不影响。

> ⚠️ **别再写家宽公网 IP**。页面里曾经写死过 `http://211.91.184.194:8000`，
> 但运营商分配的 IP 会变——实测这台机器当前的出口 IP 已经是 `141.11.146.78`，
> 旧地址落到别的设备上直接返回 502。填云端平台给的 `https://xxx.onrender.com`
> 这类固定域名才靠谱。

#### 方式一：Zeabur（免信用卡、中文界面、国内访问快，首选）

1. 打开 <https://zeabur.com> → **用 GitHub 账号登录**
2. 新建一个项目（区域选 **香港 / 东京**，离国内最近）
3. 项目里 `Add Service` → **Git** → 授权 GitHub → 选 `gooliu/douban-mini`
4. 它会读到仓库根目录的 `Dockerfile` 自动构建。
   如果它把项目识别成了别的类型，就在服务设置里把构建方式手动改成 **Dockerfile**
5. 部署完成后，服务详情页会给出一个域名，形如
   **`https://douban-mini.zeabur.app`**（也可在服务 → Domain 里改成别的）
6. 把这个地址发出来（或自己填进 `web/index.html` 的 `DEFAULT_CLOUD_API`），推送后 Pages 自动更新；
   也可以直接在页面的「设置后端地址」框里填一次（会记住，但只对这台浏览器生效）

> Zeabur Free Plan **不需要信用卡**，注册即可部署；免费版空闲会休眠，
> 冷启动只有几秒（比 Render 的 30~60 秒好很多）。

#### 方式二：Render（老牌，但可能要预授权）

1. 打开 <https://render.com> → **Sign in with GitHub**
2. 右上角 `New +` → **Blueprint** → 选 `gooliu/douban-mini` → `Apply`
3. 构建 3~8 分钟后拿到 `https://douban-mini-xxxx.onrender.com`

> ⚠️ **实测（2026-10）**：Render 免费 Web 服务对部分新账号会在建服务时弹
> **「Add card」**，要求绑卡做 **1 美元预授权（验证用，不实际扣费）**。
> 不想绑卡就改用上面的 Zeabur。
> Render 免费层的两个已知坑：15 分钟无访问休眠（唤醒 30~60 秒，页面会提示
> 「⏳ 后端正在启动」）；没有持久磁盘，重新部署后补充记录会丢。

#### 方式三：其它平台

`Dockerfile` 是通用的，Koyeb / Northflank / Railway / Fly.io / 自建服务器都能直接用：

```bash
docker build -t douban-mini .
docker run -d -p 8000:8000 -e DOUBAN_CLOUD=1 \
  -v douban-data:/data douban-mini      # 挂卷才能保住补充记录
```

免费额度参考（2026-10）：**Northflank** 免费层常驻不休眠、不用信用卡；
**Koyeb** 多数账号免卡，但区域只有法兰克福 / 华盛顿；**Railway** 已无永久免费档；
**Fly.io** 新用户必须绑卡。

> ⚠️ 豆瓣对境外 IP 风控更严。如果部署后经常出现「机器人程序」提示，
> 建议改用自己的云服务器（国内 IP）。
>
> ℹ️ 登录方式（登录弹窗里四选一）：
> ① **豆瓣账号密码** —— 后端调 frodo `auth2/token` 换 Bearer token，密码不落盘；
> ② **网页 Cookie** —— 走移动网页通道 m.douban.com/rexxar（豆瓣 H5 靠 Cookie 认
>    登录态；frodo App 接口只认 Bearer，**所以 Cookie 必须走 rexxar 通道**）。
>    - **电脑上（推荐）**：登录弹窗 →「网页 Cookie」→「从本机浏览器读取登录态」，
>      后端直接读本机 Chrome / Edge / Firefox 的 Cookie 库并解密，**一键完成**；
>    - 手机上：可以用「一键带回」书签，但见下方 ⚠️；
> ③ 粘贴抓包拿到的 Bearer token；④ 电脑已登录时给手机发 6 位接力码（token / Cookie 都能接力）。
>
> ⚠️ **为什么手机书签经常报「没读到 dbcl2」**：豆瓣把登录标识 `dbcl2` 设成了
> **HttpOnly** —— 浏览器里的 JavaScript（包括 `javascript:` 小书签、`document.cookie`）
> 根本读不到它，这是浏览器的安全机制，不是操作问题。书签脚本现在会区分三种情况并给出
> 对应指引（一个 Cookie 都没有 / 有 bid、ck 但没有 dbcl2 / 正常）。手机上真正可行的
> 是 **账号密码登录** 或 **接力码**；想在手机上用 Cookie，就在电脑读取后生成接力码。
>
> 🔧 账号密码登录必须带设备标识，否则密码校验通过后豆瓣会返回 `invalid_device_id`。
> 设备号规格：**frodo 客户端是 20 位十六进制**（早年豆瓣 FM 是 40 位），首次自动生成并存在
> `backend/data/device.json`（可用环境变量 `DOUBAN_DEVICE_ID` 指定），每次登录保持一致；
> 被拒时会自动换端点 + 换 20/40 位设备号各重试一次（密码错则不重试）。
> 仍失败通常是账号开了登录保护：先在手机豆瓣 App 里登录一次再回来重试，
> 或改用本机浏览器读取 / 接力方式。
>
> 🔒 「从本机浏览器读取」只在**自用模式**（不设 `DOUBAN_PUBLIC` / `DOUBAN_CLOUD`）且
> 请求来自本机或局域网时可用 —— 公网共享模式下绝不把这台电脑的登录态给访客。
>
> ⚠️ 实测记录（2026-10-21）：`accounts.douban.com/j/mobile/login/basic`
> 服务器直连一律返回 `captcha_required`（腾讯验证码），所以「账号密码换网页
> Cookie」这条路也堵死了；frodo apikey 走 OAuth 授权码也不行（任何 redirect_uri
> 都报 `redirect_uri_mismatch`）。能用的只有上面四条。

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

网页由 `.github/workflows/pages.yml` 在每次推送 `main` 后自动部署，
**网页本身永远在线**（存在 GitHub 服务器上，与本机开关机无关）。

但后端地址目前**留空** —— 页面打开会提示「后端还没部署到云端」，
需要在上面的「云端部署」里拿一个地址，填进 `DEFAULT_CLOUD_API` 或页面的地址框。

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
