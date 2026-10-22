# -*- coding: utf-8 -*-
"""豆瓣 frodo 内部 API 客户端

签名算法（社区逆向公开，安卓客户端 v7.x）:
    _sig = base64( hmac_sha1( secret, "GET&{url_encoded_path}&{ts}" ) )
    带登录态时为 4 段式: "GET&{path}&{bearer_token}&{ts}"
    secret = bf7dddc7c9cfe6f7

公开可用接口（无需登录，实测 2026-10）:
    GET /api/v2/user/:uid                     用户信息
    GET /api/v2/user/:uid/interests           用户看过的影视/书/音乐（含评分、短评）← 他人可查
    GET /api/v2/group/:gid                    小组详情
    GET /api/v2/group/:gid/topics             小组话题列表（含作者）← 反查他人发言的基础
    GET /api/v2/group/:gid/members            小组成员列表（不支持按 uid 过滤，大组百万级）
    GET /api/v2/search/group?q=               按关键词搜索小组 ← 用于找小组 id

    GET /api/v2/search/group?q=               按关键词搜索小组 ← 用于找小组 id

网页版公开页（***重要***，2026-10-22 实测新发现）:
    GET https://www.douban.com/group/people/:uid/joins   **他人加入的小组列表**
        —— 匿名可读、不用签名、不用登录，一次性返回全部小组（实测 163 组一次到位）。
        该用户把小组设为隐私时会返回 404「页面不存在」（此时 group/people/:uid/ 仍是 200，
        用这个差异来区分「用户不存在」和「隐藏了小组」）。
        前提：部署机器能直连 www.douban.com（海外节点需确认未被拦）。

需登录接口（Bearer token，实测 2026-10）:
    GET /api/v2/group/user/:uid/joined_groups 用户加入的小组  ← 仅允许 :uid == token 本人
    GET /api/v2/group/user/posted_topics      本人发表的话题（支持 start/count 翻页）

实测结论（重要）:
    * App 侧 joined_groups 对非本人 uid 一律返回 403 need_permission，
      但**桌面版公开页 joins 能拿到全量列表** —— 他人「加入的小组」不再是死路。
      未见此前的结论为何相反：当时只测了 frodo / rexxar / m 站，漏了 www 桌面版。
    * 该公开页也会被隐私设置挡住：用户设为隐私 → joins 返回 404。
      这种情况只能退回到「逐个核对」：给定候选小组，翻 members 名单比对 uid。
    * 成员名单每页最多 100 条、可深翻到 start=300000，因此几千人的小群能 100% 全量比对，
      给出「确定不在组内」的硬结论；大组只能给覆盖率，没找到 ≠ 不在。
    * 他人「发言记录」可反查：给定候选小组 id，遍历 group/:gid/topics 按 author.id 过滤
    * /api/v2/user/me 返回的是豆瓣占位账号（id=1048835, uid="me"），**不可用于识别本人**
    * 真实本人 uid 通过 posted_topics 首条话题的作者 id 推断
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import os
import time
import urllib.parse
from dataclasses import dataclass, field
from typing import Any

import re

import httpx

FRODO_HOST = "https://frodo.douban.com"
# 备用通道：豆瓣移动网页版 API（m.douban.com/rexxar）。匿名可用、不需要签名，
# frodo 被风控（1000）时自动切换过去，公开数据仍可查。
REXXAR_HOST = "https://m.douban.com"
BROWSER_UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
              "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 "
              "Mobile/15E148 Safari/604.1")
APIKEY = "0dad551ec0f84ed02907ff5c42e8ec70"     # 豆瓣安卓客户端公开 apikey
SECRET = "bf7dddc7c9cfe6f7"                     # HMAC-SHA1 签名密钥
UA = ("api-client/1 com.douban.frodo/7.22.0 (233) Android/22 product/shamu "
      "vendor/OPPO model/OPPOR11Plus rom/android network/wifi platform/mobile nd/1")

# 风控冷却：豆瓣返回 1000「机器人程序」后进入冷却，避免越点越糟
# 连续命中时长按 60s → 120s → 240s → 480s → 900s（封顶）
COOLDOWN_STEPS = [60, 120, 240, 480, 900]

def _www_host() -> str:
    """桌面版站点根的取值顺序：
    1. 环境变量 DOUBAN_WWW_HOST（docker run -e 注入）
    2. 持久卷文件 /data/www_host.txt（服务器 IP 被豆瓣限流时，
       写一个中转地址进去 + docker restart 即可切换，不用重建容器）
    3. 默认豆瓣官网
    """
    env = os.getenv("DOUBAN_WWW_HOST", "").strip()
    if env:
        return env.rstrip("/")
    try:
        v = open("/data/www_host.txt", encoding="utf-8").read().strip()
        if v:
            return v.rstrip("/")
    except OSError:
        pass
    return "https://www.douban.com"


# 桌面版 www 页面（比 App 接口严格得多）：403/429 后先静默一段时间，
# 期间不再发请求。实测阿里云机房出口被限流后，越重试恢复得越慢。
WWW_COOLDOWN_SECS = int(os.getenv("DOUBAN_WWW_COOLDOWN", "600") or 600)
# 「他人加入的小组」结果缓存：同一 uid 在该 TTL 内重复查询直接返回上次结果，
# 既省请求也避免用户连点把 IP 点进黑名单（列表变化本来就慢，15 分钟足够）
WWW_CACHE_TTL = int(os.getenv("DOUBAN_WWW_CACHE_TTL", "900"))
_WWW_RESULT_CACHE: dict[str, tuple[float, dict]] = {}
_WWW_BLOCK_UNTIL: float = 0.0
_WWW_BLOCK_HITS: int = 0      # 连续被 403 的次数：冷却按 600s→1200s→…→3600s 递增

# 可通过环境变量覆盖
DOUBAN_TOKEN = os.getenv("DOUBAN_TOKEN", "").strip()  # 登录态 Bearer token（抓包获取）
# 豆瓣网页版 Cookie，形如 dbcl2="281065007:xxxxxxxx"
# ⚠️ 实测结论（2026-10）：frodo 接口**不认 Cookie**。带不带 dbcl2 返回完全一样
#    （user/me 都回落到同一个匿名账号，他人接口一律 need_permission），
#    所以 Cookie 登录必然失败，不要再把它当登录方式。真正能用的是 Bearer token。
DOUBAN_COOKIE = os.getenv("DOUBAN_COOKIE", "").strip()

# 豆瓣账号密码换 token（App 登录接口，手机端最省事：不用抓包、不用开发者工具）
AUTH_TOKEN_URL = "https://frodo.douban.com/service/auth2/token"
# 备用端点：网页版 OAuth（同一个 client_id/secret）。frodo 端点报 invalid_device_id 时换它再试
AUTH_TOKEN_URL_WWW = "https://www.douban.com/service/auth2/token"

# ---- 设备标识（登录必需，实测结论 2026-10）----
# 豆瓣的 password grant **必须带设备参数**，否则账号密码校验通过后就会报
#   {"msg": "invalid_device_id"} —— 注意顺序：密码错时一律先返回 120
#   username_password_mismatch，所以「密码错」看不出缺设备，只有密码填对了才暴露。
# 设备参数格式参考豆瓣 frodo 客户端抓包：udid / device_id 相同，是 **20 位十六进制**
#   （公开抓包示例：frodo.douban.com/api/v2/group/xxx/topics?...&udid=5e4159565b89f86cccda）
#   douban_udid 是 40 位十六进制。udid 需**稳定不变**（每次都换会被当成新设备）。
#   注：早年豆瓣 FM 用的是 40 位，frodo 是 20 位；长度不合规会被判 invalid_device_id。
_DATA_DIR = os.getenv("DOUBAN_DATA_DIR", "").strip() or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "data")
DEVICE_FILE = os.path.join(_DATA_DIR, "device.json")
DEVICE_UDID = os.getenv("DOUBAN_DEVICE_ID", "").strip()   # 可用环境变量固定设备号


def _new_udid(n: int = 20) -> str:
    """生成十六进制设备号（frodo 客户端规格：20 位）"""
    import secrets
    return secrets.token_hex(n // 2)


_UDID_CACHE = ""


def _load_udid() -> str:
    """读取（必要时生成并持久化）本机设备号，失败则退回内存值"""
    global _UDID_CACHE
    if DEVICE_UDID:
        return DEVICE_UDID
    if _UDID_CACHE:
        return _UDID_CACHE
    try:
        with open(DEVICE_FILE, "r", encoding="utf-8") as f:
            v = str((json.load(f) or {}).get("udid") or "").strip()
        # 旧版本存过 40 位的设备号（豆瓣 FM 规格），frodo 实际用 20 位，这里顺手迁移
        if len(v) == 20:
            _UDID_CACHE = v
            return v
    except Exception:
        pass
    v = _new_udid()
    try:
        os.makedirs(os.path.dirname(DEVICE_FILE), exist_ok=True)
        with open(DEVICE_FILE, "w", encoding="utf-8") as f:
            json.dump({"udid": v}, f)
    except Exception:
        pass          # 只读环境（云端容器）：用内存里的值，进程内保持一致
    _UDID_CACHE = v
    return v


def _device_params(udid: str | None = None) -> dict:
    """登录请求要带的设备三件套"""
    u = udid or _load_udid()
    return {
        "udid": u,
        "device_id": u,
        "douban_udid": hashlib.sha1(u.encode()).hexdigest(),
    }
CACHE_TTL = int(os.getenv("DOUBAN_CACHE_TTL", "600"))  # 缓存秒数，默认 10 分钟
SCAN_TOPICS_LIMIT = int(os.getenv("DOUBAN_SCAN_GROUPS", "8"))  # 扫描发言记录时最多遍历的小组数
MAX_SCAN_GROUPS = int(os.getenv("DOUBAN_SCAN_GROUPS", "8"))     # 反查发言：单次最多扫描的小组数
# 反查发言：每个小组最多扫描页数（每页50条）。
# 活跃小组几分钟就能刷出几十条新帖，窗口太窄会导致"明明发过却查不到"，
# 实测每页约 2 秒，20 页（1000 条）也就 40 秒左右，可以放开。
MAX_SCAN_PAGES = int(os.getenv("DOUBAN_SCAN_PAGES", "20"))


class DoubanError(Exception):
    """豆瓣接口错误

    属性 need_login 很关键：豆瓣很多接口（评论/回帖、话题详情等）现在匿名一律返回
    HTTP 403 + code=1000 + msg="need_permission"（localized_message 是那句
    「你访问豆瓣的方式有点像机器人程序，请登录」）。
    如果只看 code=1000 就报「机器人反爬」并进入冷却，既误导又会越点越糟。
    因此这里单独打标，上层据此提示「请先登录」而不是「疑似机器人」。
    """

    def __init__(self, code: int, msg: str, need_login: bool = False):
        self.code = code
        self.msg = msg
        self.need_login = need_login
        super().__init__(f"[douban {code}] {msg}")


def _make_sig(full_path: str, ts: str, bearer: str = "") -> str:
    """签名：匿名请求为 3 段式 'GET&{path}&{ts}'；带登录态为 4 段式 'GET&{path}&{token}&{ts}'"""
    quoted = urllib.parse.quote(full_path, safe="")
    raw = f"GET&{quoted}&{bearer}&{ts}" if bearer else f"GET&{quoted}&{ts}"
    digest = hmac.new(SECRET.encode(), raw.encode(), hashlib.sha1).digest()
    return base64.b64encode(digest).decode()


@dataclass
class _CacheItem:
    data: Any
    expire_at: float


@dataclass
class DoubanClient:
    """带 TTL 内存缓存的 frodo API 异步客户端"""

    token: str = field(default_factory=lambda: DOUBAN_TOKEN)
    cookie: str = field(default_factory=lambda: DOUBAN_COOKIE)
    cache_ttl: int = CACHE_TTL
    _cache: dict = field(default_factory=dict)
    _http: httpx.AsyncClient | None = None
    _self_uid: str | None = None
    _self_uid_ts: float = 0.0
    _block_until: float = 0.0        # 风控冷却截止时间戳
    _block_hits: int = 0             # 连续命中风控次数
    _token_dead_until: float = 0.0   # token 已判定失效的缓存截止时间（避免反复带失效 token 触发风控）
    last_via: str = "frodo"          # 最近一次请求实际走的通道：frodo / rexxar

    async def _get_http(self) -> httpx.AsyncClient:
        if self._http is None or self._http.is_closed:
            self._http = httpx.AsyncClient(timeout=15, headers={"User-Agent": UA})
        return self._http

    async def close(self) -> None:
        if self._http and not self._http.is_closed:
            await self._http.aclose()

    def scoped(self, token: str = "", cookie: str = ""):
        """返回一个「使用指定凭证」的视图对象（云端多用户部署用）

        共享 HTTP 连接池与缓存，但缓存键按凭证区分，互不串号。
        用于：访问者把凭证存在自己浏览器里、每次请求通过
        X-Douban-Token / X-Douban-Cookie 头带过来，服务端不落盘。
        """
        import copy
        view = copy.copy(self)
        view.token = (token or "").strip()
        view.cookie = self._normalize_cookie(cookie) if cookie else ""
        # 标记为「访问者自己的凭证」：不得回落到服务端保存的账号（环境变量 / 配置文件）
        view._visitor = True
        view._self_uid = None          # 别继承服务端账号的 uid 缓存
        view._self_uid_ts = 0.0
        return view

    def set_token(self, token: str) -> None:
        """热更新 token：清空所有带登录态的缓存与本人 uid 缓存"""
        self.token = (token or "").strip()
        self._token_dead_until = 0.0    # 换了新 token，失效判定作废
        self._clear_auth_cache()

    def set_cookie(self, cookie: str) -> None:
        """热更新 Cookie（dbcl2 等）：清空带登录态的缓存"""
        self.cookie = self._normalize_cookie(cookie)
        self._clear_auth_cache()

    @staticmethod
    def _normalize_cookie(raw: str) -> str:
        """容错处理粘贴内容：只保留 dbcl2 / ck / bid 等关键项"""
        c = (raw or "").strip()
        if not c:
            return ""
        # 用户可能粘贴整个 Cookie 串或 Request Header 片段，提取需要的键
        wanted = {}
        for part in c.replace("\n", ";").split(";"):
            part = part.strip()
            if not part or "=" not in part:
                continue
            k, v = part.split("=", 1)
            k = k.strip()
            if k.lower() in ("dbcl2", "ck", "bid", "push_doumail_num", "ll"):
                wanted[k] = v.strip().strip('"')
        if not wanted:
            return c
        # dbcl2 原始值本身带引号才合法（dbcl2="uid:hash"）
        if "dbcl2" in wanted and not wanted["dbcl2"].startswith('"'):
            wanted["dbcl2"] = f'"{wanted["dbcl2"]}"'
        return "; ".join(f"{k}={v}" for k, v in wanted.items())

    def _clear_auth_cache(self) -> None:
        self._cache = {k: v for k, v in self._cache.items() if not k[2]}  # k[2] = need_auth
        self._self_uid = None
        self._self_uid_ts = 0.0

    async def login_with_password(self, username: str, password: str) -> dict:
        """用豆瓣账号（手机号 / 邮箱）+ 密码 换取 frodo access_token

        这是手机上最省事的登录方式：不用抓包 App、不用开发者工具、不用复制 Cookie。
        密码只用于这一次向豆瓣换取令牌，**不保存、不写日志、不回显**。

        返回 {token, refresh_token, expires_in, uid, name}
        失败抛 DoubanError（msg 已是能直接展示给用户的中文）。
        """
        username = (username or "").strip()
        password = password or ""
        if not username or not password:
            raise DoubanError(400, "请填写豆瓣账号（手机号 / 邮箱）和密码")
        if len(password) < 4:
            raise DoubanError(400, "密码看起来不完整，请重新输入")

        base = {
            "client_id": APIKEY,
            "client_secret": SECRET,
            "apikey": APIKEY,
            "grant_type": "password",
            "username": username,
            "password": password,
            "redirect_uri": "https://www.douban.com",
        }
        headers = {"User-Agent": UA, "Content-Type": "application/x-www-form-urlencoded"}

        # 依次尝试（密码错时不重试，见下面 code==120 直接 break）：
        #   frodo + 本机固定 20 位设备 → 网页端点同设备 → frodo 换 20 位新设备
        #   → frodo 换 40 位新设备（早年 FM 规格，个别账号认这个长度）
        udid = _load_udid()
        attempts = [
            (AUTH_TOKEN_URL, _device_params(udid)),
            (AUTH_TOKEN_URL_WWW, _device_params(udid)),
            (AUTH_TOKEN_URL, _device_params(_new_udid(20))),
            (AUTH_TOKEN_URL, _device_params(_new_udid(40))),
        ]

        last_code, last_raw, last_status = None, "", 0
        for url, dev in attempts:
            data = dict(base)
            data.update(dev)
            try:
                async with httpx.AsyncClient(timeout=20, headers=headers) as hc:
                    r = await hc.post(url, data=data)
            except Exception as e:
                raise DoubanError(-1, f"连不上豆瓣登录服务器（{type(e).__name__}）。"
                                      f"请检查网络，或稍后再试")
            try:
                j = r.json()
            except Exception:
                raise DoubanError(r.status_code, f"豆瓣返回了异常内容（HTTP {r.status_code}），"
                                                 f"请稍后再试")

            token = (j.get("access_token") or "").strip()
            if token:
                # 换设备号成功的：把新设备号固定下来，后续请求保持一致
                new_u = (dev.get("udid") or "").strip()
                if new_u and new_u != udid:
                    try:
                        os.makedirs(os.path.dirname(DEVICE_FILE), exist_ok=True)
                        with open(DEVICE_FILE, "w", encoding="utf-8") as f:
                            json.dump({"udid": new_u}, f)
                    except Exception:
                        pass
                return {
                    "token": token,
                    "refresh_token": (j.get("refresh_token") or "").strip(),
                    "expires_in": int(j.get("expires_in") or 0),
                    "uid": str(j.get("douban_user_id") or j.get("uid") or "").strip(),
                    "name": (j.get("douban_user_name") or j.get("name") or "").strip(),
                }

            code = j.get("code")
            raw = str(j.get("msg") or "").strip()
            last_code, last_raw, last_status = code, raw, r.status_code
            if code == 120:      # 账号或密码不对，换端点重发没有意义
                break
            await asyncio.sleep(0.8)

        code, raw = last_code, last_raw
        friendly = {
            120: "账号或密码不对，请检查后重试（注意不是昵称，是手机号 / 邮箱）",
            113: "豆瓣登录参数异常，请稍后再试",
            121: "账号不存在或已被停用",
            130: "登录过于频繁，请过几分钟再试",
        }.get(code)
        if not friendly:
            low = raw.lower()
            if "invalid_device_id" in low or "device" in low:
                friendly = ("豆瓣不认可这次登录的设备标识（invalid_device_id）。\n"
                            "已自动换设备号重试仍未通过，通常是账号开了登录保护 / "
                            "换设备登录需要验证。\n"
                            "① 先在手机的豆瓣 App 里正常登录一次，再回来重试；\n"
                            "② 或改用「手机接力」「粘贴 token」登录")
            elif any(k in low for k in ("captcha", "verify", "sms", "security")):
                friendly = ("豆瓣要求安全验证（换设备 / 异地登录常见）。"
                            "请先在手机上打开豆瓣 App 正常使用一次，"
                            "或改用「手机接力」/「粘贴 token」登录")
            else:
                friendly = raw or f"登录失败（HTTP {last_status}）"
        raise DoubanError(code if isinstance(code, int) else -1, friendly)

    async def check_cookie(self) -> dict | None:
        """验证网页 Cookie 是否真的登录着豆瓣（走移动网页通道）

        判据：rexxar `/rexxar/api/v2/user/me` 未登录时返回**占位账号**
        （id=1048835 / uid="me" / name="xingdiannao"），登录后才是真实 uid。

        返回 {uid, name} 表示 Cookie 有效；None 表示没登录 / 通道不通。
        """
        if not self.cookie:
            return None
        if "dbcl2" not in self.cookie.lower():
            # 没有登录标识，必然是登录态不完整
            return None
        try:
            j = await self._request_rexxar("/api/v2/user/me", None)
        except DoubanError:
            return None
        uid = str(j.get("id") or "").strip()
        uname = str(j.get("uid") or "").strip()
        if (not uid or uid == "1048835" or uname == "me"
                or str(j.get("name") or "") == "xingdiannao"):
            return None
        return {"uid": uid, "name": str(j.get("name") or "").strip()}

    async def check_token(self) -> dict:
        """检测当前登录凭证是否有效，返回 {valid, code, msg, uid, name, via}

        优先用 Bearer token（posted_topics 判定）；
        token 缺失或失效时，若配置了 Cookie，则退回到 joined_groups 判定。
        """
        # token 刚被判定失效（10 分钟内）：不再带失效 token 重复请求，避免加重风控
        if self.token and not self.cookie and time.time() < self._token_dead_until:
            return {"valid": False, "code": 103, "msg": "need_login（缓存判定）",
                    "hint": "登录信息失效：token 已过期，请重新抓取豆瓣 App 的 Bearer token"
                            "（或改用网页 Cookie 登录，见弹窗下方）"}
        if self.token:
            try:
                r = await self.get_posted_topics(0, 1)
            except DoubanError as e:
                if not self.cookie:
                    hint = {
                        103: "登录信息失效：token 已过期，请重新抓取豆瓣 App 的 Bearer token"
                             "（或改用网页 Cookie 登录，见弹窗下方）",
                        1000: (f"豆瓣要求登录后才能查看：{e.msg}"
                               if e.need_login else
                               f"豆瓣判定为机器人程序（反爬），已自动冷却 "
                               f"{self.cooldown_remaining()} 秒。请勿连续点击，"
                               f"公开数据会自动改用备用通道"),
                        997: "签名错误",
                    }.get(e.code, e.msg)
                    return {"valid": False, "code": e.code, "msg": e.msg,
                            "need_login": e.need_login, "hint": hint}
            else:
                uid = None
                for t in (r.get("topics") or []):
                    uid = (t.get("author") or {}).get("id")
                    if uid:
                        break
                out = {"valid": True, "code": 0, "msg": "token 有效", "via": "token",
                       "uid": str(uid) if uid else None,
                       "topics_total": r.get("total", 0)}
                if uid:
                    self._self_uid = str(uid)
                    self._self_uid_ts = time.time()
                    try:
                        u = await self.get_user(str(uid))
                        out["name"] = u.get("name")
                    except DoubanError:
                        pass
                return out

        # ---- Cookie（走移动网页通道 rexxar，这是唯一认 Cookie 的入口）----
        if self.cookie:
            ck = await self.check_cookie()
            if not ck:
                return {"valid": False, "code": 103, "msg": "cookie_invalid",
                        "need_login": True,
                        "hint": "豆瓣没认这个 Cookie：① 可能复制时没登录 m.douban.com；"
                                "② 只复制了一部分（必须含 dbcl2）；"
                                "③ 无痕窗口 / 另一个浏览器的 Cookie 不算。"
                                "请重新用手机浏览器打开 m.douban.com 登录后，再用书签一键带回"}
            self._self_uid = ck["uid"]
            self._self_uid_ts = time.time()
            return {"valid": True, "code": 0, "msg": "Cookie 登录有效", "via": "cookie",
                    "uid": ck["uid"], "name": ck["name"]}

        return {"valid": False, "code": 0, "msg": "未配置任何登录凭证"}

    # ------------------------------------------------------------------
    # 风控冷却
    # ------------------------------------------------------------------
    def cooldown_remaining(self) -> int:
        """距离风控冷却结束还剩多少秒（0 = 未冷却）"""
        return max(0, int(self._block_until - time.time()))

    def mark_blocked(self) -> int:
        """记录一次风控命中，按指数延长冷却，返回本次冷却秒数"""
        self._block_hits += 1
        idx = min(self._block_hits - 1, len(COOLDOWN_STEPS) - 1)
        secs = COOLDOWN_STEPS[idx]
        self._block_until = time.time() + secs
        return secs

    def reset_block(self) -> None:
        """请求成功后清除风控计数"""
        if self._block_hits:
            self._block_hits = 0
            self._block_until = 0.0

    def cooldown_info(self) -> dict:
        return {"cooling": self.cooldown_remaining() > 0,
                "remaining_seconds": self.cooldown_remaining(),
                "hits": self._block_hits,
                "via": self.last_via}

    # ------------------------------------------------------------------
    async def _request(self, full_path: str, params: dict | None = None,
                      need_auth: bool = False, allow_rexxar: bool = True) -> Any:
        """发起带签名的 GET 请求，自动带缓存

        frodo 主通道；命中风控（1000）/ 需登录（103）时，公开接口自动降级到
        rexxar（m.douban.com 网页版，匿名可用、不需签名）。
        """
        # 缓存键包含凭证摘要：云端多用户时各自的登录态不会串
        cache_key = (full_path, tuple(sorted((params or {}).items())),
                     need_auth, self.token[-8:], self.cookie[-24:])
        hit = self._cache.get(cache_key)
        if hit and hit.expire_at > time.time():
            return hit.data

        # 只有 Cookie、没有 Bearer token → 直接走移动网页通道
        # frodo 不认 Cookie（实测），硬走只会拿到占位账号 / need_permission
        if self.cookie and not self.token and allow_rexxar:
            try:
                data = await self._request_rexxar(full_path, params, need_auth)
                self.last_via = "rexxar"
                self._cache[cache_key] = _CacheItem(data, time.time() + self.cache_ttl)
                return data
            except DoubanError as e:
                raise DoubanError(e.code, e.msg, e.need_login)

        # 冷却中：公开接口直接走备用通道，别再撞 frodo
        if not need_auth and self.cooldown_remaining() > 0 and allow_rexxar:
            try:
                data = await self._request_rexxar(full_path, params)
                self.last_via = "rexxar"
                self._cache[cache_key] = _CacheItem(data, time.time() + self.cache_ttl)
                return data
            except DoubanError as e:
                raise DoubanError(e.code, f"豆瓣风控冷却中（还剩 {self.cooldown_remaining()}s），"
                                          f"备用通道也失败：{e.msg}")

        try:
            data = await self._request_frodo(full_path, params, need_auth)
        except DoubanError as e:
            # frodo 被风控 / 要求登录 → 公开接口降级到 rexxar
            if allow_rexxar and not need_auth and e.code in (1000, 103, 1062, 998):
                try:
                    data = await self._request_rexxar(full_path, params)
                    self.last_via = "rexxar"
                    self._cache[cache_key] = _CacheItem(data, time.time() + self.cache_ttl)
                    return data
                except DoubanError:
                    raise e          # 备用通道也不行，返回 frodo 的原始错误
            raise

        self.reset_block()
        self.last_via = "frodo"
        self._cache[cache_key] = _CacheItem(data, time.time() + self.cache_ttl)
        return data

    async def _request_frodo(self, full_path: str, params: dict | None,
                             need_auth: bool) -> Any:
        """frodo 主通道（带签名）"""
        ts = str(int(time.time()))
        bearer = self.token if need_auth else ""
        query = {
            "apikey": APIKEY,
            "_ts": ts,
            "_sig": _make_sig(full_path, ts, bearer),
            "os_rom": "android",
            "udid": _load_udid(),      # 真实客户端每次请求都带，与登录设备号保持一致
        }
        if params:
            query.update(params)
        headers = {}
        if self.cookie:
            headers["Cookie"] = self.cookie
        if need_auth:
            if not self.token and not self.cookie:
                raise DoubanError(1000, "该接口需要登录态，请在网页右上角「登录」里配置 token 或 Cookie",
                                  need_login=True)
            if self.token:
                headers["Authorization"] = f"Bearer {self.token}"

        http = await self._get_http()
        resp = await http.get(FRODO_HOST + full_path, params=query, headers=headers)

        if resp.status_code == 200:
            return resp.json()
        # 统一错误
        try:
            err = resp.json()
            code = err.get("code", resp.status_code)
            msg = err.get("localized_message") or err.get("msg", resp.text[:100])
        except ValueError:
            code, msg = resp.status_code, f"HTTP {resp.status_code}: {resp.text[:100]}"
        # 关键：豆瓣把「需要登录」也塞在 code=1000 里，靠 msg 区分。
        #   msg="need_permission"                    → 需要登录（带凭证重试即可，不该冷却）
        #   msg="invalid_signature" / 其他            → 真·风控反爬（才需要冷却）
        raw_msg = str(err.get("msg") or "").strip().lower()
        if code == 1000 and raw_msg in ("need_permission", "need_login"):
            raise DoubanError(
                1000,
                "豆瓣要求登录后才能查看：该接口（回帖/评论、话题详情）已不再对匿名开放。"
                "请点右上角「登录」用豆瓣账号密码登录（或粘贴 Bearer token）后重试。",
                need_login=True)
        if code == 103:
            # 登录信息失效 —— 同样属于「需要重新登录」，不是反爬。
            # 缓存判定结果，别再带着失效 token 反复请求（会招来风控）
            if need_auth and self.token:
                self._token_dead_until = time.time() + 600
            raise DoubanError(103, "登录信息失效（豆瓣 code=103）："
                                   "token 已过期或 Cookie 不被接受，请重新登录后重试。",
                              need_login=True)
        if code == 1000:
            secs = self.mark_blocked()
            msg = (f"豆瓣判定为机器人程序（反爬），已进入冷却 {secs}s。"
                   f"请勿连续点击；公开数据会自动改用备用通道")
        raise DoubanError(code, msg)

    async def _request_rexxar(self, full_path: str, params: dict | None,
                              need_auth: bool = False) -> Any:
        """移动网页通道：m.douban.com/rexxar（匿名可用，不需签名）

        full_path 形如 /api/v2/group/{gid}/topics → /rexxar/api/v2/group/{gid}/topics

        ⚠️ 这是**唯一认 Cookie 的通道**：豆瓣 H5 页面靠 Cookie 维持登录态，
        而 frodo（App 接口）只认 Bearer token，带 Cookie 一律回落占位账号。
        所以「手机复制 Cookie 登录」必须走这里。
        """
        http = await self._get_http()
        headers = {"User-Agent": BROWSER_UA, "Referer": "https://m.douban.com/",
                   "Accept": "application/json, text/plain, */*"}
        if self.cookie:
            headers["Cookie"] = self.cookie
        if need_auth and self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        resp = await http.get(
            REXXAR_HOST + "/rexxar" + full_path, params=params or {}, headers=headers)
        if resp.status_code == 200:
            try:
                return resp.json()
            except ValueError:
                raise DoubanError(-1, "rexxar 返回非 JSON")
        try:
            err = resp.json()
            raise DoubanError(err.get("code", resp.status_code),
                              err.get("localized_message") or err.get("msg", resp.text[:80]))
        except ValueError:
            raise DoubanError(resp.status_code, f"HTTP {resp.status_code}: {resp.text[:80]}")

    # ---------------- 公开接口 ----------------
    async def get_user(self, user_id: str) -> dict:
        """用户信息（公开）"""
        return await self._request(f"/api/v2/user/{user_id}")

    async def get_group(self, group_id: str) -> dict:
        """小组详情（公开）"""
        return await self._request(f"/api/v2/group/{group_id}")

    async def get_group_topics(self, group_id: str, start: int = 0, count: int = 20) -> dict:
        """小组话题列表（公开，含每条话题的作者）"""
        return await self._request(f"/api/v2/group/{group_id}/topics",
                                   {"start": start, "count": count})

    # ---------------- 需登录接口 ----------------
    async def get_joined_groups(self, user_id: str, start: int = 0, count: int = 50) -> dict:
        """用户加入的小组（需登录态）"""
        return await self._request(f"/api/v2/group/user/{user_id}/joined_groups",
                                   {"start": start, "count": count}, need_auth=True)

    async def get_posted_topics(self, start: int = 0, count: int = 50) -> dict:
        """当前登录用户发表的话题（需登录态，仅本人）"""
        return await self._request("/api/v2/group/user/posted_topics",
                                   {"start": start, "count": count}, need_auth=True)

    async def get_self_uid(self) -> str | None:
        """解析 token 对应账号的真实 uid（缓存 1 小时）

        策略：
          1. 环境变量 DOUBAN_SELF_UID 指定（优先）
          2. 调 posted_topics 取首条话题的作者 id 推断
        注意：不能用 /api/v2/user/me —— 它返回的是占位账号（uid="me"）。
        访问者凭证（scoped）不会回落到服务端保存的 uid，避免串号。
        """
        env = os.getenv("DOUBAN_SELF_UID", "").strip()
        if env and not getattr(self, "_visitor", False):
            return env
        if not self.token and not self.cookie:
            return None
        if self._self_uid and time.time() - self._self_uid_ts < 3600:
            return self._self_uid
        if not self.token and self.cookie:      # 纯 Cookie 登录：走移动网页通道认人
            ck = await self.check_cookie()
            if ck:
                self._self_uid = ck["uid"]
                self._self_uid_ts = time.time()
                return self._self_uid
            return None
        try:
            posted = await self.get_posted_topics(0, 1)
        except DoubanError:
            return None
        for t in (posted.get("topics") or []):
            uid = (t.get("author") or {}).get("id")
            if uid:
                self._self_uid = str(uid)
                self._self_uid_ts = time.time()
                return self._self_uid
        return None

    # ---------------- 新增：他人公开能力（2026-10-15 实测） ----------------
    async def get_user_interests(self, user_id: str, start: int = 0, count: int = 20) -> dict:
        """用户标记的影视/书/音乐（他人也可查，公开接口）

        返回的是 TA 「看过/想看」的作品及评分、短评 —— 这是目前
        唯一能拿到的「他人内容足迹」。
        """
        return await self._request(f"/api/v2/user/{user_id}/interests",
                                   {"start": start, "count": count})

    async def get_group_members(self, group_id: str, start: int = 0, count: int = 20) -> dict:
        """小组成员列表（公开接口）

        注意：不支持按 uid/q 过滤，参数会被服务端忽略；
        但实测 **支持深翻页**（start 到 30 万仍返回数据）且每页最多 100 条，
        因此小群（成员数几千以内）可全量遍历，用于「判断某 uid 是否在组内」。
        """
        return await self._request(f"/api/v2/group/{group_id}/members",
                                   {"start": start, "count": count})

    # ---------------- 桌面网页通道（www.douban.com 抓公开页面） ----------------
    # frodo/rexxar 拿不到「他人加入的小组」，但桌面版 https://www.douban.com/group/people/:uid/joins
    # 一次给出全部小组（实测 163 组一次返回，无需翻页）。它有三种结局：
    #   1) 200 有列表      → 该用户的小组对所有人可见（匿名可读）
    #   2) 404 + 同级页 403「未登录无法查看更多内容」
    #                      → 该用户把可见范围设成了「仅登录用户」（**不是隐私**）
    #                        访客自己的 Cookie 有效时能读到，匿名叫不到
    #   3) 404 + 同级页也 404 → 仅自己可见 / 已关闭
    # 桌面版站点根。默认豆瓣官网；被机房 IP 限流时可指向自建中转，
    # 不用改代码：环境变量 DOUBAN_WWW_HOST，或往 /data/www_host.txt 写地址
    WWW_HOST = _www_host()
    WWW_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
    NEED_LOGIN_HINT = "未登录无法查看更多内容"

    async def _get_www_page(self, path: str, cookie=None,
                            referer: str = "") -> tuple[int, str]:
        """抓取桌面版页面（不走代理、不走 frodo 通道）

        cookie 不传则用当前访客的登录态（self.cookie）。有些用户的小组只对
        登录用户可见，带上有效 Cookie 才读得到。
        返回 (status_code, html)。403/429 说明被反爬，调用方需提示稍后重试。
        """
        ck = ((self.cookie if cookie is None else cookie) or "").strip()
        headers = {"User-Agent": self.WWW_UA,
                   "Accept-Language": "zh-CN,zh;q=0.9",
                   "Accept": "text/html,application/xhtml+xml",
                   "Referer": referer or (self.WWW_HOST + "/")}
        if ck:
            headers["Cookie"] = ck
        http = httpx.AsyncClient(timeout=15, trust_env=False,
                                 follow_redirects=True, headers=headers)
        try:
            resp = await http.get(self.WWW_HOST + path)
            return resp.status_code, resp.text
        finally:
            await http.aclose()

    @staticmethod
    def _looks_logged_in(html: str) -> bool:
        """桌面版页面里还能看到「登录/注册」= 这份 Cookie 在 www 站点没被认。"""
        return "登录/注册" not in (html or "")

    @staticmethod
    def _strip_tags(s: str) -> str:
        return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", s or "")).strip()

    def _parse_www_groups(self, html: str) -> dict:
        """解析「加入的小组 / 管理的小组」列表页

        joins 页（完整列表）与 people/:uid/groups 页（只列前 9 个）结构一致：
            <div class="mod"><h2>加入的小组 (21)</h2><ul><li>…小组卡片…</li></ul></div>
        """
        joined: list[dict] = []
        managed: list[dict] = []
        page_declared = None

        body_start = html.find('<div class="profile-nav">')
        body = html[body_start:] if body_start > 0 else html
        for mod in re.split(r'<div class="mod', body)[1:]:
            h2 = re.search(r'<h2>(.*?)</h2>', mod, re.S)
            section = self._strip_tags(h2.group(1)) if h2 else "加入的小组"
            if page_declared is None and "加入" in section:
                m = re.search(r"(\d[\d,]*)", section)
                if m:
                    page_declared = int(m.group(1).replace(",", ""))
            bucket = managed if "管理" in section else joined
            for li in re.findall(r"<li\b.*?</li>", mod, re.S):
                g = re.search(r'douban\.com/group/(\d+)/', li)
                if not g:
                    continue
                name = re.search(r'<a[^>]*title="([^"]*)"', li)
                if not name:
                    name = re.search(r'<a[^>]*>\s*([^<]+?)\s*</a>', li)
                num = re.search(r'class="num"[^>]*>\s*\(?([\d,]+)', li)
                bucket.append({
                    "id": g.group(1),
                    "name": self._strip_tags(name.group(1)) if name else "",
                    "member_count": (int(num.group(1).replace(",", ""))
                                     if num else None),
                    "section": section,
                })

        def _uniq(xs):
            seen, out = set(), []
            for x in xs:
                if x["id"] not in seen:
                    seen.add(x["id"])
                    out.append(x)
            return out

        return {"groups": _uniq(joined), "managed": _uniq(managed),
                "page_declared": page_declared}

    async def fetch_joined_groups_www(self, user_id: str) -> dict:
        """抓取「他人加入的小组」桌面版页面

        返回:
            {
              "status": "ok" | "login_required" | "hidden" | "missing" | "blocked",
              "groups": [{"id","name","member_count","section"}],
              "managed": [同上, 该用户创建/管理的小组],
              "page_declared": 页面上标注的总数（可能为 None）,
              "partial": 是否只拿到前 9 个（来自 people/:uid/groups 预览页）,
              "with_cookie": 本次请求是否带了访客登录态,
              "cookie_accepted": 带的 Cookie 在 www 站点是否被认（None=没带）
            }

        status 说明:
            ok             —— 拿到了列表
            login_required —— 豆瓣明确返回「未登录无法查看更多内容」：
                              TA 把小组可见范围设成了「仅登录用户」，
                              带访客自己的有效 Cookie 重试可能能读到（**不是隐私**）
            hidden         —— 连登录提示都没有，页面整个 404（仅自己可见/已关闭）
            missing        —— 用户不存在
            blocked        —— 被豆瓣反爬（403/429），需稍后重试
        """
        uid = str(user_id).strip()
        has_ck = bool((self.cookie or "").strip())
        base = {"groups": [], "managed": [], "page_declared": None,
                "with_cookie": has_ck, "cookie_accepted": None}

        # ① 限流静默期内直接返回，绝不再打豆瓣（重试只会延长封禁）
        global _WWW_BLOCK_UNTIL
        left = int(_WWW_BLOCK_UNTIL - time.time())
        if left > 0:
            return {**base, "status": "blocked", "retry_after": left,
                    "cookie_accepted": False if has_ck else None}

        # ② 命中缓存：同一 uid 在 TTL 内不重复请求
        hit = _WWW_RESULT_CACHE.get(uid)
        if hit and (time.time() - hit[0]) < WWW_CACHE_TTL:
            return {**hit[1], "cached": True,
                    "cache_age": int(time.time() - hit[0])}

        status, html = await self._get_www_page(f"/group/people/{uid}/joins")
        if status == 200:
            parsed = self._parse_www_groups(html)
            if parsed["groups"] or parsed["managed"]:
                return self._remember(uid, {**base, **parsed, "status": "ok",
                                            "cookie_accepted": self._looks_logged_in(html)})

        # joins 打不开：再看 people/:uid/groups —— 它的 403 正文会明说「未登录无法查看更多内容」
        st2, html2 = await self._get_www_page(f"/people/{uid}/groups")
        if st2 == 200:
            parsed = self._parse_www_groups(html2)
            if parsed["groups"] or parsed["managed"]:
                return self._remember(uid, {**base, **parsed, "status": "ok", "partial": True,
                                            "cookie_accepted": self._looks_logged_in(html2)})

        if (self.NEED_LOGIN_HINT in (html or "")
                or self.NEED_LOGIN_HINT in (html2 or "")):
            return self._remember(uid, {**base, "status": "login_required",
                                        "cookie_accepted": False if has_ck else None})

        if status in (403, 429) or st2 in (403, 429):
            # 进入静默期：接下来一段时间不再碰 www，且越连续被封得越久
            global _WWW_BLOCK_HITS
            secs = min(WWW_COOLDOWN_SECS * (2 ** _WWW_BLOCK_HITS), 3600)
            _WWW_BLOCK_HITS += 1
            _WWW_BLOCK_UNTIL = time.time() + secs
            return {**base, "status": "blocked", "retry_after": secs,
                    "block_hits": _WWW_BLOCK_HITS,
                    "cookie_accepted": False if has_ck else None}

        # 连登录提示都没有：看用户本身还在不在
        st3, _ = await self._get_www_page(f"/group/people/{uid}/")
        return self._remember(uid, {**base, "status": "hidden" if st3 == 200 else "missing"})

    @staticmethod
    def _remember(uid: str, res: dict) -> dict:
        """把确定性的结果放进缓存（blocked 不缓存，它不代表用户真实状态）"""
        global _WWW_BLOCK_HITS
        if res.get("status") in ("ok", "login_required", "hidden", "missing"):
            _WWW_BLOCK_HITS = 0          # 能通就退出退避，下次被封重新从 600s 算起
            if len(_WWW_RESULT_CACHE) >= 500:      # 简单防膨胀
                for k in list(_WWW_RESULT_CACHE)[:100]:
                    _WWW_RESULT_CACHE.pop(k, None)
            _WWW_RESULT_CACHE[uid] = (time.time(), res)
        return res

    async def check_uid_in_group(self, group_id: str, user_id: str,
                                 max_members: int = 3000,
                                 page_size: int = 100) -> dict:
        """【隐藏小组排查】翻指定小组的成员名单，判断某 uid 是否在组内

        命中 → 100% 确定在组内；没命中时分两种:
          * 成员数 <= 已扫描数 → **确定不在组内**（全量比对过了）
          * 成员数 >  已扫描数 → 不确定，给出覆盖率百分比（大组常达百万，覆盖率极低）

        返回 {"in_group","certain","scanned","total","coverage_pct","pages","hit_at"}
        """
        gid, uid = str(group_id).strip(), str(user_id).strip()
        total, scanned, start, pages, hit_at = 0, 0, 0, 0, None
        while scanned < max_members:
            try:
                d = await self.get_group_members(gid, start, page_size)
            except DoubanError as e:
                return {"in_group": False, "certain": False, "scanned": scanned,
                        "total": total, "coverage_pct": round(scanned / total * 100, 2)
                        if total else 0.0, "pages": pages, "hit_at": None,
                        "error": f"{e.code}: {e.msg[:80]}", "gid": gid}
            members = d.get("members") or []
            if not total:
                total = int(d.get("total") or 0)
            pages += 1
            for i, m in enumerate(members):
                if str(m.get("id")) == uid or str(m.get("uid")) == uid:
                    hit_at = start + i
                    break
            if hit_at is not None:
                return {"in_group": True, "certain": True, "scanned": scanned + len(members),
                        "total": total or (scanned + len(members)),
                        "coverage_pct": 100.0, "pages": pages, "hit_at": hit_at, "gid": gid}
            scanned += len(members)
            if len(members) < page_size:      # 已到末页
                break
            start += page_size
            await asyncio.sleep(0.12)         # 限速，避免触发反爬

        covered_all = bool(total) and total <= scanned
        return {"in_group": False, "certain": covered_all, "scanned": scanned,
                "total": total, "coverage_pct": round(scanned / total * 100, 2)
                if total else 0.0, "pages": pages, "hit_at": None, "gid": gid}

    async def search_groups(self, q: str, start: int = 0, count: int = 20) -> dict:
        """按关键词搜索小组（公开接口），用于找到小组 id

        实测可用：/api/v2/search/group?q=xxx → total=185
        返回 items[]，每项 layout=group，target 才是真正的 group object。
        """
        return await self._request("/api/v2/search/group",
                                   {"q": q, "start": start, "count": count})

    async def search_users(self, q: str, start: int = 0, count: int = 20) -> dict:
        """按用户名/uid 搜索用户（公开接口），手机端输入"昵称"自动找 uid

        实测：搜索"xinge" → 命中用户（id=56053038, uid="xinge"）
        返回 items[]，每项 layout=user，target 才是真正的 user object。
        """
        return await self._request("/api/v2/search/user",
                                   {"q": q, "start": start, "count": count})

    async def get_topic(self, topic_id: str, need_auth: bool | None = None) -> dict:
        """话题详情（标题 / 摘要 / 作者 / 所属小组）

        同样需要登录态：匿名会得到 403 need_permission。
        """
        if need_auth is None:
            need_auth = bool(self.token or self.cookie)
        return await self._request(f"/api/v2/group/topic/{topic_id}", None,
                                   need_auth=need_auth)

    async def get_topic_comments(self, topic_id: str, start: int = 0, count: int = 20,
                                 need_auth: bool | None = None) -> dict:
        """单条话题的评论列表（反查他人回帖的基础）

        返回 popular_comments + comments 数组，每条评论带 author.id / author.name / text / create_time

        重要：豆瓣已把评论接口改成**必须登录**。匿名请求会得到
        HTTP 403 + code=1000 + msg="need_permission"（注意不是反爬）。
        所以：只要手上有凭证就默认带上；need_auth=None 表示"有凭证就带"。
        """
        if need_auth is None:
            need_auth = bool(self.token or self.cookie)
        return await self._request(f"/api/v2/group/topic/{topic_id}/comments",
                                   {"start": start, "count": count},
                                   need_auth=need_auth)

    async def scan_author_comments_in_groups(self, user_id: str,
                                             group_ids: list[str],
                                             topic_pages: int = 2,
                                             comments_per_topic: int = 20) -> list[dict]:
        """【反查他人回帖】遍历指定小组的近期话题，扫其评论区过滤该用户

        原理：group topic comments 接口返回每条评论的 author.id，按 uid 过滤即可。

        ⚠️ 重要变更（豆瓣侧）：评论接口已不再对匿名开放。
           匿名请求返回 HTTP 403 + code=1000 + msg="need_permission"。
           因此本方法在**没有凭证**时会直接返回 {need_login: True} 并说明原因，
           不再逐个话题去撞接口（否则只会刷出一堆无用 403，还容易连累其他接口）。

        参数：
          group_ids           候选小组 id 列表（最多 MAX_SCAN_GROUPS 个）
          topic_pages         每个小组扫描的话题页数（每页 50 条）
          comments_per_topic  每条话题最多扫多少条评论（建议 ≥20）

        返回：
          {"comments": [...], "need_login": bool, "reason": str, "blocked_groups": [...]}
        """
        import random

        group_ids = [str(g).strip() for g in group_ids if str(g).strip()][:MAX_SCAN_GROUPS]
        topic_pages = max(1, min(int(topic_pages), MAX_SCAN_PAGES))
        comments_per_topic = max(1, min(int(comments_per_topic), 50))

        LOGIN_REASON = ("豆瓣的「评论/回帖」接口已改为必须登录后才能查看，"
                        "匿名请求会被拒绝（403 need_permission）。"
                        "请点右上角「登录」用豆瓣账号密码登录（或粘贴 Bearer token）后再试。")

        # 1) 登录态预检：手上有凭证才继续，否则立刻给出明确原因
        have_cred = bool(self.token or self.cookie)
        if not have_cred:
            return {"comments": [], "need_login": True, "reason": LOGIN_REASON,
                    "blocked_groups": []}

        # 2) 反爬保护：相邻评论请求之间做随机抖动（比 0.05~0.15s 更保守）
        async def _throttle(extra: float = 0.0):
            await asyncio.sleep(random.uniform(0.12, 0.30) + extra)

        blocked_groups: list[str] = []   # 真·反爬（1000 非 need_permission）的小组
        login_failed: list[str] = []      # 提示需要重新登录的小组

        async def scan_one(gid: str):
            nonlocal blocked_groups
            out: list[dict] = []
            gname = gid
            try:
                gd = await self.get_group(gid)
                gname = gd.get("name") or gid
            except DoubanError:
                pass

            # 1) 拿本小组最近 N 页话题
            all_topics: list[dict] = []
            for p in range(topic_pages):
                try:
                    r = await self.get_group_topics(gid, start=p * 50, count=50)
                except DoubanError:
                    break
                all_topics.extend(r.get("topics", []))
                if len(r.get("topics", [])) < 50:
                    break

            # 2) 每条话题拉评论，过滤目标用户
            for i, t in enumerate(all_topics):
                try:
                    await _throttle(0.6 if i and i % 10 == 0 else 0.0)
                    cres = await self.get_topic_comments(t["id"], start=0,
                                                          count=comments_per_topic)
                except DoubanError as e:
                    if e.need_login:
                        login_failed.append(gid)
                        return out          # 登录态失效 → 整组停，别继续刷 403
                    if e.code == 1000:
                        blocked_groups.append(gid)
                        break               # 真反爬 → 停这一组，其他组继续
                    continue
                for c in (cres.get("comments") or []) + (cres.get("popular_comments") or []):
                    a = c.get("author") or {}
                    if str(a.get("id")) == str(user_id):
                        out.append({
                            "comment_id": c.get("id"),
                            "text": c.get("text", ""),
                            "create_time": c.get("create_time"),
                            "vote_count": c.get("vote_count", 0),
                            "topic_id": t.get("id"),
                            "topic_title": t.get("title", ""),
                            "group_id": gid,
                            "group_name": gname,
                        })
            return out

        results = await asyncio.gather(*(scan_one(g) for g in group_ids),
                                       return_exceptions=True)
        merged: list[dict] = []
        for r in results:
            if isinstance(r, Exception):
                continue
            merged.extend(r)
        # 去重
        seen = set()
        uniq = []
        for x in merged:
            if x["comment_id"] in seen:
                continue
            seen.add(x["comment_id"])
            uniq.append(x)
        uniq.sort(key=lambda x: x.get("create_time") or "", reverse=True)
        # 统一返回 dict（包含 comments 和可能的 warning）
        result: dict = {"comments": uniq, "need_login": False}
        if blocked_groups:
            result["blocked_groups"] = list(set(blocked_groups))
            result["warning"] = (
                "部分小组的评论接口触发了豆瓣反爬（错误 1000）。"
                "已自动跳过这些小组，其余结果正常。"
                "可减少候选小组数 / 降低扫描页数后再试。"
            )
        if login_failed:
            result["need_login"] = True
            result["reason"] = LOGIN_REASON
        return result

    async def scan_author_posts_in_groups(self, user_id: str,
                                          group_ids: list[str],
                                          pages: int = 2) -> list[dict]:
        """【反查他人发言 · 实测可行】遍历指定小组的公开话题，过滤出该用户的发言

        原理：/api/v2/group/:gid/topics 是公开接口且每条话题带 author.id，
              因此只要给定候选小组，就能反查出 TA 在这些小组里的发帖。

        限制：
          * 只能覆盖「最近」的话题（受每个小组扫描页数限制，每页 50 条）
          * 必须先知道候选小组 id —— 豆瓣不提供「他人加入的小组」接口，
            可用 search_groups() 按关键词找，或用户自行提供。

        参数：
          group_ids  候选小组 id 列表
          pages      每个小组扫描页数（每页 50 条话题）
        """
        group_ids = [str(g).strip() for g in group_ids if str(g).strip()][:MAX_SCAN_GROUPS]
        pages = max(1, min(int(pages), MAX_SCAN_PAGES))

        failed: list[dict] = []   # 扫描没跑完的小组（含原因），不能再静默吞掉

        async def scan_one(gid: str):
            out: list[dict] = []
            oldest = ""          # 本组扫到的最早话题时间 → 告诉用户覆盖到什么时候
            scanned = 0
            gname = gid
            # 话题接口不返回所在小组名，单独取一次小组详情（最多 8 个，开销可接受）
            try:
                gd = await self.get_group(gid)
                gname = gd.get("name") or gid
            except DoubanError:
                pass
            for p in range(pages):
                try:
                    res = await self.get_group_topics(gid, start=p * 50, count=50)
                except DoubanError as e:
                    # 关键：以前这里直接 break，扫描失败和「真没发帖」都显示成 0 条，
                    # 用户完全没法区分。现在把原因带出去。
                    failed.append({
                        "group_id": gid,
                        "group_name": gname,
                        "page": p,
                        "code": e.code,
                        "msg": e.msg,
                        "need_login": bool(getattr(e, "need_login", False)),
                    })
                    break
                except Exception as e:      # 网络抖动等
                    failed.append({
                        "group_id": gid, "group_name": gname, "page": p, "code": -1,
                        "msg": f"{type(e).__name__}: {e}", "need_login": False,
                    })
                    break
                scanned += len(res.get("topics", []))
                for t in res.get("topics", []):
                    ct = t.get("create_time") or ""
                    if ct and (not oldest or ct < oldest):
                        oldest = ct
                    a = t.get("author") or {}
                    if str(a.get("id")) == str(user_id):
                        out.append({
                            "topic_id": t.get("id"),
                            "title": t.get("title"),
                            "abstract": t.get("abstract", ""),
                            "create_time": t.get("create_time"),
                            "group_id": gid,
                            "group_name": (t.get("group") or {}).get("name") or gname,
                            "comments_count": t.get("comments_count", 0),
                            "likes_count": t.get("likes_count", 0),
                            "url": t.get("sharing_url") or t.get("url"),
                        })
                if len(res.get("topics", [])) < 50:
                    break
            return {"out": out, "oldest": oldest, "scanned": scanned}

        results = await asyncio.gather(*(scan_one(g) for g in group_ids),
                                       return_exceptions=True)
        topics: list[dict] = []
        oldest_all, scanned_all = "", 0
        for gid, r in zip(group_ids, results):
            if isinstance(r, Exception):
                failed.append({
                    "group_id": gid, "group_name": gid, "page": 0, "code": -1,
                    "msg": f"{type(r).__name__}: {r}", "need_login": False,
                })
                continue
            topics.extend(r["out"])
            scanned_all += r["scanned"]
            if r["oldest"] and (not oldest_all or r["oldest"] < oldest_all):
                oldest_all = r["oldest"]
        # 去重（同一话题可能出现在多处）
        seen = set()
        uniq = []
        for t in topics:
            if t["topic_id"] in seen:
                continue
            seen.add(t["topic_id"])
            uniq.append(t)
        uniq.sort(key=lambda x: x.get("create_time") or "", reverse=True)
        return {"topics": uniq, "failed": failed,
                "topics_scanned": scanned_all, "covered_from": oldest_all}


# ---------------- 配置持久化（支持网页端热更新 token，无需重启） ----------------
CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "douban_config.json")


def load_saved_config() -> dict:
    """读取 douban_config.json（优先级高于环境变量），返回配置 dict

    文件格式: {"token": "xxx", "self_uid": "281065007"}
    """
    cfg: dict = {}
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            cfg = json.load(f) or {}
    except Exception:
        cfg = {}
    token = (cfg.get("token") or "").strip() or DOUBAN_TOKEN
    cookie = (cfg.get("cookie") or "").strip() or DOUBAN_COOKIE
    self_uid = (cfg.get("self_uid") or "").strip() or os.getenv("DOUBAN_SELF_UID", "").strip()
    if token:
        client.token = token
    if cookie:
        client.cookie = DoubanClient._normalize_cookie(cookie)
    if self_uid:
        os.environ["DOUBAN_SELF_UID"] = self_uid
    return {"token": token, "cookie": cookie, "self_uid": self_uid,
            "source": cfg.get("source", "")}


def save_config(token: str = "", self_uid: str = "", cookie: str = "") -> None:
    """写入 douban_config.json 并立即生效（token / cookie 至少给一个）"""
    old = {}
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            old = json.load(f) or {}
    except Exception:
        old = {}
    cfg = {
        "token": (token or old.get("token") or "").strip(),
        "cookie": (cookie or old.get("cookie") or "").strip(),
        "self_uid": (self_uid or old.get("self_uid") or "").strip(),
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


# 全局单例
client = DoubanClient()
_cfg = load_saved_config()
client.token = _cfg["token"]
