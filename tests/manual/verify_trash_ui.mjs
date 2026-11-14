// Isolated fixtures exercise fixed retention, trash actions and responsive layouts.
import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { readFile, writeFile, mkdtemp } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawn } from 'node:child_process';
import { setTimeout as delay } from 'node:timers/promises';

const root = fileURLToPath(new URL('../../src/webui/frontend', import.meta.url));
const output = await mkdtemp(join(tmpdir(), 'quantlens-trash-ui-'));
let items = [
  { bucket: 'factor-1', name: '趋势效率因子', category: 'factor', trashed_at: '2026-11-14T02:00:00Z' },
  { bucket: 'strategy-1', name: '多因子平衡策略', category: 'strategy_profile', trashed_at: '2026-11-14T02:00:00Z' },
];
function api(path, method, body) {
  if (path === '/api/status') return { server: { instance_id: 'fixture' }, workspace: { missing: 0 } };
  if (path === '/api/trash' && method === 'GET') return { items: items.map(item => ({ ...item, expires_at: '2026-12-14T02:00:00Z' })) };
  if (method === 'POST') {
    if (!body.confirm) return { requires_confirm: true, preview: { '操作': 'fixture preview' } };
    if (path === '/api/trash/empty') { items = []; return { count: 1 }; }
    const bucket = path.split('/')[3];
    items = items.filter(item => item.bucket !== bucket);
    return { restored_to: 'fixture', purged: bucket };
  }
  return { items: [] };
}
const server = createServer(async (req, res) => {
  try {
    const path = new URL(req.url, 'http://localhost').pathname;
    if (path.startsWith('/api/')) {
      let raw = ''; for await (const part of req) raw += part;
      res.setHeader('Content-Type', 'application/json');
      res.end(JSON.stringify(api(path, req.method, raw ? JSON.parse(raw) : {}))); return;
    }
    const file = path === '/' ? '/index.html' : path;
    res.setHeader('Content-Type', file.endsWith('.css') ? 'text/css' : file.endsWith('.js') ? 'text/javascript' : file.endsWith('.svg') ? 'image/svg+xml' : 'text/html; charset=utf-8');
    res.end(await readFile(root + file));
  } catch { res.writeHead(404); res.end(); }
});
await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
const base = `http://127.0.0.1:${server.address().port}`;
const chrome = spawn(process.env.QUANTLENS_CHROME_BIN || 'google-chrome', ['--headless=new', '--no-sandbox', '--disable-gpu', '--remote-debugging-port=0', `--user-data-dir=${join(output, 'browser')}`, 'about:blank'], { stdio: ['ignore', 'ignore', 'pipe'] });
let socket;
try {
  const endpoint = await new Promise((resolve, reject) => {
    let log = '';
    const timer = setTimeout(() => reject(Error(log)), 10000);
    chrome.stderr.on('data', chunk => { log += chunk; const match = log.match(/DevTools listening on (ws:\/\/\S+)/); if (match) { clearTimeout(timer); resolve(match[1]); } });
    chrome.once('error', reject);
  });
  const target = await (await fetch(`http://127.0.0.1:${new URL(endpoint).port}/json/new?about:blank`, { method: 'PUT' })).json();
  socket = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise(resolve => { socket.onopen = resolve; });
  let serial = 0;
  const pending = new Map(), errors = [], report = [];
  socket.onmessage = event => {
    const result = JSON.parse(event.data);
    if (result.method === 'Runtime.exceptionThrown') errors.push(result.params);
    if (result.id) { const entry = pending.get(result.id); pending.delete(result.id); result.error ? entry.reject(Error(JSON.stringify(result.error))) : entry.resolve(result.result); }
  };
  const call = (method, params = {}) => new Promise((resolve, reject) => { const id = ++serial; pending.set(id, { resolve, reject }); socket.send(JSON.stringify({ id, method, params })); });
  async function js(expression) { const result = await call('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true, replMode: true }); assert(!result.exceptionDetails, JSON.stringify(result.exceptionDetails)); return result.result.value; }
  async function wait(expression) { for (let i = 0; i < 160; i++) { if (await js(expression).catch(() => false)) return; await delay(40); } throw Error(expression + '\n' + await js('document.body.innerText')); }
  async function navigate(route) { await js(`location.hash=${JSON.stringify(route)}`); await wait(`document.querySelector('[data-view="${route.split('/')[0]}"]:not([hidden])')`); }
  await call('Page.enable'); await call('Runtime.enable');
  await call('Page.navigate', { url: base + '/#trash' });
  await wait("document.querySelectorAll('[data-trash-list] tbody tr').length===2");
  assert.equal(await js("document.querySelector('[data-page-title]').textContent"), '回收站');
  assert.deepEqual(await js("[...document.querySelectorAll('[data-trash-count]')].map(e=>e.textContent)"), ['2','1','1']);
  assert.equal(await js("document.querySelector('.trash__heading p').textContent"), '删除的内容将在此保留30天');
  assert(await js("!document.querySelector('.trash a') && !document.querySelector('[data-trash-retention]')"));
  const headers = await js("[...document.querySelectorAll('[data-trash-list] th')].map(e=>e.textContent)");
  assert.deepEqual(headers, ['名称','类型','删除时间','永久删除时间','操作']);
  assert(await js("document.querySelector('[data-trash-list]').textContent.includes('趋势效率因子') && document.querySelector('[data-trash-list]').textContent.includes('多因子平衡策略')"));
  for (const width of [1440, 900, 390]) {
    await call('Emulation.setDeviceMetricsOverride', { width, height: 900, deviceScaleFactor: 1, mobile: false });
    await js('await new Promise(r=>requestAnimationFrame(r))');
    assert(await js('document.documentElement.scrollWidth<=innerWidth'));
    if (width === 1440) assert(await js("(()=>{const [a,b]=document.querySelector('.trash__actions').children;return Math.abs(a.getBoundingClientRect().top-b.getBoundingClientRect().top)<2 && b.getBoundingClientRect().left>a.getBoundingClientRect().left})()"));
  }
  await call('Emulation.setDeviceMetricsOverride', { width: 1440, height: 900, deviceScaleFactor: 1, mobile: false });
  let screenshot = await call('Page.captureScreenshot', { format: 'png' });
  await writeFile(join(output, 'trash-populated.png'), Buffer.from(screenshot.data, 'base64'));
  await wait("document.querySelector('[data-trash-list]').textContent.includes('2026/12/14') || document.querySelector('[data-trash-list]').textContent.includes('2026-12-14')");
  await js("document.querySelector('.trash__actions .btn--ghost').click()");
  await wait("document.querySelectorAll('[data-trash-list] tbody tr').length===1");
  assert.deepEqual(await js("[...document.querySelectorAll('[data-trash-count]')].map(e=>e.textContent)"), ['1','0','1']);
  await js("document.querySelector('.trash__actions .btn--danger').click()");
  await wait("document.querySelector('.dialog__title')?.textContent==='永久删除 多因子平衡策略'");
  assert(await js("!document.querySelector('.dialog .confirm__message') && !document.querySelector('.dialog .kv')"));
  await js("document.querySelector('.dialog__actions .btn--ghost').click()");
  await js("document.querySelector('[data-trash-empty]').click()");
  await wait("document.querySelector('.dialog__title')?.textContent==='清空回收站'");
  await js("document.querySelector('.dialog__actions .btn--danger').click()");
  await wait("document.querySelector('[data-trash-list] .state-card--empty')");
  assert(await js("document.querySelector('[data-trash-empty]').disabled"));
  assert.deepEqual(await js("[...document.querySelectorAll('[data-trash-count]')].map(e=>e.textContent)"), ['0','0','0']);
  assert(await js("!document.querySelector('.trash a')"));
  for (const theme of ['light', 'dark']) for (const width of [1440, 900, 390]) {
    await call('Emulation.setDeviceMetricsOverride', { width, height: 900, deviceScaleFactor: 1, mobile: false });
    await js(`document.documentElement.dataset.theme='${theme}';await new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)))`);
    const result = await js(`(()=>{const card=document.querySelector('[data-trash-list] .state-card--empty'), title=card.querySelector('.state-card__title'), icon=card.querySelector('.state-card__icon');const r=card.getBoundingClientRect(),t=title.getBoundingClientRect();return {theme:document.documentElement.dataset.theme,width:innerWidth,overflow:document.documentElement.scrollWidth>innerWidth,font:parseFloat(getComputedStyle(title).fontSize),icon:parseFloat(getComputedStyle(icon).width),height:r.height,horizontalOffset:Math.abs(t.x+t.width/2-r.x-r.width/2),verticalOffset:Math.abs(t.y+t.height/2-r.y-r.height/2)}})()`);
    assert(!result.overflow && result.font >= 14 && result.font <= 18 && result.icon === 30 && result.height >= 180 && result.height <= 240, JSON.stringify(result));
    assert(result.horizontalOffset < 5 && result.verticalOffset < 50, JSON.stringify(result));
    report.push(result);
    if (theme === 'light' && [1440, 390].includes(width)) {
      screenshot = await call('Page.captureScreenshot', { format: 'png' });
      await writeFile(join(output, `trash-empty-${width}.png`), Buffer.from(screenshot.data, 'base64'));
    }
  }
  assert.deepEqual(errors, []);
  await writeFile(join(output, 'verification.json'), JSON.stringify(report, null, 2));
  await navigate('settings');
  await wait("document.querySelector('[data-view=settings] [role=tab]')");
  assert(await js("![...document.querySelectorAll('[data-view=settings] [role=tab]')].some(e=>e.textContent==='常规') && !document.querySelector('[data-trash-settings]')"));
  console.log(`PASS: fixed retention copy, no settings or navigation links, names, counts, restore, purge and ${report.length} compact empty layouts. ${output}`);
} finally { socket?.close(); chrome.kill(); server.close(); }
