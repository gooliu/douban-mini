/* 登录界面静态自检（不启动浏览器）：
   1) 登录弹窗只保留三条可用路径：粘贴 Cookie / 粘贴 token / 手机接力
   2) 已确认失效的入口不再出现：密码登录（豆瓣 invalid_device_id）、
      「从本机浏览器读取」按钮与一键书签（云端模式下必然失败 / dbcl2 是 HttpOnly）
   3) Cookie 登录的接线完整（弹窗 + 未登录引导卡各一套）
   4) index.html 里 $('xxx') / getElementById('xxx') 引用的 id 都真实存在
   运行：node tools/login_ui_check.js   （有问题 exitCode=1）
*/
const fs = require('fs');
const path = require('path');

const html = fs.readFileSync(path.join(__dirname, '..', 'web', 'index.html'), 'utf8');
const out = [];
const bad = [];

/* ---------- 1. 登录入口 ---------- */
const tabs = [...html.matchAll(/id="tab-(\w+)"/g)].map(x => x[1]);
out.push({ name: '登录弹窗 tab', value: tabs });
if (tabs.join(',') !== 'cookie,token,handoff') bad.push('tab 不是 cookie,token,handoff：' + tabs.join(','));
if (!/id="tab-cookie" class="active"/.test(html)) bad.push('默认 tab 不是 cookie（默认应停在粘贴 Cookie）');
if (!/let loginTab = 'cookie'/.test(html)) bad.push("loginTab 默认值不是 'cookie'");

/* ---------- 2. 失效入口已移除 ---------- */
[
  ['密码登录 tab', /id="tab-pwd"/],
  ['密码登录面板', /id="pane-pwd"/],
  ['密码登录函数', /function loginByPassword/],
  ['本机一键读取按钮', /id="localCookieBtn"/],
  ['本机一键读取函数', /function readLocalCookie/],
  ['一键书签按钮', /id="cookieBmBtn"/],
  ['一键书签函数', /function cookieBookmarkCode/],
  ['引导卡密码输入', /id="lpPass"/],
].forEach(([name, re]) => {
  if (re.test(html)) bad.push('仍残留：' + name);
});
out.push({ name: '失效入口残留数', value: bad.length });

/* ---------- 3. Cookie 登录接线 ---------- */
[
  ['弹窗粘贴框', /id="cookieInput"/],
  ['弹窗保存按钮', /id="cookieSaveBtn"/],
  ['弹窗保存函数', /async function saveCookie/],
  ['引导卡粘贴框', /id="lpCookieInput"/],
  ['引导卡保存函数', /async function lpSaveCookie/],
  ['校验接口', /\/api\/config\/token/],
  ['dbcl2 校验', /\/dbcl2\/i\.test\(raw\)/],
].forEach(([name, re]) => {
  if (!re.test(html)) bad.push('缺少 Cookie 登录接线：' + name);
});
out.push({ name: 'Cookie 接线', value: 'checked' });

/* ---------- 4. DOM id 引用完整性 ---------- */
const ids = new Set([...html.matchAll(/\sid="([^"]+)"/g)].map(x => x[1]));
const refs = new Set([
  ...[...html.matchAll(/\$\('([^']+)'\)/g)].map(x => x[1]),
  ...[...html.matchAll(/getElementById\('([^']+)'\)/g)].map(x => x[1]),
]);
const missing = [...refs].filter(r => !ids.has(r));
out.push({ name: '引用的 DOM id 数量', value: refs.size });
out.push({ name: '缺失的 id', value: missing.length ? missing : '无' });
if (missing.length) bad.push('index.html 引用了不存在的 id: ' + missing.join(', '));

/* ---------- 5. 内联 JS 语法 ---------- */
const scripts = [...html.matchAll(/<script[^>]*>([\s\S]*?)<\/script>/g)].map(x => x[1]);
scripts.forEach((s, i) => {
  try { new Function(s); } catch (e) { bad.push('第 ' + (i + 1) + ' 段 script 语法错误: ' + e.message); }
});
out.push({ name: '内联 script 段数', value: scripts.length });

console.log(JSON.stringify({ results: out, problems: bad }, null, 2));
process.exitCode = bad.length ? 1 : 0;
