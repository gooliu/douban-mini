# -*- coding: utf-8 -*-
"""
一键发布到 GitHub：建仓库 → 推送代码 → 开启 Pages → 等部署 → 验证网址

用法（在 douban-mini 目录下）：
    python tools/publish_github.py --token ghp_xxx
    GH_TOKEN=ghp_xxx python tools/publish_github.py
    python tools/publish_github.py            # 交互输入 token（不回显）

只依赖 Python 标准库。
"""
import argparse
import getpass
import json
import os
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.request

PROXY = os.environ.get("HTTPS_PROXY") or "http://127.0.0.1:7890"
API = "https://api.github.com"
GIT = r"C:\Program Files\Git\cmd\git.exe"
REPO_NAME = "douban-mini"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_state = {}


def ok(msg):
    print("[OK] " + msg, flush=True)


def info(msg):
    print("     " + msg, flush=True)


def warn(msg):
    print("[!!] " + msg, flush=True)


def fail(msg):
    print("[XX] " + msg, flush=True)
    sys.exit(1)


def opener():
    handler = urllib.request.ProxyHandler({"http": PROXY, "https": PROXY})
    return urllib.request.build_opener(handler)


def api(method, path, token, data=None):
    url = path if path.startswith("http") else API + path
    body = json.dumps(data).encode("utf-8") if data is not None else None
    req = urllib.request.Request(url, data=body, method=method, headers={
        "Authorization": "Bearer " + token,
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "douban-mini-publish",
        "Content-Type": "application/json",
    })
    try:
        with opener().open(req, timeout=40) as r:
            t = r.read().decode("utf-8", "ignore")
            return r.status, (json.loads(t) if t.strip() else {})
    except urllib.error.HTTPError as e:
        t = e.read().decode("utf-8", "ignore")
        try:
            return e.code, json.loads(t)
        except Exception:
            return e.code, {"message": t[:400]}
    except Exception as e:
        return 0, {"message": "网络错误: %s" % e}


def wait_proxy():
    for _ in range(12):
        code, _ = api("GET", "/rate_limit", "x")
        if code:
            return
        time.sleep(2)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--token", default="")
    ap.add_argument("--private", action="store_true", help="创建为私有仓库")
    ap.add_argument("--skip-pages", action="store_true")
    args = ap.parse_args()

    token = args.token or os.environ.get("GH_TOKEN") or ""
    if not token:
        print("请输入 GitHub Personal Access Token（输入不回显，粘贴后回车）:")
        token = getpass.getpass("Token: ").strip()
    if not token:
        fail("没有 token")

    print("\n================ 0. 连通性检查 ================")
    info("代理: " + PROXY)
    wait_proxy()

    print("\n================ 1. 验证 token ================")
    code, data = api("GET", "/user", token)
    if code != 200:
        fail("token 无效或网络不通 (HTTP %s): %s" % (code, data.get("message")))
    login = data["login"]
    ok("已登录 GitHub 账号: %s" % login)

    full = "%s/%s" % (login, REPO_NAME)

    print("\n================ 2. 准备仓库 ================")
    code, repo = api("GET", "/repos/" + full, token)
    if code == 200:
        ok("仓库已存在: https://github.com/%s" % full)
        if repo.get("private") != args.private:
            want = "私有" if args.private else "公开"
            code2, r2 = api("PATCH", "/repos/" + full, token, {"private": args.private})
            if code2 == 200:
                ok("已调整为%s仓库（Pages 需要公开仓库才能免费开启）" % want)
            else:
                warn("修改可见性失败: %s" % r2.get("message"))
    elif code == 404:
        code2, r2 = api("POST", "/user/repos", token, {
            "name": REPO_NAME,
            "description": "豆瓣小组查询工具：查用户加入的小组、发帖与回帖历史",
            "private": args.private,
            "auto_init": False,
            "has_issues": True,
            "has_wiki": False,
        })
        if code2 not in (201, 200):
            fail("创建仓库失败 (HTTP %s): %s" % (code2, r2.get("message")))
        ok("仓库已创建: https://github.com/%s" % full)
    else:
        fail("查询仓库失败 (HTTP %s): %s" % (code, repo.get("message")))

    print("\n================ 3. 推送代码 ================")
    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["HTTPS_PROXY"] = env["HTTP_PROXY"] = PROXY

    def run(args_list, **kw):
        return subprocess.run([GIT] + args_list, cwd=ROOT, env=env,
                              capture_output=True, text=True, encoding="utf-8",
                              errors="replace", **kw)

    r = run(["rev-parse", "--is-inside-work-tree"])
    if r.returncode != 0:
        r = run(["init"])
        run(["add", "-A"])
        run(["-c", "user.name=" + login, "-c", "user.email=%s@users.noreply.github.com" % login,
             "commit", "-m", "豆瓣小组查询工具"])
    branch = run(["rev-parse", "--abbrev-ref", "HEAD"]).stdout.strip() or "main"
    if branch != "main":
        run(["branch", "-M", "main"])
        branch = "main"

    run(["remote", "remove", "origin"])
    run(["remote", "add", "origin", "https://github.com/%s.git" % full])
    run(["config", "http.proxy", PROXY])

    # 用临时 URL 推送，token 不会留在 .git/config 里
    push_url = "https://%s:%s@github.com/%s.git" % (login, token, full)
    r = run(["push", "-u", "origin", push_url, "main"])
    if r.returncode != 0:
        out = (r.stdout or "") + (r.stderr or "")
        if "non-fast-forward" in out or "fetch first" in out:
            warn("远端已有历史，尝试覆盖推送...")
            run(["push", "-u", "origin", "--force", push_url, "main"])
            out = (run(["push", "-u", "origin", push_url, "main"]).stderr or "")
        else:
            fail("推送失败:\n" + out[-1200:])
    ok("代码已推送到 main 分支")
    _state["run"] = time.time()

    if args.skip_pages:
        print("\n已完成推送（跳过 Pages）")
        return

    print("\n================ 4. 开启 GitHub Pages ================")
    code, pg = api("GET", "/repos/%s/pages" % full, token)
    if code == 200:
        ok("Pages 已开启，当前地址: %s" % pg.get("html_url"))
    else:
        code2, r2 = api("POST", "/repos/%s/pages" % full, token, {"build_type": "workflow"})
        if code2 in (201, 200):
            ok("已开启 Pages（Source = GitHub Actions）")
        else:
            msg = r2.get("message", "")
            if "already" in str(msg).lower() or code2 == 409:
                warn("Pages 可能已开启: %s" % msg)
            else:
                warn("开启 Pages 失败 (HTTP %s): %s" % (code2, msg))
                info("可稍后在仓库 Settings → Pages 里手动确认")

    print("\n================ 5. 等待部署 ================")
    deadline = time.time() + 300
    run_info = None
    while time.time() < deadline:
        code, runs = api("GET", "/repos/%s/actions/runs?per_page=8" % full, token)
        if code == 200:
            for it in runs.get("workflow_runs", []):
                if "pages" in (it.get("name") or "").lower():
                    run_info = it
                    break
        if run_info:
            status = run_info.get("status")
            info("工作流状态: %s" % status)
            if status == "completed":
                break
            if status != "in_progress" and run_info.get("created_at") and \
                    time.time() - _state.get("run", 0) > 240:
                break
        time.sleep(10)

    if not run_info:
        warn("没等到 Actions 运行记录，稍后可在仓库 Actions 页查看")
    else:
        concl = run_info.get("conclusion")
        info("工作流结论: %s" % concl)
        if concl == "success":
            ok("部署成功")
        else:
            warn("部署结论为 %s，可在 %s 查看日志" %
                 (concl, run_info.get("html_url")))

    print("\n================ 6. 最终网址验证 ================")
    page_url = None
    for _ in range(30):
        code, pg = api("GET", "/repos/%s/pages" % full, token)
        if code == 200 and pg.get("html_url"):
            page_url = pg["html_url"]
            break
        time.sleep(6)
    if not page_url:
        warn("没能读到 Pages 地址，请手动访问 https://%s.github.io/%s/" % (login, REPO_NAME))
        return

    ok("Pages 地址: %s" % page_url)
    for attempt in range(10):
        req = urllib.request.Request(page_url, headers={"User-Agent": "Mozilla/5.0"})
        try:
            with opener().open(req, timeout=30) as r:
                body = r.read().decode("utf-8", "ignore")
                code = r.status
        except urllib.error.HTTPError as e:
            code, body = e.code, e.read().decode("utf-8", "ignore")
        except Exception as e:
            code, body = 0, str(e)
        good = code == 200 and "豆瓣" in body
        info("第 %d 次检查: HTTP %s%s" % (attempt + 1, code,
             "（含豆瓣小组查询页面内容 ✓）" if good else ""))
        if good:
            ok("最终网址可以正常打开")
            print("\n" + "=" * 46)
            print(" 仓库: https://github.com/%s" % full)
            print(" 网址: %s" % page_url)
            print("=" * 46)
            return
        time.sleep(10)
    warn("网址暂时未返回正常页面，可能还在部署，稍后再访问即可")


if __name__ == "__main__":
    main()