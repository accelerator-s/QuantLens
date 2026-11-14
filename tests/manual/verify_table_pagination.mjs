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
const output=await mkdtemp(join(tmpdir(),'quantlens-table-pagination-'));
const index=await readFile(join(root,'index.html'),'utf8');
const styles=[...index.matchAll(/<link rel="stylesheet" href="([^"]+)"/g)].map(m=>`<link rel="stylesheet" href="${m[1]}">`).join('');
const component=await readFile(join(root,'components/backtest/backtest.html'),'utf8');
const html=`<!doctype html><html lang="zh-CN" data-theme="light"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">${styles}<style>body{overflow:auto}main{padding:16px;container:workspace / inline-size}</style></head><body><main><div class="view" id="host">${component}</div><section id="local"></section><section id="remote"></section><section id="markup"></section></main><script type="module">
import {mount} from '/components/backtest/backtest.js';
import {tableEl,remoteTableEl} from '/core/dom.js';
import {initMarkupTablePagination} from '/core/pagination.js';
window.calls={list:0,detail:0,nav:0,index:0,trades:0,positions:0};window.listeners={};
const items=Array.from({length:47},(_,i)=>({run_id:'BT-'+i,profile:'research',profile_display_name:'盈利防御组合研究 · '+i,start:'2018-01-01',end:'2021-12-11',state:'completed',updated_at:'v1'}));
window.ctx={tabState:{},bus:{on:(event,fn)=>{listeners[event]=fn},emit(){}},api:{
backtestRuns:async()=>{calls.list++;return {items}},backtestRun:async id=>{calls.detail++;return {run_id:id,profile:'research',config:{},status:{state:'completed'}}},
backtestNav:async()=>{calls.nav++;return {items:[]}},backtestReferences:async()=>{calls.index++;return {items:[]}},
backtestTrades:async(id,{limit,offset})=>{calls.trades++;return {items:Array.from({length:Math.min(limit,65-offset)},(_,i)=>({instrument:'股票-'+(i+offset),date:'2024-01-01'})),total_rows:65}},
backtestPositions:async(id,{limit=10,offset=0,date=''})=>{calls.positions++;return {items:Array.from({length:Math.min(limit,23-offset)},(_,i)=>({instrument:'持仓-'+(i+offset)})),total_rows:23,dates:['2024-01-01'],date:date||'2024-01-01'}},experiments:async()=>({items:[]})}};
await mount(document.querySelector('#host'),ctx);
window.selected=[];window.local=tableEl([{key:'id',label:'编号'}],Array.from({length:47},(_,id)=>({id})),{onRowClick:r=>selected.push(r.id),onRowRender:(node,row)=>node.dataset.id=row.id});document.querySelector('#local').append(local);
window.remoteCalls=[];window.failPage=true;window.remote=remoteTableEl([{key:'id',label:'编号'}],async ({limit,offset})=>{remoteCalls.push({limit,offset});if(offset===10&&failPage)throw Error('fixture failure');return {total_rows:47,items:Array.from({length:Math.min(limit,47-offset)},(_,i)=>({id:i+offset}))}});document.querySelector('#remote').append(remote);await remote.ready;
initMarkupTablePagination(document.querySelector('#markup'));document.querySelector('#markup').innerHTML='<div class="ql-table-wrap"><table class="ql-table"><thead><tr><th>股票</th></tr></thead><tbody>'+Array.from({length:24},(_,i)=>'<tr><td><button data-stock="'+i+'">'+i+'</button></td></tr>').join('')+'</tbody></table></div>';document.querySelectorAll('#markup [data-stock]').forEach(b=>b.onclick=()=>selected.push(Number(b.dataset.stock)));
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
 const js=async expression=>{const r=await call('Runtime.evaluate',{expression,awaitPromise:true,returnByValue:true});assert(!r.exceptionDetails,JSON.stringify(r.exceptionDetails));return r.result.value};
 async function wait(expression){for(let i=0;i<100;i++){if(await js(expression))return;await delay(30)}throw Error('Timed out: '+expression+' '+JSON.stringify(await js('({ready:window.ready,text:document.body.innerText})'))+' '+JSON.stringify(exceptions))}
 const click=selector=>js(`document.querySelector(${JSON.stringify(selector)}).click()`);
 await call('Runtime.enable');await call('Page.enable');
 await call('Emulation.setDeviceMetricsOverride',{width:1440,height:1000,deviceScaleFactor:1,mobile:false});
 await call('Page.navigate',{url:`http://127.0.0.1:${server.address().port}/`});
 await wait('window.ready && document.querySelectorAll("#markup tbody tr").length===10');
 const runList='#host [data-list]';
 assert.equal(await js(`document.querySelectorAll('${runList} tbody tr').length`),10);
 await click(`${runList} [data-page=last]`);
 assert.equal(await js(`document.querySelector('${runList} tbody tr').dataset.runId`),'BT-40');
 assert.equal(await js(`document.querySelectorAll('${runList} tbody tr').length`),7);
 await click('[data-run-id=BT-40]');await wait("document.querySelector('[data-detail] h3')?.title==='BT-40' || document.querySelector('[data-detail] h3')?.textContent==='BT-40'");
 const loaded=await js('({...calls})');
 await click('[data-run-id=BT-40]');await delay(30);
 assert.deepEqual(await js('({...calls})'),loaded);
 await js("listeners.route('backtest')");await delay(40);
 assert.equal(await js('calls.detail'),loaded.detail);
 assert.equal(await js(`document.querySelector('${runList} .table-pagination__current').textContent`),'5 / 5 页');
 assert.equal(await js(`!!document.querySelector('#host .state-card--loading')`),false);
 await click(`${runList} [data-page=first]`);await click('[data-run-id=BT-0]');await delay(40);
 assert.equal(await js('calls.detail'),loaded.detail);
 const trades='#host [data-detail] .detail-card:nth-child(2)';
 await click(`${trades} [data-page=last]`);await wait(`document.querySelector('${trades} tbody tr')?.textContent.includes('股票-60')`);
 assert.equal(await js(`document.querySelectorAll('${trades} tbody tr').length`),5);
 const tradeCount=await js('calls.trades');
 await click(`${trades} [data-page=first]`);await delay(30);assert.equal(await js('calls.trades'),tradeCount);
 const positions='#host [data-detail] .detail-card:nth-child(3)';
 await click(`${positions} [data-page=last]`);await wait(`document.querySelector('${positions} tbody tr')?.textContent.includes('持仓-20')`);
 assert.equal(await js(`document.querySelectorAll('${positions} tbody tr').length`),3);
 await click('#local [data-page=next]');await click('#local [data-id="10"]');assert.equal(await js('selected.at(-1)'),10);
 await js(`document.querySelector('#local input').value='4';document.querySelector('#local form').requestSubmit()`);
 assert.equal(await js(`document.querySelector('#local tbody tr').dataset.id`),'30');
 await click('#local [data-page=prev]');assert.equal(await js(`document.querySelector('#local tbody tr').dataset.id`),'20');
 await js(`document.querySelector('#local [data-page-size] [data-select-trigger]').click();document.querySelector('#local [data-page-size] [data-value="20"]').click()`);
 assert.equal(await js(`document.querySelectorAll('#local tbody tr').length`),20);
 assert.equal(await js(`document.querySelector('#local .table-pagination__current').textContent`),'1 / 3 页');
 assert.equal(await js(`document.querySelectorAll('.table-pagination select').length`),0);
 await click('#local [data-page-size] [data-select-trigger]');
 await call('Input.dispatchKeyEvent',{type:'keyDown',key:'Escape',code:'Escape'});
 assert.equal(await js(`document.querySelector('#local [data-select-trigger]').getAttribute('aria-expanded')`),'false');
 await call('Input.dispatchKeyEvent',{type:'keyDown',key:'ArrowUp',code:'ArrowUp'});
 await call('Input.dispatchKeyEvent',{type:'keyDown',key:'Enter',code:'Enter'});
 assert.equal(await js(`document.querySelector('#local [data-page-size] [data-select-value]').textContent`),'10');
 assert.equal(await js(`document.querySelectorAll('#local tbody tr').length`),10);
 await click('#local [data-page-size] [data-select-trigger]');await click('#local th');
 assert.equal(await js(`document.querySelector('#local [data-select-list]').hidden`),true);
 await click('#remote [data-page=next]');await wait(`document.querySelector('#remote .table-panel__status').textContent.includes('fixture failure')`);
 assert.equal(await js(`document.querySelector('#remote tbody tr').textContent`),'0');
 await js('failPage=false');await click('#remote > .table-panel > button');await wait(`document.querySelector('#remote tbody tr').textContent==='10'`);
 await click('#remote [data-page=last]');await wait(`document.querySelector('#remote tbody tr').textContent==='40'`);
 assert.deepEqual(await js('remoteCalls.at(-1)'),{limit:10,offset:40});
 await click('#remote [data-page=prev]');await wait(`document.querySelector('#remote tbody tr').textContent==='30'`);
 const before=await js('remoteCalls.length');await click('#remote [data-page=last]');await delay(20);assert.equal(await js('remoteCalls.length'),before);
 await click('#markup [data-page=last]');await click('#markup [data-stock="23"]');assert.equal(await js('selected.at(-1)'),23);
 for(const theme of ['light','dark']) for(const width of [1440,768,390,320]) {
  await call('Emulation.setDeviceMetricsOverride',{width,height:1000,deviceScaleFactor:1,mobile:false});
  await js(`document.documentElement.dataset.theme='${theme}';document.querySelector('[data-list]').scrollIntoView()`);await delay(50);
  const layout=await js(`(()=>{const list=document.querySelector('[data-list]'),wrap=list.querySelector('.table-wrap'),last=wrap.querySelector('tbody tr:last-child').getBoundingClientRect(),box=wrap.getBoundingClientRect();return {pageWidth:document.documentElement.scrollWidth,width:innerWidth,listScroll:list.scrollHeight>list.clientHeight,tableScroll:wrap.scrollHeight>wrap.clientHeight,lastBottom:last.bottom,boxBottom:box.bottom,padding:getComputedStyle(list).paddingRight,weight:getComputedStyle(wrap.querySelector('th')).fontWeight,controls:[...list.querySelectorAll('.table-pagination button, .table-pagination input, .table-pagination select')].every(n=>n.getBoundingClientRect().right<=innerWidth)}})()`);
  assert(layout.pageWidth<=width&&!layout.listScroll&&!layout.tableScroll&&layout.lastBottom<=layout.boxBottom&&layout.padding==='0px'&&layout.controls,JSON.stringify({theme,...layout}));
  assert.equal(layout.weight,'500');
  await js(`document.querySelector('[data-list] .table-pagination__size').scrollIntoView({block:'center'})`);
  await click('[data-list] [data-page-size] [data-select-trigger]');
  const menu=await js(`(()=>{const m=document.querySelector('[data-list] [data-select-list]'),r=m.getBoundingClientRect(),t=document.querySelector('[data-list] [data-select-trigger]'),b=document.querySelector('[data-list] [data-page=first]');return {top:r.top,bottom:r.bottom,left:r.left,right:r.right,height:innerHeight,width:innerWidth,font:getComputedStyle(t).fontSize,buttonFont:getComputedStyle(b).fontSize,triggerHeight:t.getBoundingClientRect().height,buttonHeight:b.getBoundingClientRect().height,background:getComputedStyle(m).backgroundColor}})()`);
  assert(menu.top>=0&&menu.bottom<=menu.height&&menu.left>=0&&menu.right<=menu.width&&menu.font===menu.buttonFont&&menu.triggerHeight===menu.buttonHeight,JSON.stringify(menu));
  const shot=await call('Page.captureScreenshot',{format:'png'});await writeFile(join(output,`tables-${width}-${theme}.png`),Buffer.from(shot.data,'base64'));
  await click('[data-list] [data-page-size] [data-select-trigger]');
 }
 assert.deepEqual(exceptions,[]);
 console.log(`PASS: local/API/markup pagination, last/previous/next/jump/page-size, cached pages and run details, retry, row handlers, 8 responsive layouts. ${output}`);
}finally{socket?.close();browser.kill();server.close()}
