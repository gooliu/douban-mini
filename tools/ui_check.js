/* 用本机 Chrome + CDP 自动走一遍关键交互，检查有没有 JS 报错、补充记录区是否正常渲染。
   仅开发期自检用，不参与线上运行。 */
const { spawn } = require('child_process');
const fs = require('fs');
const os = require('os');
const path = require('path');
const http = require('http');
const WebSocket = require('ws');

const CHROME = 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe';
const PORT = 9333;
const URL = process.argv[2] || 'http://127.0.0.1:8001/web/';
const OUT = process.env.TEMP || os.tmpdir();

const profile = fs.mkdtempSync(path.join(os.tmpdir(), 'cdp-prof-'));
const chrome = spawn(CHROME, [
  '--headless=new', `--remote-debugging-port=${PORT}`,
  `--user-data-dir=${profile}`, '--no-first-run', '--no-default-browser-check',
  '--disable-gpu', '--hide-scrollbars', '--window-size=430,900', 'about:blank',
], { stdio: 'ignore', detached: false });

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
  const errors = [];
  const logs = [];
  ws.on('message', raw => {
    const m = JSON.parse(raw);
    if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id); return; }
    if (m.method === 'Runtime.exceptionThrown') {
      errors.push('EXCEPTION: ' + (m.params.exceptionDetails.exception?.description
        || m.params.exceptionDetails.text));
    }
    if (m.method === 'Runtime.consoleAPICalled' && m.params.type === 'error') {
      errors.push('CONSOLE.ERROR: ' + m.params.args.map(a => a.value || a.description || '').join(' '));
    }
    if (m.method === 'Log.entryAdded' && m.params.entry.level === 'error') {
      logs.push(m.params.entry.text + ' ' + (m.params.entry.url || ''));
    }
  });
  const send = (method, params = {}) => new Promise(res => {
    const mid = ++id;
    pending.set(mid, res);
    ws.send(JSON.stringify({ id: mid, method, params }));
  });

  await send('Page.enable');
  await send('Runtime.enable');
  await send('Log.enable');
  await send('Emulation.setDeviceMetricsOverride',
             { width: 430, height: 900, deviceScaleFactor: 2, mobile: true });

  await send('Page.navigate', { url: URL });
  await sleep(2500);

  const evaluate = async (expr) => {
    const r = await send('Runtime.evaluate',
      { expression: expr, awaitPromise: true, returnByValue: true });
    if (r.result && r.result.exceptionDetails) {
      return { error: r.result.exceptionDetails.exception?.description
        || r.result.exceptionDetails.text };
    }
    return { value: r.result?.result?.value };
  };

  const steps = [];
  const check = async (name, expr) => {
    const r = await evaluate(expr);
    steps.push({ name, ...r });
    return r.value;
  };

  await check('页面标题', 'document.title');
  await check('补充记录弹窗存在', '!!document.getElementById("noteModal")');
  await check('mountNotes 已定义', 'typeof mountNotes');
  await check('openNoteModal 已定义', 'typeof openNoteModal');
  await check('笔记接口可达', `fetch(API+'/api/notes/281065007').then(r=>r.json()).then(j=>j.ok + '/' + j.source + '/' + (j.data?j.data.total:'-'))`);

  // 进入某个 uid 的发言记录页（会触发 mountNotes）
  await evaluate('queryUserById("281065007")');
  await sleep(2500);
  await check('用户页已渲染', 'document.querySelectorAll("#groupsBody .group-item, #groupsBody .user-card").length');
  await check('补充记录区已挂载', '!!document.querySelector("#notesBoxGroups .note-sec-title")');
  await check('补充记录条数', 'document.querySelectorAll("#notesBoxGroups .note-item").length');
  await check('标题正确', 'document.querySelector("#notesBoxGroups .note-item .note-title")?.textContent');
  await check('补充按钮存在', '!!document.querySelector("#notesBoxGroups .note-btn.solid")');

  // 打开弹窗
  await evaluate('document.querySelector("#notesBoxGroups .note-btn.solid").click()');
  await sleep(600);
  await check('弹窗已显示', 'document.getElementById("noteModal").classList.contains("show")');
  await check('目标行文案', 'document.getElementById("noteForWhom").textContent');

  // 网址模式：填 URL → 自动带标题（需要登录，预期走「手动改标题」分支）
  await evaluate(`document.getElementById('noteUrl').value='https://www.douban.com/group/topic/503173206/';`);
  await evaluate('autoFillFromUrl()');
  await sleep(3000);
  await check('自动带标题结果', 'document.getElementById("noteTitle").value');
  await check('网址提示', 'document.getElementById("noteUrlMsg").textContent');

  // 切到截图 tab
  await evaluate('switchNoteTab("image")');
  await check('截图面板激活', 'document.getElementById("npane-image").classList.contains("active")');
  await evaluate('switchNoteTab("text")');
  await check('手写面板激活', 'document.getElementById("npane-text").classList.contains("active")');

  // 保存一条手写记录
  await evaluate(`document.getElementById('noteText').value='浏览器自检：出现过的疑似小号';`);
  await evaluate(`document.getElementById('noteTags').value='自检,小号';`);
  await evaluate('saveNote()');
  await sleep(2500);
  await check('保存状态', 'document.getElementById("noteSaveStatus").textContent');
  await check('保存后条数', 'document.querySelectorAll("#notesBoxGroups .note-item").length');
  await check('弹窗已自动关闭', 'document.getElementById("noteModal").classList.contains("show")');

  // 反查面板：回帖模式的登录提示
  await evaluate('switchLkMode("comments")');
  await check('回帖模式登录提示可见', 'getComputedStyle(document.getElementById("lkLoginWarn")).display');
  await evaluate('switchLkMode("posts")');

  // 反查回帖（未登录 → 应给出「需要登录」而不是「反爬」）
  await evaluate('document.getElementById("lkGroups").value="146409"');
  await evaluate('switchLkMode("comments")');
  await evaluate('doReverseLookup()');
  await sleep(6000);
  await check('回查结果文案', 'document.querySelector("#lkResult .ib-title")?.textContent');
  await check('回查结果全文', 'document.querySelector("#lkResult")?.innerText.slice(0,120)');

  await send('Page.captureScreenshot', { format: 'png' }).then(r => {
    if (r.result && r.result.data) {
      fs.writeFileSync(path.join(OUT, 'ui_shot_1.png'), Buffer.from(r.result.data, 'base64'));
    }
  });

  // 回到首页截图
  await evaluate('goBack();goBack();');
  await sleep(800);
  await evaluate('queryUserById("281065007")');
  await sleep(2500);
  await send('Page.captureScreenshot', { format: 'png' }).then(r => {
    if (r.result && r.result.data) {
      fs.writeFileSync(path.join(OUT, 'ui_shot_2.png'), Buffer.from(r.result.data, 'base64'));
    }
  });

  console.log(JSON.stringify({ steps, errors, logs }, null, 1));
  ws.close();
  chrome.kill();
  try { fs.rmSync(profile, { recursive: true, force: true }); } catch (e) {}
  process.exit(0);
}

main().catch(e => {
  console.error('FAIL', e.message);
  try { chrome.kill(); } catch (_) {}
  process.exit(1);
});
