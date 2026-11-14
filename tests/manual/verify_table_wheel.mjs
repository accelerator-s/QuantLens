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
const output=await mkdtemp(join(tmpdir(),'quantlens-table-wheel-'));
const index=await readFile(join(root,'index.html'),'utf8');
const styles=[...index.matchAll(/<link rel="stylesheet" href="([^"]+)"/g)].map(m=>`<link rel="stylesheet" href="${m[1]}">`).join('');
const html=`<!doctype html><html lang="zh-CN" data-theme="light"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">${styles}
<style>body{overflow:auto}main{padding:16px}section{margin-bottom:24px;min-width:0}.tail{height:1600px}#nested{height:240px;overflow:auto}#nested .tail{height:700px}</style></head><body><main>
<section class="experiments" id="paged"></section><section class="experiments" id="small"></section>
<section class="backtest"><div class="split"><div class="item-list backtest__runs" id="backtest"></div></div></section>
<section class="experiments" id="remote"></section><section class="ql-page" id="markup"></section>
<section class="ql-page" id="dashboard"><div class="ql-dashboard-scroll"></div></section>
<section class="experiments" id="wide"></section><section class="experiments" id="bounded"></section>
<section class="experiments" id="scrollable"></section><section id="nested"></section><div class="tail"></div>
</main><script type="module">
import {tableEl,remoteTableEl} from '/core/dom.js';import {initMarkupTablePagination} from '/core/pagination.js';
const columns=[{key:'id',label:'编号'},{key:'name',label:'记录'}];const rows=Array.from({length:40},(_,id)=>({id,name:'研究记录 '+id}));
for(const [id,count] of [['paged',40],['small',3],['backtest',40],['wide',3],['bounded',3],['scrollable',40],['nested',3]]){
 document.querySelector('#'+id).append(tableEl(columns,rows.slice(0,count),{pagination:['paged','backtest'].includes(id)}));
}
document.querySelector('#wide table').style.minWidth='1800px';
for(const id of ['bounded','scrollable'])Object.assign(document.querySelector('#'+id+' .table-wrap').style,{height:'150px',maxHeight:'150px'});
document.querySelector('#nested').insertAdjacentHTML('beforeend','<div class="tail"></div>');
const remote=remoteTableEl(columns,async({offset,limit})=>({items:rows.slice(offset,offset+limit),total_rows:rows.length}));document.querySelector('#remote').append(remote);await remote.ready;
const markup=count=>'<div class="ql-table-wrap"><table class="ql-table"><thead><tr><th>编号</th></tr></thead><tbody>'+rows.slice(0,count).map(row=>'<tr><td>'+row.id+'</td></tr>').join('')+'</tbody></table></div>';
document.querySelector('#markup').innerHTML=markup(40);initMarkupTablePagination(document.querySelector('#markup'));
document.querySelector('#dashboard .ql-dashboard-scroll').innerHTML=markup(3);window.ready=true;
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
 await wait('window.ready');
 async function wheel(selector,deltaX=0,deltaY=160){
  await js(`document.querySelector(${JSON.stringify(selector)}).scrollIntoView({block:'center',inline:'nearest'});await new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)))`);
  const before=await js(`(()=>{const n=document.querySelector(${JSON.stringify(selector)}),r=n.getBoundingClientRect();return {page:scrollY,own:n.scrollTop,x:n.scrollLeft,parent:document.querySelector('#nested').scrollTop,mouseX:Math.min(innerWidth-20,Math.max(20,r.left+Math.min(r.width/2,180))),mouseY:Math.min(innerHeight-20,Math.max(20,r.top+Math.min(r.height/2,120)))}})()`);
  await call('Input.dispatchMouseEvent',{type:'mouseMoved',x:before.mouseX,y:before.mouseY});
  await call('Input.dispatchMouseEvent',{type:'mouseWheel',x:before.mouseX,y:before.mouseY,deltaX,deltaY});
  await delay(220);
  const after=await js(`({page:scrollY,own:document.querySelector(${JSON.stringify(selector)}).scrollTop,x:document.querySelector(${JSON.stringify(selector)}).scrollLeft,parent:document.querySelector('#nested').scrollTop})`);
  return {before,after};
 }
 const passThrough=['#paged .table-wrap','#small .table-wrap','#backtest .table-wrap','#remote .table-wrap','#markup .ql-table-wrap','#dashboard .ql-dashboard-scroll','#wide .table-wrap','#bounded .table-wrap'];
 for(const theme of ['light','dark'])for(const width of [1440,390]){
  await call('Emulation.setDeviceMetricsOverride',{width,height:800,deviceScaleFactor:1,mobile:false});
  await js(`document.documentElement.dataset.theme='${theme}'`);
  for(const selector of passThrough){
   const result=await wheel(selector);
   assert(result.after.page>result.before.page+40&&result.after.own===result.before.own,JSON.stringify({theme,width,selector,...result}));
  }
  await js(`document.querySelector('#scrollable .table-wrap').scrollTop=0`);
  const internal=await wheel('#scrollable .table-wrap');
  assert(internal.after.own>internal.before.own+40&&internal.after.page===internal.before.page,JSON.stringify(internal));
  await js(`document.querySelector('#wide .table-wrap').scrollLeft=0`);
  const horizontal=await wheel('#wide .table-wrap',160,0);
  assert(horizontal.after.x>horizontal.before.x+40&&horizontal.after.page===horizontal.before.page,JSON.stringify(horizontal));
  await js(`document.querySelector('#nested').scrollTop=0`);
  const nested=await wheel('#nested .table-wrap');
  assert(nested.after.parent>nested.before.parent+40&&nested.after.page===nested.before.page,JSON.stringify(nested));
 }
 assert.deepEqual(exceptions,[]);
 console.log(`PASS: real wheel input scrolls the page over paged/static/short/horizontal tables; vertical tables, horizontal gestures and nested scrollers retain native scrolling, 4 viewport/theme layouts. ${output}`);
}finally{socket?.close();browser.kill();server.close()}
