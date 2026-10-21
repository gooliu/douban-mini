// 轻量 Service Worker：优先走网络，断网时回退缓存（让「加到桌面」后离线也能打开壳）
const CACHE = 'douban-mini-v1';
const ASSETS = [
  '/web/',
  '/web/index.html',
  '/web/manifest.json',
  '/web/icon-192.png',
  '/web/icon-512.png',
];

self.addEventListener('install', (e) => {
  self.skipWaiting();
  e.waitUntil(
    caches.open(CACHE).then((c) => c.addAll(ASSETS).catch(() => {}))
  );
});

self.addEventListener('activate', (e) => {
  e.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))
    )
  );
  self.clients.claim();
});

self.addEventListener('fetch', (e) => {
  const req = e.request;
  if (req.method !== 'GET') return;                       // 登录提交等 POST 不碰
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;        // 豆瓣头像等外链不缓存
  if (url.pathname.startsWith('/api/')) return;           // 实时数据永不缓存

  // network-first：拿到新版本就顺手更新缓存；网络失败才用缓存
  e.respondWith(
    fetch(req)
      .then((res) => {
        const copy = res.clone();
        caches.open(CACHE).then((c) => c.put(req, copy)).catch(() => {});
        return res;
      })
      .catch(() =>
        caches.match(req).then((r) => r || caches.match('/web/index.html'))
      )
  );
});
