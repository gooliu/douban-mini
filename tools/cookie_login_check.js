/* Cookie 登录通道自检（手机视口）：
   1) 登录弹窗默认停在「粘贴 Cookie」，密码登录 / 一键读取 / 书签入口都已移除
   2) 粘贴缺少 dbcl2 的 Cookie、空输入时都给出明确中文提示
   3) 未登录引导卡上也有同样的粘贴框（lpCookieInput）
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

  // 打开弹窗 → 默认就是「粘贴 Cookie」
  await evalJs(`document.getElementById('loginFab').click()`);
  await sleep(300);
  await check('默认激活 Cookie 面板', `document.getElementById('pane-cookie').classList.contains('active')`);
  await check('密码登录入口已移除', `!document.getElementById('tab-pwd') && !document.getElementById('pane-pwd')`);
  await check('一键读取按钮已移除', `!document.getElementById('localCookieBtn') && !document.getElementById('cookieBmBtn')`);
  await check('有粘贴框', `!!document.getElementById('cookieInput')`);
  await check('引导卡也有粘贴框', `!!document.getElementById('lpCookieInput')`);

  // 缺少 dbcl2 / 空输入 → 给出明确中文提示
  await evalJs(`document.getElementById('cookieInput').value='bid=abc; ck=x'`);
  await evalJs(`saveCookie()`);
  await sleep(1500);
  await check('缺 dbcl2 的提示', `document.getElementById('cookieStatus').textContent`);
  await shot('cookie_tab_1.png');

  await evalJs(`document.getElementById('cookieInput').value=''`);
  await evalJs(`saveCookie()`);
  await sleep(800);
  await check('空输入提示', `document.getElementById('cookieStatus').textContent`);
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
