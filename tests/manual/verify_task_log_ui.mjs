// Browser regression for newest-first logs, line counts and adaptive layouts.
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {spawn} from 'node:child_process';
import {readFile, writeFile, mkdtemp} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {setTimeout as delay} from 'node:timers/promises';

const root=fileURLToPath(new URL('../../src/webui/frontend/',import.meta.url));
const output=await mkdtemp(join(tmpdir(),'quantlens-task-log-'));
const index=await readFile(join(root,'index.html'),'utf8');
const styles=[...index.matchAll(/<link rel="stylesheet" href="([^"]+)"/g)].map(m=>`<link rel="stylesheet" href="${m[1]}">`).join('');
const html=`<!doctype html><html lang="zh-CN" data-theme="light"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">${styles}<style>body{overflow:auto}main{padding:16px}#fixture{max-width:1100px;min-width:0}</style></head><body><main><section class="card" id="fixture"></section></main><script type="module">
import {updateTaskLog,captureLogScroll} from '/core/task-log.js';
window.subject='task';window.current=null;window.lines=[];
window.setLines=values=>{lines=values;const restore=captureLogScroll(current);const next=updateTaskLog(current,lines,{subject});if(next){document.querySelector('#fixture').replaceChildren(next);current=next;}restore();};
setLines(['日志 1','日志 2','日志 3']);window.ready=true;
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
 const frame=()=>js('await new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)))');
 const metrics=()=>js(`(()=>{const code=current.querySelector('pre'),s=getComputedStyle(code),pad=parseFloat(s.paddingTop)+parseFloat(s.paddingBottom);return {height:code.getBoundingClientRect().height,rows:Math.round((code.clientHeight-pad)/parseFloat(s.lineHeight)),limit:parseFloat(s.maxHeight),top:code.scrollTop===0,total:code.textContent.split(String.fromCharCode(10)).length,page:document.documentElement.scrollWidth,width:innerWidth}})()`);
 assert.equal(await js('current.open'),false);
 assert.equal(await js("current.querySelector('summary').textContent"),'运行日志（展示 3 行）');
 await js('current.open=true');await frame();
 let m=await metrics();assert(m.rows===3&&m.height<100&&m.top,JSON.stringify(m));
 await js(`setLines(Array.from({length:80},(_,i)=>'日志 '+i))`);await frame();
 m=await metrics();assert(m.total===80&&m.rows>8&&m.rows<=16&&m.top,JSON.stringify(m));
 assert(await js(`current.querySelector('pre').textContent.startsWith('[时间未记录]  日志 79')`));
 assert.equal(await js("current.querySelector('summary').textContent"),'运行日志（展示 80 行）');
 const desktopRows=m.rows;
 const samples=[];
 for(const theme of ['light','dark'])for(const [width,height] of [[1440,1000],[768,900],[390,700],[320,420],[667,260]]) {
  await call('Emulation.setDeviceMetricsOverride',{width,height,deviceScaleFactor:1,mobile:false});
  await js(`document.documentElement.dataset.theme='${theme}';setLines(Array.from({length:100},(_,i)=>'日志 '+i+'：读取行情并执行回测'))`);await frame();
  m=await metrics();assert(m.page<=width&&m.rows>=(height<400?4:6)&&m.rows<=16&&m.height<=height*.45+2&&m.top,JSON.stringify({width,height,...m}));
  if(height===420)assert(m.rows<desktopRows,JSON.stringify(m));
  const shot=await call('Page.captureScreenshot',{format:'png'});await writeFile(join(output,`log-${width}-${height}-${theme}.png`),Buffer.from(shot.data,'base64'));samples.push({width,height,theme,...m});
 }
 await call('Emulation.setDeviceMetricsOverride',{width:1440,height:1000,deviceScaleFactor:1,mobile:false});
 await js(`setLines(Array.from({length:1000},(_,i)=>'日志 '+i))`);await frame();
 m=await metrics();assert(m.total===200&&m.rows<=16&&m.top,JSON.stringify(m));
 assert(await js(`current.querySelector('pre').textContent.startsWith('[时间未记录]  日志 999')&&current.querySelector('pre').textContent.endsWith('日志 800')`));
 assert.equal(await js("current.querySelector('summary').textContent"),'运行日志（200/1000 行）');
 assert.equal(await js("current.querySelector('summary').title"),'展示最近 200 行，共 1000 行');
 await js(`current.querySelector('pre').scrollTop=30;setLines([...lines,'最新日志'])`);await frame();assert((await metrics()).top);
 await js(`current.open=false;setLines([...lines,'收起时新增'])`);await frame();assert.equal(await js('current.open'),false);
 assert.equal(await js("current.querySelector('summary').textContent"),'运行日志（200/1002 行）');
 await js('current.open=true');await frame();assert((await metrics()).top);
 assert(await js("current.querySelector('pre').textContent.startsWith('[时间未记录]  收起时新增')"));
 await js(`window.multiline=Object.freeze(['旧日志', '', '报错\\r\\n  堆栈第一行\\n  堆栈第二行', '最新日志']);setLines(multiline)`);await frame();
 assert.equal(await js("current.querySelector('pre').textContent"),'[时间未记录]  最新日志\n[时间未记录]  报错\n[时间未记录]    堆栈第一行\n[时间未记录]    堆栈第二行\n[时间未记录]  旧日志');
 assert.equal(await js("current.querySelector('summary').textContent"),'运行日志（展示 5 行）');
 assert.equal(await js('multiline[0]'),'旧日志');
 await js(`setLines(['[2026-11-14 18:03:04+08:00]  报错\\n  堆栈第一行', '[2026-11-14 18:03:05+08:00]  已完成'])`);await frame();
 assert.equal(await js("current.querySelector('pre').textContent"),'[2026-11-14 18:03:05+08:00]  已完成\n[2026-11-14 18:03:04+08:00]  报错\n[2026-11-14 18:03:04+08:00]    堆栈第一行');
 await js(`setLines(['[时间未记录]  历史日志'])`);await frame();
 assert.equal(await js("current.querySelector('pre').textContent"),'[时间未记录]  历史日志');
 assert.deepEqual(await js(`(await import('/core/fmt.js')).fmtLogTime('2026-11-14T16:03:04Z')`),'2026-11-15 00:03:04+08:00');
 await js(`setLines(['短日志']);`);await frame();m=await metrics();assert(m.rows===1&&m.height<60,JSON.stringify(m));
 await call('Emulation.setDeviceMetricsOverride',{width:320,height:700,deviceScaleFactor:1,mobile:false});
 await js(`setLines(Array.from({length:6},(_,i)=>('日志 '+i+' 包含较长的计算说明。').repeat(20)))`);await frame();m=await metrics();assert(m.rows>=6&&m.rows<=16&&m.page<=320&&m.top,JSON.stringify(m));
 await js(`document.querySelector('#fixture').style.width='240px'`);await frame();await frame();m=await metrics();assert(m.page<=320&&m.rows<=16,JSON.stringify(m));
 assert.deepEqual(exceptions,[]);
 console.log(`PASS: content-sized short logs, adaptive row budgets, width wrapping, latest-follow/collapse retained, bounded history, 10 viewport/theme layouts. ${output}`);
}finally{socket?.close();browser.kill();server.close()}
