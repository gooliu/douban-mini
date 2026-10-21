/* 登录弹窗自检（手机视口）：
   1) 打开弹窗默认「账号密码」tab
   2) 错误密码 → 显示豆瓣给的失败原因（不是「Cookie 已失效」那种误导）
   3) 切换「粘贴 token」「手机接力」tab 正常
   4) 手写流程无 JS 报错；顺带截图
   仅开发期自检用。 */
const { spawn } = require('child_process');
const fs = require('fs');
const os = require('os');
const path = require('path');
const http = require('http');
const WebSocket = require('ws');

const CHROME = 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe';
const PORT = 9337;
const OUT = process.env.TEMP || os.tmpdir();
const BASE = process.argv[2] || 'http://127.0.0.1:8001/web/';

const profile = fs.mkdtempSync(path.join(os.tmpdir(), 'cdp-login-'));
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
  await check('登录提示卡可见', `getComputedStyle(document.getElementById('loginPrompt')).display`);
  await check('引导卡有账号密码输入框', `!!document.getElementById('lpUser') && !!document.getElementById('lpPass')`);
  await check('引导卡无 Cookie 输入框', `!document.getElementById('lpCookieInput')`);

  // 打开弹窗
  await evalJs(`document.getElementById('loginFab').click()`);
  await sleep(400);
  await check('弹窗显示', `document.getElementById('tokenModal').classList.contains('show')`);
  await check('默认在账号密码 tab', `document.getElementById('pane-pwd').classList.contains('active')`);
  await check('弹窗输入框字号(防iOS缩放)', `getComputedStyle(document.getElementById('pwdUser')).fontSize`);
  await shot('login_shot_1.png');

  // 错误密码
  await evalJs(`
    document.getElementById('pwdUser').value = '13800000000';
    document.getElementById('pwdPass').value = 'definitely-wrong-123';
    document.getElementById('pwdBtn').click();
  `);
  await sleep(9000);
  await check('错误密码提示', `document.getElementById('pwdStatus').textContent`);

  // 密码可见切换
  await evalJs(`document.getElementById('pwdEye').click()`);
  await check('点「显示」后是明文', `document.getElementById('pwdPass').type`);
  await evalJs(`document.getElementById('pwdEye').click()`);

  // 切到 token tab
  await evalJs(`document.getElementById('tab-token').click()`);
  await sleep(200);
  await check('token tab 打开', `document.getElementById('pane-token').classList.contains('active')`);
  await check('token 说明书存在', `!!document.querySelector('#pane-token details')`);

  // 切到接力 tab
  await evalJs(`document.getElementById('tab-handoff').click()`);
  await sleep(200);
  await check('接力 tab 打开', `document.getElementById('pane-handoff').classList.contains('active')`);
  await check('接力提示文案', `document.getElementById('hoStatus').textContent`);
  await shot('login_shot_2.png');

  // 接力码领取（用无效码，验证报错路径）
  await evalJs(`document.getElementById('hoInput').value = 'ZZZZZZ'; claimHandoff();`);
  await sleep(1500);
  await check('无效接力码提示', `document.getElementById('hoStatus').textContent`);

  console.log(JSON.stringify({ results, errors, logs }, null, 1));
  ws.close();
  chrome.kill();
  process.exit(0);
})().catch(e => { console.error('FAILED', e); chrome.kill(); process.exit(1); });
