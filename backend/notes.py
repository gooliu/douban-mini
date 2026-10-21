# -*- coding: utf-8 -*-
"""手动补充记录（notes）——「这个 ID 我还知道点别的」

场景：豆瓣只给得到公开接口拿到的东西，但使用者自己往往还掌握额外信息，例如
      * 对方在别处发过的帖子网址（微信/QQ 里转给你的）
      * 聊天记录或帖子的截图
      * 自己记下的关键词（"这个人收了定金不发货"）

      这些东西豆瓣 API 给不了，但对"判断这个人靠不靠谱"恰恰最有用。
      所以提供「补充记录」：按 uid 记下来，下次再查同一个 uid 时自动一起展示。

存储与隔离：
  * 数据落在 backend/data/notes.json，按「scope」分区：
      - 本机模式（自己一个人用）                → scope = "local"
      - 云端 / 公网共享模式且访问者带了凭证     → scope = "u_<凭证指纹>"
        这样多人共用一个后端时，各人的补充记录互不串、互相看不到。
  * 共享模式下若访问者没登录（无法区分是谁），后端拒绝写入，
    前端自动回落为「只存你自己浏览器 localStorage」。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Any, Optional

DATA_DIR = Path(__file__).parent / "data"
NOTES_FILE = DATA_DIR / "notes.json"

MAX_ITEMS_PER_USER = 200          # 单个 uid 最多保留多少条
MAX_TEXT_LEN = 4000               # 单条文本长度上限
MAX_IMAGE_BYTES = 3 * 1024 * 1024  # 单张图片 base64 上限（约 3MB）

# 用可重入锁：内部函数会在持锁状态下互相调用（曾经因为普通 Lock 自锁把接口挂死）
_lock = threading.RLock()


# ----------------------------------------------------------------------
# 基础读写
# ----------------------------------------------------------------------
def _read_all() -> dict:
    if not NOTES_FILE.exists():
        return {"version": 1, "scopes": {}}
    try:
        with NOTES_FILE.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except (ValueError, OSError):
        return {"version": 1, "scopes": {}}
    if not isinstance(data, dict):
        return {"version": 1, "scopes": {}}
    data.setdefault("version", 1)
    data.setdefault("scopes", {})
    return data


def _write_all(data: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = NOTES_FILE.with_suffix(".json.tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    tmp.replace(NOTES_FILE)


def scope_id(token: str = "", cookie: str = "") -> str:
    """根据凭证生成隔离分区 id（同一个人同一凭证 → 同一个分区）"""
    raw = f"{token or ''}|{cookie or ''}".strip("|")
    if not raw:
        return "local"
    return "u_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:14]


def _user_bucket(data: dict, scope: str, uid: str) -> dict:
    scopes = data.setdefault("scopes", {})
    sc = scopes.setdefault(scope, {"users": {}})
    users = sc.setdefault("users", {})
    return users.setdefault(str(uid), {"name": "", "updated_at": "", "items": []})


# ----------------------------------------------------------------------
# 数据清洗
# ----------------------------------------------------------------------
_TOPIC_RE = re.compile(r"(?:douban\.com)?/group/topic/(\d+)", re.I)
_GROUP_RE = re.compile(r"(?:douban\.com)?/group/(\d+)", re.I)
_URL_RE = re.compile(r"https?://[^\s，,；;）)】\]]+", re.I)


def parse_douban_url(text: str) -> dict:
    """从一段文本/网址里解析出 topic_id / group_id（支持 m./www. 和短链里的数字）"""
    out: dict[str, Any] = {"topic_id": "", "group_id": "", "url": ""}
    if not text:
        return out
    m = _TOPIC_RE.search(text)
    if m:
        out["topic_id"] = m.group(1)
    m = _GROUP_RE.search(text)
    if m:
        out["group_id"] = m.group(1)
    m = _URL_RE.search(text)
    if m:
        out["url"] = m.group(0).rstrip("。，.")
    return out


def clean_tags(raw: Any) -> list[str]:
    if isinstance(raw, str):
        parts = re.split(r"[,，;；\s]+", raw)
    elif isinstance(raw, list):
        parts = [str(x) for x in raw]
    else:
        parts = []
    seen, out = set(), []
    for p in parts:
        p = p.strip()
        if p and p not in seen and len(p) <= 16:
            seen.add(p)
            out.append(p)
        if len(out) >= 8:
            break
    return out


def _clean_item(raw: dict) -> dict:
    kind = (raw.get("kind") or "").strip().lower()
    if kind not in ("url", "image", "text"):
        kind = "text"
    text = str(raw.get("text") or "").strip()[:MAX_TEXT_LEN]
    title = str(raw.get("title") or "").strip()[:200]
    url = str(raw.get("url") or "").strip()[:600]

    parsed = parse_douban_url(url or text)
    topic_id = str(raw.get("topic_id") or parsed.get("topic_id") or "").strip()[:32]
    group_id = str(raw.get("group_id") or parsed.get("group_id") or "").strip()[:32]

    image = str(raw.get("image") or "").strip()
    if image and not image.startswith("data:image/"):
        image = ""
    if len(image) > MAX_IMAGE_BYTES:
        image = ""

    if kind == "url" and not url and parsed.get("url"):
        url = parsed["url"]

    return {
        "id": str(raw.get("id") or "").strip() or ("n" + hashlib.sha1(
            f"{time.time()}{text}{url}{title}".encode()).hexdigest()[:12]),
        "kind": kind,
        "url": url or (f"https://www.douban.com/group/topic/{topic_id}/" if topic_id else ""),
        "topic_id": topic_id,
        "group_id": group_id,
        "group_name": str(raw.get("group_name") or "").strip()[:60],
        "title": title,
        "text": text,
        "image": image,
        "tags": clean_tags(raw.get("tags")),
        "created_at": str(raw.get("created_at") or "").strip()
                      or time.strftime("%Y-%m-%d %H:%M:%S"),
        "pinned": bool(raw.get("pinned")),
        "source": str(raw.get("source") or "manual").strip()[:20] or "manual",
    }


# ----------------------------------------------------------------------
# 对外接口
# ----------------------------------------------------------------------
def _shape(bucket: dict, uid: str) -> dict:
    """把某个 uid 的 bucket 整理成前端要的结构（置顶在前，其余按时间倒序）"""
    items = list(bucket.get("items") or [])
    pinned = [x for x in items if x.get("pinned")]
    normal = sorted([x for x in items if not x.get("pinned")],
                    key=lambda x: x.get("created_at") or "", reverse=True)
    return {"uid": str(uid), "name": bucket.get("name") or "",
            "items": pinned + normal, "total": len(items)}


def list_notes(scope: str, uid: str) -> dict:
    with _lock:
        data = _read_all()
        bucket = _user_bucket(data, scope, str(uid))
        return _shape(bucket, uid)


def add_note(scope: str, uid: str, raw: dict, user_name: str = "") -> dict:
    item = _clean_item(raw or {})
    with _lock:
        data = _read_all()
        bucket = _user_bucket(data, scope, str(uid))
        items = list(bucket.get("items") or [])
        now = time.strftime("%Y-%m-%d %H:%M:%S")

        # 同一条帖子不重复添加：合并更新已有那条的正文 / 标题 / 截图 / 标签
        merged = False
        if item["topic_id"]:
            for x in items:
                if x.get("topic_id") and str(x["topic_id"]) == str(item["topic_id"]):
                    if item["text"]:
                        x["text"] = item["text"]
                    if item["title"]:
                        x["title"] = item["title"]
                    if item["image"]:
                        x["image"] = item["image"]
                    if item["tags"]:
                        x["tags"] = clean_tags(list(x.get("tags") or []) + item["tags"])
                    x["updated_at"] = now
                    merged = True
                    break
        if not merged:
            items.insert(0, item)
        bucket["items"] = items[:MAX_ITEMS_PER_USER]
        if user_name:
            bucket["name"] = user_name
        bucket["updated_at"] = now
        _write_all(data)
        return _shape(bucket, uid)


def delete_note(scope: str, uid: str, item_id: str) -> dict:
    with _lock:
        data = _read_all()
        bucket = _user_bucket(data, scope, str(uid))
        bucket["items"] = [x for x in (bucket.get("items") or [])
                           if str(x.get("id")) != str(item_id)]
        bucket["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        _write_all(data)
        return _shape(bucket, uid)


def toggle_pin(scope: str, uid: str, item_id: str) -> dict:
    with _lock:
        data = _read_all()
        bucket = _user_bucket(data, scope, str(uid))
        for x in (bucket.get("items") or []):
            if str(x.get("id")) == str(item_id):
                x["pinned"] = not x.get("pinned")
                break
        bucket["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        _write_all(data)
        return _shape(bucket, uid)


def export_all(scope: str) -> dict:
    with _lock:
        data = _read_all()
        users = (data.get("scopes", {}).get(scope, {}) or {}).get("users", {})
    return {"version": 1, "scope": scope, "users": users,
            "exported_at": time.strftime("%Y-%m-%d %H:%M:%S")}


def import_all(scope: str, payload: dict) -> dict:
    """导入备份：整体覆盖当前 scope 的 users（用于换设备/恢复）"""
    users = (payload or {}).get("users") or {}
    if not isinstance(users, dict):
        raise ValueError("备份格式不对：缺少 users 字段")
    with _lock:
        data = _read_all()
        sc = data.setdefault("scopes", {}).setdefault(scope, {"users": {}})
        sc["users"] = users
        sc["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        _write_all(data)
    return {"ok": True, "imported_users": len(users)}
