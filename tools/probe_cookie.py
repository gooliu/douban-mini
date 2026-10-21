# -*- coding: utf-8 -*-
"""看 rexxar user/me 匿名 / 带假 Cookie 的完整字段，找「是否已登录」判据"""
import asyncio, json
import httpx

REXXAR = "https://m.douban.com/rexxar/api/v2"
UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
      "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 "
      "Mobile/15E148 Safari/604.1")
FAKE = 'bid=abcdefghijk; dbcl2="999999999:fakefakefake"; ck=abcd'


async def main():
    async with httpx.AsyncClient(timeout=20) as hc:
        for name, ck in (("匿名", None), ("假Cookie", FAKE)):
            h = {"User-Agent": UA, "Referer": "https://m.douban.com/"}
            if ck:
                h["Cookie"] = ck
            r = await hc.get(REXXAR + "/user/me", headers=h)
            j = r.json()
            keep = {k: j.get(k) for k in ("id", "uid", "name", "is_login", "user_id",
                                          "avatar", "url", "kind", "login")}
            print(name, "HTTP", r.status_code, json.dumps(keep, ensure_ascii=False))
            print(" 全部字段:", sorted(j.keys())[:40])
        # 需登录接口在匿名下的表现
        for path in ("/group/topic/503184787", "/group/user/281065007/joined_groups"):
            r = await hc.get(REXXAR + path, headers={"User-Agent": UA,
                                                     "Referer": "https://m.douban.com/"})
            print(path, "匿名 HTTP", r.status_code, r.text[:150].replace("\n", " "))

asyncio.run(main())
