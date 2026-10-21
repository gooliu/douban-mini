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

需登录接口（Bearer token，实测 2026-10）:
    GET /api/v2/group/user/:uid/joined_groups 用户加入的小组  ← 仅允许 :uid == token 本人
    GET /api/v2/group/user/posted_topics      本人发表的话题（支持 start/count 翻页）

实测结论（重要）:
    * joined_groups 对非本人 uid 一律返回 403 need_permission
      —— 他人「加入的小组」无任何可用接口（frodo / rexxar / 网页版 全部堵死）
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

# 可通过环境变量覆盖
DOUBAN_TOKEN = os.getenv("DOUBAN_TOKEN", "").strip()  # 登录态 Bearer token（抓包获取）
# 备选登录凭证：豆瓣网页版 Cookie，形如 dbcl2="281065007:xxxxxxxx"（浏览器 F12 可直接复制）
# 优点：不用手机抓包、有效期通常比 frodo token 长。是否可用取决于豆瓣服务端是否认这个 Cookie。
DOUBAN_COOKIE = os.getenv("DOUBAN_COOKIE", "").strip()
CACHE_TTL = int(os.getenv("DOUBAN_CACHE_TTL", "600"))  # 缓存秒数，默认 10 分钟
SCAN_TOPICS_LIMIT = int(os.getenv("DOUBAN_SCAN_GROUPS", "8"))  # 扫描发言记录时最多遍历的小组数
MAX_SCAN_GROUPS = int(os.getenv("DOUBAN_SCAN_GROUPS", "8"))     # 反查发言：单次最多扫描的小组数
MAX_SCAN_PAGES = int(os.getenv("DOUBAN_SCAN_PAGES", "5"))       # 反查发言：每个小组最多扫描页数（每页50条）


class DoubanError(Exception):
    """豆瓣接口错误"""

    def __init__(self, code: int, msg: str):
        self.code = code
        self.msg = msg
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
                        1000: f"豆瓣判定为机器人程序（反爬），已自动冷却 "
                              f"{self.cooldown_remaining()} 秒。请勿连续点击，"
                              f"公开数据会自动改用备用通道",
                        997: "签名错误",
                    }.get(e.code, e.msg)
                    return {"valid": False, "code": e.code, "msg": e.msg, "hint": hint}
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

        # ---- Cookie 兜底 ----
        if self.cookie:
            uid = os.getenv("DOUBAN_SELF_UID", "").strip() or self._self_uid
            if not uid:
                return {"valid": False, "code": 0, "msg": "已配置 Cookie，但还不知道你的 uid",
                        "hint": "请在网页「登录」弹窗里同时填写 uid（如 281065007）"}
            try:
                g = await self.get_joined_groups(uid, 0, 1)
            except DoubanError as e:
                hint = {
                    103: "Cookie 登录失效：豆瓣不认这个 Cookie（可能已登出或需要复制完整的 dbcl2）",
                    1000: "豆瓣判定为机器人程序（反爬），请稍后重试",
                }.get(e.code, e.msg)
                return {"valid": False, "code": e.code, "msg": e.msg, "hint": hint}
            self._self_uid = str(uid)
            self._self_uid_ts = time.time()
            out = {"valid": True, "code": 0, "msg": "Cookie 登录有效", "via": "cookie",
                   "uid": str(uid), "groups_total": g.get("total", 0)}
            try:
                u = await self.get_user(str(uid))
                out["name"] = u.get("name")
            except DoubanError:
                pass
            return out

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
        }
        if params:
            query.update(params)
        headers = {}
        if self.cookie:
            headers["Cookie"] = self.cookie
        if need_auth:
            if not self.token and not self.cookie:
                raise DoubanError(1000, "该接口需要登录态，请在网页右上角「登录」里配置 token 或 Cookie")
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
        if code == 1000:
            secs = self.mark_blocked()
            msg = (f"豆瓣判定为机器人程序（反爬），已进入冷却 {secs}s。"
                   f"请勿连续点击；公开数据会自动改用备用通道")
        if code == 103 and need_auth and self.token:
            # token 已失效：缓存判定结果，别再带着失效 token 反复请求（会招来风控）
            self._token_dead_until = time.time() + 600
        raise DoubanError(code, msg)

    async def _request_rexxar(self, full_path: str, params: dict | None) -> Any:
        """备用通道：m.douban.com/rexxar（匿名可用，不需签名）

        full_path 形如 /api/v2/group/{gid}/topics → /rexxar/api/v2/group/{gid}/topics
        """
        http = await self._get_http()
        resp = await http.get(
            REXXAR_HOST + "/rexxar" + full_path, params=params or {},
            headers={"User-Agent": BROWSER_UA, "Referer": "https://m.douban.com/",
                     "Accept": "application/json, text/plain, */*"})
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
        if not self.token:
            return None
        if self._self_uid and time.time() - self._self_uid_ts < 3600:
            return self._self_uid
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
        大组成员总量可达百万，无法用于精确判断某用户是否为成员。
        """
        return await self._request(f"/api/v2/group/{group_id}/members",
                                   {"start": start, "count": count})

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

    async def get_topic_comments(self, topic_id: str, start: int = 0, count: int = 20) -> dict:
        """单条话题的评论列表（公开接口，反查他人回帖的基础）

        返回 popular_comments + comments 数组，每条评论带 author.id / author.name / text / create_time
        """
        return await self._request(f"/api/v2/group/topic/{topic_id}/comments",
                                   {"start": start, "count": count})

    async def scan_author_comments_in_groups(self, user_id: str,
                                             group_ids: list[str],
                                             topic_pages: int = 2,
                                             comments_per_topic: int = 20) -> list[dict]:
        """【反查他人回帖 · 公开接口】遍历指定小组的近期话题，扫其评论区过滤该用户

        原理：group topic comments 接口公开，扫描最近 N 个话题 × 每话题前 20 条评论。
        限制：
          1. 覆盖范围取决于"用户在哪些小组近期话题里回过帖"
          2. 频繁调用会触发豆瓣反爬（错误码 1000「你访问豆瓣的方式有点像机器人程序」），
             建议加大 comments_per_topic 减少请求数，或降低 topic_pages 减少总请求量

        参数：
          group_ids           候选小组 id 列表（最多 MAX_SCAN_GROUPS 个）
          topic_pages         每个小组扫描的话题页数（每页 50 条）
          comments_per_topic  每条话题最多扫多少条评论（建议 ≥20）
        """
        group_ids = [str(g).strip() for g in group_ids if str(g).strip()][:MAX_SCAN_GROUPS]
        topic_pages = max(1, min(int(topic_pages), MAX_SCAN_PAGES))
        comments_per_topic = max(1, min(int(comments_per_topic), 50))
        # 反爬保护：每次评论请求间加少量抖动
        import random
        async def _throttle():
            await asyncio.sleep(random.uniform(0.05, 0.15))

        blocked_groups: list[str] = []  # 被反爬的小组

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
            #    反爬保护：每 5 条评论后额外停一会
            stop_early = False
            for i, t in enumerate(all_topics):
                if stop_early:
                    break
                try:
                    await _throttle()
                    cres = await self.get_topic_comments(t["id"], start=0, count=comments_per_topic)
                except DoubanError as e:
                    if e.code == 1000:
                        blocked_groups.append(gid)
                        stop_early = True
                        break
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
        result: dict = {"comments": uniq}
        if blocked_groups:
            result["blocked_groups"] = list(set(blocked_groups))
            result["warning"] = (
                "评论接口触发了豆瓣反爬（错误 1000「你访问豆瓣的方式有点像机器人程序」）。"
                "请减少候选小组数 / 降低扫描页数后重试，或带 DOUBAN_TOKEN 降低触发概率。"
            )
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

        async def scan_one(gid: str):
            out: list[dict] = []
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
                except DoubanError:
                    break
                for t in res.get("topics", []):
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
            return out

        results = await asyncio.gather(*(scan_one(g) for g in group_ids),
                                       return_exceptions=True)
        topics: list[dict] = []
        for r in results:
            if isinstance(r, Exception):
                continue
            topics.extend(r)
        # 去重（同一话题可能出现在多处）
        seen = set()
        uniq = []
        for t in topics:
            if t["topic_id"] in seen:
                continue
            seen.add(t["topic_id"])
            uniq.append(t)
        uniq.sort(key=lambda x: x.get("create_time") or "", reverse=True)
        return uniq


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
