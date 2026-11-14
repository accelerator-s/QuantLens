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
const output=await mkdtemp(join(tmpdir(),'quantlens-backtest-comparison-'));
const index=await readFile(join(root,'index.html'),'utf8');
const styles=[...index.matchAll(/<link rel="stylesheet" href="([^"]+)"/g)].map(m=>`<link rel="stylesheet" href="${m[1]}">`).join('');
const component=await readFile(join(root,'components/backtest/backtest.html'),'utf8');
const html=`<!doctype html><html lang="zh-CN" data-theme="light"><head><meta name="viewport" content="width=device-width,initial-scale=1">${styles}
<style>body{overflow:auto}main{padding:16px;width:100%;box-sizing:border-box;container:workspace / inline-size}</style></head><body><main><div class="view" id="host">${component}</div></main><script type="module">
import {mount} from '/components/backtest/backtest.js';
const days=['2024-01-02','2024-01-03','2024-01-04','2024-01-05'];
const run={profile:'research',profile_display_name:'趋势研究策略',status:{state:'completed'},config:{config:{initial_capital:100,start:days[0],end:days.at(-1)},profile:{factors:{items:[{name:'momentum'}]}}},metrics:{benchmark:{name:'universe_equal_weight'},returns:{total_return:.15}}};
const nav={total_rows:4,items:days.map((date,i)=>({date,nav:105+i*3,benchmark_nav:1.02+i*.01,drawdown:0})),monthly_returns:[]};
window.releases={};window.rejects={};window.referenceCalls={};window.ctx={tabState:{},bus:{on(){}},api:{
backtestRuns:async()=>({items:['a','b','c','d'].map(run_id=>({run_id,profile:'research',start:days[0],end:days.at(-1),state:'completed'}))}),
backtestRun:async id=>({...run,run_id:id,config:{...run.config,profile:{...run.config.profile,extra:{purpose:id==='b'?'factor_research_only':'strategy_research'}}}}),backtestNav:async()=>nav,
backtestReferences:id=>new Promise((resolve,reject)=>{releases[id]=resolve;rejects[id]=reject;referenceCalls[id]=(referenceCalls[id]||0)+1}),
backtestTrades:async()=>({items:[],total_rows:0}),backtestPositions:async()=>({items:[],dates:[],date:''}),experiments:async()=>({items:[]})}};
window.makeReference=name=>({name,status:'available',reason:'回测配置未限定市值或板块，使用中证全指作整体市场参照。',source:'csmar',total_return:.0743,excess_return:-.1525,items:days.map((date,i)=>({date,nav:1.01+i*.02}))});
await mount(document.querySelector('#host'),ctx);window.ready=true;
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
 const js=async expression=>{const r=await call('Runtime.evaluate',{expression,awaitPromise:true,returnByValue:true});assert(!r.exceptionDetails,JSON.stringify(r.exceptionDetails));return r.result.value};
 async function wait(expression){for(let i=0;i<100;i++){if(await js(expression))return;await delay(30)}throw Error('Timed out: '+expression+' '+JSON.stringify(await js('({ready:window.ready,text:document.body.innerText})'))+' '+JSON.stringify(exceptions))}
 const click=selector=>js(`document.querySelector(${JSON.stringify(selector)}).click()`);
 const card='.backtest__comparison',lines=`document.querySelectorAll('${card} .chart__line').length`;
 await call('Runtime.enable');await call('Page.enable');
 await call('Emulation.setDeviceMetricsOverride',{width:1440,height:1000,deviceScaleFactor:1,mobile:false});
 await call('Page.navigate',{url:`http://127.0.0.1:${server.address().port}/`});
 await wait(`window.ready&&${lines}===2`);
 assert(await js("document.querySelector('[data-curve=market]').disabled"));
 await click('[data-curve=benchmark]');assert.equal(await js(lines),1);
 await click('[data-run-id=b]');await wait('!!releases.b');
 await js("releases.a(makeReference('迟到的旧指数'))");await delay(40);
 assert(!(await js(`document.querySelector('${card}').textContent.includes('迟到')`)));
 await js("releases.b(makeReference('中证全指'))");await wait(`document.querySelector('${card}').textContent.includes('中证全指')`);
 assert.equal(await js(lines),2);assert.equal(await js("document.querySelector('[data-curve=benchmark]').checked"),false);
 assert.equal(await js(`document.querySelector('${card} .backtest__index-note').textContent`),'指数收益 7.43% · 因子超额 -15.25 个百分点');
 assert(await js(`!document.querySelector('${card}').textContent.includes('来源：')&&!document.querySelector('${card}').textContent.includes('未限定')`));
 assert.equal(await js(`document.querySelector('${card} [data-curve=strategy]').closest('label').textContent`),'回测净值');
 assert.equal(await js(`document.querySelector('${card} [data-curve=benchmark]').closest('label').textContent`),'股票池等权');
 assert(await js("![...document.querySelectorAll('.backtest__summary .kv__key')].some(node=>node.textContent==='收益基准')"));
 await click('[data-curve=strategy]');assert.equal(await js(lines),1);
 await click('[data-curve=market]');assert.equal(await js(lines),0);
 assert(await js(`document.querySelector('${card} .backtest__curves-empty')!==null`));
 assert((await js(`document.querySelector('${card}').getBoundingClientRect().height`))<190);
 await click('[data-curve=benchmark]');await click('[data-curve=strategy]');await click('[data-curve=market]');assert.equal(await js(lines),3);
 for(const theme of ['light','dark'])for(const width of [1440,768,390]){
  await call('Emulation.setDeviceMetricsOverride',{width,height:1000,deviceScaleFactor:1,mobile:false});
  await js(`document.documentElement.dataset.theme='${theme}'`);await delay(100);
  const layout=await js(`(()=>{const c=document.querySelector('${card}'),r=c.getBoundingClientRect(),s=c.querySelector('svg').getBoundingClientRect(),h=c.querySelector('.backtest__comparison-heading').getBoundingClientRect(),t=c.querySelector('.backtest__curve-controls').getBoundingClientRect();return {width:innerWidth,page:document.documentElement.scrollWidth,left:r.left,right:r.right,height:r.height,chart:s.height,overflow:c.scrollWidth>c.clientWidth,centerOffset:Math.abs((h.top+h.bottom-t.top-t.bottom)/2),toggleOffsets:[...c.querySelectorAll('label')].flatMap(label=>{const rects=[...label.children].map(node=>node.getBoundingClientRect());return rects.map(r=>Math.abs((r.top+r.bottom-rects[0].top-rects[0].bottom)/2))})}})()`);
  assert(layout.page<=width&&layout.left>=0&&layout.right<=width&&!layout.overflow,JSON.stringify(layout));
  assert(layout.chart===280&&layout.height<480,JSON.stringify(layout));
  if(width===1440) assert(layout.centerOffset<1,JSON.stringify(layout));
  assert(layout.toggleOffsets.every(offset=>offset<1),JSON.stringify(layout));
  await js(`document.querySelector('${card}').scrollIntoView({block:'start'})`);
  const shot=await call('Page.captureScreenshot',{format:'png'});await writeFile(join(output,`comparison-${width}-${theme}.png`),Buffer.from(shot.data,'base64'));
 }
 await click('[data-run-id=a]');await wait(`document.querySelector('${card}').textContent.includes('迟到的旧指数')`);
 assert.equal(await js(lines),3);
 assert.equal(await js(`document.querySelector('${card} .backtest__index-note').textContent`),'指数收益 7.43% · 策略超额 -15.25 个百分点');
 await click('[data-run-id=c]');await wait("document.querySelector('[data-curve=market]').disabled");
 await js("releases.c({status:'unavailable',name:'中证全指',message:'指数行情暂不可用',items:[]})");await wait(`document.querySelector('${card}').textContent.includes('暂不可用')`);
 assert.equal(await js(lines),2);assert(await js("document.querySelector('[data-curve=market]').disabled"));
 await click('[data-run-id=d]');await wait('!!rejects.d');
 await js("rejects.d(new Error('连接暂时中断'))");
 await wait(`!!document.querySelector('${card} [data-reference-retry]')`);
 assert(await js(`document.querySelector('${card}').textContent.includes('加载失败')`));
 await click('[data-reference-retry]');await wait('referenceCalls.d===2');
 await js("releases.d(makeReference('中证全指'))");
 await wait(`${lines}===3&&!document.querySelector('${card} [data-reference-retry]')`);
 assert.deepEqual(exceptions,[]);
 console.log(`PASS: current reference contract, asynchronous loading, stale-response guard, persistent toggles, all-hidden state, unavailable data, failed-request retry, 6 compact responsive layouts. ${output}`);
}finally{socket?.close();browser.kill();server.close()}
