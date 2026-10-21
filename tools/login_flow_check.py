# -*- coding: utf-8 -*-
"""用假响应验证登录重试逻辑：invalid_device_id 时自动换端点 / 换设备号，并给出中文提示"""
import asyncio, sys, os, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
import httpx
from douban_client import DoubanClient, DoubanError

CALLS = []


class FakeResp:
    def __init__(self, payload, status=400):
        self._p, self.status_code = payload, status
    def json(self):
        return self._p


class FakeClient:
    def __init__(self, *a, **k):
        pass
    async def __aenter__(self):
        return self
    async def __aexit__(self, *a):
        return False
    async def post(self, url, data=None):
        CALLS.append((url, (data or {}).get("udid")))
        return FakeResp(SCRIPT.pop(0) if SCRIPT else {"msg": "invalid_device_id", "code": 1000})
    async def aclose(self):
        pass


httpx.AsyncClient = FakeClient
SCRIPT = []


async def main():
    c = DoubanClient()

    # 1) 三次全部 invalid_device_id → 应换端点 + 换新设备号，最后给中文提示
    SCRIPT[:] = [{"msg": "invalid_device_id", "code": 1000}] * 3
    CALLS.clear()
    try:
        await c.login_with_password("u", "password123")
        print("!! 不该成功")
    except DoubanError as e:
        print("尝试次数:", len(CALLS))
        for u, d in CALLS:
            print("  ->", u, "| udid =", d)
        print("提示:", e.msg.replace("\n", " / "))

    # 2) 第一次就成功
    SCRIPT[:] = [{"access_token": "tok123", "douban_user_id": "281065007",
                  "douban_user_name": "测试", "expires_in": 7776000}]
    got = await c.login_with_password("u", "password123")
    print("成功:", got)
    await c.close()

asyncio.run(main())
