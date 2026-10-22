/* 国内服务器「同源自托管」自检（模拟用非 8000 端口 + 局域网 IP 访问，
   等价于服务器上 http://公网IP/ 的场景）：
   1) 根路径 / 自动跳到 /web/
   2) 前端推断出的后端地址 = 页面自身的 origin（同源，不需要任何配置）
   3) 后端连得上：不出现「未连接后端」横幅
   4) 首页主视图正常渲染，无 JS 报错
   仅开发期自检用。 */
const { spawn } = require('child_process');
const fs = require('fs');
const os = require('os');
const path = require('path');
const http = require('http');
const WebSocket = require('ws');

const CHROME = 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe';
const PORT = 9341;
const OUT = process.env.TEMP || os.tmpdir();
// 用局域网 IP 访问，模拟服务器上的公网 IP（hostname 不是 127.0.0.1/localhost）
const HOST = process.argv[2] || '192.168.100.101:8011';
const BASE = `http://${HOST}/`;

const profile = fs.mkdtempSync(path.join(os.tmpdir(), 'cdp-self-'));
const chrome = spawn(CHROME, [
  '--headless=new', `--remote-debugging-port=${PORT}`,
  `--user-data-dir=${profile}`, '--no-first-run', '--no-default-browser-check',
  '--disable-gpu', '--hide-scrollbars', '--window-size=1280,900',
  'about:blank',
], { stdio: 'ignore' });

const sleep = ms => new Promise(r => setTimeout(r, ms));
function httpJson(url) {
  return new Promise((res, rej) => {
    http.get(url, r => {
      let b = '';
      r.on('data', d => (b += d));
      r.on('end', () => { try { res(JSON.parse(b)); } catch (e) { rej(e); } });
    }).on('error', rej);
  });
}
const out = [];
const log = s => { out.push(s); fs.writeFileSync(path.join(OUT, 'selfhost_check.txt'), out.join('\n')); };

(async () => {
  // ---- HTTP 层：根路径重定向 ----
  const root = await new Promise(res => {
    http.get(BASE, r => { r.resume(); res({ status: r.statusCode, loc: r.headers.location }); })
      .on('error', e => res({ error: String(e) }));
  });
  // 后端用 RedirectResponse，实际是 307（保留方法），302 也算正常
  log(`[1] GET / -> ${root.status} location=${root.loc || '-'}`);
  log(`    ${[301, 302, 307, 308].includes(root.status) && /\/web\/?$/.test(root.loc || '') ? '通过' : '不通过'}：根路径跳到 /web/`);

  let targets;
  for (let i = 0; i < 40; i++) {
    try { targets = await httpJson(`http://127.0.0.1:${PORT}/json/list`); break; }
    catch (e) { await sleep(300); }
  }
  if (!targets) { log('Chrome 调试端口未就绪'); chrome.kill(); process.exit(1); }
  const page = targets.find(t => t.type === 'page');
  const ws = new WebSocket(page.webSocketDebuggerUrl, { perMessageDeflate: false });
  await new Promise(r => ws.on('open', r));

  let id = 0;
  const pending = new Map();
  const errors = [];
  ws.on('message', raw => {
    const m = JSON.parse(raw);
    if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id); return; }
    if (m.method === 'Runtime.exceptionThrown') {
      errors.push(m.params.exceptionDetails.exception?.description || m.params.exceptionDetails.text);
    }
  });
  const send = (method, params = {}) => new Promise(r => {
    const myId = ++id; pending.set(myId, r);
    ws.send(JSON.stringify({ id: myId, method, params }));
  });
  const evalJs = async expr => {
    const r = await send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true });
    if (r.result?.exceptionDetails) errors.push(r.result.exceptionDetails.exception?.description || 'eval error');
    return r.result?.result?.value;
  };

  await send('Page.enable');
  await send('Runtime.enable');
  await send('Page.navigate', { url: BASE });
  await sleep(4000);

  const url = await evalJs('location.href');
  // initApiBase() 探测到「页面就是后端自己提供的」时会把 API 置空=走相对路径，
  // 所以空字符串和 origin 都算同源成功
  const api = await evalJs('(typeof API !== "undefined") ? String(API) : "MISSING"');
  const origin = await evalJs('location.origin');
  log(`[2] 最终地址 ${url}`);
  log(`    前端后端地址 = "${api}"（空=同源相对路径）；页面 origin = ${origin}`);
  log(`    ${(api === '' || api === origin) ? '通过' : '不通过'}：同源，无需配置 DEFAULT_CLOUD_API`);

  const banner = await evalJs(`getComputedStyle(document.getElementById('backendBanner')).display`);
  const fab = await evalJs(`document.getElementById('loginFab').textContent.trim()`);
  log(`[3] 断连横幅 display=${banner}（应为 none）；右下角状态="${fab}"`);
  log(`    ${banner === 'none' ? '通过' : '不通过'}：后端已连上`);

  const idx = await evalJs(`document.getElementById('view-index').classList.contains('active')`);
  const lib = await evalJs(`!!document.getElementById('view-notes')`);
  log(`[4] 首页视图激活=${idx}；补充记录库视图存在=${lib}`);
  log(`    ${idx && lib ? '通过' : '不通过'}：页面正常渲染`);

  // 真跑一次查询，确认接口通
  const health = await evalJs(`fetch('/api/health').then(r=>r.status)`);
  log(`[5] 页面内 fetch /api/health -> HTTP ${health}`);
  log(`    ${health === 200 ? '通过' : '不通过'}：同源接口可用`);

  const shot = await send('Page.captureScreenshot', { format: 'png' });
  const p = path.join(OUT, 'selfhost_shot.png');
  fs.writeFileSync(p, Buffer.from(shot.result.data, 'base64'));
  log(`[6] 截图 ${p}`);
  log(`[7] JS 报错 ${errors.length} 条`);

  log('\n=== 结果 ===');
  log(out.filter(l => l.includes('通过') || l.includes('不通过')).join('\n'));
  chrome.kill();
  process.exit(0);
})().catch(e => { log('FAILED ' + e); try { chrome.kill(); } catch (_) {} process.exit(1); });
