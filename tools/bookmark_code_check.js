/* 纯逻辑自检（不启动浏览器）：
   1) 从 web/index.html 里抽出 cookieBookmarkCode()，在假 DOM 上跑三种场景：
      - 有 dbcl2   → 跳回本页并带上 ck 参数
      - 有 bid/ck 但没有 dbcl2 → 提示 HttpOnly（并说明替代办法）
      - 什么都没有 → 引导先在 m.douban.com 登录
   2) 校验 index.html 里 $('xxx') / getElementById('xxx') 引用的 id 都真实存在
*/
const fs = require('fs');
const path = require('path');

const html = fs.readFileSync(path.join(__dirname, '..', 'web', 'index.html'), 'utf8');
const out = [];
const bad = [];

/* ---------- 1. 书签脚本 ---------- */
const m = html.match(/function cookieBookmarkCode\(\)\s*\{[\s\S]*?\n\}/);
if (!m) {
  console.log(JSON.stringify({ fatal: '没找到 cookieBookmarkCode()' }, null, 2));
  process.exit(1);
}
const src = m[0];
const fakeLoc = { href: 'http://127.0.0.1:8000/web/' };   // 书签里用 location.href 算回跳地址
const cookieBookmarkCode = new Function('location', `
  ${src}
  return cookieBookmarkCode;
`)(fakeLoc);

function run(cookieText) {
  let alerted = '';
  const doc = { cookie: cookieText };
  const loc = { href: 'http://127.0.0.1:8000/web/' };
  const win = { alert: (s) => { alerted = s; } };
  const code = cookieBookmarkCode().replace(/^javascript:/, '');
  try {
    // 书签脚本用的是全局 document / location / alert，这里把它们注入成假对象
    new Function('document', 'location', 'window', 'alert', code)(doc, loc, win, win.alert);
  } catch (e) {
    return { threw: String(e) };
  }
  return { alerted, href: loc.href };
}

const code = cookieBookmarkCode();
out.push({ name: '书签脚本前缀', value: code.slice(0, 11) });

let r1 = run('dbcl2="281065007:abcdef"; ck=xyz; bid=qqq');
if (!(r1.href || '').includes('ck=')) bad.push('有 dbcl2 时没有跳回本页：' + JSON.stringify(r1));
out.push({ name: '有 dbcl2 → 跳转带 ck', value: (r1.href || '').slice(0, 60) });
if ((r1.href || '').indexOf('ck=') < 0) bad.push('跳转 URL 缺少 ck 参数');

let r2 = run('bid=abcdef; ck=xy');
if (!/HttpOnly/.test(r2.alerted || '')) bad.push('读不到 dbcl2 时没提示 HttpOnly：' + r2.alerted);
out.push({ name: '有 bid/ck 无 dbcl2 → 提示 HttpOnly', value: r2.alerted });
if (!/本机浏览器/.test(r2.alerted || '')) bad.push('HttpOnly 提示里没引导替代方案');

let r3 = run('');
if (!/m\.douban\.com/.test(r3.alerted || '')) bad.push('空 cookie 时没引导登录：' + r3.alerted);
out.push({ name: '空 cookie → 引导登录', value: r3.alerted });

let r4 = run('foo=1; bar=2');
out.push({ name: '只有无关 cookie → 提示', value: (r4.alerted || '').slice(0, 40) });
if (!/dbcl2/.test(r4.alerted || '')) bad.push('无关 cookie 场景提示异常');

/* ---------- 2. id 引用完整性 ---------- */
const ids = new Set([...html.matchAll(/\sid="([^"]+)"/g)].map(x => x[1]));
const refs = new Set([
  ...[...html.matchAll(/\$\('([^']+)'\)/g)].map(x => x[1]),
  ...[...html.matchAll(/getElementById\('([^']+)'\)/g)].map(x => x[1]),
]);
const missing = [...refs].filter(r => !ids.has(r));
out.push({ name: '引用的 DOM id 数量', value: refs.size });
out.push({ name: '缺失的 id', value: missing.length ? missing : '无' });
if (missing.length) bad.push('index.html 引用了不存在的 id: ' + missing.join(', '));

/* ---------- 3. 本机读取相关接线 ---------- */
['localCookieBtn', 'cookieLocalStatus', 'readLocalCookie'].forEach(k => {
  if (!html.includes(k)) bad.push('缺少本机读取相关代码: ' + k);
});
if (!html.includes('/api/local-cookie')) bad.push('前端未调用 /api/local-cookie');
out.push({ name: '本机读取接线', value: ['localCookieBtn', 'cookieLocalStatus', 'readLocalCookie']
  .every(k => html.includes(k)) ? 'ok' : 'missing' });

console.log(JSON.stringify({ results: out, problems: bad }, null, 2));
process.exitCode = bad.length ? 1 : 0;
