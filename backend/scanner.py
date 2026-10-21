# -*- coding: utf-8 -*-
"""全量扫描任务调度器

背景：豆瓣 group/:gid/topics 翻页到 start>200 后需要登录态（带 Bearer token）。
     评论接口（topic/:tid/comments）极容易被反爬（1000），全量目前不可行。

设计：
  - ScanJob 后台协程任务，单组一前一后顺序翻页；
  - 全局限速（默认 0.3~0.9s 随机）；
  - 遇 1000 自动退避 + 重试 + 记录 blocked（不退出任务）；
  - 断点续跑：checkpoints 记录每组已扫到的 start，重启或新任务都从断点继续；
  - 进度可查，可取消（status=canceled 后协程在下一个边界退出）。
"""
from __future__ import annotations

import asyncio
import random
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

from douban_client import DoubanError, client

# ---- 限速参数（可被环境变量覆盖） ----
import os
# 全量扫描默认限速：单组 ~200 页 × 1s ≈ 3-4 分钟，稳。
# 任务里还有评论请求时评论接口会触发 1000，所以这里只约束话题接口；
# 评论接口的退避由 COMMENT_BACKOFFS 控制。
MIN_DELAY = float(os.getenv("DOUBAN_SCAN_MIN_DELAY", "0.6"))
MAX_DELAY = float(os.getenv("DOUBAN_SCAN_MAX_DELAY", "1.5"))
COMMENT_BACKOFFS = [30, 90, 180]   # 连续 1000 后退避秒数
COMMENT_COUNT_PER_TRY = 30         # 单次评论请求取 30 条
COMMENT_MAX_TRIES = len(COMMENT_BACKOFFS) + 1
PAGE_SIZE = 100  # 豆瓣 topics 接口实际单页最多 93 条（按时间窗口截断），用 100 减少页数


@dataclass
class ScanJob:
    id: str
    user_id: str
    user_name: str
    group_ids: list
    mode: str                       # "posts" | "comments" | "both"
    max_topics_per_group: int       # 0 = 不限，扫到小组最早一条
    status: str = "pending"         # pending|running|paused|done|canceled|error
    progress: dict = field(default_factory=lambda: {
        "groups_total": 0, "group_index": 0,
        "current_group_id": None, "current_group_name": None,
        "current_topic_title": None,
        "scanned_topics": 0,
        "post_hits": 0, "comment_hits": 0,
        "requests": 0, "comment_requests": 0, "comment_blocked": 0,
        "oldest_topic_time": None, "latest_topic_time": None,
        "started_at": None, "finished_at": None,
    })
    results: dict = field(default_factory=lambda: {"posts": [], "comments": []})
    checkpoints: dict = field(default_factory=dict)
    blocked: dict = field(default_factory=dict)
    notes: list = field(default_factory=list)
    error: Optional[str] = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    _task: Optional[asyncio.Task] = field(default=None, repr=False)
    client: Any = field(default=None, repr=False)   # 云端多用户：任务创建时绑定的凭证视图

    def to_dict(self, limit: int = 500) -> dict:
        """序列化（results 默认只返回前 limit 条，避免响应过大）"""
        posts = sorted(self.results["posts"],
                       key=lambda x: x.get("create_time") or "", reverse=True)
        comments = sorted(self.results["comments"],
                          key=lambda x: x.get("create_time") or "", reverse=True)
        return {
            "id": self.id,
            "user_id": self.user_id,
            "user_name": self.user_name,
            "group_ids": list(self.group_ids),
            "mode": self.mode,
            "max_topics_per_group": self.max_topics_per_group,
            "status": self.status,
            "progress": self.progress,
            "checkpoints": self.checkpoints,
            "blocked": self.blocked,
            "notes": self.notes[-5:],
            "results_count": {"posts": len(posts), "comments": len(comments)},
            "results": {"posts": posts[:limit], "comments": comments[:limit]},
            "results_truncated": len(posts) > limit or len(comments) > limit,
            "limit": limit,
            "error": self.error,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


_jobs: dict[str, ScanJob] = {}
_jobs_lock = asyncio.Lock()


async def _rate_limit():
    await asyncio.sleep(random.uniform(MIN_DELAY, MAX_DELAY))


async def _scan_one_topic_comments(job: ScanJob, topic: dict, gid: str, gname: str) -> str:
    """拉一条话题的评论（30 条/次），过滤出目标用户
       返回: "ok" | "skipped" | "blocked" | "canceled"
    """
    if job.status == "canceled":
        return "canceled"
    cl = job.client or client
    for attempt in range(COMMENT_MAX_TRIES):
        await _rate_limit()
        try:
            job.progress["comment_requests"] += 1
            r = await cl.get_topic_comments(topic["id"], 0, COMMENT_COUNT_PER_TRY)
            break
        except DoubanError as e:
            if e.code == 1000:
                backoff = COMMENT_BACKOFFS[min(attempt, len(COMMENT_BACKOFFS) - 1)]
                job.notes.append(f"反爬 1000（话题 {topic['id']}），退避 {backoff}s")
                if attempt == COMMENT_MAX_TRIES - 1:
                    job.blocked.setdefault("__comments__", []).append({
                        "topic_id": topic["id"], "gid": gid, "reason": "1000 反爬达到重试上限",
                    })
                    job.progress["comment_blocked"] += 1
                    return "blocked"
                await asyncio.sleep(backoff)
                continue
            if e.code == 103:
                job.blocked["__token__"] = "token 已失效（code=103），回帖扫描终止"
                return "blocked"
            return "skipped"     # 其他错误跳过该话题
    else:
        return "blocked"

    n = 0
    for c in (r.get("comments") or []) + (r.get("popular_comments") or []):
        a = c.get("author") or {}
        if str(a.get("id")) == str(job.user_id):
            job.results["comments"].append({
                "comment_id": c.get("id"),
                "text": c.get("text", ""),
                "create_time": c.get("create_time"),
                "vote_count": c.get("vote_count", 0),
                "topic_id": topic.get("id"),
                "topic_title": topic.get("title", ""),
                "group_id": gid, "group_name": gname,
            })
            n += 1
    job.progress["comment_hits"] += n
    return "ok"


async def _run_job(job: ScanJob) -> None:
    job.status = "running"
    job.progress["started_at"] = time.time()
    job.progress["groups_total"] = len(job.group_ids)
    cl = job.client or client       # 云端多用户：用任务创建时绑定的凭证视图

    try:
        for gi, gid in enumerate(job.group_ids):
            if job.status == "canceled":
                break
            job.progress["group_index"] = gi
            job.progress["current_group_id"] = gid
            job.progress["current_topic_title"] = None
            gname = gid
            try:
                gd = await cl.get_group(gid)
                gname = gd.get("name") or gid
            except DoubanError:
                pass
            job.progress["current_group_name"] = gname
            job.updated_at = time.time()

            start = int(job.checkpoints.get(gid, 0))
            empty_streak = 0
            total_topics: Optional[int] = None

            while True:
                if job.status == "canceled":
                    break
                await _rate_limit()
                try:
                    job.progress["requests"] += 1
                    resp = await cl.get_group_topics(gid, start, PAGE_SIZE)
                except DoubanError as e:
                    if e.code == 103:
                        job.blocked[gid] = "token 已失效（code=103），本组停止"
                        break
                    if e.code == 1000:
                        # 话题列表也被反爬：按全局冷却剩余时间退避（至少 30s）后重试一次
                        wait = max(30, cl.cooldown_remaining())
                        job.notes.append(
                            f"topics 反爬 1000（组 {gid} start={start}），退避 {wait}s")
                        await asyncio.sleep(wait)
                        try:
                            job.progress["requests"] += 1
                            resp = await cl.get_group_topics(gid, start, PAGE_SIZE)
                        except DoubanError as e2:
                            job.blocked[gid] = f"topics 持续反爬: {e2.code}"
                            break
                    else:
                        job.blocked[gid] = f"topics 错误 {e.code}: {e.msg}"
                        break

                topics = resp.get("topics") or []
                # 记录该组的 total（话题总数）；用于判断翻到末尾
                if total_topics is None and resp.get("total") is not None:
                    total_topics = int(resp["total"])
                    job.progress["group_total"] = total_topics
                if not topics:
                    empty_streak += 1
                    if empty_streak >= 3:
                        break
                    start += PAGE_SIZE
                    continue
                empty_streak = 0

                # 过滤 author（发帖命中）
                if job.mode in ("posts", "both"):
                    for t in topics:
                        a = t.get("author") or {}
                        if str(a.get("id")) == str(job.user_id):
                            job.results["posts"].append({
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
                            job.progress["post_hits"] += 1

                # 回帖命中
                if job.mode in ("comments", "both"):
                    for t in topics:
                        job.progress["current_topic_title"] = t.get("title", "")
                        result = await _scan_one_topic_comments(job, t, gid, gname)
                        if result == "canceled":
                            break
                        if result == "blocked" and job.blocked.get("__token__"):
                            break
                    if job.status == "canceled":
                        break
                    if job.blocked.get("__token__"):
                        break

                # 更新进度：最早/最晚时间（按时间倒序的话题列表）
                if topics:
                    first = topics[0].get("create_time")
                    last = topics[-1].get("create_time")
                    job.progress["latest_topic_time"] = job.progress["latest_topic_time"] or first
                    job.progress["oldest_topic_time"] = last  # 越往后越早

                start += len(topics)
                job.checkpoints[gid] = start
                job.progress["scanned_topics"] += len(topics)
                job.updated_at = time.time()

                # 终止条件：扫到 total 上限 / 达到 max_topics
                # 注意：豆瓣按时间窗口截断（请求 100 可能只返回 93），不能因 len<count 提前停
                if total_topics and start >= total_topics:
                    break
                if job.max_topics_per_group and start >= job.max_topics_per_group:
                    break

        if job.status == "running":
            job.status = "done"
        job.progress["finished_at"] = time.time()
    except Exception as e:  # noqa: BLE001
        job.status = "error"
        job.error = str(e)
        job.progress["finished_at"] = time.time()


# ---- 公共 API ----
def estimate_requests(groups: list[str], mode: str, max_topics_per_group: int) -> dict:
    """粗估请求数（按 max_topics=0 时按 2000 话题 / 组算）"""
    cap = max_topics_per_group or 2000
    per_group_topics_pages = (cap + (PAGE_SIZE - 1)) // PAGE_SIZE
    posts_reqs = per_group_topics_pages * len(groups)
    comments_reqs = posts_reqs * (cap // PAGE_SIZE) if mode in ("comments", "both") else 0
    return {
        "groups": len(groups),
        "estimated_topic_pages": posts_reqs,
        "estimated_comment_requests": comments_reqs,
        "estimated_seconds": int((posts_reqs + comments_reqs) * ((MIN_DELAY + MAX_DELAY) / 2)),
    }


async def create_job(user_id: str, user_name: str, group_ids: list[str],
                    mode: str = "posts", max_topics_per_group: int = 0,
                    resume_from: Optional[dict] = None,
                    client_override: Any = None) -> ScanJob:
    if mode not in ("posts", "comments", "both"):
        raise ValueError(f"mode 必须是 posts/comments/both，得到 {mode}")
    job = ScanJob(
        id=uuid.uuid4().hex[:12],
        user_id=str(user_id),
        user_name=user_name or str(user_id),
        group_ids=[str(g) for g in group_ids if str(g).strip()],
        mode=mode,
        max_topics_per_group=int(max_topics_per_group),
        client=client_override,
    )
    if resume_from:
        job.checkpoints.update({k: int(v) for k, v in resume_from.items()})
        job.notes.append(f"已恢复断点：{job.checkpoints}")
    async with _jobs_lock:
        _jobs[job.id] = job
    job._task = asyncio.create_task(_run_job(job))
    return job


def get_job(job_id: str) -> Optional[ScanJob]:
    return _jobs.get(job_id)


def list_jobs() -> list[ScanJob]:
    return sorted(_jobs.values(), key=lambda j: j.created_at, reverse=True)


def cancel_job(job_id: str) -> bool:
    j = _jobs.get(job_id)
    if not j:
        return False
    j.status = "canceled"
    j.updated_at = time.time()
    return True


def delete_job(job_id: str) -> bool:
    j = _jobs.pop(job_id, None)
    if j and j._task and not j._task.done():
        j.status = "canceled"
        j._task.cancel()
    return j is not None
