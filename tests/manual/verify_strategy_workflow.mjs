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
const output = await mkdtemp(join(tmpdir(), 'quantlens-strategy-workflow-'));
let status = 'draft', emptyCatalog = false, emptySessions = false, filterFixtures = false;
const calls = [];
const chats = new Map();
let generated = false, strategyApproved = false, factorGenerated = false, factorApproved = false;
const accountSnapshot = {account_id:'test-account',alias:'研究账户',capital:'1000000'};
const spec = {account_id:'test-account',display_name:'质量与景气组合',model:'model_linear',factors:['quality','growth'],train_start:'2020-01-01',train_end:'2022-12-15',start:'2023-01-01',end:'2023-12-31',capital:1000000,top_n:20,rebalance_frequency:'monthly'};
const candidates = [
  {method:'equal',max_positions:20,frequency:'monthly'},
  {method:'model_ridge',max_positions:20,frequency:'monthly'},
  {method:'equal',max_positions:20,frequency:'quarterly'},
];
const trainingResults = candidates.map((candidate,i)=>({candidate,folds:[2020,2021,2022].map((year,j)=>({
  run_id:`BT-inner-${i}-${j}`,window:{start:`${year}-01-01`,end:`${year}-12-15`},
  metrics:{returns:{annualized_return:(i+1)*.01+j*.001,annualized_excess_return:-.02},risk:{max_drawdown:-.1}},
}))}));
const finalMetrics={returns:{total_return:.15,annualized_return:.15},risk:{sharpe:1.2,max_drawdown:-.08},window:{start:'2023-01-03',end:'2023-12-29'}};
const strategy = () => ({profile_name:'strategy_fixture',display_name:spec.display_name,hypothesis:'质量与成长互补，盈利恶化时可能失效。',status,model:spec.model,factors:spec.factors,spec,account_id:'test-account',account_snapshot:accountSnapshot,
  factor_display_names:{quality:'质量因子',growth:'景气因子'},task:{task_id:'task-run-1',status:'completed',log:[]},
  result:{state:'completed',accepted:true,account_snapshot:accountSnapshot,run_id:'RUN-42',spec,weights:{quality:.7,growth:-.3},factor_versions:{quality:'v1',growth:'v1'},selection_date:'2023-12-29',
    selection:Array.from({length:25},(_,i)=>({code:String(i).padStart(6,'0'),weight:.02,score:2-i*.01})),
    experiment_summary:{conclusion:'已完成最终评估，通过启用条件。',ai_generated:true,model_name:'discussion-model'},
    selected_candidate:candidates[2],search:{results:trainingResults},
    final_evaluation:{candidate:candidates[2],folds:[{run_id:'RUN-42',window:{start:spec.start,end:spec.end},metrics:finalMetrics}]},
    final_baseline:{candidate:candidates[0],folds:[{run_id:'BT-final-baseline',window:{start:spec.start,end:spec.end},metrics:{returns:{annualized_return:.02},risk:{max_drawdown:-.12}}}]},
    metrics:finalMetrics,report:'# 质量与景气组合\n\n本地模型训练和样本外回测已完成。'}});
const factorItems = [{name:'quality',display_name:'质量因子',status:'draft'}, {name:'growth',display_name:'景气因子',status:'active'}, {name:'empty',display_name:'用于验证筛选框随名称长度自动变化的因子',status:'disabled'}];
const factorSessions = Array.from({length:14},(_,i)=>({research_id:'quality-'+i,base_factor:'quality',base_factor_display_name:'质量因子',started_at:`2026-11-${String(i+1).padStart(2,'0')}T10:00:00Z`,state:'completed',phase:'研究完成'}));
factorSessions.push({research_id:'growth-0',base_factor:'growth',base_factor_display_name:'景气因子',started_at:'2026-11-14T10:00:00Z',state:'completed',phase:'研究完成'});
const strategySessions = Array.from({length:14},(_,i)=>({experiment_id:'filtered-'+i,profile_name:'strategy_fixture',display_name:spec.display_name,model:'model_linear',started_at:`2026-11-${String(i+1).padStart(2,'0')}T10:00:00Z`,state:'completed',phase:'研究完成'}));
strategySessions.push({experiment_id:'task-other',profile_name:'other',display_name:'另一策略',model:'model_linear',started_at:'2026-11-14T10:00:00Z',state:'completed',phase:'研究完成'});
function api(path, method, body) {
  calls.push({path,method,body});
  if (/^\/api\/(factors|strategies)\/[^/]+\/reviews$/.test(path)) return {items:[{
    review_id:'REV-fixture',model_name:'discussion-model',created_at:'2026-11-15T10:00:00Z',
    scope:'训练期选模诊断',source_id:'RUN-42',conclusion:'配置模型最近一次保存的评价',risks:[],next_steps:[],
  }],task:null};
  if(path==='/api/accounts')return {items:[{account_id:'test-account',alias:'研究账户',market:'CN-A',instrument_class:'stock'}]};
  if(path==='/api/status')return {server:{instance_id:'fixture'},workspace:{missing:0}};
  if(path==='/api/jobs')return {items:[...(generated?[{task_id:'task-idea',resource:'strategy-ai-research'}]:[]),...(factorGenerated?[{task_id:'task-factor-idea',resource:'factor-ai-research'}]:[])]};
  if(path==='/api/strategies/research/models')return {recommended:'discussion-model',items:[{name:'discussion-model',selectable:true,recommended:true}]};
  if(path==='/api/strategies/research/start'){generated=true;return {task_id:'task-idea',model:'discussion-model'};}
  const ideaResult = {profile_name:'strategy_fixture',display_name:spec.display_name,hypothesis:'质量与成长互补，盈利恶化时可能失效。',approval_status:strategyApproved?'approved':'pending',...(strategyApproved?{experiment_id:'task-run-1'}:{})};
  if(path==='/api/strategies/research/task-idea/progress')return {task_id:'task-idea',status:'completed',log:['方案检查通过'],progress:{step:'等待启动实验',percent:100},result:ideaResult};
  if(path==='/api/strategies/research/task-idea/approve'){strategyApproved=true;return {...ideaResult,approval_status:'approved',experiment_id:'task-run-1'};}
  const factorResult = {factor_name:'quality',display_name:'质量因子',assumption:'检验因子信号。',approval_status:factorApproved?'approved':'pending',...(factorApproved?{research_id:'quality-0'}:{})};
  if(path==='/api/factors/chat/research/task-factor-idea/progress')return {task_id:'task-factor-idea',status:'completed',log:['候选检查通过'],progress:{step:'等待启动实验',percent:100},result:factorResult};
  if(path==='/api/factors/chat/research/task-factor-idea/approve'){factorApproved=true;return {...factorResult,approval_status:'approved',research_id:'quality-0'};}
  if(path==='/api/strategies/experiments')return {items:filterFixtures?strategySessions:emptySessions?[]:[{experiment_id:'task-run-1',profile_name:'strategy_fixture',display_name:spec.display_name,model:'model_linear',started_at:'2026-11-14T10:00:00Z',state:'completed',phase:'回测与报告已生成'},{experiment_id:'task-run-failed',profile_name:'strategy_fixture',display_name:'失败的实验',model:'model_linear',started_at:'2026-11-13T10:00:00Z',state:'failed',phase:'训练失败'}]};
  if(path.startsWith('/api/strategies/experiments/')){const failed=path.endsWith('task-run-failed');return {...strategy(),...(filterFixtures?strategySessions.find(row=>row.experiment_id===path.split('/').at(-1))||{}:{}),hypothesis:'质量与成长互补，盈利恶化时可能失效。',task:{task_id:failed?'task-run-failed':'task-run-1',status:failed?'failed':'completed',progress:{step:'已完成'},log:['训练本地模型','完成回测'],error:failed?'训练样本不足':''},result:failed?null:strategy().result};}
  if(path==='/api/chat-history')return {items:[...chats.values()]};
  if(path==='/api/chat-history/title')return {title:'质量与成长的组合想法'};
  if(path.startsWith('/api/chat-history/')){const id=path.split('/').at(-1);const item={...body,id,revision:(body.revision||0)+1,updatedAt:Date.now()};chats.set(id,item);return item;}
  if(path==='/api/strategies/options')return {models:[{name:'model_linear',display_name:'线性回归'},{name:'model_logistic',display_name:'逻辑回归'}],factors:[{name:'quality',display_name:'质量因子'},{name:'growth',display_name:'景气因子'}],defaults:spec};
  if(path==='/api/strategies')return method==='POST'?{profile_name:'strategy_fixture',task_id:'task-new'}:{items:filterFixtures?[strategy(),{...strategy(),profile_name:'other',display_name:'另一策略'},{...strategy(),profile_name:'empty',display_name:'用于验证筛选框随名称长度自动变化的策略'}]:emptyCatalog?[]:[strategy()],statuses:['draft','active','disabled']};
  if(path==='/api/factors')return {items:filterFixtures?factorItems:[],statuses:['draft','active','disabled']};
  if(path.startsWith('/api/factors/'))return {factor:{...factorItems.find(row=>row.name===path.split('/').at(-1)),assumption:'检验因子信号。',version:'v1',timescale:'daily',direction:'positive'},items:[]};
  if(path==='/api/factor-evolution')return {items:filterFixtures?factorSessions:[]};
  if(path.startsWith('/api/factor-evolution/')){const row=factorSessions.find(row=>row.research_id===path.split('/').at(-1));return {config:{base_factor:row.base_factor,hypothesis:'检验'+row.base_factor_display_name},status:row,events:[]};}
  if(path==='/api/strategies/strategy_fixture/status'){status=body.status;return {profile:strategy()};}
  if(path==='/api/strategies/strategy_fixture')return strategy();
  if(filterFixtures&&['/api/strategies/other','/api/strategies/empty'].includes(path)){const name=path.split('/').at(-1);return {...strategy(),profile_name:name,display_name:name==='other'?'另一策略':'用于验证筛选框随名称长度自动变化的策略',task:{task_id:name==='other'?'task-other':'missing',status:'completed'}};}
  if(path==='/api/backtest/runs')return {items:[{run_id:'RUN-42',profile:'strategy_fixture',state:'completed',start:spec.start,end:spec.end},
    ...trainingResults.flatMap(result=>result.folds.map(fold=>({run_id:fold.run_id,profile:'strategy_fixture',state:'completed',start:fold.window.start,end:fold.window.end})))]};
  if(/^\/api\/backtest\/runs\/(RUN-42|BT-inner-\d-\d)$/.test(path)){const run_id=path.split('/').at(-1);return {run_id,profile:'strategy_fixture',status:{state:'completed'},config:{config:{initial_capital:1e6}},metrics:trainingResults.flatMap(r=>r.folds).find(r=>r.run_id===run_id)?.metrics||finalMetrics};}
  if(/^\/api\/backtest\/runs\/(RUN-42|BT-inner-\d-\d)\/nav$/.test(path))return {total_rows:3,items:[{date:'2023-01-03',nav:1000000,benchmark_nav:1},{date:'2023-06-01',nav:1100000,benchmark_nav:1.03},{date:'2023-12-29',nav:1150000,benchmark_nav:1.05}],monthly_returns:[]};
  return {items:[],dates:[],total_rows:0};
}
const server = createServer(async (req, res) => {
  try {
    const path = new URL(req.url, 'http://localhost').pathname;
    if (path === '/api/strategies/research/chat') {
      let raw='';for await(const chunk of req)raw+=chunk;const body=JSON.parse(raw);calls.push({path,method:req.method,body});
      res.setHeader('Content-Type','application/x-ndjson');
      res.end([{type:'start',model:'discussion-model'},{type:'delta',channel:'content',text:'可以先研究质量与成长的互补关系，盈利恶化时可能失效。信息已足够，点击生成策略方案。'},{type:'done'}].map(row=>JSON.stringify(row)+'\n').join(''));return;
    }
    if (path.startsWith('/api/')) { res.setHeader('Content-Type', 'application/json'); let raw='';for await(const chunk of req)raw+=chunk;res.end(JSON.stringify(api(path,req.method,raw?JSON.parse(raw):{}))); return; }
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
  async function wait(expression) { for (let i = 0; i < 160; i++) { if (await js(expression).catch(() => false)) return; await delay(40); } throw Error(expression + '\n' + await js('document.body.innerText')+'\n'+JSON.stringify({calls:calls.slice(-8),errors})); }
  async function navigate(route) { await js(`location.hash=${JSON.stringify(route)}`); await wait(`document.querySelector('[data-view="${route.split('/')[0]}"]:not([hidden])')`); }
  await call('Page.enable'); await call('Runtime.enable');
  await call('Emulation.setDeviceMetricsOverride',{width:1440,height:1000,deviceScaleFactor:1,mobile:false});
  await call('Page.navigate',{url:base+'/#strategy-research/library'});
  await wait("document.querySelector('.strategy__overview') && document.querySelector('.strategy__detail .research-weights')");
  await wait("document.querySelector('.strategy__detail .model-reviews__conclusion')?.textContent==='配置模型最近一次保存的评价'");
  assert(await js("document.querySelector('.strategy__detail .model-reviews').textContent.includes('discussion-model')"));
  assert(!calls.some(c=>c.method==='POST'&&c.path.endsWith('/reviews')));
  assert(await js("(()=>{const row=document.querySelector('.strategy__hypothesis .kv__row'),key=row.querySelector('dt').getBoundingClientRect(),value=row.querySelector('dd').getBoundingClientRect();return value.top>=key.bottom&&Math.abs(value.left-key.left)<1})()"));
  assert(await js("!document.querySelector('.strategy__detail').textContent.includes('未通过最终准入门槛')&&!document.querySelector('.strategy__detail').textContent.includes('权重在回测前冻结')"));
  assert.deepEqual(await js("[...document.querySelectorAll('[data-view=\"strategy-research\"] [role=tab]')].map(e=>e.textContent)"),['策略库','新策略研究','策略实验']);
  assert(await js("document.querySelector('.strategy__detail').textContent.includes('-30.00%')"));
  assert(await js("!document.querySelector('.strategy__detail').textContent.includes('目标仓位') && !document.querySelector('.strategy__detail').textContent.includes('选股方案')"));
  assert(await js("!document.querySelector('.strategy').textContent.includes('审批') && !document.querySelector('.strategy__detail .chart,.strategy__detail .research-metrics,.strategy__detail .research-report') && !document.querySelector('.strategy__detail').textContent.includes('回测区间')"));
  assert(await js("[...document.querySelectorAll('.strategy .badge--accent')].filter(e=>e.textContent==='研究中').length===2"));
  assert.deepEqual(await js("[...document.querySelectorAll('.strategy__controls button')].map(e=>e.textContent)"),['转为已启用','转为已停止','删除','查看实验']);
  await js("[...document.querySelectorAll('.strategy-actions button')].find(e=>e.textContent==='转为已启用').click()");
  await wait("document.querySelector('.strategy .item-row__meta').textContent.includes('已启用')");
  await wait("document.querySelector('.strategy__overview .research-detail__title .badge')?.textContent==='已启用'");
  assert.deepEqual(await js("[...document.querySelectorAll('.strategy__controls button')].map(e=>e.textContent)"),['转为研究中','转为已停止','删除','查看实验']);
  await js("[...document.querySelectorAll('.strategy-actions button')].find(e=>e.textContent==='转为已停止').click()");
  await wait("document.querySelector('.strategy .item-row__meta').textContent.includes('已停止')");
  await wait("document.querySelector('.strategy__overview .research-detail__title .badge')?.textContent==='已停止'");
  assert(!calls.some(c=>c.path.includes('approve')||c.path.includes('evolution')||c.body.confirm));
  assert(await js("!document.querySelector('.strategy__detail .research-weights').closest('details') && !document.querySelector('.strategy__detail').textContent.includes('选股方案')"));
  for(const theme of ['light','dark'])for(const width of [1440,1024,768,390,320]){
    await call('Emulation.setDeviceMetricsOverride',{width,height:1000,deviceScaleFactor:1,mobile:false});
    await js(`document.documentElement.dataset.theme='${theme}'`);await delay(100);
    const layout=await js("({page:document.documentElement.scrollWidth,width:innerWidth})");assert(layout.page<=width,JSON.stringify(layout));
    assert(await js("(()=>{const b=document.querySelector('[data-strategy-experiments]'),h=b.closest('.research-detail__head'),t=h.querySelector('.research-detail__title').getBoundingClientRect(),a=h.querySelector('.strategy__controls'),r=a.getBoundingClientRect();return h.contains(document.querySelector('.strategy-actions'))&&!b.closest('.strategy-actions')&&h.scrollWidth<=h.clientWidth&&(t.right<=r.left||r.top>=t.bottom)&&r.bottom<=h.getBoundingClientRect().bottom+1&&[...a.querySelectorAll('button')].every(e=>{const box=e.getBoundingClientRect();return box.left>=r.left&&box.right<=r.right+1&&(innerWidth>390||box.height>=40)})})()"));
    assert(await js("(()=>{const r=document.querySelector('.strategy__reviews');return !r.closest('.strategy__overview')&&r.scrollWidth<=r.clientWidth&&!r.textContent.includes('暂无评价记录。')})()"));
    assert(await js("(()=>{const a=document.querySelector('.strategy__overview').getBoundingClientRect(),b=document.querySelector('.strategy__reviews').getBoundingClientRect();return Math.abs(a.left-b.left)<1&&Math.abs(a.right-b.right)<1&&b.top-a.bottom<=12&&!document.querySelector('.strategy__weights table')})()"));
    const shot=await call('Page.captureScreenshot',{format:'png'});await writeFile(join(output,`library-${width}-${theme}.png`),Buffer.from(shot.data,'base64'));
  }
  await js("document.querySelector('[data-research-open]').click()");
  await wait("document.querySelector('[data-ai-research]:not(:disabled)')");
  assert(await js("document.querySelector('[data-pane=strategy-builder] .factors__research-empty')!==null && !document.querySelector('[data-strategy-form]')"));
  for(const theme of ['light','dark'])for(const width of [1440,390]){
    await call('Emulation.setDeviceMetricsOverride',{width,height:1000,deviceScaleFactor:1,mobile:false});
    await js(`document.documentElement.dataset.theme='${theme}'`);await delay(100);
    const layout=await js("(()=>{const c=document.querySelector('[data-pane=strategy-builder] [data-research-empty]'),r=c.getBoundingClientRect(),cx=(r.left+r.right)/2;return {overflow:document.documentElement.scrollWidth>innerWidth,paragraphs:c.querySelectorAll('p').length,centered:[...c.children].every(e=>{const b=e.getBoundingClientRect();return Math.abs((b.left+b.right)/2-cx)<2})}})()");
    assert(!layout.overflow&&layout.paragraphs===0&&layout.centered,JSON.stringify(layout));
    const shot=await call('Page.captureScreenshot',{format:'png'});await writeFile(join(output,`strategy-empty-${width}-${theme}.png`),Buffer.from(shot.data,'base64'));
  }
  await delay(150);
  await js("document.querySelector('[data-ai-research]').click()");
  await wait("document.querySelector('.chat-dialog .chat__input:not(:disabled)')");
  assert(await js("document.querySelector('.chat-history__heading').textContent==='对话历史'"));
  assert(await js("document.querySelector('.chat-dialog__actions .btn--primary').disabled"));
  await js("document.querySelector('.chat-dialog__toolbar .chat-dialog__model [data-select-trigger]').click();document.querySelector('.chat-dialog [data-value=\"test-account\"]').click()");
  await js("document.querySelector('.chat__input').value='我想结合质量与成长选股，控制回撤。';document.querySelector('.chat__input').dispatchEvent(new Event('input'));document.querySelector('.chat__form').requestSubmit()");
  await wait("!document.querySelector('.chat-dialog__actions .btn--primary').disabled && document.querySelector('.chat__item--assistant')");
  const chatCall=calls.find(c=>c.path==='/api/strategies/research/chat');assert(chatCall);assert.equal(chatCall.body.model,'discussion-model');assert.equal(chatCall.body.account_id,'test-account');assert(chatCall.body.messages[0].content.includes('质量与成长'));
  for(const theme of ['light','dark'])for(const width of [1440,390]){
    await call('Emulation.setDeviceMetricsOverride',{width,height:1000,deviceScaleFactor:1,mobile:false});
    await js(`document.documentElement.dataset.theme='${theme}'`);await delay(100);
    assert(await js('document.documentElement.scrollWidth<=innerWidth'));
    const shot=await call('Page.captureScreenshot',{format:'png'});await writeFile(join(output,`discussion-${width}-${theme}.png`),Buffer.from(shot.data,'base64'));
  }
  await js("document.querySelector('.chat-dialog__actions .btn--primary').click()");
  await wait("!document.querySelector('.chat-dialog') && document.querySelector('[data-pane=strategy-builder] [data-research-result]').textContent.includes('等待启动实验')");
  assert(!strategyApproved && !calls.some(c=>c.path.endsWith('/approve')));
  assert(await js("!document.querySelector('[data-pane=strategy-builder] [data-candidate-task]').textContent.includes('自动启动')"));
  await navigate('strategy-research/library');
  await navigate('strategy-research/research');
  await wait("[...document.querySelectorAll('[data-pane=strategy-builder] [data-research-result] button')].some(e=>e.textContent==='启动实验')");
  assert(!strategyApproved);
  await js("[...document.querySelectorAll('[data-pane=strategy-builder] [data-research-result] button')].find(e=>e.textContent==='启动实验').click()");
  await wait("document.querySelector('[data-pane=strategy-builder] [data-research-result]').textContent.includes('已进入策略实验')");
  assert.equal(calls.filter(c=>c.path==='/api/strategies/research/task-idea/approve').length,1);
  const started=calls.find(c=>c.path==='/api/strategies/research/start');assert(started);assert.equal(started.body.model,'discussion-model');assert(started.body.messages.some(m=>m.role==='assistant'));assert(!('confirm' in started.body));
  await js("[...document.querySelectorAll('[data-research-result] button')].find(e=>e.textContent==='查看自动研究进展').click()");
  await wait("location.hash==='#strategy-research/experiments' && document.querySelector('[data-pane=strategy-runs] .experiment-results')");
  assert(await js("document.querySelector('[data-pane=strategy-runs] .experiment-conclusion strong')?.textContent==='已完成最终评估，通过启用条件。'"));
  const runRows=await js("[...document.querySelectorAll('[data-pane=strategy-runs] .experiment-backtests tbody tr')].map(row=>[...row.cells].map(cell=>cell.textContent))");
  assert.equal(runRows.length,11);
  assert.deepEqual(await js("[...document.querySelectorAll('[data-pane=strategy-runs] .experiment-backtests .experiments__run-link')].map(link=>link.title||link.textContent)"),[...trainingResults.flatMap(r=>r.folds.map(f=>f.run_id)),'BT-final-baseline','RUN-42']);
  assert(runRows[0][0].includes('每月')&&runRows[0][0].includes('基线')&&runRows[6][0].includes('每季度'));
  assert.equal(runRows[1][1],'训练内第 2 段');
  assert.equal(runRows[1][2],'2021-01-01 至 2021-12-15');
  assert.equal(runRows[1][3],'1.10%');
  assert.equal(runRows.at(-1)[1],'最终评估');
  assert.equal(runRows.at(-1)[2],'2023-01-03 至 2023-12-29');
  assert.equal(runRows.at(-1)[3],'15.00%');
  assert.equal(await js("document.querySelectorAll('[data-pane=strategy-runs] .evolution-sessions tbody tr').length"),2);
  assert(await js("!document.querySelector('[data-pane=strategy-runs] .experiment-intro').textContent.includes('质量与成长互补')"));
  assert(await js("document.querySelector('[data-pane=strategy-runs] .experiment-backtests').textContent.includes('RUN-42')"));
  await js("[...document.querySelectorAll('[data-pane=strategy-runs] summary')].forEach(e=>e.click())");
  await wait("document.querySelector('[data-pane=strategy-runs] .research-report__body')");
  assert(await js("document.querySelector('[data-pane=strategy-runs] .research-weights').textContent.includes('-30.00%')"));
  assert(await js("!document.querySelector('[data-pane=strategy-runs] .research-weights').closest('details') && !document.querySelector('[data-pane=strategy-runs]').textContent.includes('选股方案')"));
  for(const theme of ['light','dark'])for(const width of [1440,768,390]){
    await call('Emulation.setDeviceMetricsOverride',{width,height:1000,deviceScaleFactor:1,mobile:false});
    await js(`document.documentElement.dataset.theme='${theme}'`);await delay(100);
    assert(await js('document.documentElement.scrollWidth<=innerWidth'));
    const shot=await call('Page.captureScreenshot',{format:'png'});await writeFile(join(output,`experiments-${width}-${theme}.png`),Buffer.from(shot.data,'base64'));
  }
  await js("document.querySelectorAll('[data-pane=strategy-runs] .experiment-backtests .experiments__run-link')[1].click()");
  await wait("location.hash==='#backtest'");
  for(let i=0;i<100&&!calls.some(c=>c.path==='/api/backtest/runs/BT-inner-0-1');i++)await delay(30);
  assert(calls.some(c=>c.path==='/api/backtest/runs/BT-inner-0-1'));
  await navigate('strategy-research/experiments');
  await wait("document.querySelectorAll('[data-pane=strategy-runs] .experiment-backtests tbody tr').length===11");
  await js("document.querySelector('[data-research-id=task-run-failed]').click()");
  await wait("document.querySelector('[data-pane=strategy-runs] .experiment-error')?.textContent.includes('训练样本不足')");
  assert(await js("!document.querySelector('[data-pane=strategy-runs] .experiment-results')"));
  assert([...chats.values()].some(chat=>chat.scope==='strategy-research'&&chat.messages.some(m=>m.role==='user')));
  await navigate('factors/research');
  await wait("document.querySelector('[data-pane=factor-research] [data-ai-research]:not(:disabled)')");
  for(const theme of ['light','dark'])for(const width of [1440,390]){
    await call('Emulation.setDeviceMetricsOverride',{width,height:1000,deviceScaleFactor:1,mobile:false});
    await js(`document.documentElement.dataset.theme='${theme}'`);await delay(100);
    const layout=await js("(()=>{const c=document.querySelector('[data-pane=factor-research] [data-research-empty]'),r=c.getBoundingClientRect(),cx=(r.left+r.right)/2;return {overflow:document.documentElement.scrollWidth>innerWidth,paragraphs:c.querySelectorAll('p').length,centered:[...c.children].every(e=>{const b=e.getBoundingClientRect();return Math.abs((b.left+b.right)/2-cx)<2})}})()");
    assert(!layout.overflow&&layout.paragraphs===0&&layout.centered,JSON.stringify(layout));
    assert(await js("document.querySelector('[data-pane=factor-research] [data-research-empty]').innerHTML===document.querySelector('[data-pane=strategy-builder] [data-research-empty]').innerHTML"));
    const shot=await call('Page.captureScreenshot',{format:'png'});await writeFile(join(output,`factor-empty-${width}-${theme}.png`),Buffer.from(shot.data,'base64'));
  }
  factorGenerated = true;
  await call('Page.reload');
  await wait("[...document.querySelectorAll('[data-pane=factor-research] [data-research-result] button')].some(e=>e.textContent==='启动实验')");
  assert(!factorApproved);
  assert(await js("!document.querySelector('[data-pane=factor-research] [data-candidate-task]').textContent.includes('自动启动')"));
  await js("[...document.querySelectorAll('[data-pane=factor-research] [data-research-result] button')].find(e=>e.textContent==='启动实验').click()");
  await wait("document.querySelector('[data-pane=factor-research] [data-research-result]').textContent.includes('已进入自动迭代')");
  assert.equal(calls.filter(c=>c.path==='/api/factors/chat/research/task-factor-idea/approve').length,1);
  emptyCatalog = true;
  for (const [name, route, selector, next] of [
    ['factor', 'factors/list', '[data-pane=factors]', 'factors/research'],
    ['strategy', 'strategy-research/library', '[data-pane=strategy]', 'strategy-research/research'],
  ]) {
    await navigate(route);
    if (name === 'strategy') await js("document.querySelector('#strategy-research-tab-library').click()");
    await wait(`document.querySelector('${selector} [data-library-empty]:not([hidden])')`);
    for(const theme of ['light','dark'])for(const width of [1440,390]) {
      await call('Emulation.setDeviceMetricsOverride',{width,height:1000,deviceScaleFactor:1,mobile:false});
      await js(`document.documentElement.dataset.theme='${theme}'`);await delay(100);
      const layout=await js(`(()=>{const c=document.querySelector('${selector} [data-library-empty]'),r=c.getBoundingClientRect(),s=c.querySelector('[data-icon=ban] svg').getBoundingClientRect();return {overflow:document.documentElement.scrollWidth>innerWidth,icon:s.width,splitHidden:c.parentElement.querySelector('.split').hidden,centered:[...c.children].every(e=>{const b=e.getBoundingClientRect();return Math.abs((b.left+b.right-r.left-r.right)/2)<2})}})()`);
      assert(!layout.overflow&&layout.centered&&layout.splitHidden&&layout.icon>=44,JSON.stringify(layout));
      assert(await js(`(()=>{const c=document.querySelector('${selector} [data-library-empty]'),icon=c.querySelector('[data-icon=ban]');return getComputedStyle(icon).color===getComputedStyle(c.querySelector('h3')).color&&getComputedStyle(icon).backgroundColor!=='rgba(0, 0, 0, 0)'})()`));
      if (name === 'strategy') {
        assert(await js(`(()=>{const c=document.querySelector('${selector} [data-library-empty]'),b=document.querySelector('${selector} [data-research-open]'),r=c.getBoundingClientRect(),p=b.getBoundingClientRect();return !b.hidden&&r.height<=240&&p.bottom<=r.top&&Math.abs(p.right-r.right)<2&&!c.querySelector('button')})()`));
      }
      const shot=await call('Page.captureScreenshot',{format:'png'});await writeFile(join(output,`${name}-library-empty-${width}-${theme}.png`),Buffer.from(shot.data,'base64'));
    }
    await js(`document.querySelector('${selector} [${name === 'strategy' ? 'data-research-open' : 'data-empty-research'}]').click()`);
    await wait(`location.hash==='#${next}'`);
  }
  emptySessions = true;
  for (const [name, route, selector] of [
    ['factor', 'factors/experiments', '[data-pane=experiments]'],
    ['strategy', 'strategy-research/experiments', '[data-pane=strategy-runs]'],
  ]) {
    await navigate(route);
    if(name==='strategy') await js("document.querySelector('#strategy-research-tab-experiments').click()");
    await wait(`document.querySelector('${selector} [data-experiments-empty]:not([hidden])')`);
    for(const theme of ['light','dark'])for(const width of [1440,390]) {
      await call('Emulation.setDeviceMetricsOverride',{width,height:1000,deviceScaleFactor:1,mobile:false});
      await js(`document.documentElement.dataset.theme='${theme}'`);await delay(100);
      assert(await js(`(()=>{const c=document.querySelector('${selector} [data-experiments-empty]'),icon=c.querySelector('[data-icon=ban]'),h=c.querySelector('h3'),r=c.getBoundingClientRect(),i=icon.getBoundingClientRect();return icon.querySelector('svg')&&getComputedStyle(icon).backgroundColor!=='rgba(0, 0, 0, 0)'&&getComputedStyle(icon).color===getComputedStyle(h).color&&Math.abs((r.left+r.right-i.left-i.right)/2)<2&&document.documentElement.scrollWidth<=innerWidth&&!document.querySelector('${selector} [data-evolution-detail]').childElementCount})()`));
      const shot=await call('Page.captureScreenshot',{format:'png'});await writeFile(join(output,`${name}-experiments-empty-${width}-${theme}.png`),Buffer.from(shot.data,'base64'));
    }
  }
  filterFixtures = true;
  await call('Emulation.setDeviceMetricsOverride',{width:1440,height:1000,deviceScaleFactor:1,mobile:false});
  await navigate('factors/list');
  await wait("document.querySelector('[data-factor-experiments=quality]')");
  await js("document.querySelector('[data-factor-experiments=quality]').click()");
  const factorPane='[data-pane=experiments]', strategyPane='[data-pane=strategy-runs]';
  async function chooseFilter(pane,value) {
    await js(`document.querySelector('${pane} [data-session-filter] [data-select-trigger]').click()`);
    await js(`document.querySelector('${pane} [data-session-filter] [role=option][data-value="${value}"]').click()`);
  }
  await wait(`document.querySelector('${factorPane} [data-session-filter] [data-select-value]')?.textContent==='质量因子'&&document.querySelectorAll('${factorPane} [data-research-id]').length===10`);
  assert(await js(`document.querySelector('${factorPane} [data-session-filter] .icon svg')!==null`));
  assert(await js(`!document.querySelector('${factorPane} [data-research-id=growth-0]')`));
  await js(`document.querySelector('${factorPane} [data-page=last]').click()`);
  assert.equal(await js(`document.querySelectorAll('${factorPane} [data-research-id]').length`),4);
  await chooseFilter(factorPane,'growth');
  await wait(`document.querySelectorAll('${factorPane} [data-research-id]').length===1&&document.querySelector('${factorPane} .experiment-intro').textContent.includes('景气因子')`);
  assert.equal(await js(`document.querySelector('${factorPane} .table-pagination__current')?.textContent`),'1 / 1 页');
  await chooseFilter(factorPane,'empty');
  await wait(`document.querySelector('${factorPane} [data-experiments-empty]:not([hidden])')?.textContent.includes('该因子暂无实验')`);
  assert(await js(`!document.querySelector('${factorPane} [data-evolution-detail]').childElementCount`));
  await chooseFilter(factorPane,'');
  await wait(`document.querySelectorAll('${factorPane} [data-research-id]').length===10`);
  await navigate('factors/list');
  await wait("document.querySelector('[data-name=growth]')");
  await js("document.querySelector('[data-name=growth]').click()");
  await wait("document.querySelector('[data-factor-experiments=growth]')");
  await js("document.querySelector('[data-factor-experiments=growth]').click()");
  await wait(`document.querySelector('${factorPane} [data-session-filter] [data-select-value]').textContent==='景气因子'&&document.querySelectorAll('${factorPane} [data-research-id]').length===1`);
  for(const theme of ['light','dark'])for(const width of [1440,390]) {
    await call('Emulation.setDeviceMetricsOverride',{width,height:1000,deviceScaleFactor:1,mobile:false});
    await js(`document.documentElement.dataset.theme='${theme}'`);await delay(100);
    assert(await js('document.documentElement.scrollWidth<=innerWidth'));
    const shot=await call('Page.captureScreenshot',{format:'png'});await writeFile(join(output,`factor-session-filter-${width}-${theme}.png`),Buffer.from(shot.data,'base64'));
  }
  await navigate('strategy-research/library');
  await js("document.querySelector('#strategy-research-tab-library').click()");
  await wait("document.querySelector('.strategy__overview .strategy__facts')");
  assert(await js("document.querySelectorAll('.strategy__overview .strategy__facts > div').length===5"));
  await js("document.querySelector('[data-strategy-experiments]').click()");
  await wait(`document.querySelector('${strategyPane} [data-session-filter] [data-select-value]').textContent==='质量与景气组合'&&document.querySelectorAll('${strategyPane} [data-research-id]').length===10`);
  await js(`document.querySelector('${strategyPane} [data-page=last]').click()`);
  assert.equal(await js(`document.querySelectorAll('${strategyPane} [data-research-id]').length`),4);
  await chooseFilter(strategyPane,'other');
  await wait(`document.querySelectorAll('${strategyPane} [data-research-id]').length===1&&document.querySelector('${strategyPane} .experiment-intro').textContent.includes('另一策略')`);
  assert.equal(await js(`document.querySelector('${strategyPane} .table-pagination__current')?.textContent`),'1 / 1 页');
  await chooseFilter(strategyPane,'empty');
  await wait(`document.querySelector('${strategyPane} [data-experiments-empty]:not([hidden])')?.textContent.includes('该策略暂无实验')`);
  assert(await js(`!document.querySelector('${strategyPane} [data-evolution-detail]').childElementCount`));
  await chooseFilter(strategyPane,'');
  await wait(`document.querySelectorAll('${strategyPane} [data-research-id]').length===10`);
  await navigate('strategy-research/library');
  await js("document.querySelector('#strategy-research-tab-library').click()");
  await wait("document.querySelector('.strategy [data-name=other]')");
  await js("document.querySelector('.strategy [data-name=other]').click()");
  await wait("document.querySelector('.strategy__overview h3')?.textContent==='另一策略'");
  await js("document.querySelector('[data-strategy-experiments]').click()");
  await wait(`document.querySelector('${strategyPane} [data-session-filter] [data-select-value]').textContent==='另一策略'&&document.querySelectorAll('${strategyPane} [data-research-id]').length===1`);
  for(const theme of ['light','dark'])for(const width of [1440,390]) {
    await call('Emulation.setDeviceMetricsOverride',{width,height:1000,deviceScaleFactor:1,mobile:false});
    await js(`document.documentElement.dataset.theme='${theme}'`);await delay(100);
    assert(await js('document.documentElement.scrollWidth<=innerWidth'));
    const shot=await call('Page.captureScreenshot',{format:'png'});await writeFile(join(output,`strategy-session-filter-${width}-${theme}.png`),Buffer.from(shot.data,'base64'));
  }
  for (const [pane,route,short] of [[factorPane,'factors/experiments','growth'],[strategyPane,'strategy-research/experiments','other']]) {
    await navigate(route);
    await wait(`document.querySelector('${pane} [data-session-filter] [role=option][data-value=empty]')`);
    for(const width of [1440,390]) {
      await call('Emulation.setDeviceMetricsOverride',{width,height:1000,deviceScaleFactor:1,mobile:false});
      await chooseFilter(pane,'empty');
      const longWidth=await js(`document.querySelector('${pane} [data-session-filter]').getBoundingClientRect().width`);
      await chooseFilter(pane,short);
      const shortWidth=await js(`document.querySelector('${pane} [data-session-filter]').getBoundingClientRect().width`);
      assert(shortWidth<longWidth-30&&shortWidth<140,JSON.stringify({width,longWidth,shortWidth}));
      await chooseFilter(pane,'empty');
      await chooseFilter(pane,'');
      const allWidth=await js(`document.querySelector('${pane} [data-session-filter]').getBoundingClientRect().width`);
      assert(allWidth<longWidth-30&&allWidth<140,JSON.stringify({width,longWidth,allWidth}));
      assert(await js('document.documentElement.scrollWidth<=innerWidth'));
      const shot=await call('Page.captureScreenshot',{format:'png'});await writeFile(join(output,`${pane.includes('strategy')?'strategy':'factor'}-filter-auto-width-${width}.png`),Buffer.from(shot.data,'base64'));
    }
  }
  assert.deepEqual(errors,[]);
  console.log('PASS: AI strategy workflow, isolated experiment results, larger research icons, both empty-library research entries, empty experiment sessions, theme-aware red empty-state backgrounds, linked library filters, pagination/empty-filter recovery, responsive layouts, and long-to-short/all filter resizing on desktop and mobile. '+output);
} finally {socket?.close();chrome.kill();server.close();}
