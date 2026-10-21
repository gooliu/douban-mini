/* 补充记录库 + Cookie 登录自检（手机视口 390x844）：
   1) 主页入口 → 打开记录库 → 统计/列表渲染
   2) 按 ID 搜索 → 命中；关键词搜索 → 命中
   3) Cookie tab：书签按钮 / 无效 Cookie 提示
   4) 全程收集 console error
   用法：node tools/notes_check.js [页面地址] */
const { spawn } = require('child_process');
const fs = require('fs');
const os = require('os');
const path = require('path');
const http = require('http');
const WebSocket = require('ws');

const CHROME = 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe';
const PORT = 9339;
const OUT = process.env.TEMP || os.tmpdir();
const BASE = process.argv[2] || 'http://127.0.0.1:8000/web/';

const profile = fs.mkdtempSync(path.join(os.tmpdir(), 'cdp-notes-'));
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
  const logs = [];
  ws.on('message', raw => {
    const m = JSON.parse(raw);
    if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id); return; }
    if (m.method === 'Runtime.exceptionThrown') {
      errors.push(m.params.exceptionDetails.exception?.description
        || m.params.exceptionDetails.text);
    }
    if (m.method === 'Runtime.consoleAPICalled' && m.params.type === 'error') {
      logs.push((m.params.args || []).map(a => a.value || a.description).join(' '));
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

  await check('页面标题', 'document.title');
  await check('主页有记录库入口',
    `[...document.querySelectorAll('.t-item')].some(e=>/openNotesLibrary/.test(e.getAttribute('onclick')||''))`);

  // 打开记录库
  await evalJs(`openNotesLibrary()`);
  await sleep(1500);
  await check('记录库视图已激活', `document.getElementById('view-notes').classList.contains('active')`);
  const stat = await check('统计文案',
    `(document.querySelector('#notesLibBody .note-sec-title')||{}).textContent||''`);
  await check('至少一张 ID 卡片', `document.querySelectorAll('#notesLibBody .note-item').length > 0`);
  await check('有「查这个人」按钮',
    `[...document.querySelectorAll('#notesLibBody .note-acts span')].some(s=>s.textContent.includes('查这个人'))`);
  await shot('notes_lib_1.png');

  // 取第一个 ID 用于搜索
  const firstUid = await evalJs(`
    (function(){
      const el=[...document.querySelectorAll('#notesLibBody .note-badge.kind')]
        .map(e=>e.textContent).find(t=>/ID \d+/.test(t))||'';
      const m=el.match(/(\\d+)/); return m?m[1]:'';
    })()`);
  results.push({ name: '取到首个 ID', value: firstUid });

  if (firstUid) {
    await evalJs(`openNotesLibrary('${firstUid}')`);
    await sleep(1500);
    await check('按 ID 搜索命中',
      `(document.querySelector('#notesLibBody .note-text')||{}).textContent||''`);
  }

  // 关键词搜索（拿已有记录的关键词「麻丝」
  await evalJs(`openNotesLibrary('麻丝')`);
  await sleep(1500);
  await check('关键词搜索命中',
    `(document.querySelector('#notesLibBody .note-text')||{}).textContent||''`);

  // 清空搜索
  await evalJs(`openNotesLibrary('')`);
  await sleep(1200);
  await check('清空后恢复全部',
    `document.querySelectorAll('#notesLibBody .note-item').length > 0`);

  // ---- Cookie tab ----
  await evalJs(`openTokenModal('cookie')`);
  await sleep(500);
  await check('Cookie tab 打开', `document.getElementById('pane-cookie').classList.contains('active')`);
  await check('有复制书签按钮', `!!document.getElementById('cookieBmBtn')`);
  await evalJs(`copyCookieBookmark()`);
  await sleep(600);
  await check('书签复制结果提示', `document.getElementById('cookieBmStatus').textContent.slice(0,60)`);
  await check('书签代码含 javascript:',
    `document.getElementById('cookieInput').value.slice(0,11)`);
  await shot('notes_cookie_1.png');

  // 无效 Cookie（没有 dbcl2）
  await evalJs(`
    document.getElementById('cookieInput').value = 'bid=abc; ll="118318"';
    saveCookie();
  `);
  await sleep(600);
  await check('无 dbcl2 提示', `document.getElementById('cookieStatus').textContent`);

  console.log(JSON.stringify({ results, errors, logs }, null, 1));
  ws.close();
  chrome.kill();
  process.exit(0);
})().catch(e => { console.error('FAILED', e); chrome.kill(); process.exit(1); });
