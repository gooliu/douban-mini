# -*- coding: utf-8 -*-
"""前端静态自检：onclick 引用的函数是否定义、$() 引用的 id 是否会被创建"""
import os
import re
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HTML = os.path.join(BASE, "web", "index.html")
html = open(HTML, encoding="utf-8").read()

script = "\n".join(re.findall(r"<script(?![^>]*src=)[^>]*>(.*?)</script>", html, re.S))
static_html = re.sub(r"<script.*?</script>", "", html, flags=re.S)

funcs = set(re.findall(r"function\s+([A-Za-z_$][\w$]*)", script))
funcs |= set(re.findall(r"(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?\(?"
                        r"[^=]*?=>", script))

# onclick / 字符串里的 onClick 调用
called = set(re.findall(r'on\w+="([A-Za-z_$][\w$]*)\(', html))
called |= set(re.findall(r"onclick=\\?'?\"?([A-Za-z_$][\w$]*)\(", script))

# JS 关键字 / 三元表达式会被正则误当成函数名，排除掉
KEYWORDS = {"if", "for", "while", "switch", "return", "function", "typeof",
            "catch", "new", "else", "do"}
missing_fn = sorted(c for c in called if c not in funcs and c not in KEYWORDS)

# 静态 HTML 里的 id + JS 模板字符串里生成的 id
ids = set(re.findall(r'id="([^"{}]+)"', html))
ids |= set(re.findall(r"id=\\\\?'?([A-Za-z0-9_-]+)", script))
ids |= set(re.findall(r"id=\\?'([A-Za-z0-9_-]+)'", script))
ids |= set(re.findall(r'id="([A-Za-z0-9_-]+)"', script))

used = set(re.findall(r"\$\('([^']+)'\)", script))
missing_id = sorted(u for u in used if u not in ids)

print("已定义函数数:", len(funcs))
print("被调用函数数:", len(called))
print()
print("[函数] 缺失:", missing_fn or "无 ✅")
print()
print("[$() id] 缺失:", missing_id or "无 ✅")
print()
# 新功能相关的关键元素
checks = [
    ("hiddenCheckPanelHtml", "function hiddenCheckPanelHtml" in script),
    ("toggleChkPanel", "function toggleChkPanel" in script),
    ("doCheckMembership", "function doCheckMembership" in script),
    ("renderChkResult", "function renderChkResult" in script),
    ("addChkFromInput", "function addChkFromInput" in script),
    ("removeChkGroup", "function removeChkGroup" in script),
    ("doChkSearchGroup", "function doChkSearchGroup" in script),
    ("pickChkGroup", "function pickChkGroup" in script),
    ("renderChkChips", "function renderChkChips" in script),
    ("toggleChkSearch", "function toggleChkSearch" in script),
    ("CSS .chk-panel", ".chk-panel {" in html),
    ("CSS .chk-badge.in", ".chk-badge.in {" in html),
    ("POST 调用 check-membership", "/api/check-membership" in script),
    ("restricted 时挂排查面板", "priv ? hiddenCheckPanelHtml() : lookupPanelHtml()" in script),
    ("列表页挂排查入口", "toggleChkPanel()" in script),
]
bad = [n for n, ok in checks if not ok]
for n, ok in checks:
    print(("  ✅ " if ok else "  ❌ ") + n)

print()
print("结果:", "全部通过 ✅" if not (missing_fn or missing_id or bad)
      else f"有问题: {missing_fn} {missing_id} {bad}")
sys.exit(1 if (missing_fn or missing_id or bad) else 0)
