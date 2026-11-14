// Browser regression for asynchronous index loading, toggles and compact layouts.
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {spawn} from 'node:child_process';
import {readFile, writeFile, mkdtemp} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {setTimeout as delay} from 'node:timers/promises';

const root=fileURLToPath(new URL('../../src/webui/frontend/',import.meta.url));
const output=await mkdtemp(join(tmpdir(),'quantlens-experiment-sessions-'));
const index=await readFile(join(root,'index.html'),'utf8');
const styles=[...index.matchAll(/<link rel="stylesheet" href="([^"]+)"/g)].map(m=>`<link rel="stylesheet" href="${m[1]}">`).join('');
const component=await readFile(join(root,'components/experiments/experiments.html'),'utf8');
const html=`<!doctype html><html lang="zh-CN" data-theme="light"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">${styles}<style>body{overflow:auto}main{padding:16px;container:workspace / inline-size;max-width:1640px;margin:auto}#rules{margin-top:16px}</style></head><body><main><section class="view" id="host">${component}</section><section id="rules"></section></main><script type="module">
import {mount} from '/components/experiments/experiments.js';
import {mount as mountRules} from '/components/market-rules/market-rules.js';
import {tableEl} from '/core/dom.js';
import {createResearchReport} from '/core/research-report.js';
window.reportText=source=>createResearchReport(source).textContent;
window.events=[];window.listeners={};window.calls={};window.reportReads=0;window.lines=Array.from({length:230},(_,i)=>'日志 '+i+'：执行训练与验证回测');
const config={base_factor:'quality',profile_name:'research_quality',train_window:{start:'2016-01-01',end:'2021-12-11'},validation_window:{start:'2022-01-01',end:'2024-12-31'},test_window:{start:'2025-01-01',end:'2026-11-13'}};
const record={exp_id:'EXP-baseline',subject_name:'quality',subject_display_name:'盈利改善质量防御',profile_name:'research_quality',change_reason:'用盈利能力和波动率识别脆弱增长，检验是否保留盈利且降低尾部损失。',change_summary:'固定公式比较，不搜索权重。',created_at:'2026-11-14T09:59:00Z',status:'registered'};
const metrics={returns:{annualized_return:.049,annualized_excess_return:-.002},risk:{max_drawdown:-.14},window:{start:'2016-01-04',end:'2021-12-10'}};
const sessionEvents=[{event_type:'training_backtest_completed',created_at:record.created_at,payload:{version_id:'质量防御均值',experiment_id:record.exp_id,result:{run_id:'BT-train',window:'fixed_train',metrics}}},
{event_type:'window_completed',created_at:record.created_at,payload:{fold_id:'wf001',window_name:'validation',run_id:'BT-window'}},
{event_type:'version_evaluated',payload:{version_id:'均值方案',generalization:{classification:'not_effective',train_median_excess_return:.024,validation_median_excess_return:-.1325,generalization_gap:.1565},folds:[{fold_id:'wf002',train_window:config.train_window,validation_window:config.validation_window,train:{run_id:'BT-fold-train',metrics},validation:{run_id:'BT-fold-valid',metrics}}]}},
{event_type:'experiment_summary',payload:{conclusion:'候选未通过验证门槛，未运行锁定测试。',ai_generated:true,model_name:'主题模型'}}];
const rows=['completed','running','slow','fast','paused',...Array.from({length:8},(_,i)=>'saved-'+i)].map((research_id,index)=>({research_id,base_factor:'quality',base_factor_display_name:research_id==='fast'?'快速切换实验':'盈利改善质量防御',profile_name:'research_quality',model_name:'主题模型',started_at:new Date(Date.UTC(2026,10,14,9,58-index)).toISOString(),state:research_id==='running'?'running':research_id==='paused'?'paused':'completed',phase:research_id==='running'?'正在运行验证窗口':'训练筛选完成'}));
const standalone=new URLSearchParams(location.search).has('standalone');
window.api={factorEvolutions:async()=>({items:standalone?[]:[...rows].reverse()}),experiments:async()=>({items:[record]}),
factorEvolution:async id=>{calls[id]=(calls[id]||0)+1;if(id==='slow')await new Promise(r=>setTimeout(r,500));const row=rows.find(r=>r.research_id===id);return {config,experiment_ids:[record.exp_id],status:{...row,progress:.5,terminal_reason:row.state==='completed'?'三个固定公式均未通过训练门槛，没有开始验证。':'',best_version_id:'均值方案'},lifecycle_state:row.state,task:{status:row.state,log:lines},events:sessionEvents,locked_test_exposures:[{run_id:'BT-locked'}],research_report:row.state==='completed'?{object_id:'RR-'+id}:null}},
experiment:async()=>({experiment:{...record,train_window:'2016-01-01..2021-12-11'},events:[{event_type:'result',payload:{run_id:'BT-standalone',metrics}}]}),
researchReportContent:async()=>{reportReads++;await new Promise(r=>setTimeout(r,50));return {content:'## 研究结论\\n\\n未通过训练门槛。'}},jobs:async()=>({items:[]})};
window.startRequests=[];window.startFailure=false;
window.factorItems=[{name:'quality',display_name:'盈利改善质量防御',status:'draft'},{name:'earnings',display_name:'披露盈利改善',status:'active'},{name:'disabled',display_name:'已停用',status:'disabled'}];
api.factors=async()=>({items:factorItems});
api.factorEvolutionStart=async payload=>{startRequests.push(payload);if(startFailure)throw Error('模型连接不可用');await new Promise(r=>window.releaseStart=r);return {research_id:'completed',task_id:'T-start'}};
const ctx={api,bus:{on:(event,fn)=>listeners[event]=fn,emit:(...args)=>events.push(args)}};
await mount(document.querySelector('#host'),ctx);
const rule={name:'CN-A-stock',status:'active',market:'CN-A',instrument_class:'stock',price_limit:{enabled:true,default_pct:10,first_day_rule:'按市场和板块适用上市初期规则。',notes:'价格按最小变动单位取整。',special_rules:Array.from({length:7},(_,i)=>({condition:'规则 '+i,limit_pct:10,notes:'固定规则说明'}))},disclosure_deadlines:{items:Array.from({length:13},(_,i)=>({period_end:'事项 '+i,deadline:'固定截止时间',notes:'固定规则说明'}))}};
document.querySelector('#rules').innerHTML=await(await fetch('/components/market-rules/market-rules.html')).text();await mountRules(document.querySelector('#rules'),{api:{marketRules:async()=>({items:[rule]})},bus:{on(){},emit(){}}});
const small=tableEl([{key:'id',label:'编号'}],Array.from({length:7},(_,id)=>({id})));small.id='small';document.querySelector('#rules').append(small);
window.ready=true;
</script></body></html>`;
const server=createServer(async(req,res)=>{try{const path=new URL(req.url,'http://localhost').pathname;res.setHeader('Content-Type',path.endsWith('.css')?'text/css':path.endsWith('.js')?'text/javascript':path.endsWith('.svg')?'image/svg+xml':'text/html; charset=utf-8');res.end(path==='/'?html:await readFile(join(root,path)));}catch{res.writeHead(404);res.end()}});
await new Promise(r=>server.listen(0,'127.0.0.1',r));
const browser=spawn(process.env.QUANTLENS_CHROME_BIN||'google-chrome',['--headless=new','--no-sandbox','--disable-gpu','--remote-debugging-port=0',`--user-data-dir=${join(output,'browser')}`,'about:blank'],{stdio:['ignore','ignore','pipe']});
let socket;
try{
 const endpoint=await new Promise((resolve,reject)=>{let log='';const timer=setTimeout(()=>reject(Error(log)),10000);browser.stderr.on('data',chunk=>{log+=chunk;const m=log.match(/DevTools listening on (ws:\/\/\S+)/);if(m){clearTimeout(timer);resolve(m[1])}});browser.once('error',reject)});
 const target=await(await fetch(`http://127.0.0.1:${new URL(endpoint).port}/json/new?about:blank`,{method:'PUT'})).json();
 socket=new WebSocket(target.webSocketDebuggerUrl);await new Promise(r=>socket.onopen=r);
 let serial=0;const pending=new Map(),exceptions=[];
 socket.onmessage=e=>{const m=JSON.parse(e.data);if(m.method==='Runtime.exceptionThrown')exceptions.push(m.params);if(m.id){const p=pending.get(m.id);pending.delete(m.id);m.error?p.reject(m.error):p.resolve(m.result)}};
 const call=(method,params={})=>new Promise((resolve,reject)=>{const id=++serial;pending.set(id,{resolve,reject});socket.send(JSON.stringify({id,method,params}))});
 const js=async expression=>{const r=await call('Runtime.evaluate',{expression,awaitPromise:true,returnByValue:true,replMode:true});assert(!r.exceptionDetails,JSON.stringify(r.exceptionDetails));return r.result.value};
 async function wait(expression){for(let i=0;i<100;i++){if(await js(expression))return;await delay(30)}throw Error('Timed out: '+expression+' '+JSON.stringify(await js('({ready:window.ready,text:document.body.innerText})'))+' '+JSON.stringify(exceptions))}
 const click=selector=>js(`document.querySelector(${JSON.stringify(selector)}).click()`);
 await call('Runtime.enable');await call('Page.enable');
 await call('Emulation.setDeviceMetricsOverride',{width:1440,height:1000,deviceScaleFactor:1,mobile:false});
 await call('Page.navigate',{url:`http://127.0.0.1:${server.address().port}/`});
 await wait('window.ready && !!document.querySelector(".experiment-backtests")');
 assert.equal(await js(`document.querySelectorAll('[data-evolution-list] tbody tr').length`),10);
 assert.equal(await js(`document.querySelector('[data-evolution-list] tbody tr').dataset.researchId`),'completed');
 await click('[data-evolution-list] [data-page=last]');
 assert.equal(await js(`document.querySelectorAll('[data-evolution-list] tbody tr').length`),3);
 assert.equal(await js(`document.querySelector('[data-evolution-list] tbody tr').dataset.researchId`),'saved-5');
 await click('[data-research-id=saved-5]');await wait(`!!calls['saved-5']`);
 await js(`listeners.route('factors/experiments')`);await delay(60);
 assert.equal(await js(`document.querySelector('[data-evolution-list] .table-pagination__current').textContent`),'2 / 2 页');
 assert(await js(`document.querySelector('[data-research-id=saved-5]').classList.contains('is-active')`));
 await click('[data-evolution-list] [data-page=first]');await click('[data-research-id=completed]');await wait(`document.querySelector('.experiment-results')!==null`);
 await js(`document.querySelector('[data-evolution-list] input').value='2';document.querySelector('[data-evolution-list] form').requestSubmit()`);
 assert.equal(await js(`document.querySelector('[data-evolution-list] tbody tr').dataset.researchId`),'saved-5');
 await js(`document.querySelector('[data-evolution-list] [data-page-size] [data-select-trigger]').click();document.querySelector('[data-evolution-list] [data-page-size] [data-value="20"]').click()`);
 assert.equal(await js(`document.querySelectorAll('[data-evolution-list] tbody tr').length`),13);
 await js(`document.querySelector('[data-evolution-list] [data-page-size] [data-select-trigger]').click();document.querySelector('[data-evolution-list] [data-page-size] [data-value="10"]').click()`);
 assert.equal(await js(`document.querySelectorAll('#host .table-pagination:not([hidden])').length`),1);
 assert.equal(await js(`!!document.querySelector('#host [data-list],#host [data-detail],#host [data-improved-filter],#host .experiments__record')`),false);
 assert.equal(await js(`document.querySelectorAll('.experiment-backtests tbody tr').length`),5);
 await wait('!!document.querySelector(".research-report .chat-markdown h2")');
 assert(await js('reportReads>=1'));
 assert.deepEqual(await js(`([...document.querySelector('[data-evolution-detail]').children].map(node=>node.querySelector('h3,h4')?.textContent))`),['盈利改善质量防御','实验结果','已完成回测','实验进度','研究报告']);
 assert(await js(`document.querySelector('.experiment-results__head .badge--danger')?.textContent==='无效'`));
 assert.deepEqual(await js(`([...document.querySelectorAll('.experiment-results__metrics > .metric')].map(node=>node.querySelector('.metric__value').textContent))`),['2.40%','-13.25%','15.65%']);
 assert(await js(`!document.querySelector('.research-report').closest('details')`));
 assert(await js(`document.querySelector('.experiment-results .experiment-conclusion strong')?.textContent==='候选未通过验证门槛，未运行锁定测试。'`));
 assert(await js(`(async()=>{const {appendExperimentSummary}=await import('/core/research-summary.js');const host=document.createElement('div');for(const record of [undefined,{}, {conclusion:'旧记录'}, {ai_generated:false,model_name:'model',conclusion:'固定文案'}])appendExperimentSummary(host,record);return host.childElementCount===0})()`));
 const legacySource=['# 研究报告','- 数据快照：`DS-A5A5719E70F1FF694064`','- 固定协议哈希：`cf5191a06c608bea`','回测编号：BT-kept；数据快照：DS-inline。','保留研究结论和回测数据。'].join('\n');
 const cleaned=await js(`reportText(${JSON.stringify(legacySource)})`);
 assert(!/数据快照|固定协议哈希|DS-A5A|DS-inline|cf5191/.test(cleaned),cleaned);
 assert(cleaned.includes('BT-kept')&&cleaned.includes('保留研究结论和回测数据'),cleaned);
 assert.equal(await js(`document.querySelector('[data-task-log]').open`),false);
 assert.equal(await js(`document.querySelectorAll('#rules .table-pagination').length`),0);
 assert.equal(await js(`document.querySelector('#rules .rule-detail__head h3').textContent`),'中国A股股票交易规则');
 assert.equal(await js(`document.querySelectorAll('#rules .rule-detail__head .badge').length`),1);
 assert.equal(await js(`document.querySelectorAll('#small tbody tr').length`),7);
 assert.equal(await js(`document.querySelectorAll('[data-rule-section=disclosures] tbody tr').length`),13);
 await click('.experiment-backtests .experiments__run-link');
 assert.deepEqual(await js('events.slice(-2)'),[['backtest:open','BT-train'],['route','backtest']]);
 const loadedReports=await js('reportReads');
 await js(`listeners.route('factors/experiments')`);await delay(100);
 assert.equal(await js('reportReads'),loadedReports);
 await click('[data-research-id=running]');await wait(`!!document.querySelector('[role=progressbar]')`);
 assert(await js(`!!document.querySelector('.experiment-results') && !document.querySelector('.research-report')`));
 assert.equal(await js(`document.querySelectorAll('.experiment-backtests tbody tr').length`),5);
 await js(`document.querySelector('[data-task-log]').open=true;await new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)))`);
 assert(await js(`(()=>{const code=document.querySelector('[data-task-log] pre');return code.scrollTop===0&&code.textContent.split('\\n')[0].includes('日志 229')})()`));
 await js(`document.querySelector('[data-task-log]').open=false`);await delay(2200);assert.equal(await js(`document.querySelector('[data-task-log]').open`),false);
 await click('[data-research-id=paused]');await wait(`document.querySelector('[data-session-progress] button')?.textContent==='继续实验'`);
 await click('[data-research-id=slow]');await click('[data-research-id=fast]');await wait(`document.querySelector('.experiment-intro h3')?.textContent.includes('快速切换实验')`);
 await delay(600);assert(await js(`document.querySelector('.experiment-intro h3').textContent.includes('快速切换实验')`));
 await click('[data-research-id=completed]');await wait(`document.querySelector('.experiment-results')!==null`);
 const cached=await js('calls.fast');await click('[data-research-id=fast]');await wait(`document.querySelector('.experiment-intro h3').textContent.includes('快速切换实验')`);assert.equal(await js('calls.fast'),cached);
 for(const theme of ['light','dark']) for(const width of [1440,768,390,320]) {
  await call('Emulation.setDeviceMetricsOverride',{width,height:1000,deviceScaleFactor:1,mobile:false});
  await js(`document.documentElement.dataset.theme='${theme}';document.querySelector('#host').scrollIntoView()`);await delay(50);
  const layout=await js(`(()=>{const cards=[...document.querySelectorAll('#host .card')],button=document.querySelector('[data-evolution-start]'),intro=document.querySelector('.experiment-intro');return {width:innerWidth,page:document.documentElement.scrollWidth,font:getComputedStyle(button).fontSize,height:button.getBoundingClientRect().height,bodyFont:getComputedStyle(intro.querySelector('p')).fontSize,padding:cards.map(c=>parseFloat(getComputedStyle(c).paddingTop)),gap:parseFloat(getComputedStyle(document.querySelector('.experiments__evolution-detail')).gap),emptyLog:document.querySelector('[data-task-log]').getBoundingClientRect().height,scroll:cards.some(c=>c.scrollWidth>c.clientWidth)}})()`);
  assert(layout.page<=width&&!layout.scroll&&layout.font===layout.bodyFont&&layout.height>=32&&layout.padding.every(p=>p<=12)&&layout.gap<=10&&layout.emptyLog<30,JSON.stringify(layout));
  const shot=await call('Page.captureScreenshot',{format:'png'});await writeFile(join(output,`sessions-${width}-${theme}.png`),Buffer.from(shot.data,'base64'));
  const results=await js(`(()=>{const card=document.querySelector('.experiment-results'),head=card.querySelector('h4').getBoundingClientRect(),badge=card.querySelector('.badge').getBoundingClientRect(),metrics=[...card.querySelectorAll('.metric')],report=document.querySelector('.research-report');return {inline:Math.abs(head.top-badge.top)<8,metrics:metrics.map(node=>({height:node.getBoundingClientRect().height,padding:parseFloat(getComputedStyle(node).paddingTop)})),reportScroll:report.querySelector('.research-report__body').scrollHeight>report.querySelector('.research-report__body').clientHeight}})()`);
  assert(results.inline&&results.metrics.every(m=>m.height<90&&m.padding<=10)&&!results.reportScroll,JSON.stringify(results));
  assert(await js(`!!document.querySelector('.experiment-results .experiment-conclusion')&&[...document.querySelectorAll('.experiment-results__metrics .metric')].every(node=>getComputedStyle(node).borderTopColor===getComputedStyle(node.querySelector('.metric__value')).color)`));
  if(width===1440||width===390){await js(`document.querySelector('.experiment-results').scrollIntoView({block:'start'})`);const shot=await call('Page.captureScreenshot',{format:'png'});await writeFile(join(output,`results-${width}-${theme}.png`),Buffer.from(shot.data,'base64'));}
  await js(`document.querySelector('[data-rule-section=limits]').scrollIntoView({block:'start'})`);
  const rule=await js(`(()=>{const card=document.querySelector('[data-rule-section=limits]'),table=card.querySelector('table'),panel=card.querySelector('.table-panel');return {page:document.documentElement.scrollWidth,width:innerWidth,rows:table.tBodies[0].rows.length,pagers:card.querySelectorAll('.table-pagination').length,gap:panel.getBoundingClientRect().height-table.getBoundingClientRect().height,stacked:panel.getBoundingClientRect().top>=card.querySelector('.kv').getBoundingClientRect().bottom}})()`);
  assert(rule.page<=width&&rule.rows===7&&!rule.pagers&&rule.gap<4&&rule.stacked,JSON.stringify(rule));
  if(width===1440||width===390){const shot=await call('Page.captureScreenshot',{format:'png'});await writeFile(join(output,`rules-${width}-${theme}.png`),Buffer.from(shot.data,'base64'));}
 }
 await click('[data-evolution-start]');await wait(`!!document.querySelector('[role=dialog]')`);
 assert.equal(await js(`document.querySelectorAll('[role=dialog] .confirm__field').length`),1);
 assert.equal(await js(`document.querySelectorAll('[role=dialog] [role=option]').length`),2);
 assert(await js(`document.querySelector('[role=dialog]').textContent.includes('开始自我进化')`));
 await click('[role=dialog] .btn--ghost');await wait(`!document.querySelector('[data-evolution-start]').disabled`);
 assert.deepEqual(await js('startRequests'),[]);
 await js('startFailure=true');await click('[data-evolution-start]');await wait(`!!document.querySelector('[role=dialog]')`);
 await click('[role=dialog] [data-select-trigger]');await click('[role=dialog] [role=option][data-value=earnings]');await click('[role=dialog] .btn--primary');
 await wait(`!document.querySelector('[data-evolution-start]').disabled`);
 assert.deepEqual(await js('startRequests'),[{base_factor:'earnings'}]);
 assert(await js(`events.some(([type,value])=>type==='toast'&&value.detail==='模型连接不可用')`));
 await js('startFailure=false');await click('[data-evolution-start]');await wait(`!!document.querySelector('[role=dialog]')`);
 await click('[role=dialog] .btn--primary');await wait('typeof releaseStart==="function"');
 await click('[data-evolution-start]');assert.equal(await js('startRequests.length'),2);
 assert(await js(`document.querySelector('[data-evolution-start]').disabled&&!document.querySelector('[role=dialog]')`));
 await js('releaseStart()');await wait(`!document.querySelector('[data-evolution-start]').disabled`);
 assert.deepEqual(await js('startRequests'),[{base_factor:'earnings'},{base_factor:'quality'}]);
 await call('Page.navigate',{url:`http://127.0.0.1:${server.address().port}/?standalone`});await wait('window.ready&&!!document.querySelector(".experiment-backtests")');
 assert.equal(await js(`document.querySelectorAll('[data-evolution-list] tbody tr').length`),1);
 assert.equal(await js(`document.querySelectorAll('#host .table-pagination:not([hidden])').length`),0);
 assert(await js(`!!document.querySelector('.experiment-results') && document.querySelector('.experiment-backtests').textContent.includes('BT-standalone')`));
 assert.deepEqual(exceptions,[]);
 console.log(`PASS: single-factor evolution start, cancel/error recovery and duplicate-submit guard; compact session-only layout, newest-first session pagination, preserved page/selection, training/fold/locked backtest links, completed-only results, inline colored verdict, metric cards, expanded report and cached reads, bottom backtests, log following, stale-response guard/cache, standalone records, fixed rule tables without pagination, 8 theme/width layouts. ${output}`);
}finally{socket?.close();browser.kill();server.close()}
