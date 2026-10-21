# -*- coding: utf-8 -*-
"""
零基础一键发布：不需要懂 token。

运行后会在屏幕上给你一个网址和 8 位数字，
你在浏览器打开网址、输入数字、点授权，剩下的全自动完成：
建仓库 → 推代码 → 开启 Pages → 等构建 → 验证网址能不能打开。
"""
import json
import os
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from publish_github import run as publish_run, opener, api  # noqa: E402

# GitHub 官方命令行工具（GitHub CLI）的公开应用 ID，授权页显示的正是这个官方应用
CLIENT_ID = "Iv1.b507a08c87ecfe98"
SCOPE = "repo"

# 授权凭据缓存在系统临时目录（不进仓库），避免重复授权
TOKEN_FILE = os.path.join(tempfile.gettempdir(), "douban_gh_token")


def load_cached():
    try:
        with open(TOKEN_FILE, encoding="utf-8") as f:
            t = f.read().strip()
        return t if t.startswith(("ghp_", "gho_", "ghu_", "ghs_")) else ""
    except Exception:
        return ""


def save_cached(token):
    try:
        with open(TOKEN_FILE, "w", encoding="utf-8") as f:
            f.write(token)
    except Exception:
        pass


def post_form(url, data):
    body = urllib.parse.urlencode(data).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST", headers={
        "Accept": "application/json",
        "User-Agent": "douban-mini-publish",
        "Content-Type": "application/x-www-form-urlencoded",
    })
    with opener().open(req, timeout=40) as r:
        t = r.read().decode("utf-8", "ignore")
        return json.loads(t) if t.strip() else {}


def device_login():
    d = post_form("https://github.com/login/device/code",
                  {"client_id": CLIENT_ID, "scope": SCOPE})
    print("=" * 58, flush=True)
    print("  请在浏览器里做这三步：", flush=True)
    print("    1. 打开网址： " + d["verification_uri"], flush=True)
    print("    2. 输入这串数字： >>> %s <<<" % d["user_code"], flush=True)
    print("    3. 点绿色的 Authorize 按钮", flush=True)
    print("  授权页面显示的 app 名字是 GitHub CLI，是 GitHub 官方工具。", flush=True)
    print("=" * 58, flush=True)
    print("  等待你授权中（最多 15 分钟）...", flush=True)

    interval = int(d.get("interval", 5))
    deadline = time.time() + int(d.get("expires_in", 899))
    waited = 0
    while time.time() < deadline:
        time.sleep(interval)
        waited += interval
        try:
            r = post_form("https://github.com/login/oauth/access_token", {
                "client_id": CLIENT_ID,
                "device_code": d["device_code"],
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            })
        except Exception:
            continue
        if "access_token" in r:
            print("[OK] 授权成功，开始发布", flush=True)
            return r["access_token"]
        err = r.get("error")
        if err == "authorization_pending":
            if waited % 30 < interval:
                print("     还在等你点授权...（已等 %d 秒）" % waited, flush=True)
            continue
        if err == "slow_down":
            interval += 5
            continue
        print("[XX] 授权失败: %s" % (err or r), flush=True)
        sys.exit(1)
    print("[XX] 授权超时，请重新运行本脚本", flush=True)
    sys.exit(1)


if __name__ == "__main__":
    token = ""
    if "--reauth" not in sys.argv:
        cached = load_cached()
        if cached:
            code, data = api("GET", "/user", cached)
            if code == 200:
                print("[OK] 复用上次的授权（GitHub 账号: %s）" % data.get("login"), flush=True)
                token = cached
    if not token:
        token = device_login()
        save_cached(token)
    publish_run(token)
