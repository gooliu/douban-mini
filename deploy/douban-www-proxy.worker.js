// 豆瓣 www 页面中转（Cloudflare Worker，免费版每天 10 万次请求，完全够用）
//
// 用途：云服务器（阿里云/腾讯云等机房）的出口 IP 容易被 www.douban.com 限流（403），
//       把后端的「桌面版页面」请求改经 Cloudflare 边缘转发，用 CF 的出口 IP 访问豆瓣。
//
// 部署（约 3 分钟，不用命令行）：
//   1. 浏览器打开 https://dash.cloudflare.com ，没有账号就注册一个（免费）
//   2. 左侧菜单 Workers & Pages → Create（创建）→ 起个名字，比如 douban-www → Deploy
//   3. 点 Edit code（编辑代码），把本文件内容整段粘贴进去，覆盖默认代码 → Deploy
//   4. 记下分配的地址，形如 https://douban-www.xxxxx.workers.dev
//
// 切换 douban-mini（服务器上一条命令，不用重建容器）：
//   docker exec douban-mini sh -c 'echo https://douban-www.xxxxx.workers.dev > /data/www_host.txt' \
//     && docker restart douban-mini
//
// 切回官方直连：
//   docker exec douban-mini sh -c 'rm -f /data/www_host.txt' && docker restart douban-mini

// 只放行小组/用户相关路径，防止被扫到后当成开放代理滥用
const ALLOWED_PREFIXES = ["/group/", "/people/"];

export default {
  async fetch(request) {
    if (request.method !== "GET") {
      return new Response("method not allowed", { status: 405 });
    }
    const url = new URL(request.url);
    const path = url.pathname;
    if (!ALLOWED_PREFIXES.some((p) => path.startsWith(p))) {
      return new Response("not found", { status: 404 });
    }

    const target = "https://www.douban.com" + path + url.search;
    let upstream;
    try {
      upstream = await fetch(target, {
        method: "GET",
        headers: {
          "User-Agent":
            request.headers.get("User-Agent") ||
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
          "Accept": "text/html,application/xhtml+xml",
          "Accept-Language": "zh-CN,zh;q=0.9",
          "Referer": "https://www.douban.com/",
        },
        redirect: "follow",
      });
    } catch (e) {
      return new Response("upstream error: " + e, { status: 502 });
    }

    return new Response(upstream.body, {
      status: upstream.status,
      headers: {
        "Content-Type": upstream.headers.get("Content-Type") || "text/html; charset=utf-8",
        "Cache-Control": "no-store",
      },
    });
  },
};
