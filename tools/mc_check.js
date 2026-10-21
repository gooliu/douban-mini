/* 验证「混合内容横幅」行为：
   1) https 页 + 能连通的 http 后端  → 不该出现横幅
   2) https 页 + 连不通的 http 后端  → 出现横幅，且点「不再提示」后 localStorage 有静默标记
   仅开发期自检用。 */
const { spawn } = require('child_process');
const fs = require('fs');
const os = require('os');
const path = require('path');
const http = require('http');
const WebSocket = require('ws');

const CHROME = 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe';
const PORT = 9334;
const OUT = process.env.TEMP || os.tmpdir();

const profile = fs.mkdtempSync(path.join(os.tmpdir(), 'cdp-prof-'));
const chrome = spawn(CHROME, [
  '--headless=new', `--remote-debugging-port=${PORT}`,
  `--user-data-dir=${profile}`, '--no-first-run', '--no-default-browser-check',
  '--disable-gpu', '--hide-scrollbars', '--window-size=430,900',
  '--proxy-server=http://127.0.0.1:7890',
  '--proxy-bypass-list=127.0.0.1;localhost',
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

async function main() {
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
  ws.on('message', raw => {
    const m = JSON.parse(raw);
    if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id); }
  });
  const send = (method, params = {}) => new Promise(r => {
    const myId = ++id;
    pending.set(myId, r);
    ws.send(JSON.stringify({ id: myId, method, params }));
  });
  const evalJs = async (expr) => {
    const r = await send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true });
    return r.result?.result?.value;
  };

  const out = [];
  const PAGES = 'https://gooliu.github.io/douban-mini/';

  // ---- 场景1：https 页 + 能连通的 http 后端（127.0.0.1，Chrome 视为可信源不拦混合内容）
  await send('Page.enable');
  await send('Runtime.enable');
  await send('Page.navigate', { url: PAGES + '?api=' + encodeURIComponent('http://127.0.0.1:8000') });
  await sleep(6000);
  out.push({
    name: '[通] 是否弹出横幅（应为 false）',
    value: await evalJs(`!!document.getElementById('__mcclose')`),
  });
  out.push({
    name: '[通] 后端连通性',
    value: await evalJs(`(async()=>{try{const r=await fetch('http://127.0.0.1:8000/api/health');return r.ok}catch(e){return 'blocked:'+e.message}})()`),
  });

  // ---- 场景2：https 页 + 连不通的 http 后端（公网家宽 http，必被混合内容策略拦截）
  await evalJs(`localStorage.removeItem('douban_api_base');localStorage.removeItem('douban_mc_muted_until');`);
  await send('Page.navigate', { url: PAGES + '?api=' + encodeURIComponent('http://211.91.184.194:8000') });
  await sleep(8000);
  out.push({
    name: '[断] 是否弹出横幅（应为 true）',
    value: await evalJs(`!!document.getElementById('__mcclose')`),
  });
  out.push({
    name: '[断] 是否有「不再提示」',
    value: await evalJs(`!!document.getElementById('__mcmute')`),
  });
  // 点「不再提示」→ 刷新 → 应不再出现
  await evalJs(`document.getElementById('__mcmute') && document.getElementById('__mcmute').click(); true`);
  await sleep(500);
  out.push({
    name: '[断] 点不再提示后静默标记',
    value: await evalJs(`!!(parseInt(localStorage.getItem('douban_mc_muted_until')||'0',10) > Date.now())`),
  });
  await send('Page.navigate', { url: PAGES + '?api=' + encodeURIComponent('http://211.91.184.194:8000') });
  await sleep(8000);
  out.push({
    name: '[断] 再次打开是否还弹（应为 false）',
    value: await evalJs(`!!document.getElementById('__mcclose')`),
  });

  console.log(JSON.stringify(out, null, 1));
  ws.close();
  chrome.kill();
  process.exit(0);
}

main().catch(e => { console.error('FAIL', e.message); try { chrome.kill(); } catch (_) {} process.exit(1); });
