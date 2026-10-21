/* Cookie 登录通道自检（手机视口）：
   1) 登录弹窗「网页 Cookie」tab 有「从本机浏览器读取」按钮
   2) 一键书签脚本语法合法，且读不到 dbcl2 时给出可判断原因的提示（HttpOnly）
   3) 点「从本机浏览器读取」→ 后端给出中文结果（无 JS 报错）
   4) 接力码能带 cookie（接口层已支持，这里只验前端不报错）
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
const BASE = process.argv[2] || 'http://127.0.0.1:8000/web/';

const profile = fs.mkdtempSync(path.join(os.tmpdir(), 'cdp-cookie-'));
const chrome = spawn(CHROME, [
  '--headless=new', `--remote-debugging-port=${PORT}`,
  `--user-data-dir=${profile}`, '--no-first-run', '--no-default-browser-check',
  '--disable-gpu', '--hide-scrollbars', '--window-size=430,900',
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

(async () => {
  let targets;
  for (let i = 0; i < 40; i++) {
    try { targets = await httpJson(`http://127.0.0.1:${PORT}/json/list`); break; }
    catch (e) { await sleep(300); }
  }
  if (!targets) throw new Error('Chrome 调试端口未就绪');
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
      errors.push(m.params.exceptionDetails.exception?.description
        || m.params.exceptionDetails.text);
    }
  });
  const send = (method, params = {}) => new Promise(r => {
    const myId = ++id;
    pending.set(myId, r);
    ws.send(JSON.stringify({ id: myId, method, params }));
  });
  const evalJs = async expr => {
    const r = await send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true });
    if (r.result?.exceptionDetails) errors.push(r.result.exceptionDetails.exception?.description || 'eval error');
    return r.result?.result?.value;
  };
  const shot = async name => {
    const r = await send('Page.captureScreenshot', { format: 'png' });
    const p = path.join(OUT, name);
    fs.writeFileSync(p, Buffer.from(r.result.data, 'base64'));
    return p;
  };

  await send('Page.enable');
  await send('Runtime.enable');
  await send('Emulation.setDeviceMetricsOverride', {
    width: 390, height: 844, deviceScaleFactor: 2, mobile: true,
  });
  await send('Page.navigate', { url: BASE });
  await sleep(3500);

  const results = [];
  const check = async (name, expr) => {
    const v = await evalJs(expr);
    results.push({ name, value: v });
    return v;
  };

  // 打开弹窗 → cookie tab
  await evalJs(`document.getElementById('loginFab').click()`);
  await sleep(300);
  await evalJs(`switchLoginTab('cookie')`);
  await sleep(300);
  await check('Cookie tab 激活', `document.getElementById('pane-cookie').classList.contains('active')`);
  await check('有本机浏览器读取按钮', `!!document.getElementById('localCookieBtn')`);
  await check('按钮文案', `(document.getElementById('localCookieBtn').textContent||'').trim().slice(0,20)`);

  // 书签脚本：语法 + 分支提示
  await check('书签脚本前缀', `cookieBookmarkCode().slice(0, 11)`);
  await check('书签脚本可解析', `(()=>{try{new Function(cookieBookmarkCode().replace(/^javascript:/,''));return 'ok'}catch(e){return String(e)}})()`);
  await check('无 dbcl2（有 bid/ck）提示含 HttpOnly', `(()=>{
    window.__alertMsg=''; window.alert = (m)=>{window.__alertMsg=m;};
    Object.defineProperty(document,'cookie',{get:()=>'bid=abcdef; ck=xy',configurable:true});
    eval(cookieBookmarkCode().replace(/^javascript:/,''));
    return window.__alertMsg;
  })()`);
  await check('无 cookie 提示引导登录', `(()=>{
    window.__alertMsg=''; window.alert = (m)=>{window.__alertMsg=m;};
    Object.defineProperty(document,'cookie',{get:()=>'',configurable:true});
    eval(cookieBookmarkCode().replace(/^javascript:/,''));
    return window.__alertMsg;
  })()`);
  await check('有 dbcl2 则跳转', `(()=>{
    window.__href=''; window.alert=(m)=>{};
    Object.defineProperty(document,'cookie',{get:()=>'dbcl2="1:abc"; ck=x',configurable:true});
    try{ eval(cookieBookmarkCode().replace(/^javascript:/,'')); }catch(e){ return 'threw:'+e; }
    return location.href.indexOf('ck=')>=0 ? 'redirected' : 'no-redirect';
  })()`);
  await shot('cookie_tab_1.png');

  // 本机浏览器读取（后端会给出中文结果/失败原因）
  await evalJs(`document.getElementById('localCookieBtn').click()`);
  await sleep(4000);
  await check('本机读取结果', `document.getElementById('cookieLocalStatus').textContent`);
  await shot('cookie_tab_2.png');

  // 注意：process.exit 会丢掉未 flush 的 stdout，所以同步写文件
  const outFile = path.join(OUT, 'cookie_check_result.json');
  fs.writeFileSync(outFile, JSON.stringify({ results, errors }, null, 2), 'utf8');
  console.log('RESULT_FILE=' + outFile);
  chrome.kill();
  process.exit(0);
})().catch(e => {
  try {
    fs.writeFileSync(path.join(OUT, 'cookie_check_result.json'),
                     JSON.stringify({ fatal: String(e && e.stack || e) }, null, 2), 'utf8');
  } catch (_) {}
  try { chrome.kill(); } catch (_) {}
  process.exit(1);
});
