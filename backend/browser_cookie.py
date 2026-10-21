"""从本机浏览器里读出豆瓣的登录 Cookie（绕开 HttpOnly）

背景
----
豆瓣的登录标识 `dbcl2` 是 **HttpOnly** 的：浏览器里的 JavaScript
（包括小书签 `javascript:` 脚本、`document.cookie`）**根本读不到**它。
所以「点书签自动带回 Cookie」这条路在手机上注定失败——不是用户操作问题。

但是：浏览器自己把 Cookie 存在本机的 SQLite 数据库里。后端跑在同一台机器上时，
可以直接读这个数据库并把加密值解开，于是用户只要在浏览器里登录过豆瓣，
点一下按钮就能把登录态交给本工具，不需要抓包、不需要 F12。

安全边界
--------
* 只在 **Windows + Chromium（Chrome / Edge / Brave / 360 等）** 上实现；
* 只读豆瓣（`host_key` 含 douban）的 `dbcl2 / ck / bid` 三项，其它一律不碰；
* 不修改浏览器数据：先把 Cookies 库复制到临时目录再读，避免占用/锁库；
* 解密依赖 Windows DPAPI（当前登录用户自己的密钥），换用户/换机器解不开。
"""

from __future__ import annotations

import base64
import json
import os
import shutil
import sqlite3
import tempfile
from typing import Any

WANTED = ("dbcl2", "ck", "bid")     # dbcl2=登录标识，ck=CSRF，bid=设备标识


# ---------------------------------------------------------------- 解密工具
def _dpapi(blob: bytes) -> bytes:
    """Windows DPAPI 解密（CryptUnprotectData），不需要任何第三方库"""
    import ctypes
    import ctypes.wintypes as wt

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", wt.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    buf = ctypes.create_string_buffer(blob)
    inp = DATA_BLOB(len(blob), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    out = DATA_BLOB()
    ok = ctypes.windll.crypt32.CryptUnprotectData(
        ctypes.byref(inp), None, None, None, None, 0, ctypes.byref(out))
    if not ok:
        raise OSError("CryptUnprotectData 失败")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(out.pbData)


def _master_key(user_data_dir: str) -> bytes | None:
    """Chromium 的 AES 主密钥（Local State 里用 DPAPI 包着）"""
    p = os.path.join(user_data_dir, "Local State")
    if not os.path.exists(p):
        return None
    try:
        with open(p, "r", encoding="utf-8") as f:
            ek = (json.load(f).get("os_crypt") or {}).get("encrypted_key") or ""
        blob = base64.b64decode(ek)
        if blob[:5] != b"DPAPI":
            return None
        return _dpapi(blob[5:])
    except Exception:
        return None


def _decrypt_value(raw: bytes, key: bytes | None) -> str:
    """解一条 cookie 值：v10 = AES-256-GCM，老版本 = 直接 DPAPI"""
    if not raw:
        return ""
    if raw[:3] in (b"v10", b"v11", b"v20") and key:
        try:
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM
            nonce, ct = raw[3:15], raw[15:]
            return AESGCM(key).decrypt(nonce, ct, None).decode("utf-8", "replace")
        except Exception:
            return ""
    try:
        return _dpapi(raw).decode("utf-8", "replace")
    except Exception:
        return ""


# ---------------------------------------------------------------- 定位浏览器
def _browser_dirs() -> list[tuple[str, str]]:
    """(用户数据目录, 浏览器名) —— 常见浏览器 + 自动扫一遍国产 / 小众 Chromium"""
    la = os.environ.get("LOCALAPPDATA", "")
    ra = os.environ.get("APPDATA", "")
    named = [
        (os.path.join(la, "Google", "Chrome", "User Data"), "Chrome"),
        (os.path.join(la, "Microsoft", "Edge", "User Data"), "Edge"),
        (os.path.join(la, "BraveSoftware", "Brave-Browser", "User Data"), "Brave"),
        (os.path.join(la, "Chromium", "User Data"), "Chromium"),
        (os.path.join(ra, "360Chrome", "Chrome", "User Data"), "360浏览器"),
        (os.path.join(la, "Yandex", "YandexBrowser", "User Data"), "Yandex"),
    ]
    out: list[tuple[str, str]] = [(d, n) for d, n in named if d and os.path.isdir(d)]
    seen = {d.lower() for d, _ in out}

    # 兜底：不知道用户装了什么浏览器，就把常见位置里带 "User Data" 的目录都扫一遍
    for root in (la, ra):
        if not root or not os.path.isdir(root):
            continue
        try:
            tops = os.listdir(root)
        except Exception:
            continue
        for t in tops:
            p1 = os.path.join(root, t)
            if not os.path.isdir(p1):
                continue
            for rel in (os.path.join(p1, "User Data"), os.path.join(p1, "Chrome", "User Data")):
                if (os.path.isdir(rel) and rel.lower() not in seen
                        and os.path.exists(os.path.join(rel, "Local State"))):
                    seen.add(rel.lower())
                    out.append((rel, t))
                    break
            # 再往下钻一层（如 Microsoft\Edge\User Data、Tencent\QQBrowser\User Data）
            try:
                subs = os.listdir(p1)
            except Exception:
                continue
            for s in subs[:40]:
                p2 = os.path.join(p1, s, "User Data")
                if (os.path.isdir(p2) and p2.lower() not in seen
                        and os.path.exists(os.path.join(p2, "Local State"))):
                    seen.add(p2.lower())
                    out.append((p2, s))
    return out


def _profiles(user_data_dir: str) -> list[str]:
    """列出所有 profile 目录（Default / Profile 1 / ...）"""
    out = []
    try:
        for name in os.listdir(user_data_dir):
            p = os.path.join(user_data_dir, name)
            if os.path.isdir(p) and os.path.exists(os.path.join(p, "Cookies")):
                out.append(p)
    except Exception:
        pass
    return out


# ---------------------------------------------------------------- 主入口
def read_douban_cookie() -> dict[str, Any]:
    """读出本机浏览器里的豆瓣登录 Cookie

    返回 {"ok": bool, "cookie": str, "browser": str, "profile": str,
          "found": [...], "detail": str}
    """
    if os.name != "nt":
        return {"ok": False, "detail": "这个功能只在 Windows 上能用（读本机浏览器的 Cookie 库）"}

    dirs = _browser_dirs()
    if not dirs:
        return {"ok": False, "detail": "没在本机找到 Chrome / Edge 等 Chromium 浏览器"}

    scanned: list[str] = []
    for user_data, bname in dirs:
        key = _master_key(user_data)
        for prof in _profiles(user_data):
            scanned.append(f"{bname}/{os.path.basename(prof)}")
            got = _read_one(prof, key, bname)
            if got.get("cookie"):
                return got

    got = _read_firefox()
    if got.get("cookie"):
        return got
    if got.get("detail"):
        scanned.append("Firefox")

    # 扫到了浏览器，但里面没有豆瓣的登录态
    if scanned:
        detail = ("在 " + "、".join(scanned[:6]) + " 里没找到豆瓣的登录 Cookie。")
    else:
        detail = ("本机浏览器的 Cookie 库里没有豆瓣登录记录"
                  "（可能是这个浏览器还没登录过豆瓣，或数据被清理过）。")
    return {
        "ok": False,
        "detail": detail + "请用这个电脑上的浏览器打开 douban.com 登录一次，再回来点这个按钮。",
        "scanned": scanned,
    }


def _read_firefox() -> dict[str, Any]:
    """Firefox 的 cookie 是明文存的（cookies.sqlite），直接读即可"""
    ra = os.environ.get("APPDATA", "")
    base = os.path.join(ra, "Mozilla", "Firefox", "Profiles")
    if not ra or not os.path.isdir(base):
        return {"ok": False, "cookie": "", "detail": ""}
    for name in os.listdir(base):
        src = os.path.join(base, name, "cookies.sqlite")
        if not os.path.exists(src):
            continue
        tmp = None
        try:
            fd, tmp = tempfile.mkstemp(suffix=".sqlite")
            os.close(fd)
            shutil.copy2(src, tmp)
            con = sqlite3.connect(tmp)
            cur = con.cursor()
            try:
                cur.execute("SELECT name, value FROM moz_cookies WHERE host LIKE '%douban%'")
                rows = cur.fetchall()
            except sqlite3.Error:
                continue
            finally:
                con.close()
            vals = {str(n).strip().lower(): (v or "").strip()
                    for n, v in rows if str(n).strip().lower() in WANTED and v}
            if not vals.get("dbcl2"):
                continue
            d = vals["dbcl2"]
            if not d.startswith('"'):
                d = f'"{d}"'
            cookie = f'dbcl2={d}'
            for extra in ("ck", "bid"):
                if vals.get(extra):
                    cookie += f'; {extra}={vals[extra]}'
            return {"ok": True, "cookie": cookie, "browser": "Firefox",
                    "profile": name, "found": list(vals.keys()),
                    "detail": f"从 Firefox（{name}）读到豆瓣登录态"}
        except Exception:
            continue
        finally:
            if tmp:
                try:
                    os.remove(tmp)
                except Exception:
                    pass
    return {"ok": False, "cookie": "", "detail": "Firefox 里也没有"}


def _read_one(profile_dir: str, key: bytes | None, bname: str) -> dict[str, Any]:
    src = os.path.join(profile_dir, "Cookies")
    tmp = None
    try:
        fd, tmp = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        shutil.copy2(src, tmp)          # 复制一份，避免和正在运行的浏览器抢锁
        con = sqlite3.connect(tmp)
        cur = con.cursor()
        try:
            cur.execute(
                "SELECT name, value, encrypted_value, is_httponly FROM cookies "
                "WHERE host_key LIKE '%douban%'")
            rows = cur.fetchall()
        except sqlite3.Error:
            return {"ok": False, "cookie": "", "detail": ""}
        finally:
            con.close()

        vals: dict[str, str] = {}
        httponly: dict[str, int] = {}
        for name, value, enc, flag in rows:
            n = (name or "").strip().lower()
            if n not in WANTED:
                continue
            v = (value or "").strip()
            if not v:
                v = _decrypt_value(enc or b"", key).strip()
            if v:
                vals[n] = v
                httponly[n] = int(flag or 0)
        if "dbcl2" not in vals:
            return {"ok": False, "cookie": "", "browser": bname,
                    "profile": os.path.basename(profile_dir), "detail": ""}
        # dbcl2 原始值自带引号才合法：dbcl2="uid:hash"
        d = vals["dbcl2"]
        if not d.startswith('"'):
            d = f'"{d}"'
        cookie = f'dbcl2={d}'
        for extra in ("ck", "bid"):
            if vals.get(extra):
                cookie += f'; {extra}={vals[extra]}'
        return {"ok": True, "cookie": cookie, "browser": bname,
                "profile": os.path.basename(profile_dir),
                "found": list(vals.keys()), "httponly": httponly,
                "detail": f"从 {bname}（{os.path.basename(profile_dir)}）读到豆瓣登录态"}
    except Exception as e:
        return {"ok": False, "cookie": "", "browser": bname, "detail": str(e)}
    finally:
        if tmp:
            try:
                os.remove(tmp)
            except Exception:
                pass
