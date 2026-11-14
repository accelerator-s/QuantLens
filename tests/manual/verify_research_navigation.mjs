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
const output = await mkdtemp(join(tmpdir(), 'quantlens-research-navigation-'));
const settingsOnly = process.env.QUANTLENS_SETTINGS_ONLY === '1';
const window = { start: '2020-01-01', end: '2024-12-31' };
const rows = Array.from({ length: 60 }, (_, i) => i);
const sessions = rows.map(i => ({ research_id: `EVO-${i}`, state: 'completed', base_factor_display_name: `因子实验 ${i}`, model_name: '研究模型', phase: '验证完成', progress: 1 }));
const runs = rows.map(i => ({ run_id: `RUN-${i}`, profile: 'fixture', profile_display_name: `${i % 2 ? '策略研究' : '因子实验'} ${i}`, state: 'completed', start: window.start, end: window.end }));
let visibleRuns = runs;
let detailUnavailable = false;
let clusterStage = 'completed';
let taskItems = [];
let failNextJobs = false;
const jobsRequests = [];
const marketRule = {
  name:'CN-A-stock',status:'active',market:'CN-A',instrument_class:'stock',t_plus:1,min_unit:100,
  fractional_shares:false,night_session:false,timezone:'Asia/Shanghai',
  calendar_source:'上交所/深交所官方休市安排',holidays_source:'国务院办公厅节假日安排 + 交易所休市公告',
  settlement:'中国结算 A 股 T+1 交收；资金 T+1 可用、T+1 可取',
  suspension_handling:'停牌期间不计入交易日；复牌首日按正常涨跌幅限制交易（除特殊规定外）。科创板/创业板/北交所盘中临时停牌：涨跌达 ±30% 停牌 10 分钟，达 ±60% 停至当日 14:57 复牌。',
  price_anchor:'收盘价',backtest_anchor:'收盘价',live_window:'14:30-14:57 下单，以接近收盘价成交为目标',
  session_anchor:'交易日以自然日为基准，夜盘归属下一交易日（A 股无夜盘）',
  sessions:[{name:'连续竞价（上午）',start:'09:30',end:'11:30',session_type:'continuous',notes:''}],
  quantity_rules:[{board:'主板',min_unit:100,increment:0,notes:'100 股整手交易'}],
};
const models = [
  {name:'研究模型',kind:'api_model',api_type:'openai',api_model_id:'model-with-a-long-identifier-for-layout-checks',enabled:true,configured:true,capability_tags:['reasoning_high','coding_high']},
  {name:'另一个带有较长名称的研究模型',kind:'api_model',api_type:'anthropic',api_model_id:'another-long-model-name',enabled:false,configured:false,capability_tags:['structured_output','review_strict']},
];
function api(path, query = new URLSearchParams()) {
  if (path === '/api/status') return { server: { instance_id: 'fixture' }, workspace: { missing: 0 } };
  if (path === '/api/backtest/runs') return { items: visibleRuns };
  if (/\/backtest\/runs\/[^/]+$/.test(path)) return {
    profile: 'fixture', profile_display_name: '多因子验证策略', status: { state: 'completed', updated_at: '2026-11-14T06:00:00Z' },
    config: { config_hash: 'ff9d7ec6d20331c2', protocol_hash: '2aca7762a38a8b1a',
      profile: { market: 'CN-A', timescale: 'daily', factors: { items: [{ name: '趋势效率' }, { name: '成交量偏度' }], weighting: 'equal' } },
      config: { start: window.start, end: window.end, initial_capital: 1000000, account_name: '通用研究（包含科创板）' } },
    metrics: { returns: { total_return: .1 } },
  };
  if (path.startsWith('/api/backtest/runs/')) return { items: [], dates: [], total_rows: 0 };
  if (path === '/api/factor-evolution') return { items: sessions };
  if (path.startsWith('/api/factor-evolution/')) return {
    status: sessions[0], config: {}, experiment_ids: [],
    events: [{ event_type: 'window_completed', payload: { fold_id: '1', window_name: 'train', run_id: 'RUN-42' } }],
  };
  if (path === '/api/experiments') return { items: [] };
  if (path === '/api/jobs') {
    const start=Number(query.get('cursor')||0),limit=Number(query.get('limit')||20);
    jobsRequests.push({start,limit});
    return {items:taskItems.slice(start,start+limit),next_cursor:start+limit<taskItems.length?String(start+limit):null};
  }
  if (path === '/api/market-rules') return {items:[marketRule]};
  if (path === '/api/ai/models') return {items:models};
  if (path === '/api/ai/matrix') return {tags:['structured_output'],tasks:[{task:'structured_config',description:'生成配置',required_tags:['structured_output'],optional_tags:[],permitted_executors:['api_model']}]};
  if (path === '/api/ai/route-preview') return {task:'structured_config',required_tags:['structured_output'],optional_tags:[],permitted_executors:['api_model'],candidates:[]};
  if (path === '/api/accounts') return { items: [{ account_id: 'manual', alias: '测试账户' }], default_account_id: 'manual' };
  if (path === '/api/settings/cluster') return {
    available:clusterStage==='completed',training_ready:clusterStage==='completed',
    nodes:['cluster-access','dn-nm-1','dn-nm-2','hbase','hive','metastore-db','nn-rm','spark-client','spark-history'].map(name=>({name,state:'running',health:'healthy'})),
    service_startup:{task_id:'startup-fixture',status:clusterStage,phase:'启动训练服务',error:clusterStage==='failed'?'训练服务启动失败':''},
  };
  return { items: [] };
}
const server = createServer(async (req, res) => {
  try {
    const url = new URL(req.url, 'http://localhost');
    const path = url.pathname;
    if (path === '/api/jobs' && failNextJobs) { failNextJobs=false;res.writeHead(503);res.end(JSON.stringify({detail:'临时读取失败'}));return; }
    if (detailUnavailable && path === '/api/backtest/runs/RUN-42') {
      res.writeHead(404, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ detail: '未找到回测 RUN-42' })); return;
    }
    if (path.startsWith('/api/')) { res.setHeader('Content-Type', 'application/json'); res.end(JSON.stringify(api(path,url.searchParams))); return; }
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
  await call('Page.navigate', { url: base + (settingsOnly?'/#settings/system':'/#backtest') });
  if (!settingsOnly) {
  async function checkTables(page, selector) {
    for (const theme of ['light', 'dark']) for (const width of [1440, 900, 390]) {
      await call('Emulation.setDeviceMetricsOverride', { width, height: 900, deviceScaleFactor: 1, mobile: false });
      await js(`document.documentElement.dataset.theme='${theme}';await new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)))`);
      const result = await js(`(()=>{const tables=[...document.querySelectorAll(${JSON.stringify(selector)})];return {page:${JSON.stringify(page)},width:innerWidth,theme:document.documentElement.dataset.theme,overflow:document.documentElement.scrollWidth>innerWidth,tables:tables.map(e=>{e.scrollTop=e.scrollHeight;e.scrollLeft=e.scrollWidth;const r=e.getBoundingClientRect(),t=e.querySelector('table').getBoundingClientRect(),s=getComputedStyle(e);return {scrolls:e.scrollHeight>e.clientHeight,gutter:s.scrollbarGutter,rightGap:r.right-t.right,bottomGap:r.bottom-t.bottom,paddingRight:parseFloat(s.paddingRight),paddingBottom:parseFloat(s.paddingBottom),rows:e.querySelectorAll('tbody tr').length}})}})()`);
      assert(!result.overflow, JSON.stringify(result));
      assert(result.tables.length > 0, JSON.stringify(result));
      for (const table of result.tables) {
        assert(table.scrolls && table.gutter === 'stable' && table.paddingRight >= 12 && table.paddingBottom >= 10, JSON.stringify(result));
        assert(table.rightGap >= 11 && table.bottomGap >= 9, JSON.stringify(result));
        assert.equal(table.rows, 60);
      }
      report.push(result);
      if (theme === 'light' && [1440,390].includes(width)) {
        const screenshot = await call('Page.captureScreenshot', { format: 'png' });
        await writeFile(join(output, `${page}-${width}.png`), Buffer.from(screenshot.data, 'base64'));
      }
    }
  }
  await navigate('backtest');
  await wait("document.querySelectorAll('[data-view=backtest] tbody tr').length===60 && document.querySelector('[data-view=backtest] .metric')");
  assert.equal(await js("document.querySelector('[data-page-title]').textContent"), '回测结果');
  assert(await js("!document.querySelector('[data-view=backtest] [role=tab]') && !document.querySelector('[data-view=backtest] [data-backtest-start]')"));
  assert(await js("!document.querySelector('[data-view=backtest] > .backtest > .toolbar')"));
  assert(await js("document.querySelector('[data-view=backtest]').textContent.includes('因子实验') && document.querySelector('[data-view=backtest]').textContent.includes('策略研究')"));
  await checkTables('backtest', '[data-view=backtest] .backtest__runs');
  await js("window.retainedBacktestDetail=document.querySelector('[data-view=backtest] [data-detail] h3')");
  visibleRuns = [{ ...runs[0], run_id: 'RUN-new' }, ...runs];
  await wait("document.querySelectorAll('[data-view=backtest] [data-list] tbody tr').length===61");
  assert(await js("window.retainedBacktestDetail===document.querySelector('[data-view=backtest] [data-detail] h3')"), 'New published results must appear without rebuilding the selected detail');
  visibleRuns = runs;
  await navigate('factors/experiments');
  await wait("document.querySelectorAll('.evolution-sessions tbody tr').length===60");
  await checkTables('factor', '.evolution-sessions');
  await js("document.querySelectorAll('.evolution-sessions tbody tr').forEach((e,i)=>{if(i)e.remove()});await new Promise(r=>requestAnimationFrame(r))");
  assert(await js("(()=>{const e=document.querySelector('.evolution-sessions');return e.scrollHeight===e.clientHeight})()"), 'Short tables must not have a vertical scrollbar');
  await wait("[...document.querySelectorAll('.experiments__run-link')].some(e=>e.textContent==='RUN-42')");
  async function openLinkedRun() {
    await js("[...document.querySelectorAll('.experiments__run-link')].find(e=>e.textContent==='RUN-42').click()");
    await wait("location.hash==='#backtest' && !document.querySelector('[data-view=backtest]').hidden");
  }
  visibleRuns = runs.filter(run => run.run_id !== 'RUN-42');
  await openLinkedRun();
  await wait("document.querySelector('[data-view=backtest] [data-detail] h3')?.textContent==='RUN-42'");
  assert(await js("!document.querySelector('[data-view=backtest] tbody tr.is-active')"), 'A linked run outside the list must not select the first run');
  await navigate('factors/experiments');
  visibleRuns = [];
  await openLinkedRun();
  await wait("document.querySelector('[data-view=backtest] [data-list]').textContent.includes('暂无已发布回测') && document.querySelector('[data-view=backtest] [data-detail] h3')?.textContent==='RUN-42'");
  await navigate('factors/experiments');
  detailUnavailable = true;
  await openLinkedRun();
  await wait("document.querySelector('[data-view=backtest] [data-detail]').textContent.includes('未找到回测 RUN-42')");
  assert(await js("!document.querySelector('[data-view=backtest] .metric')"), 'An unpublished run must show the API error without stale results');
  detailUnavailable = false;
  visibleRuns = runs;
  await wait("document.querySelector('[data-view=backtest] tbody tr.is-active')?.dataset.runId==='RUN-42' && document.querySelector('[data-view=backtest] [data-detail] h3')?.textContent==='RUN-42'");
  }
  await navigate('settings/system');
  await wait("document.querySelector('[data-view=settings] [data-pane=tasks] h3')?.textContent==='任务记录'");
  assert.deepEqual(await js("[...document.querySelectorAll('[data-pane=tasks] button:not([hidden])')].map(e=>e.textContent)"), ['刷新']);
  assert(await js("document.querySelector('[data-pane=tasks] [data-refresh]').parentElement.tagName==='HEADER'"), 'Task refresh belongs on the title row');
  for (const width of [1440,390]) {
    await call('Emulation.setDeviceMetricsOverride', {width,height:900,deviceScaleFactor:1,mobile:false});
    await js('await new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)))');
    const layout=await js("(()=>{const c=document.querySelector('[data-pane=tasks] .ql-task-records'),h=c.querySelector('header'),t=h.querySelector('h3').getBoundingClientRect(),b=h.querySelector('button').getBoundingClientRect();return {overflow:document.documentElement.scrollWidth>innerWidth,alignment:Math.abs((t.top+t.bottom-b.top-b.bottom)/2),gap:parseFloat(getComputedStyle(c).rowGap)}})()");
    assert(!layout.overflow && layout.alignment<2 && layout.gap<=12,JSON.stringify(layout));
    const screenshot=await call('Page.captureScreenshot',{format:'png'});
    await writeFile(join(output,`tasks-${width}.png`),Buffer.from(screenshot.data,'base64'));
  }
  assert.deepEqual(await js("[...document.querySelectorAll('[data-view=settings] [role=tab]')].map(e=>e.textContent)"), ['数据同步', '模型配置', '市场规则', '任务', '集群']);
  await navigate('settings/cluster');
  await wait("document.querySelector('[data-pane=cluster] [data-service-summary]')?.textContent==='服务已就绪'");
  assert.equal(await js("document.querySelectorAll('[data-pane=cluster] .ql-card').length"),1);
  assert(await js("!document.querySelector('[data-service-log]') && !document.querySelector('[data-pane=cluster] pre')"));
  for (const phase of ['running','failed','completed']) {
    clusterStage=phase;
    await js("document.querySelector('[data-pane=cluster] [data-refresh]').click()");
    await wait(`document.querySelector('[data-service-summary]').textContent.includes('${phase==='running'?'启动中':phase==='failed'?'启动失败':'服务已就绪'}')`);
    assert.equal(await js("document.querySelector('[data-start]').disabled"),phase==='running');
    assert.equal(await js("document.querySelector('[data-stop]').disabled"),phase==='running');
    assert(await js("!document.querySelector('[data-service-task]') && !document.querySelector('.ql-task-dialog')"));
  }
  for (const theme of ['light','dark']) for (const width of [1440,390]) {
    await call('Emulation.setDeviceMetricsOverride',{width,height:900,deviceScaleFactor:1,mobile:false});
    await js(`document.documentElement.dataset.theme='${theme}';await new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)))`);
    const layout=await js("(()=>{const c=document.querySelector('.ql-cluster-card'),header=c.querySelector('header').getBoundingClientRect(),button=c.querySelector('[data-refresh]').getBoundingClientRect();return {overflow:document.documentElement.scrollWidth>innerWidth,height:c.getBoundingClientRect().height,headerContainsRefresh:button.top>=header.top&&button.bottom<=header.bottom,gap:parseFloat(getComputedStyle(c).rowGap)}})()");
    assert(!layout.overflow&&layout.headerContainsRefresh&&layout.gap<=12,JSON.stringify(layout));
    const screenshot=await call('Page.captureScreenshot',{format:'png'});
    await writeFile(join(output,`cluster-${width}-${theme}.png`),Buffer.from(screenshot.data,'base64'));
    report.push({page:'cluster',width,theme,...layout});
  }
  assert(await js("document.querySelector('[data-service-summary]').dataset.state==='ok'"));
  assert.equal(await js("document.querySelectorAll('.ql-service-item [title=状态] .badge--accent').length"),10);
  assert.equal(await js("document.querySelectorAll('.ql-service-item [title=健康] .badge--safe').length"),10);
  assert(await js("getComputedStyle(document.querySelector('.service-status__dot')).backgroundColor===getComputedStyle(document.querySelector('.topbar__dot')).backgroundColor"));
  taskItems=Array.from({length:45},(_,i)=>({task_id:`task-${i}`,name:`${['因子研究','策略研究','数据同步'][i%3]}：完整任务名称 ${i+1}${i===0?'，用于检查长任务名称折行以及状态和时间的对齐效果':''}`,status:['running','completed','failed','queued','interrupted','cancelled'][i%6],created_at:'2026-11-14T10:00:00Z'}));
  await navigate('settings/system');
  await js("document.querySelector('[data-pane=tasks] [data-refresh]').click()");
  await wait("document.querySelectorAll('.ql-task-row').length===20 && !document.querySelector('[data-load-more]').disabled");
  assert(await js("document.querySelectorAll('.ql-task-row .badge').length===20 && !!document.querySelector('.ql-task-row .badge--danger') && !document.querySelector('[data-task]')"));
  await js("window.firstTaskRow=document.querySelector('.ql-task-row')");
  failNextJobs=true;
  await js("document.querySelector('[data-load-more]').click()");
  await wait("!document.querySelector('[data-load-more]').disabled && !document.querySelector('.ql-task-list').hasAttribute('aria-busy')");
  assert.equal(await js("document.querySelectorAll('.ql-task-row').length"),20);
  await js("document.querySelector('[data-load-more]').click();document.querySelector('[data-load-more]').click()");
  await wait("document.querySelectorAll('.ql-task-row').length===40");
  assert(await js("window.firstTaskRow===document.querySelector('.ql-task-row')"));
  await js("document.querySelector('[data-load-more]').click()");
  await wait("document.querySelectorAll('.ql-task-row').length===45 && document.querySelector('[data-load-more]').hidden");
  assert.equal(new Set(await js("[...document.querySelectorAll('.ql-task-row strong')].map(e=>e.textContent)")).size,45);
  await js("document.querySelector('[data-pane=tasks] [data-refresh]').click()");
  await wait("document.querySelectorAll('.ql-task-row').length===20 && !document.querySelector('[data-load-more]').hidden");
  assert(jobsRequests.every(req=>req.limit===20));
  async function checkSettings(page,route,selector) {
    await navigate(route);
    await wait(`document.querySelector(${JSON.stringify(selector)})`);
    if (page==='tasks') await wait("document.querySelectorAll('.ql-task-row').length===20");
    if (page==='rules') await wait("document.querySelectorAll('[data-rule-section=overview] .kv__row').length===13");
    if (page==='models') await wait("document.querySelectorAll('.ai-config__models-table tbody tr').length===2");
    for (const theme of ['light','dark']) for (const width of [1440,900,390]) {
      await call('Emulation.setDeviceMetricsOverride',{width,height:1000,deviceScaleFactor:1,mobile:false});
      await js(`document.documentElement.dataset.theme='${theme}';await new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)))`);
      const layout=await js(`(()=>{const root=document.querySelector(${JSON.stringify(selector)}),buttons=[...root.querySelectorAll('button.btn')],standard=parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--control-h'));return {overflow:document.documentElement.scrollWidth>innerWidth,standard,buttons:buttons.map(b=>{const r=b.getBoundingClientRect(),p=b.parentElement.getBoundingClientRect();return {height:r.height,font:getComputedStyle(b).fontSize,inside:r.left>=p.left-1&&r.right<=p.right+1}}),overviewHeight:root.querySelector('[data-rule-section=overview]')?.getBoundingClientRect().height}})()`);
      if(layout.overflow){
        const screenshot=await call('Page.captureScreenshot',{format:'png'});
        await writeFile(join(output,`${page}-${width}-${theme}-overflow.png`),Buffer.from(screenshot.data,'base64'));
        layout.outside=await js("[...document.querySelectorAll('.ai-config *')].filter(e=>e.getBoundingClientRect().right>innerWidth+1).slice(0,20).map(e=>({tag:e.tagName,cls:e.className,width:e.getBoundingClientRect().width,right:e.getBoundingClientRect().right,display:getComputedStyle(e).display}))");
      }
      assert(!layout.overflow,JSON.stringify({page,width,theme,...layout}));
      if(page==='tasks') {
        const rows=await js("[...document.querySelectorAll('.ql-task-row')].map(e=>{const r=e.getBoundingClientRect();return {left:r.left,right:r.right,top:r.top,bottom:r.bottom,height:r.height}})");
        assert(rows.every((r,i)=>r.height<110&&(!i||(r.top>=rows[i-1].bottom-1&&Math.abs(r.left-rows[i-1].left)<1))),JSON.stringify(rows));
      }
      if(page==='models'){
        const cells=await js("(()=>{const root=document.querySelector('.ai-config__models-table'),r=root.getBoundingClientRect();return [...root.querySelectorAll('tbody td')].map(c=>{const b=c.getBoundingClientRect();return {label:c.dataset.label,left:b.left,right:b.right,inside:b.left>=r.left-1&&b.right<=r.right+1}})})()");
        assert(cells.every(c=>c.inside&&c.label),JSON.stringify({page,width,theme,cells}));
      }
      for (const button of layout.buttons) assert(button.inside&&Math.abs(button.height-layout.standard)<2,JSON.stringify({page,width,theme,button,standard:layout.standard}));
      if (page==='rules'&&width===1440) assert(layout.overviewHeight<310,JSON.stringify(layout));
      const screenshot=await call('Page.captureScreenshot',{format:'png'});
      await writeFile(join(output,`${page}-${width}-${theme}.png`),Buffer.from(screenshot.data,'base64'));
      report.push({page,width,theme,...layout});
    }
  }
  await checkSettings('tasks','settings/system','[data-pane=tasks]');
  await checkSettings('cluster','settings/cluster','[data-pane=cluster]');
  await checkSettings('models','settings/interfaces','.ai-config');
  assert.equal(await js("document.querySelectorAll('.ai-config__models-table tbody tr').length"),models.length);
  await checkSettings('rules','settings/rules','[data-rule-detail]');
  assert.equal(await js("document.querySelectorAll('[data-rule-section=overview] .kv__row').length"),13);
  assert(await js("document.querySelector('[data-rule-section=overview]').textContent.includes('复牌首日按正常涨跌幅限制交易')"));
  assert(await js("!document.querySelector('[data-pane=review]') && !document.querySelector('link[href*=\"/components/review/\"]')"));
  assert.deepEqual(errors, []);
  await writeFile(join(output, 'verification.json'), JSON.stringify(report, null, 2));
  console.log(`PASS: ${settingsOnly?'settings controls, status colors, long names, compact rules':'research navigation and settings'}, ${report.length} layouts. ${output}`);
} finally { socket?.close(); chrome.kill(); server.close(); }
