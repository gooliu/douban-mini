/* 云端部署改造自检（纯静态，不依赖浏览器）
 * 1. 新增元素 wakeHint / bbCloudTip / bbLocalTip 是否都在 HTML 里
 * 2. JS 里 $('xxx') 引用的 id 是否都存在（防止手改 HTML 漏掉）
 * 3. DEFAULT_CLOUD_API 是否已清空（不能残留家宽 IP）
 * 4. Dockerfile 关键项：PORT / VOLUME / DOUBAN_DATA_DIR / proxy-headers
 * 5. README 是否还残留旧家宽 IP
 */
const fs = require('fs');
const path = require('path');

const ROOT = path.join(__dirname, '..');
const html = fs.readFileSync(path.join(ROOT, 'web/index.html'), 'utf8');
const dockerfile = fs.readFileSync(path.join(ROOT, 'Dockerfile'), 'utf8');
const readme = fs.readFileSync(path.join(ROOT, 'README.md'), 'utf8');

const out = [];
function check(name, cond, extra) {
  out.push({ name, pass: !!cond, extra: extra === undefined ? '' : String(extra) });
}

// 1. 新增元素
['wakeHint', 'bbCloudTip', 'bbLocalTip'].forEach(id => {
  check('HTML 含 #' + id, html.includes('id="' + id + '"'));
});

// 2. $('id') 引用完整性
const ids = new Set();
const idRe = /\bid="([^"]+)"/g;
let m;
while ((m = idRe.exec(html))) ids.add(m[1]);
const missing = [];
const refRe = /\$\('([^']+)'\)/g;
while ((m = refRe.exec(html))) {
  if (!ids.has(m[1])) missing.push(m[1]);
}
check('JS 引用的 DOM id 全部存在', missing.length === 0,
  missing.length ? '缺失: ' + [...new Set(missing)].join(', ') : ids.size + ' 个 id');

// 3. DEFAULT_CLOUD_API
const apiLine = (html.match(/const DEFAULT_CLOUD_API\s*=\s*'([^']*)'/) || [])[1];
check('DEFAULT_CLOUD_API 不含家宽 IP', apiLine !== undefined && !/\d+\.\d+\.\d+\.\d+/.test(apiLine),
  '当前值: ' + JSON.stringify(apiLine));

// 4. Dockerfile
check('Dockerfile 支持 $PORT', dockerfile.includes('${PORT:-8000}'));
check('Dockerfile 声明持久卷 /data', dockerfile.includes('VOLUME') && dockerfile.includes('/data'));
check('Dockerfile 默认云端模式', /DOUBAN_CLOUD=1/.test(dockerfile));
check('Dockerfile 设 DOUBAN_DATA_DIR', /DOUBAN_DATA_DIR=\/data/.test(dockerfile));
check('Dockerfile 带 --proxy-headers', dockerfile.includes('--proxy-headers'));

// 5. README 不应再把旧 IP 当成有效配置
const badIp = readme.match(/211\.91\.184\.194/g) || [];
check('README 只在「不要再写家宽 IP」的警示里提到旧 IP', badIp.length <= 1,
  '出现次数: ' + badIp.length);
check('README 有云端部署章节', readme.includes('云端部署：让网址 24/7 可用'));

const failed = out.filter(o => !o.pass);
console.log(JSON.stringify({ total: out.length, failed: failed.length, results: out }, null, 2));
process.exit(failed.length ? 1 : 0);
