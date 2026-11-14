// Isolated browser fixtures exercise the real router and long research tables.
import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { readFile, writeFile, mkdtemp } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawn } from 'node:child_process';
import { setTimeout as delay } from 'node:timers/promises';

const root = fileURLToPath(new URL('../../src/webui/frontend', import.meta.url));
const output = await mkdtemp(join(tmpdir(), 'quantlens-backtest-selection-'));
const runs = Array.from({length: 60}, (_, i) => ({run_id:`RUN-${i}`, profile:'fixture', profile_display_name:`策略 ${i}`,
  start:'2020-01-01', end:'2024-12-31', state:'completed', created_at:new Date(Date.UTC(2026,0,60-i)).toISOString()}));
let visibleRuns = runs, listReads = 0, detailReads = [];
const session = {research_id:'EVO-1',state:'completed',base_factor_display_name:'因子验证',phase:'验证完成'};
function api(path) {
  if (path === '/api/status') return {server:{instance_id:'fixture'},workspace:{missing:0}};
  if (path === '/api/backtest/runs') { listReads++; return {items:visibleRuns}; }
  if (/^\/api\/backtest\/runs\/[^/]+$/.test(path)) {
    detailReads.push(path.split('/').at(-1));
    return {profile:'fixture',status:{state:'completed'},config:{},metrics:{returns:{total_return:.1}}};
  }
  if (path.startsWith('/api/backtest/runs/')) return {items:[],dates:[],total_rows:0};
  if (path === '/api/factor-evolution') return {items:[session]};
  if (path.startsWith('/api/factor-evolution/')) return {status:session,config:{},experiment_ids:[],events:[
    {event_type:'window_completed',payload:{fold_id:'1',window_name:'train',run_id:'RUN-42'}}]};
  if (path === '/api/strategies/experiments') return {items:[{experiment_id:'strategy-1',display_name:'策略验证',state:'completed'}]};
  if (path === '/api/strategies/experiments/strategy-1') return {display_name:'策略验证',factor_display_names:{},
    spec:{model:'model_linear',train_start:'2020-01-01',train_end:'2022-12-31',start:'2023-01-01',end:'2024-12-31',top_n:20},
    task:{status:'completed',log:['完成']},result:{state:'completed',run_id:'RUN-58',weights:{},selection:[],metrics:{returns:{total_return:.1},risk:{}}}};
  return {items:[]};
}
const server = createServer(async(req,res) => {
  try {
    const path = new URL(req.url,'http://localhost').pathname;
    if (path.startsWith('/api/')) {res.setHeader('Content-Type','application/json');res.end(JSON.stringify(api(path)));return;}
    const file = path === '/' ? '/index.html' : path;
    res.setHeader('Content-Type',file.endsWith('.css')?'text/css':file.endsWith('.js')?'text/javascript':file.endsWith('.svg')?'image/svg+xml':'text/html; charset=utf-8');
    res.end(await readFile(root+file));
  } catch {res.writeHead(404);res.end();}
});
await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
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
  await call('Emulation.setDeviceMetricsOverride',{width:1440,height:1000,deviceScaleFactor:1,mobile:false});
  await call('Page.navigate',{url:base+'/#factors/experiments'});
  const list='[data-view=backtest] [data-list]', detail='[data-view=backtest] [data-detail] h3';
  const page=()=>js(`document.querySelector('${list} .table-pagination__current').textContent`);
  const active=()=>js(`document.querySelector('${list} tr.is-active')?.dataset.runId`);
  const click=selector=>js(`document.querySelector(${JSON.stringify(selector)}).click()`);
  async function openFactor() {
    await navigate('factors/experiments');
    await wait(`!!document.querySelector('#factors-panel-experiments .experiments__run-link')`);
    await click('#factors-panel-experiments .experiments__run-link');
    await wait(`location.hash==='#backtest'&&document.querySelector('${detail}')?.textContent==='RUN-42'`);
  }
  await openFactor();
  assert.equal(await page(),'5 / 6 页');
  assert.equal(await active(),'RUN-42');
  assert.equal(await js(`document.querySelector('${list} tr.is-active').getAttribute('aria-selected')`),'true');
  await click(`${list} [data-page=prev]`);
  await navigate('factors/experiments');await navigate('backtest');await delay(100);
  assert.equal(await page(),'4 / 6 页','Ordinary navigation preserves the manually selected page');
  const readsBefore=listReads, detailBefore=detailReads.length;
  await openFactor();await wait(`${JSON.stringify('RUN-42')}===document.querySelector('${list} tr.is-active')?.dataset.runId`);
  assert.equal(await page(),'5 / 6 页');
  await wait(`!document.querySelector('[data-view=backtest] .state-card--loading')`);
  assert.equal(detailReads.length,detailBefore,'Repeated deep link reuses the loaded detail');
  assert(listReads>readsBefore);
  await js(`document.querySelector('${list} [data-page-size] [data-select-trigger]').click();document.querySelector('${list} [data-page-size] [data-value="20"]').click()`);
  await click(`${list} [data-page=last]`);await click(`${list} [data-run-id=RUN-45]`);
  await wait(`document.querySelector('${detail}')?.textContent==='RUN-45'`);
  await navigate('factors/experiments');await navigate('backtest');await delay(100);
  assert.equal(await page(),'3 / 3 页');assert.equal(await active(),'RUN-45');
  assert.equal(await js(`document.querySelector('${list} [data-page-size] [data-select-value]').textContent`),'20');
  await navigate('strategy-research/experiments');
  await wait(`!!document.querySelector('#strategy-research-panel-experiments .experiments__run-link')`);
  await click('#strategy-research-panel-experiments .experiments__run-link');
  await wait(`document.querySelector('${detail}')?.textContent==='RUN-58'`);
  assert.equal(await page(),'3 / 3 页');assert.equal(await active(),'RUN-58');
  await click(`${list} [data-page=first]`);
  // A data invalidation refresh must preserve a page chosen after a deep link.
  visibleRuns=[{...runs[0],run_id:'RUN-new'},...runs];
  await js(`window.dispatchEvent(new Event('quantlens:changed'))`);
  await navigate('factors/experiments');await navigate('backtest');
  await wait(`document.querySelector('${list} tbody tr')?.dataset.runId==='RUN-new'`);
  assert.equal(await page(),'1 / 4 页');
  assert.equal(await js(`document.querySelector('${detail}').textContent`),'RUN-58');
  // A missing list entry may still open its detail, with no unrelated row selected.
  visibleRuns=runs.filter(run=>run.run_id!=='RUN-42');
  await openFactor();assert.equal(await active(),undefined);
  visibleRuns=runs;
  await js(`window.dispatchEvent(new Event('quantlens:changed'))`);
  await navigate('strategy-research/experiments');await navigate('backtest');
  await wait(`document.querySelector('${list} tr.is-active')?.dataset.runId==='RUN-42'`);
  assert.equal(await page(),'3 / 3 页');
  // Even an empty refreshed list must retain the explicitly opened run detail.
  await navigate('strategy-research/experiments');
  await click('#strategy-research-panel-experiments .experiments__run-link');
  await wait(`document.querySelector('${detail}')?.textContent==='RUN-58'`);
  visibleRuns=[];
  await openFactor();
  await wait(`document.querySelector('${list} .state-card')?.textContent.includes('暂无已发布回测')`);
  assert.equal(await js(`document.querySelector('${detail}').textContent`),'RUN-42');
  assert.deepEqual(errors,[]);
  const shot=await call('Page.captureScreenshot',{format:'png'});
  await writeFile(join(output,'selected-run.png'),Buffer.from(shot.data,'base64'));
  console.log(`PASS: real factor/strategy routing, first/repeated deep links, correct page and selection, page size and manual page retained, cached detail, refreshed/missing rows. ${output}`);
} finally {socket?.close();chrome.kill();server.close();}
