# -*- coding: utf-8 -*-
"""豆瓣小组查询小程序 - 后端 API

启动:
    pip install -r requirements.txt
    uvicorn main:app --host 0.0.0.0 --port 8000 --reload

可选环境变量:
    DOUBAN_TOKEN   豆瓣登录态 Bearer token（抓包获取），配置后「加入的小组/发言记录」为真实数据
    DOUBAN_SELF_UID  手动指定 token 对应的账号 uid（默认自动从 posted_topics 推断）
    DOUBAN_CACHE_TTL  缓存秒数（默认 600）
    DOUBAN_SCAN_GROUPS  扫描发言记录时最多遍历的小组数（默认 8）

数据能力矩阵（实测 2026-10）:
    任意用户: 公开资料 / 兴趣(看过影视书) / 小组详情 / 小组话题 / 小组成员 / 小组搜索 -> 真实数据
    token 本人: 加入的小组(joined_groups) / 完整发言(posted_topics) -> 真实数据
    其他用户: 加入的小组 -> 豆瓣无任何可用接口，返回 source=restricted 并说明原因
    其他用户: 发言记录 -> 无法直接查询，但可用「反查」：给定候选小组 id 扫描其话题过滤作者
    未配置 DOUBAN_TOKEN: 受限接口返回演示数据（source=mock）
"""
from __future__ import annotations

import contextvars
import json as _json
import os
import re
import secrets
import socket
import time
from pathlib import Path as FilePath
from typing import Optional

from fastapi import FastAPI, HTTPException, Path, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import browser_cookie
import mock_data
import notes
import scanner
from douban_client import CONFIG_FILE, DoubanError, client, save_config

app = FastAPI(title="豆瓣小组查询 API", version="1.0.0")

# 云端部署模式（Render / Docker 等设置 DOUBAN_CLOUD=1）：
#   凭证不写入服务端文件，只存在访问者自己的浏览器里，每次请求通过
#   X-Douban-Token / X-Douban-Cookie 头带过来 —— 多人共用一个后端也不会互相看到账号。
CLOUD_MODE = os.getenv("DOUBAN_CLOUD", "").strip().lower() in ("1", "true", "yes")

# 公网隧道模式（DOUBAN_PUBLIC=1）：通过 localhost.run / serveo 等隧道把本机后端暴露到公网。
#   此时所有请求到达后端时源 IP 都是 127.0.0.1（由本机 ssh 进程转发出去），
#   若按 IP 判断是否本机，等于把全世界都当成你自己 —— 必须强制每个访客自带凭证。
PUBLIC_MODE = os.getenv("DOUBAN_PUBLIC", "").strip().lower() in ("1", "true", "yes")

# 是否信任局域网（信任后家里 WiFi 下的设备可免登录，直接用自己的账号）
TRUST_LAN = os.getenv("DOUBAN_TRUST_LAN", "").strip().lower() in ("1", "true", "yes")

# 当前请求生效的客户端（默认全局单例；带凭证头时换成临时凭证视图）
_active_client = contextvars.ContextVar("douban_active_client", default=client)


def _c():
    """当前请求生效的 DoubanClient（云端多用户时可能是临时凭证视图）"""
    return _active_client.get()


# 小程序开发阶段需要（微信开发者工具勾选"不校验合法域名"即可）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"],
)


_LOOPBACK = ("127.0.0.1", "::1", "localhost", "::ffff:127.0.0.1")


def _client_ip(request) -> str:
    """取真实客户端 IP。

    经反向代理 / 隧道（localhost.run、serveo、nginx 等）转发时，
    request.client.host 会变成 127.0.0.1，必须优先看 X-Forwarded-For，
    否则会把外部访客误判成本机，直接泄露服务端账号。
    """
    for key in ("x-forwarded-for", "x-real-ip", "cf-connecting-ip",
                "true-client-ip", "forwarded"):
        v = (request.headers.get(key) or "").strip()
        if not v:
            continue
        first = v.split(",")[0].strip()
        if key == "forwarded" and "for=" in first:
            first = first.split("for=", 1)[1].split(";")[0].strip().strip('"')
            if first.startswith("[") and "]" in first:
                first = first[1:first.find("]")]
        if first:
            return first
    return (request.client.host if request.client else "") or ""


def _visitor_must_login(request) -> bool:
    """外部访问者是否必须用自己的凭证登录（避免把服务端保存的账号暴露给别人）

    - 云端模式（DOUBAN_CLOUD=1）：永远是，凭证只存访问者浏览器。
    - 公网隧道模式（DOUBAN_PUBLIC=1）：永远是。隧道转发后源 IP 是 127.0.0.1，
      若按 IP 判断会把全世界都当成本机，必须强制隔离。
    - 本机模式：只有从 127.0.0.1 / ::1 访问才沿用服务端保存的凭证；
      其他人（含局域网）必须自己登录，看不到服务端账号。
    - 想让局域网设备也免登录，设置 DOUBAN_TRUST_LAN=1。
    """
    if CLOUD_MODE or PUBLIC_MODE:
        return True
    host = _client_ip(request)
    if host in _LOOPBACK:
        return False
    if TRUST_LAN and host.startswith(("192.168.", "10.", "172.")):
        return False
    return True


@app.middleware("http")
async def _credential_middleware(request, call_next):
    """把请求头里的凭证绑定到本次请求（多用户隔离）"""
    tok = request.headers.get("X-Douban-Token") or ""
    ck = request.headers.get("X-Douban-Cookie") or ""
    if (tok or ck) or _visitor_must_login(request):
        _active_client.set(client.scoped(tok, ck))
    else:
        _active_client.set(client)
    return await call_next(request)

# Web 网页版：直接访问 http://<host>:8000/ 即可使用
_WEB_DIR = FilePath(__file__).parent.parent / "web"
app.mount("/web", StaticFiles(directory=_WEB_DIR, html=True), name="web")


@app.get("/", include_in_schema=False)
async def root():
    return RedirectResponse("/web/")


class Resp(BaseModel):
    """统一响应体"""
    ok: bool
    source: str  # "douban"（真实）| "mock"（演示）| "restricted"（豆瓣限制，无法查询）
    data: Optional[dict | list] = None
    message: str = ""


def _err(e: DoubanError) -> HTTPException:
    mapping = {1000: 401, 404: 404, 996: 502, 997: 502}
    return HTTPException(status_code=mapping.get(e.code, 502),
                         detail=f"豆瓣接口错误 code={e.code}: {e.msg}")


async def _self_uid() -> Optional[str]:
    """token 对应的本人 uid（未配置凭证时 None）"""
    if not _c().token and not _c().cookie:
        return None
    try:
        return await _c().get_self_uid()
    except Exception:
        return None


# --------------------------------------------------------------------
@app.get("/api/health")
async def health(request: Request):
    """服务状态 + 登录凭证有效性（前端据此显示「登录有效 / 登录失效」）

    共享模式下，凭证来自请求头（访问者自己的浏览器），每个人看到的是自己的登录态，
    不会返回服务端保存的账号。
    """
    need_login = _visitor_must_login(request)
    if not _c().token and not _c().cookie:
        status, detail = "missing", "未登录：只能查公开数据（用户信息、小组搜索、反查发帖/回帖）"
    else:
        chk = await _c().check_token()
        if chk["valid"]:
            status = "valid"
            extra = (f"，共 {chk.get('topics_total', 0)} 条发言" if chk.get("via") == "token"
                     else f"，{chk.get('groups_total', 0)} 个小组")
            detail = (f"登录有效（{'token' if chk.get('via') == 'token' else 'Cookie'}）："
                      f"{chk.get('name') or ''}（uid {chk.get('uid')}）{extra}")
        elif chk.get("code") == 103:
            status = "expired"
            detail = chk.get("hint") or "登录信息失效，请更新凭证"
        else:
            status = "invalid"
            detail = chk.get("hint") or chk.get("msg", "")
    uid = await _self_uid()
    return {
        "ok": True,
        "token_configured": bool(_c().token),
        "cookie_configured": bool(_c().cookie),
        "token_valid": status == "valid",
        "token_status": status,          # valid | expired | invalid | missing
        "self_uid": uid,
        "detail": detail,
        "cooldown": client.cooldown_info(),   # 风控冷却状态（1000 反爬后自动进入）
        "cloud_mode": CLOUD_MODE,        # 云端部署：凭证只存访问者浏览器
        "public_mode": PUBLIC_MODE,      # 公网隧道：强制每个访客自带凭证
        "need_login": need_login,        # true：必须用你自己的豆瓣账号登录
        "mode": ("共享模式：请用你自己的豆瓣账号登录（不会显示他人账号）"
                 if need_login and status != "valid" else
                 ("云端共享模式（凭证存你自己浏览器）" if CLOUD_MODE else
                  "公网隧道模式（凭证存你自己浏览器）" if PUBLIC_MODE else "本机模式"))
                if status != "valid" else
                "真实数据（本人可查小组/发言）",
    }


@app.get("/api/cooldown")
async def cooldown():
    """豆瓣风控冷却状态：{cooling, remaining_seconds, hits, via}"""
    return {"ok": True, **client.cooldown_info()}


@app.get("/api/local-credential")
async def local_credential(request: Request):
    """供「本机浏览器」一键继承服务端已保存的凭证（免重新登录）。

    这是唯一会往外吐凭证的接口，因此校验必须最严格：
      1) 源地址必须是回环（127.0.0.1 / ::1）；
      2) 不能带任何转发/代理头 —— 一旦带了，说明请求是经由隧道或反代
         转发进来的，此时回包会被送给外部访客，必须拒绝。
    公网隧道模式（DOUBAN_PUBLIC=1）下直接关闭该接口。
    """
    if CLOUD_MODE or PUBLIC_MODE:
        return {"ok": False, "reason": "shared_mode"}
    host = (request.client.host if request.client else "") or ""
    if host not in _LOOPBACK:
        return {"ok": False, "reason": "not_loopback"}
    for key in ("x-forwarded-for", "x-real-ip", "cf-connecting-ip",
                "true-client-ip", "forwarded", "via", "x-forwarded-host"):
        if (request.headers.get(key) or "").strip():
            return {"ok": False, "reason": "via_proxy"}
    return {
        "ok": True,
        "token": client.token or "",
        "cookie": client.cookie or "",
        "self_uid": os.getenv("DOUBAN_SELF_UID", "").strip(),
    }


# --------------------------------------------------------------------
# 分享给他人（局域网 / 公网）
PORT = int(os.getenv("DOUBAN_PORT", "8000"))


def _lan_ips() -> list[str]:
    """本机局域网 IPv4（排除回环、APIPA、Docker/WSL 虚拟网卡常见段）"""
    ips: list[str] = []
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if ip.startswith(("127.", "169.254.")):
                continue
            if ip not in ips:
                ips.append(ip)
    except Exception:
        pass
    if not ips:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("223.5.5.5", 80))     # 不发包，只为拿到出口网卡 IP
            ips.append(s.getsockname()[0])
            s.close()
        except Exception:
            pass
    # 真实网卡优先（192.168 / 10. / 172.16-31 属于常见局域网段）
    def score(ip: str) -> int:
        if ip.startswith("192.168."):
            return 0
        if ip.startswith("10."):
            return 1
        if ip.startswith("172."):
            return 2
        return 3
    ips.sort(key=score)
    return ips


@app.get("/api/share")
async def share_info(request: Request):
    """分享信息：同一 WiFi 下的手机/电脑可直接访问的地址列表

    云端模式（部署到 Render 等）时，局域网地址没意义，直接给出当前公网地址。
    """
    ips = _lan_ips()
    urls = [f"http://{ip}:{PORT}/" for ip in ips]
    host = request.headers.get("host", "")
    public_url = f"https://{host}/" if (CLOUD_MODE and host) else ""
    return {
        "ok": True,
        "port": PORT,
        "lan_ips": ips,
        "lan_urls": urls,
        "primary_url": public_url or (urls[0] if urls else f"http://127.0.0.1:{PORT}/"),
        "self_url": f"http://127.0.0.1:{PORT}/",
        "public_url": public_url,          # 云端部署时的公网地址（直接发给别人）
        "cloud_mode": CLOUD_MODE,
        # 已配置登录凭证时，别人访问会共享你的账号数据 → 前端据此提醒
        "credential_configured": bool(client.token or client.cookie),
        "need_login": _visitor_must_login(request),
        # 共享模式下不把服务端账号 uid 发给外部访问者
        "self_uid": "" if _visitor_must_login(request) else os.getenv("DOUBAN_SELF_UID", ""),
    }


@app.get("/api/qrcode.svg")
async def qrcode_svg(text: str = Query("", max_length=500),
                     scale: int = Query(6, ge=2, le=12)):
    """把文本生成二维码 SVG（给分享面板用）"""
    if not text:
        raise HTTPException(400, "text 不能为空")
    try:
        import segno
    except ImportError:
        raise HTTPException(501, "后端未安装 segno：pip install segno")
    svg = segno.make(text, error="m").svg_inline(scale=scale, border=2)
    return Response(content=svg, media_type="image/svg+xml")


@app.post("/api/config/logout")
async def logout():
    """清除保存的登录凭证（把工具分享给别人用之前，保护自己的账号）

    云端模式：服务端本来没存凭证，只需前端清掉浏览器里的那份。
    """
    if CLOUD_MODE:
        return {"ok": True, "cloud": True,
                "detail": "云端模式：凭证只存在你自己的浏览器里，已在你这边清除"}
    uid = ""
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            uid = (_json.load(f) or {}).get("self_uid", "")
    except Exception:
        pass
    client.set_token("")
    client.set_cookie("")
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            _json.dump({"token": "", "cookie": "", "self_uid": uid,
                        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S")},
                       f, ensure_ascii=False, indent=2)
    except Exception as e:
        raise HTTPException(500, f"写入配置失败：{e}")
    return {"ok": True, "detail": "已退出登录，本机凭证已清除（uid 保留，方便你下次登录）"}


class TokenIn(BaseModel):
    token: Optional[str] = None      # frodo Bearer token（手机抓包）
    cookie: Optional[str] = None     # 豆瓣网页 Cookie（浏览器 F12 复制，含 dbcl2）
    self_uid: Optional[str] = None   # Cookie 登录时需要（如 281065007）


async def _apply_credential(token: str, cookie: str, self_uid: str = "",
                            readonly: bool = False) -> dict:
    """校验登录凭证（token 或 cookie 二选一，token 优先）

    本机模式：校验通过写盘（douban_config.json）并立即生效；
    云端模式（DOUBAN_CLOUD=1）：只校验、不写盘，凭证由前端存访问者浏览器，
    之后每次请求用 X-Douban-Token / X-Douban-Cookie 头带过来。

    返回: {"ok": True, "uid", "name", "via", "saved", "detail"} 或
          {"ok": False, "status", "code", "detail"}
    """
    token = (token or "").strip()
    cookie = (cookie or "").strip()
    if not token and not cookie:
        return {"ok": False, "status": "empty", "detail": "请先登录（豆瓣账号密码 / token）"}
    if not token and cookie and "dbcl2" not in cookie.lower():
        # 登录标识都没有，不用去问豆瓣了（省一次请求，也避免给出误导性提示）
        return {"ok": False, "status": "cookie_incomplete",
                "detail": "这段内容里没有 dbcl2（豆瓣的登录标识），肯定不是登录状态的 Cookie。"
                          "请先在手机浏览器打开 m.douban.com 登录，再用书签一键带回。"}

    # 用临时凭证视图校验，绝不污染全局单例（云端多人共享时尤其重要）
    probe = client.scoped(token, cookie)
    if self_uid:
        if not readonly:
            # 本机自用才记住 uid；共享模式绝不能把访问者的 uid 写进进程环境
            os.environ["DOUBAN_SELF_UID"] = str(self_uid).strip()
        probe._self_uid = str(self_uid).strip()
        probe._self_uid_ts = time.time()
    chk = await probe.check_token()

    if not chk["valid"]:
        return {"ok": False, "status": "expired" if chk.get("code") == 103 else "invalid",
                "code": chk.get("code"), "detail": chk.get("hint") or chk.get("msg", "凭证无效")}

    extra = (f"，{chk.get('topics_total', 0)} 条发言" if chk.get("via") == "token" else "")
    detail = f"登录成功（{'token' if chk.get('via') == 'token' else 'Cookie'}）：" \
             f"{chk.get('name') or ''}（uid {chk.get('uid')}）{extra}"

    if CLOUD_MODE or readonly:
        # 不落盘：告诉前端自己存 localStorage
        # readonly：外部访问者共享这台后端时，绝不能覆盖服务端保存的账号
        return {"ok": True, "status": "valid", "uid": chk.get("uid"),
                "name": chk.get("name"), "via": chk.get("via"),
                "saved": False, "cloud": True, "detail": detail,
                # 云模式凭证必须留在访问者浏览器里，所以要把凭证回传一次
                "token": token, "cookie": cookie}

    client.set_token(token)          # 只给 cookie 时会清空 token，自动走 Cookie 分支
    client.set_cookie(cookie)
    save_config(token=token, cookie=cookie,
                self_uid=str(chk.get("uid") or self_uid or ""))
    return {"ok": True, "status": "valid", "uid": chk.get("uid"),
            "name": chk.get("name"), "via": chk.get("via"),
            "saved": True, "cloud": False, "detail": detail}


@app.post("/api/config/token")
async def set_token(body: TokenIn, request: Request):
    """热更新登录凭证（本机模式写入 douban_config.json，立即生效，无需重启后端）

    共享模式下（外部访问者 / DOUBAN_CLOUD=1）只校验不写盘，
    凭证由访问者自己的浏览器保存，不会覆盖服务端账号。

    两种凭证二选一（都给也行，token 优先）：
      body: {"token": "xxx"}
      body: {"cookie": "dbcl2=\\"uid:xxx\\"", "self_uid": "281065007"}
    返回: {"ok": true, "status": "valid"/"expired"/"invalid", "uid", "name", "via", "detail"}
    """
    return await _apply_credential(body.token, body.cookie, body.self_uid,
                                   readonly=_visitor_must_login(request))


class PasswordIn(BaseModel):
    username: Optional[str] = None   # 豆瓣手机号 / 邮箱
    password: Optional[str] = None   # 只用于向豆瓣换 token，不保存


@app.post("/api/login/password")
async def login_by_password(body: PasswordIn, request: Request):
    """豆瓣账号密码登录 —— 手机上最省事的方式（不用抓包、不用开发者工具）

    密码只用于向豆瓣换取 access_token：不写盘、不写日志、不回显。
    本机模式：校验通过后保存，重启服务仍有效；
    共享 / 公网模式：令牌只回传给访问者自己的浏览器，绝不写服务端。
    """
    try:
        got = await client.login_with_password(body.username or "", body.password or "")
    except DoubanError as e:
        return {"ok": False, "status": "invalid", "code": e.code, "detail": e.msg}

    res = await _apply_credential(got["token"], "", got.get("uid") or "",
                                  readonly=_visitor_must_login(request))
    if res.get("ok"):
        days = int((got.get("expires_in") or 0) / 86400)
        res["via"] = "password"
        res["expires_in"] = got.get("expires_in", 0)
        res["detail"] = (f"登录成功：{res.get('name') or ''}（uid {res.get('uid')}）"
                         + (f"，令牌约 {days} 天有效" if days else ""))
    return res


@app.post("/api/local-cookie")
async def local_cookie(request: Request):
    """从本机浏览器（Chrome / Edge…）里读豆瓣登录态 —— 绕开 dbcl2 的 HttpOnly

    为什么需要它：豆瓣的登录标识 `dbcl2` 是 HttpOnly，浏览器里的 JS（含小书签
    `javascript:`）根本读不到，所以手机上「点书签带回 Cookie」注定失败。
    后端和浏览器在同一台机器上时，可以直接读浏览器的 Cookie 库并解密。

    安全：只在自用模式（非云端 / 非公网共享）且请求来自本机或局域网时可用，
    绝不把这台电脑的登录态暴露给公网访客。
    """
    if CLOUD_MODE or PUBLIC_MODE:
        return {"ok": False, "status": "disabled",
                "detail": "当前是「每个访客登录自己账号」的模式，不能把这台电脑的"
                          "登录态给别人用。请改用账号密码登录 / 接力码 / 手动粘贴 Cookie。"}
    import ipaddress
    host = _client_ip(request)
    try:
        private = ipaddress.ip_address(host).is_private
    except Exception:
        private = False
    if not private:
        return {"ok": False, "status": "forbidden",
                "detail": "只允许从本机或家庭 / 公司局域网里读取浏览器登录态"}

    got = browser_cookie.read_douban_cookie()
    if not got.get("ok") or not got.get("cookie"):
        return {"ok": False, "status": "not_found",
                "detail": got.get("detail") or "没读到豆瓣的登录 Cookie"}

    res = await _apply_credential("", got["cookie"], "", readonly=False)
    if res.get("ok"):
        # 本机 / 局域网自用：把 Cookie 明文回传一份，手机端也能直接带上
        res["cookie"] = got["cookie"]
        res["via"] = "local_browser"
        res["browser"] = got.get("browser") or ""
        res["source"] = got.get("profile") or ""
        res["detail"] = (f"已从本机 {res['browser']}（{res['source']}）读到豆瓣登录态："
                         f"{res.get('name') or ''}（uid {res.get('uid')}）")
    return res


@app.get("/api/local-cookie/support")
async def local_cookie_support():
    """本机浏览器读取是否可用（前端据此决定是否显示按钮）"""
    ok = (os.name == "nt") and not (CLOUD_MODE or PUBLIC_MODE)
    reason = ""
    if os.name != "nt":
        reason = "只支持 Windows"
    elif CLOUD_MODE or PUBLIC_MODE:
        reason = "当前是访客各自登录的模式，不读本机浏览器"
    return {"ok": ok, "reason": reason}


# ---------- 跨设备「接力码」：电脑上已登录，手机输入 6 位码即可 ----------
_HANDOFF: dict[str, dict] = {}
_HANDOFF_TTL = 300                     # 5 分钟
_HANDOFF_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"   # 去掉易混的 I O 0 1


class HandoffIn(BaseModel):
    token: Optional[str] = None
    code: Optional[str] = None
    cookie: Optional[str] = None      # 网页 Cookie 也能接力（手机拿不到时用）


@app.post("/api/handoff/create")
async def handoff_create(body: HandoffIn):
    """生成一次性接力码（5 分钟有效）。

    凭证来源：优先用请求里的 token（访问者存在自己浏览器里的），
    否则退回服务端保存的 token（仅本机自用模式，公网共享模式绝不外借）。
    """
    cred = (body.token or "").strip()
    cred_ck = (body.cookie or "").strip()
    if not cred and not cred_ck and not (PUBLIC_MODE or CLOUD_MODE):
        cred = (client.token or "").strip()
        cred_ck = (client.cookie or "").strip()
    if not cred and not cred_ck:
        return {"ok": False, "detail": "这边还没有登录，无法生成接力码。"
                                      "请先在本页登录，或改用「账号密码」在手机上直接登录。"}
    now = time.time()
    for k in [k for k, v in _HANDOFF.items() if v["exp"] < now]:
        _HANDOFF.pop(k, None)
    code = "".join(secrets.choice(_HANDOFF_ALPHABET) for _ in range(6))
    _HANDOFF[code] = {"token": cred, "cookie": cred_ck, "exp": now + _HANDOFF_TTL}
    return {"ok": True, "code": code, "expires_in": _HANDOFF_TTL,
            "has_cookie": bool(cred_ck)}


@app.post("/api/handoff/claim")
async def handoff_claim(body: HandoffIn):
    """用接力码取回凭证（一次性，用过即失效）"""
    code = (body.code or "").strip().upper().replace("-", "").replace(" ", "")
    item = _HANDOFF.get(code)
    if not item or item["exp"] < time.time():
        _HANDOFF.pop(code, None)
        return {"ok": False, "detail": "接力码无效或已过期（5 分钟内有效），请在电脑上重新生成"}
    _HANDOFF.pop(code, None)
    return {"ok": True, "token": item.get("token") or "",
            "cookie": item.get("cookie") or ""}


@app.get("/api/cookie-login", response_class=HTMLResponse)
async def cookie_login(request: Request, cookie: str = Query(""), uid: str = Query("")):
    """（已停用）旧的小书签 Cookie 通道 —— 豆瓣 App 接口不认 Cookie，这里只回一句说明。

    用法（新窗口打开）：/api/cookie-login?cookie=dbcl2%3D%22uid%3Axxx%22&uid=281065007
    """
    if not cookie:
        return _login_result_html(False, "没有收到 cookie 参数")
    r = await _apply_credential("", cookie, uid,
                                readonly=_visitor_must_login(request))
    return _login_result_html(r["ok"], r.get("detail", ""))


def _login_result_html(ok: bool, detail: str) -> str:
    color = "#2a6" if ok else "#d93025"
    title = "登录成功" if ok else "登录失败"
    tip = ("可以关掉这个窗口，回到查询页面刷新即可。" if ok
           else "请回到查询页面，用「账号密码登录」或「粘贴 token」重新登录。")
    return f"""<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title><style>
body{{font-family:-apple-system,'Microsoft YaHei',sans-serif;background:#f6f6f6;
display:flex;align-items:center;justify-content:center;height:100vh;margin:0}}
.card{{background:#fff;border-radius:12px;padding:28px 32px;max-width:420px;
box-shadow:0 2px 16px rgba(0,0,0,.08);text-align:center}}
h2{{color:{color};margin:0 0 12px;font-size:20px}}
p{{color:#555;font-size:14px;line-height:1.7;margin:8px 0}}
code{{background:#f0f0f0;padding:1px 5px;border-radius:4px}}
</style></head><body><div class="card">
<h2>{title}</h2><p>{detail}</p><p>{tip}</p>
<p><a href="/">← 回到查询页面</a></p>
</div></body></html>"""


@app.get("/api/config/token")
async def get_token_status():
    """查看当前 token 状态（不返回完整 token，只给前后缀用于确认）"""
    t = _c().token or ""
    masked = (t[:6] + "..." + t[-4:]) if len(t) > 12 else ("已配置" if t else "")
    chk = await _c().check_token() if t else {"valid": False, "code": 0, "msg": "未配置"}
    return {"ok": True, "configured": bool(t), "masked": masked,
            "valid": chk.get("valid", False),
            "status": "valid" if chk.get("valid") else
                      ("expired" if chk.get("code") == 103 else
                       ("missing" if not t else "invalid")),
            "detail": chk.get("hint") or chk.get("msg", "")}


@app.get("/api/me", response_model=Resp)
async def api_me():
    """当前 token 对应的豆瓣账号（用于前端自动识别"我自己"）

    - 未配置 token: ok=False
    - 已配置: 返回 uid / 昵称 / 头像 / 加入小组数 / 发言数
    """
    if not _c().token:
        return Resp(ok=False, source="none", message="未配置豆瓣 token（点右上角「登录」粘贴即可）")
    uid = await _self_uid()
    if not uid:
        return Resp(ok=False, source="none",
                    message="无法识别 token 对应账号（该账号可能从未发过小组话题），"
                            "可设置环境变量 DOUBAN_SELF_UID 手动指定 uid")
    data: dict = {"uid": uid}
    try:
        u = await _c().get_user(uid)
        data.update({
            "id": u.get("id") or uid,
            "name": u.get("name"),
            "avatar": u.get("avatar"),
            "reg_time": u.get("reg_time", ""),
            "joined_group_count": u.get("joined_group_count", 0),
            "statuses_count": u.get("statuses_count", 0),
        })
    except DoubanError:
        data.setdefault("name", uid)
    try:
        posted = await _c().get_posted_topics(0, 1)
        data["topics_total"] = posted.get("total", 0)
    except DoubanError as e:
        if e.code in (1000, 103):
            # token 失效：返回部分信息，不抛错
            return Resp(ok=True, source="douban", data=data,
                        message="登录已失效（豆瓣 code=103）：点右上角「登录」重新粘贴豆瓣 token 即可，"
                                "公开数据仍可用，本人专属数据（加入的小组/完整发言）暂停")
        data["topics_total"] = None
    return Resp(ok=True, source="douban", data=data)


@app.get("/api/user/{user_id}", response_model=Resp)
async def get_user(user_id: str = Path(description="豆瓣用户数字 uid 或用户名")):
    """用户公开信息（真实数据，无需登录）"""
    try:
        u = await _c().get_user(user_id)
    except DoubanError as e:
        raise _err(e)
    return Resp(ok=True, source="douban", data={
        "id": u.get("id") or u.get("uid"),
        "name": u.get("name"),
        "avatar": u.get("avatar"),
        "signature": u.get("signature", ""),
        "loc": (u.get("loc") or {}).get("name", "") if isinstance(u.get("loc"), dict) else u.get("loc"),
        "reg_time": u.get("reg_time", ""),
        "followers_count": u.get("followers_count", 0),
    })


@app.get("/api/user/{user_id}/groups", response_model=Resp)
async def get_user_groups(user_id: str, start: int = 0, count: int = Query(50, le=100)):
    """用户加入的小组

    - 配置 token 且 user_id == token 本人: 真实数据（frodo joined_groups）
    - 配置 token 但查的是别人: 豆瓣返回 403，此处明确告知（source=restricted）
    - 未配置 token: 演示数据
    """
    if not _c().token:
        return Resp(ok=True, source="restricted", data={"total": 0, "groups": []},
                    message="未登录：豆瓣「加入的小组」只有本人登录态才能查。"
                            "点右上角「登录」粘贴豆瓣 token 后再查本人即可；"
                            "查他人请改用「反查发言」")

    self_uid = await _self_uid()
    if self_uid and str(user_id) != str(self_uid):
        return Resp(ok=True, source="restricted", data={"total": 0, "groups": []},
                    message=f"豆瓣不提供「他人加入的小组」接口（已实测 frodo / 移动网页版 / rexxar "
                            f"全部不可用），只能查 token 本人（uid {self_uid}）。"
                            f"替代方案：用「反查发言」看 TA 在指定小组的发帖，"
                            f"或用小组搜索按关键词找到小组 id")
    try:
        g = await _c().get_joined_groups(user_id, start, count)
    except DoubanError as e:
        if e.code == 103:
            return Resp(ok=True, source="restricted", data={"total": 0, "groups": []},
                        message="登录已失效（豆瓣 code=103）：请点右上角「登录」重新粘贴豆瓣 token，"
                                "更新后立即生效，无需重启服务")
        if e.code == 1000:
            return Resp(ok=True, source="restricted", data={"total": 0, "groups": []},
                        message=f"豆瓣拒绝访问（need_permission）：该接口仅允许查询 token 本人"
                                f"（uid {self_uid or '未知'}）加入的小组")
        raise _err(e)
    groups = [{
        "id": x.get("id"),
        "name": x.get("name"),
        "member_count": x.get("member_count"),
        "create_time": x.get("create_time"),
        "desc": x.get("desc_abstract") or x.get("desc", ""),
        "category": (x.get("category") or {}).get("title", "") if isinstance(x.get("category"), dict) else "",
        "url": x.get("url"),
    } for x in g.get("groups", [])]
    return Resp(ok=True, source="douban",
                data={"total": g.get("total", len(groups)), "groups": groups})


@app.get("/api/user/{user_id}/topics", response_model=Resp)
async def get_user_topics(user_id: str, start: int = 0, count: int = Query(50, le=100)):
    """用户历史发言记录（小组内发过的话题）

    - 配置 token 且 user_id == token 本人: posted_topics 真实完整记录（可翻页，total 准确）
    - 配置 token 但查的是别人: 豆瓣无公开接口（joined_groups 被限制，扫描方案失效）→ restricted
    - 未配置 token: 演示数据
    """
    if not _c().token:
        return Resp(ok=True, source="restricted", data={"topics": [], "total": 0},
                    message="未登录：豆瓣「完整发言记录」只有本人登录态才能查。"
                            "点右上角「登录」粘贴豆瓣 token 后再查本人；"
                            "查他人请用「反查发言 / 反查回帖」")

    self_uid = await _self_uid()
    if self_uid and str(user_id) != str(self_uid):
        return Resp(ok=True, source="restricted", data={"topics": [], "total": 0},
                    message=f"豆瓣不提供「他人完整发言记录」接口，只能查 token 本人（uid {self_uid}）。"
                            f"替代方案：用「反查发言」接口 —— 提供候选小组 id，"
                            f"扫描这些小组的公开话题即可过滤出 TA 的发帖"
                            f"（GET /api/user/{user_id}/posts-in-groups?groups=小组id,小组id）")

    try:
        posted = await _c().get_posted_topics(start, count)
    except DoubanError as e:
        if e.code in (1000, 103):
            return Resp(ok=True, source="restricted", data={"topics": [], "total": 0},
                        message="登录已失效（豆瓣 code=103）：请点右上角「登录」重新粘贴豆瓣 token，"
                                "更新后立即生效，无需重启服务。"
                                "公开数据（反查发帖/回帖、小组搜索）不受影响")
        raise _err(e)

    topics = [{
        "topic_id": t.get("id"),
        "title": t.get("title"),
        "abstract": t.get("abstract", ""),
        "create_time": t.get("create_time"),
        "group_id": (t.get("group") or {}).get("id"),
        "group_name": (t.get("group") or {}).get("name"),
        "comments_count": t.get("comments_count", 0),
        "likes_count": t.get("likes_count", 0),
        "url": t.get("url"),
    } for t in posted.get("topics", [])]
    return Resp(ok=True, source="douban",
                data={"topics": topics, "total": posted.get("total", len(topics)), "start": start})


@app.get("/api/user/{user_id}/posts-in-groups", response_model=Resp)
async def reverse_lookup_posts(
    user_id: str,
    gids: str = Query("", alias="groups",
                      description="候选小组 id，逗号分隔，如 146409,736218（最多 8 个）"),
    pages: int = Query(4, ge=1, le=20, description="每个小组扫描页数，每页 50 条话题"),
):
    """【反查他人发言】扫描指定小组的公开话题，过滤出该用户的发帖

    这是目前唯一能查「他人发言」的可行方式：
      1. 先用 /api/search/group?q=关键词 找到候选小组 id
      2. 再用本接口扫描这些小组的话题，过滤出 TA 的帖子

    限制：只能覆盖各小组「最近」的话题（受 pages 限制），不是全量历史。
    """
    group_ids = [g for g in (gids or "").replace("，", ",").split(",") if g.strip()]
    if not group_ids:
        return Resp(ok=False, source="none",
                    message="请通过 groups 参数提供候选小组 id（逗号分隔），"
                            "可先用 /api/search/group?q=关键词 搜索小组")
    try:
        res = await _c().scan_author_posts_in_groups(user_id, group_ids, pages)
    except DoubanError as e:
        raise _err(e)
    topics = res["topics"]
    failed = res.get("failed") or []
    covered_from = res.get("covered_from") or ""
    payload = {
        "topics": topics,
        "total": len(topics),
        "scanned_groups": group_ids,
        "pages_per_group": pages,
        "topics_scanned": res.get("topics_scanned", 0),
        "covered_from": covered_from,
        "note": f"已扫 {res.get('topics_scanned', 0)} 条话题"
                + (f"，最早覆盖到 {covered_from}" if covered_from else "")
                + "。热门小组帖子刷新很快，更早的发帖请用「全量扫描」。",
    }
    if failed:
        need_login = any(f.get("need_login") for f in failed)
        payload["failed_groups"] = failed
        payload["scan_incomplete"] = True
        payload["warning"] = (
            ("豆瓣要求登录后才能扫描这批小组：" if need_login else "")
            + f"{len(failed)}/{len(group_ids)} 个小组没扫完（{failed[0]['msg'][:60]}）。"
            + "下面的 0 条不代表 TA 没发过帖，可能是扫描被中断了，请稍后重试或换个小组。"
        )
    return Resp(ok=True, source="douban", data=payload)


# --------------------------------------------------------------------
# 全量扫描任务（后台翻完小组全部历史话题 / 评论区）
# --------------------------------------------------------------------
class ScanIn(BaseModel):
    user_id: str
    user_name: Optional[str] = None
    groups: list[str] = []              # 候选小组 id
    mode: str = "posts"                 # posts | comments | both
    max_topics_per_group: int = 0       # 0 = 不限，扫到小组最早一条
    resume: Optional[dict] = None       # 断点续跑：{gid: start}


@app.post("/api/scan/jobs")
async def create_scan_job(body: ScanIn):
    """启动一个全量扫描任务（后台运行，立即返回 job_id）

    - mode=posts    : 只扫发帖（评论接口不请求，最稳、最快）
    - mode=comments : 只扫回帖（评论接口易触发反爬 1000，会退避重试）
    - mode=both     : 两者都扫

    max_topics_per_group=0 表示不限（扫到最早一条），建议先用 500 试跑。
    返回 estimate 为请求数 / 耗时预估。
    """
    if not body.groups:
        return {"ok": False, "detail": "请提供至少一个小组 id（groups）"}
    # 回帖（评论）接口需要登录态：没凭证时直接拦下，别让任务空跑一堆 403
    if body.mode in ("comments", "both") and not (_c().token or _c().cookie):
        return {"ok": False, "need_login": True,
                "detail": "豆瓣的「回帖/评论」接口已改为必须登录才能查看"
                          "（匿名会返回 403 need_permission）。"
                          "请先点右上角「登录」用豆瓣账号密码登录（或粘贴 Bearer token），"
                          "再启动回帖扫描。只扫发帖（posts）模式不受影响。"}
    try:
        job = await scanner.create_job(
            body.user_id, body.user_name or "", body.groups,
            body.mode, body.max_topics_per_group, body.resume,
            client_override=_c(),      # 云端多用户：任务用发起者的凭证跑
        )
    except ValueError as e:
        return {"ok": False, "detail": str(e)}
    return {"ok": True, "job_id": job.id,
            "estimate": scanner.estimate_requests(body.groups, body.mode,
                                                  body.max_topics_per_group),
            "job": job.to_dict(limit=200)}


@app.get("/api/scan/jobs")
async def list_scan_jobs():
    return {"ok": True, "jobs": [j.to_dict(limit=0) for j in scanner.list_jobs()]}


@app.get("/api/scan/jobs/{job_id}")
async def get_scan_job(job_id: str, limit: int = Query(500, ge=0, le=5000)):
    j = scanner.get_job(job_id)
    if not j:
        raise HTTPException(404, "任务不存在")
    d = j.to_dict(limit=limit)
    # 附带耗时与速率
    st, now = j.progress.get("started_at"), time.time()
    d["elapsed_seconds"] = int((j.progress.get("finished_at") or now) - st) if st else 0
    d["topics_per_minute"] = (round(j.progress["scanned_topics"] / ((now - st) / 60), 1)
                              if st and now > st else 0)
    return d


@app.post("/api/scan/jobs/{job_id}/cancel")
async def cancel_scan_job(job_id: str):
    """取消任务（保留断点，可用 resume 继续）"""
    if not scanner.cancel_job(job_id):
        raise HTTPException(404, "任务不存在")
    return {"ok": True, "message": "已请求取消，进度与断点已保留"}


@app.delete("/api/scan/jobs/{job_id}")
async def delete_scan_job(job_id: str):
    if not scanner.delete_job(job_id):
        raise HTTPException(404, "任务不存在")
    return {"ok": True}


@app.get("/api/user/{user_id}/interests", response_model=Resp)
async def get_user_interests(
    user_id: str,
    start: int = 0,
    count: int = Query(20, le=50),
):
    """用户标记的影视 / 书 / 音乐（真实数据，**他人也可查**）

    这是目前唯一能拿到的「他人内容足迹」：TA 看过/想看的作品、评分和短评。
    """
    try:
        d = await _c().get_user_interests(user_id, start, count)
    except DoubanError as e:
        raise _err(e)
    items = []
    for x in d.get("interests", []):
        subj = x.get("subject") or {}
        rating = x.get("rating") or {}
        items.append({
            "id": x.get("id"),
            "create_time": x.get("create_time"),
            "comment": x.get("comment", ""),
            "rating": rating.get("value") if isinstance(rating, dict) else None,
            "status": x.get("status"),
            "subject_title": subj.get("title"),
            "subject_year": subj.get("year"),
            "subject_type": subj.get("type"),
            "subject_id": subj.get("id"),
            "subject_cover": (subj.get("pic") or {}).get("normal") if isinstance(subj.get("pic"), dict) else None,
            "url": x.get("sharing_url"),
        })
    return Resp(ok=True, source="douban",
                data={"interests": items, "total": d.get("total", len(items)), "start": start})


@app.get("/api/search/group", response_model=Resp)
async def search_group(
    q: str = Query(..., min_length=1, description="小组关键词"),
    start: int = 0,
    count: int = Query(20, le=50),
):
    """按关键词搜索小组（真实数据），用于找到候选小组 id 供反查使用

    返回 items[] 嵌套结构里 layout=group 的项，取 target 才是真正的 group object。
    """
    try:
        d = await _c().search_groups(q, start, count)
    except DoubanError as e:
        raise _err(e)
    groups: list[dict] = []
    for it in d.get("items", []):
        if it.get("layout") != "group":
            continue
        t = it.get("target") or {}
        groups.append({
            "id": str(t.get("id")),
            "name": t.get("name"),
            "member_count": t.get("member_count"),
            "desc": t.get("desc") or "",
            "url": t.get("url"),
        })
    return Resp(ok=True, source="douban",
                data={"groups": groups, "total": d.get("total", len(groups)), "q": q})


@app.get("/api/search/user", response_model=Resp)
async def search_user(
    q: str = Query(..., min_length=1, description="用户名/uid 关键词（手机端友好）"),
    start: int = 0,
    count: int = Query(10, le=20),
):
    """按用户名/uid 搜索用户（真实数据，公开）

    手机端友好：用户输入"昵称"自动找到对应的数字 uid，再点击进入查询。
    """
    try:
        d = await _c().search_users(q, start, count)
    except DoubanError as e:
        raise _err(e)
    users: list[dict] = []
    for it in d.get("items", []):
        if it.get("layout") != "user":
            continue
        t = it.get("target") or {}
        users.append({
            "id": str(t.get("id") or t.get("uid")),
            "uid": t.get("uid"),
            "name": t.get("name"),
            "avatar": t.get("avatar"),
            "signature": t.get("abstract") or t.get("signature", ""),
            "loc": (t.get("loc") or {}).get("name") if isinstance(t.get("loc"), dict) else t.get("loc"),
            "url": t.get("url"),
        })
    return Resp(ok=True, source="douban",
                data={"users": users, "total": d.get("total", len(users)), "q": q})


@app.get("/api/group/{group_id}/members", response_model=Resp)
async def get_group_members(
    group_id: str,
    start: int = 0,
    count: int = Query(20, le=50),
):
    """小组成员列表（真实数据，公开）

    注意：豆瓣不支持按用户过滤，且不支持深翻页（大组百万成员），
    因此无法用于「判断某用户是否在组内」。
    """
    try:
        d = await _c().get_group_members(group_id, start, count)
    except DoubanError as e:
        raise _err(e)
    members = [{
        "id": str(m.get("id")),
        "name": m.get("name"),
        "avatar": m.get("avatar"),
        "loc": (m.get("loc") or {}).get("name") if isinstance(m.get("loc"), dict) else None,
        "reg_time": m.get("reg_time"),
        "url": m.get("url"),
    } for m in d.get("members", [])]
    return Resp(ok=True, source="douban",
                data={"members": members, "total": d.get("total", len(members)),
                      "owner_id": d.get("owner_id")})


@app.get("/api/user/{user_id}/comments-in-groups", response_model=Resp)
async def reverse_lookup_comments(
    user_id: str,
    gids: str = Query("", alias="groups",
                      description="候选小组 id，逗号分隔，如 146409,736218（最多 8 个）"),
    topic_pages: int = Query(2, ge=1, le=5, description="每个小组扫描的话题页数（每页 50 条）"),
    comments_per_topic: int = Query(20, ge=5, le=50, description="每条话题最多扫多少条评论"),
):
    """【反查他人回帖】扫描指定小组近期话题的评论区，过滤出该用户的回帖

    与 posts-in-groups 的区别：
      * posts-in-groups → 找 TA 的发帖（话题首帖）
      * comments-in-groups → 找 TA 的回帖（评论）

    ⚠️ 评论接口需要登录：豆瓣已把评论/回帖接口改成必须登录才能查看，
       匿名请求会得到 403 need_permission。此时本接口返回
       source=restricted + data.need_login=true，前端会提示先登录。
       这不是「反爬/机器人」提示 —— 登录后再查即可正常。

    限制：覆盖范围 = 候选小组最近 N 个话题 × 每话题前 M 条评论。
    """
    group_ids = [g for g in (gids or "").replace("，", ",").split(",") if g.strip()]
    if not group_ids:
        return Resp(ok=False, source="none",
                    message="请通过 groups 参数提供候选小组 id（逗号分隔），"
                            "可先用 /api/search/group?q=关键词 搜索小组")
    try:
        result = await _c().scan_author_comments_in_groups(
            user_id, group_ids, topic_pages, comments_per_topic,
        )
    except DoubanError as e:
        if e.need_login:
            return Resp(ok=True, source="restricted",
                        data={"comments": [], "total": 0, "need_login": True},
                        message=e.msg)
        raise _err(e)
    comments = result.get("comments", []) if isinstance(result, dict) else result
    payload = {
        "comments": comments,
        "total": len(comments),
        "need_login": bool(result.get("need_login")) if isinstance(result, dict) else False,
        "scanned_groups": group_ids,
        "note": f"已扫描 {len(group_ids)} 个小组 × {topic_pages} 页话题 × 每条 {comments_per_topic} 条评论，"
                f"命中的是 TA 在这批话题里的近期回帖（非全量历史）",
    }
    if isinstance(result, dict) and result.get("warning"):
        payload["warning"] = result["warning"]
        payload["blocked_groups"] = result.get("blocked_groups", [])
    if payload["need_login"]:
        return Resp(ok=True, source="restricted", data=payload,
                    message=result.get("reason") or "该查询需要登录豆瓣账号")
    return Resp(ok=True, source="douban", data=payload)


@app.get("/api/user/{user_id}/comments", response_model=Resp)
async def get_user_comments(user_id: str):
    """用户评论记录 —— 详见 /api/user/{user_id}/comments-in-groups 真实反查接口"""
    return Resp(ok=True, source="restricted", data={"comments": []},
                message="豆瓣未开放「任意用户的评论列表」接口，不做模拟数据。"
                        "请用 /api/user/{user_id}/comments-in-groups?groups=小组id 真实反查")


@app.get("/api/group/{group_id}", response_model=Resp)
async def get_group(group_id: str):
    """小组详情（真实数据，无需登录）"""
    try:
        g = await _c().get_group(group_id)
    except DoubanError as e:
        raise _err(e)
    return Resp(ok=True, source="douban", data={
        "id": g.get("id"),
        "name": g.get("name"),
        "member_count": g.get("member_count"),
        "create_time": g.get("create_time"),
        "desc": g.get("desc", ""),
        "avatar": g.get("avatar"),
        "url": g.get("url"),
    })


@app.get("/api/group/{group_id}/topics", response_model=Resp)
async def get_group_topics(group_id: str, start: int = 0, count: int = Query(20, le=50)):
    """小组近期话题列表（真实数据，无需登录）"""
    try:
        t = await _c().get_group_topics(group_id, start, count)
    except DoubanError as e:
        raise _err(e)
    topics = [{
        "topic_id": x.get("id"),
        "title": x.get("title"),
        "abstract": x.get("abstract", ""),
        "create_time": x.get("create_time"),
        "author_name": (x.get("author") or {}).get("name"),
        "author_id": (x.get("author") or {}).get("id"),
        "comments_count": x.get("comments_count", 0),
        "likes_count": x.get("likes_count", 0),
    } for x in t.get("topics", [])]
    return Resp(ok=True, source="douban", data={"topics": topics, "total": t.get("total")})


# --------------------------------------------------------------------
# 手动补充记录（notes）
# --------------------------------------------------------------------
def _note_scope() -> str:
    """补充记录的分区。

    默认固定为 "global"：**连到同一个后端的所有人共享同一份补充记录**。
    这样你补充的内容，别人查同一个 uid 时也能看到（查人时别人的线索同样有用）。
    每条记录带 by 字段标记来源（匿名本机标识），只用来显示"我 / 别人"。
    """
    return "global"


class NoteIn(BaseModel):
    kind: str = "text"                    # url | image | text
    url: Optional[str] = None
    title: Optional[str] = None
    text: Optional[str] = None
    image: Optional[str] = None           # data:image/...;base64,...
    tags: Optional[list[str] | str] = None
    by: Optional[str] = None                  # 补充者的匿名本机标识（区分「我 / 他人」）
    user_name: Optional[str] = None
    topic_id: Optional[str] = None
    group_id: Optional[str] = None
    group_name: Optional[str] = None


@app.get("/api/notes/{user_id}", response_model=Resp)
async def get_notes(user_id: str = Path(...)):
    """取某个 uid 的手动补充记录"""
    return Resp(ok=True, source="local", data=notes.list_notes(_note_scope(), user_id))


@app.post("/api/notes/{user_id}", response_model=Resp)
async def create_note(body: NoteIn, user_id: str = Path(...)):
    """新增一条补充记录（同一个帖子网址会合并更新，不重复添加）"""
    try:
        data = notes.add_note(_note_scope(), user_id, body.model_dump(), body.user_name or "")
    except Exception as e:  # noqa: BLE001
        return Resp(ok=False, source="local", data=None, message=f"保存失败：{e}")
    return Resp(ok=True, source="local", data=data,
                message=f"已保存，该 uid 现有 {data.get('total', 0)} 条补充记录")


@app.delete("/api/notes/{user_id}/{item_id}", response_model=Resp)
async def remove_note(user_id: str = Path(...), item_id: str = Path(...)):
    """删除一条补充记录"""
    return Resp(ok=True, source="local",
                data=notes.delete_note(_note_scope(), user_id, item_id))


@app.post("/api/notes/{user_id}/pin", response_model=Resp)
async def pin_note(body: dict, user_id: str = Path(...)):
    """置顶 / 取消置顶一条补充记录"""
    item_id = str(body.get("item_id") or "")
    return Resp(ok=True, source="local", data=notes.toggle_pin(_note_scope(), user_id, item_id))


@app.get("/api/notes-library", response_model=Resp)
async def notes_library(q: str = "", limit: int = 100):
    """补充记录库：所有用户手动补充过的 id 记录，支持按 id / 关键词搜索

    q 为空 → 全部（按最近更新排序）；纯数字 → 按 uid 匹配；其它 → 关键词匹配。
    列表里不含截图 base64（只有 has_image 标记），要看图请点进该 id。
    """
    return Resp(ok=True, source="local", data=notes.library(_note_scope(), q, limit))


@app.get("/api/notes/export/all", response_model=Resp)
async def export_notes():
    """导出全部补充记录（备份 / 换设备用）"""
    return Resp(ok=True, source="local", data=notes.export_all(_note_scope()))


@app.post("/api/notes/import/all", response_model=Resp)
async def import_notes(payload: dict):
    """导入备份（覆盖当前分区）"""
    try:
        out = notes.import_all(_note_scope(), payload or {})
    except ValueError as e:
        return Resp(ok=False, source="local", data=None, message=str(e))
    return Resp(ok=True, source="local", data=out, message="导入完成")


# --------------------------------------------------------------------
# 话题详情（供「粘贴帖子网址 → 自动带出标题」使用）
# --------------------------------------------------------------------
@app.get("/api/topic/{topic_id}", response_model=Resp)
async def get_topic(topic_id: str = Path(...)):
    """按话题 id 取详情（标题 / 摘要 / 所属小组 / 作者 / 时间）

    用途：补充记录里粘贴帖子网址时自动带出标题，省得手打。
    注意：豆瓣该接口同样需要登录态，未登录时返回 restricted。
    """
    tid = re.sub(r"\D", "", topic_id or "")
    if not tid:
        return Resp(ok=False, source="none", message="话题 id 无效")
    try:
        t = await _c().get_topic(tid)
    except DoubanError as e:
        if e.need_login:
            return Resp(ok=True, source="restricted",
                        data={"topic_id": tid, "need_login": True},
                        message="话题详情需要登录态，可手动填写标题")
        raise _err(e)
    g = t.get("group") or {}
    a = t.get("author") or {}
    return Resp(ok=True, source="douban", data={
        "topic_id": t.get("id") or tid,
        "title": t.get("title") or "",
        "abstract": t.get("abstract") or "",
        "create_time": t.get("create_time") or "",
        "group_id": g.get("id") or "",
        "group_name": g.get("name") or "",
        "author_id": a.get("id") or "",
        "author_name": a.get("name") or "",
        "comments_count": t.get("comments_count", 0),
        "likes_count": t.get("likes_count", 0),
        "url": t.get("sharing_url") or t.get("url")
                 or f"https://www.douban.com/group/topic/{tid}/",
    })


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
