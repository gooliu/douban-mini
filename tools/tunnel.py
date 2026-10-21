# -*- coding: utf-8 -*-
"""把本机的豆瓣查询后端暴露成公网 HTTPS 地址 —— 免注册、免账号、免 token。

内置三条通道，依次尝试：
  1. Cloudflare 官方免费隧道（最稳，首次会自动下载 cloudflared）
  2. localhost.run（SSH 反向隧道，用系统自带 OpenSSH，无需下载）
  3. serveo.net（同上，备用）

安全要点（重要）：
    隧道转发之后，所有外部请求到达后端时「源 IP 都是 127.0.0.1」。
    如果后端还按 IP 判断是不是本人，等于把全世界都认成你自己，
    你的豆瓣账号就会泄露给任何打开链接的人。
    所以本脚本会强制以 DOUBAN_PUBLIC=1 重启后端：
    不论来源一律要求访客自带凭证，凭证只存在他自己的浏览器里。
    你自己想免登录就用脚本生成的「专属链接」，凭证同样只进你自己的浏览器。

用法:
    python tools/tunnel.py              # 建隧道并打印地址
    python tools/tunnel.py --check      # 只检查已有隧道是否还活着
    python tools/tunnel.py --restore-local   # 把后端恢复为本机模式
"""
from __future__ import print_function

import argparse
import json
import os
import re
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND = os.path.join(ROOT, "backend")
BINDIR = os.path.join(ROOT, "tools", "bin")
LOG = os.path.join(ROOT, "tunnel.log")
CF_LOG = os.path.join(ROOT, "cf.log")
URL_FILE = os.path.join(ROOT, "tunnel_url.txt")
CONFIG = os.path.join(BACKEND, "douban_config.json")
LOCAL_HOST = "127.0.0.1"
PORT = int(os.getenv("DOUBAN_PORT", "8000"))
LOCAL = "%s:%d" % (LOCAL_HOST, PORT)

CF_URL = ("https://github.com/cloudflare/cloudflared/releases/latest/"
          "download/cloudflared-windows-amd64.exe")

PROVIDERS = ["cloudflare", "localhostrun", "serveo"]
SSH_HOSTS = {"localhostrun": ("localhost.run", "nokey"),
             "serveo": ("serveo.net", "nokey")}
URL_RE = re.compile(r"https?://[A-Za-z0-9][A-Za-z0-9._-]*(?::\d+)?")
CF_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")


def say(*a):
    print(*a)
    sys.stdout.flush()


def find_ssh():
    for p in (r"C:\Program Files\Git\usr\bin\ssh.exe",
              r"C:\Program Files\OpenSSH\ssh.exe",
              r"C:\Windows\System32\OpenSSH\ssh.exe"):
        if os.path.exists(p):
            return p
    from shutil import which
    return which("ssh") or "ssh"


def find_python():
    for p in (r"C:\Users\73117\.workbuddy\binaries\python\envs\default\Scripts\python.exe",
              r"C:\Users\73117\.workbuddy\binaries\python\versions\3.13.12\python.exe"):
        if os.path.exists(p):
            return p
    return sys.executable


def _http(url, timeout=8, use_proxy=False):
    """发请求。use_proxy=True 时走系统代理 —— 用于验证公网地址，
    因为本机直连的 DNS 常常解析不了隧道域名，但真实访客的网络可以。"""
    import urllib.request
    import urllib.error
    proxies = None if use_proxy else {}
    op = urllib.request.build_opener(urllib.request.ProxyHandler(proxies))
    try:
        r = op.open(urllib.request.Request(
            url, headers={"User-Agent": "Mozilla/5.0"}), timeout=timeout)
        raw = r.read().decode("utf-8", "ignore")
    except urllib.error.HTTPError as e:
        return e.code, {"raw": (e.read() or b"").decode("utf-8", "ignore")[:200]}
    except Exception as e:
        return 0, {"raw": str(e)}
    try:
        return r.status, json.loads(raw)
    except Exception:
        return r.status, {"raw": raw[:200]}


def health():
    code, data = _http("http://%s/api/health" % LOCAL, timeout=6)
    return data if code == 200 and isinstance(data, dict) and data.get("ok") else None


def kill_backend():
    try:
        out = subprocess.run(["netstat", "-ano"], capture_output=True,
                             text=True, encoding="utf-8", errors="replace").stdout
        pids = set()
        for line in out.splitlines():
            if ":%d" % PORT in line and "LISTENING" in line.upper():
                parts = line.split()
                if parts:
                    pids.add(parts[-1])
        for pid in pids:
            subprocess.run(["taskkill", "/F", "/PID", pid], capture_output=True)
        if pids:
            time.sleep(2)
    except Exception:
        pass


def _spawn(cmd, env):
    """启动脱离父进程的后台进程（脚本被终止时不会把它一起带走）"""
    logf = open(os.path.join(ROOT, "server.log"), "ab")
    kwargs = {"cwd": BACKEND, "env": env, "stdin": subprocess.DEVNULL,
              "stdout": logf, "stderr": subprocess.STDOUT}
    if os.name == "nt":
        kwargs["creationflags"] = (subprocess.DETACHED_PROCESS |
                                   subprocess.CREATE_NEW_PROCESS_GROUP)
    return subprocess.Popen(cmd, **kwargs)


def start_backend(public: bool):
    """启动后端。public=True 时强制要求每个访客自带凭证。"""
    env = dict(os.environ)
    if public:
        env["DOUBAN_PUBLIC"] = "1"
    else:
        env.pop("DOUBAN_PUBLIC", None)
    return _spawn([find_python(), "-m", "uvicorn", "main:app",
                   "--host", "0.0.0.0", "--port", str(PORT)], env)


def download_cloudflared():
    import urllib.request
    try:
        os.makedirs(BINDIR, exist_ok=True)
        req = urllib.request.Request(CF_URL, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=240) as r, \
                open(os.path.join(BINDIR, "cloudflared.exe"), "wb") as f:
            f.write(r.read())
        return True
    except Exception as e:
        say("    下载失败: %s" % e)
        return False


def start_cloudflare():
    cf = os.path.join(BINDIR, "cloudflared.exe")
    say("[..] 尝试 cloudflare (trycloudflare.com) ...")
    if not os.path.exists(cf) and not download_cloudflared():
        return None, None
    # 清掉上一轮残留的进程：脚本被强杀时，脱离父进程的隧道会留下来占着端口
    if os.name == "nt":
        subprocess.run(["taskkill", "/F", "/IM", "cloudflared.exe"],
                       capture_output=True)
        time.sleep(1)
    try:
        os.remove(CF_LOG)
    except OSError:
        pass
    env = dict(os.environ)
    env["NO_PROXY"] = "127.0.0.1,localhost"
    env["no_proxy"] = "127.0.0.1,localhost"
    try:
        with open(CF_LOG, "wb") as out:
            kwargs = {"cwd": ROOT, "env": env, "stdin": subprocess.DEVNULL,
                      "stdout": out, "stderr": subprocess.STDOUT}
            if os.name == "nt":
                # 脱离父进程：否则脚本被终止时隧道会一起断掉，表现为 502
                kwargs["creationflags"] = (subprocess.DETACHED_PROCESS |
                                           subprocess.CREATE_NEW_PROCESS_GROUP)
            p = subprocess.Popen(
                [cf, "tunnel", "--url", "http://%s" % LOCAL, "--no-autoupdate"],
                **kwargs)
    except Exception as e:
        say("[!!] 启动失败: %s" % e)
        return None, None

    for _ in range(40):
        time.sleep(1)
        if p.poll() is not None:
            break
        try:
            with open(CF_LOG, "r", encoding="utf-8", errors="replace") as f:
                txt = f.read()
        except Exception:
            continue
        m = CF_RE.search(txt)
        if m:
            say("[OK] 隧道已建立: %s" % m.group(0))
            return p, m.group(0)
    try:
        p.kill()
    except Exception:
        pass
    say("[!!] cloudflare 超时未返回地址")
    return None, None


def start_ssh(name):
    host, user = SSH_HOSTS[name]
    say("[..] 尝试 %s (%s) ..." % (name, host))
    try:
        os.remove(LOG)
    except OSError:
        pass
    cmd = [find_ssh(),
           "-o", "StrictHostKeyChecking=no",
           "-o", "UserKnownHostsFile=NUL",
           "-o", "ServerAliveInterval=30",
           "-o", "ServerAliveCountMax=3",
           "-o", "ExitOnForwardFailure=yes",
           "-o", "LogLevel=ERROR",
           "-R", "80:%s" % LOCAL,
           "%s@%s" % (user, host)]
    try:
        with open(LOG, "wb") as out:
            kwargs = {"cwd": ROOT, "stdin": subprocess.DEVNULL,
                      "stdout": out, "stderr": subprocess.STDOUT}
            if os.name == "nt":
                kwargs["creationflags"] = (subprocess.DETACHED_PROCESS |
                                           subprocess.CREATE_NEW_PROCESS_GROUP)
            p = subprocess.Popen(cmd, **kwargs)
    except Exception as e:
        say("[!!] 启动失败: %s" % e)
        return None, None

    for _ in range(30):
        time.sleep(1)
        if p.poll() is not None:
            break
        try:
            with open(LOG, "r", encoding="utf-8", errors="replace") as f:
                txt = f.read()
        except Exception:
            continue
        for m in URL_RE.finditer(txt):
            url = m.group(0).rstrip(".,;:)")
            if "lhr.life" in url or "localhost.run" in url or "serveo.net" in url:
                say("[OK] 隧道已建立: %s" % url)
                return p, url
        low = txt.lower()
        for kw in ("permission denied", "connection refused",
                   "could not resolve", "timed out", "connection closed"):
            if kw in low:
                say("[!!] %s 失败: %s" % (name, kw))
                try:
                    p.kill()
                except Exception:
                    pass
                return None, None
    try:
        p.kill()
    except Exception:
        pass
    say("[!!] %s 超时未返回地址" % name)
    return None, None


def start(name):
    return start_cloudflare() if name == "cloudflare" else start_ssh(name)


def read_local_credential():
    """读本机保存的凭证（用于生成只有你能用的免登录链接）"""
    code, data = _http("http://%s/api/local-credential" % LOCAL, timeout=6)
    if code == 200 and data.get("ok") and (data.get("token") or data.get("cookie")):
        return data
    try:
        with open(CONFIG, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        return {"ok": True, "token": cfg.get("token") or cfg.get("douban_token") or "",
                "cookie": cfg.get("cookie") or cfg.get("douban_cookie") or ""}
    except Exception:
        return {"ok": False}


def wait_backend(public: bool, tries=30):
    for _ in range(tries):
        time.sleep(1)
        h = health()
        if h and bool(h.get("public_mode")) == public:
            return True
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider", default="", choices=[""] + PROVIDERS)
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--restore-local", action="store_true",
                    help="把后端恢复为本机模式（直接关窗口没走收尾逻辑时用）")
    args = ap.parse_args()

    if args.check:
        if not os.path.exists(URL_FILE):
            say("没有正在运行的隧道")
            return 1
        url = open(URL_FILE, encoding="utf-8").read().strip()
        code, data = _http(url + "/api/health", timeout=25, use_proxy=True)
        say("%s -> HTTP %s | need_login=%s | self_uid=%s"
            % (url, code, data.get("need_login"), data.get("self_uid")))
        return 0 if code == 200 else 1

    if args.restore_local:
        kill_backend()
        start_backend(public=False)
        if wait_backend(False, 20):
            say("[OK] 后端已恢复本机模式（你自己访问免登录）")
            return 0
        say("[!!] 恢复失败，请双击「启动豆瓣查询.bat」")
        return 1

    h = health()
    if h and h.get("public_mode"):
        say("[OK] 后端已在公网隧道模式（强制凭证隔离）")
    else:
        say("[..] 正在以公网隧道模式启动后端 ...")
        kill_backend()
        start_backend(public=True)
        if wait_backend(True, 30):
            say("[OK] 后端已就绪（公网隧道模式，凭证强制隔离）")
        else:
            say("[!!] 后端启动失败，请查看 server.log")
            return 1

    proc = url = None
    for name in ([args.provider] if args.provider else PROVIDERS):
        proc, url = start(name)
        if url:
            break

    if not url:
        say("[!!] 所有隧道通道都失败了。请检查网络，或改用路由器端口映射：")
        say("    双击「开通公网访问.bat」")
        return 1

    say("[..] 从公网验证地址 ...")
    code, data = _http(url + "/api/health", timeout=30, use_proxy=True)
    if code == 200:
        say("[OK] 公网可访问！HTTP 200")
        say("     need_login = %s" % data.get("need_login"))
        say("     self_uid   = %s  (未登录时应为 None，即不泄露你的账号)"
            % data.get("self_uid"))
    else:
        say("[!!] 验证返回 HTTP %s：%s" % (code, data.get("raw", "")[:150]))

    cred = read_local_credential()
    mine = url
    if cred.get("ok") and (cred.get("token") or cred.get("cookie")):
        q = []
        if cred.get("token"):
            q.append("cred=" + cred["token"])
        if cred.get("cookie"):
            q.append("ck=" + cred["cookie"])
        mine = url + "/?" + "&".join(q)

    with open(URL_FILE, "w", encoding="utf-8") as f:
        f.write(url)

    say("")
    say("=" * 64)
    say("  发给别人（他们需要登录自己的豆瓣账号）:")
    say("  %s" % url)
    say("")
    if mine != url:
        say("  你自己用（打开一次即记住，之后用上面的地址也一样）:")
        say("  %s" % mine)
        say("")
    say("  注意：保持本窗口开着，隧道才一直有效；关掉即失效。")
    say("=" * 64)
    say("")
    say("  按 Ctrl+C 结束分享。")

    try:
        while proc and proc.poll() is None:
            time.sleep(5)
    except KeyboardInterrupt:
        pass
    finally:
        if proc:
            try:
                proc.kill()
            except Exception:
                pass
        # 恢复本机模式：隧道关掉后，你自己在本机访问不需要再登录
        say("[..] 隧道已关闭，正在把后端恢复为本机模式 ...")
        kill_backend()
        start_backend(public=False)
        if wait_backend(False, 20):
            say("[OK] 后端已恢复本机模式（你自己访问免登录）")
        else:
            say("[!!] 后端恢复失败，请双击「启动豆瓣查询.bat」")
    return 0


if __name__ == "__main__":
    sys.exit(main())
