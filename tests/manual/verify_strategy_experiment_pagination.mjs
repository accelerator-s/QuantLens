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
const output=await mkdtemp(join(tmpdir(),'quantlens-strategy-pages-'));
const index=await readFile(join(root,'index.html'),'utf8');
const styles=[...index.matchAll(/<link rel="stylesheet" href="([^"]+)"/g)].map(m=>`<link rel="stylesheet" href="${m[1]}">`).join('');
const component=await readFile(join(root,'components/strategy-runs/strategy-runs.html'),'utf8');
const html=`<!doctype html><html lang="zh-CN" data-theme="light"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">${styles}<style>body{overflow:auto}main{padding:16px;container:workspace / inline-size}</style></head><body><main><section class="view" id="host">${component}</section></main><script type="module">
import {mount} from '/components/strategy-runs/strategy-runs.js';
window.listeners={};window.reads=[];
window.items=Array.from({length:25},(_,i)=>({experiment_id:'session-'+i,profile_name:'profile-'+i,display_name:'实验 '+i,model:'model_linear',started_at:new Date(Date.UTC(2026,10,i+1)).toISOString(),state:'completed',phase:'研究完成'}));
const spec={model:'model_linear',train_start:'2020-01-01',train_end:'2022-12-31',start:'2023-01-01',end:'2024-12-31',top_n:20};
window.api={strategies:async()=>({items:items.map(row=>({profile_name:row.profile_name,display_name:row.display_name}))}),strategyExperiments:async()=>({items}),strategyExperiment:async id=>{reads.push(id);const row=items.find(row=>row.experiment_id===id);return {...row,spec,status:'draft',task:{status:'completed',progress:{step:'研究完成'},log:['开始计算','计算中','计算已完成']},hypothesis:'检验固定因子的组合表现。',factor_display_names:{quality:'质量'},result:{state:'completed',run_id:'BT-'+id,weights:{quality:1},selection:[],selection_date:'2024-12-31',metrics:{returns:{total_return:.1,annualized_return:.05,annualized_excess_return:.02},risk:{max_drawdown:-.08,sharpe:1.2}},report:'# 研究结果'}}}};
await mount(document.querySelector('#host'),{api,bus:{on:(event,fn)=>listeners[event]=fn,emit(){}}});window.ready=true;
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
 await wait('window.ready&&!!document.querySelector(".experiment-backtests")');
 assert.equal(await js(`document.querySelector('[data-task-log] pre').textContent.replaceAll('[时间未记录]  ','')`),'计算已完成\n计算中\n开始计算');
 assert.equal(await js(`document.querySelector('[data-task-log] summary').textContent`),'运行日志（展示 3 行）');
 assert.deepEqual(await js(`([...document.querySelector('[data-evolution-detail]').children].map(node=>node.querySelector('h3,h4')?.textContent))`),['实验介绍 · 实验 24','实验进度','实验结果','研究报告','已完成回测']);
 assert(await js(`document.querySelector('.experiment-results__head .badge--safe')?.textContent==='已完成'`));
 assert.equal(await js(`document.querySelectorAll('.experiment-results__metrics > .metric').length`),3);
 assert(await js(`!!document.querySelector('.research-report .chat-markdown h1')&&!document.querySelector('.research-report').closest('details')`));
 const list='[data-evolution-list]';
 assert.equal(await js(`document.querySelectorAll('${list} tbody tr').length`),10);
 assert.equal(await js(`document.querySelector('${list} tbody tr').dataset.researchId`),'session-24');
 await click(`${list} [data-page=last]`);
 assert.equal(await js(`document.querySelectorAll('${list} tbody tr').length`),5);
 assert.equal(await js(`document.querySelector('${list} tbody tr').dataset.researchId`),'session-4');
 await js(`document.querySelector('${list} input').value='2';document.querySelector('${list} form').requestSubmit()`);
 assert.equal(await js(`document.querySelector('${list} tbody tr').dataset.researchId`),'session-14');
 await click('[data-research-id=session-14]');await wait(`document.querySelector('.experiment-intro h3').textContent.includes('实验 14')`);
 await js(`listeners.subtab({page:'strategy-research',tab:'experiments'})`);await delay(60);
 assert.equal(await js(`document.querySelector('${list} .table-pagination__current').textContent`),'2 / 3 页');
 assert(await js(`document.querySelector('[data-research-id=session-14]').getAttribute('aria-selected')==='true'`));
 await js(`listeners['strategy:experiment']('session-0')`);await wait(`document.querySelector('.experiment-intro h3').textContent.includes('实验 0')`);
 assert.equal(await js(`document.querySelector('${list} .table-pagination__current').textContent`),'3 / 3 页');
 assert(await js(`document.querySelector('[data-research-id=session-0]').classList.contains('is-active')`));
 await js(`document.querySelector('${list} [data-page-size] [data-select-trigger]').click();document.querySelector('${list} [data-page-size] [data-value="20"]').click()`);
 assert.equal(await js(`document.querySelectorAll('${list} tbody tr').length`),20);
 await click(`${list} [data-page=last]`);assert.equal(await js(`document.querySelectorAll('${list} tbody tr').length`),5);
 await click(`${list} [data-page=first]`);
 assert.equal(await js(`document.querySelector('${list} tbody tr').dataset.researchId`),'session-24');
 for(const theme of ['light','dark'])for(const width of [1440,768,390,320]){
  await call('Emulation.setDeviceMetricsOverride',{width,height:1000,deviceScaleFactor:1,mobile:false});
  await js(`document.documentElement.dataset.theme='${theme}';document.querySelector('#host').scrollIntoView()`);await delay(50);
  const layout=await js(`(()=>{const list=document.querySelector('[data-evolution-list]'),wrap=list.querySelector('.table-wrap');return {width:innerWidth,page:document.documentElement.scrollWidth,vertical:wrap.scrollHeight>wrap.clientHeight,controls:[...list.querySelectorAll('button,input,select')].every(node=>node.getBoundingClientRect().right<=innerWidth)}})()`);
  assert(layout.page<=width&&!layout.vertical&&layout.controls,JSON.stringify(layout));
  const results=await js(`(()=>{const card=document.querySelector('.experiment-results'),head=card.querySelector('h4').getBoundingClientRect(),badge=card.querySelector('.badge').getBoundingClientRect();return {inline:Math.abs(head.top-badge.top)<8,metrics:[...card.querySelectorAll('.metric')].every(node=>node.getBoundingClientRect().height<90),bottom:document.querySelector('[data-evolution-detail]').lastElementChild.classList.contains('experiment-backtests'),report:document.querySelector('.experiment-results').nextElementSibling.classList.contains('research-report')}})()`);
  assert(results.inline&&results.metrics&&results.bottom&&results.report,JSON.stringify(results));
  assert(await js(`!document.querySelector('.experiment-results .experiment-conclusion')&&[...document.querySelectorAll('.experiment-results__metrics .metric')].every(node=>getComputedStyle(node).borderTopColor===getComputedStyle(node.querySelector('.metric__value')).color)`));
  await js(`document.querySelector('.experiment-results').scrollIntoView({block:'start'})`);
  const shot=await call('Page.captureScreenshot',{format:'png'});await writeFile(join(output,`strategy-pages-${width}-${theme}.png`),Buffer.from(shot.data,'base64'));
 }
 assert.deepEqual(exceptions,[]);
 console.log(`PASS: newest-first strategy experiment pages, first/last/jump/page-size, row selection and refresh retained, deep-link page reveal, 8 responsive layouts. ${output}`);
}finally{socket?.close();browser.kill();server.close()}
