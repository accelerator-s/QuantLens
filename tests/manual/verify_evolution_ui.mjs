// Run with Node.js; set QUANTLENS_CHROME if Chrome is installed elsewhere.
import assert from 'node:assert/strict';
import {fileURLToPath} from 'node:url';
import {createServer} from 'node:http';
import {readFile, writeFile} from 'node:fs/promises';
import {spawn} from 'node:child_process';
import {setTimeout as delay} from 'node:timers/promises';

const root = fileURLToPath(new URL('../../src/webui/frontend', import.meta.url));
const html = `<!doctype html><html lang="zh-CN" data-theme="light"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
${['core/theme.css','core/base.css','core/page-components.css','core/page-contracts.css','components/experiments/experiments.css','core/content-layout.css','core/appearance.css'].map(p=>`<link rel="stylesheet" href="/${p}">`).join('')}
<style>body{padding:16px}main{max-width:1640px;margin:auto}</style></head><body><main></main><script type="module">
import {mount} from '/components/experiments/experiments.js';
const variant=new URLSearchParams(location.search).get('variant')||'running';
const state=['legacy','empty','durable'].includes(variant)?'running':variant;
const status={research_id:'EVO-test',state,decision:'continue',iteration:0,successful_rounds:0,failed_rounds:0,progress:.025,phase:'基线：wf001 (1/3) 验证回测',started_at:'2026-11-13T13:26:15Z',elapsed_seconds:120};
const experiment={exp_id:'EXP-baseline',subject_type:'factor',subject_name:'probe',subject_display_name:'60日温和上涨效率',profile_name:'research_probe',status:'registered',created_at:'2026-11-13T13:26:16Z',change_summary:'基础版本'};
const task={status:state==='paused'?'interrupted':state,log:Array.from({length:230},(_,i)=>'日志 '+i+'：读取行情，执行训练与验证回测'),progress:{state},name:'factor-evolution:EVO-test'};
const detail={status,config:{base_factor:'probe',profile_name:'research_probe'},lifecycle_state:state,events:[{event_type:'session_created',created_at:'2026-11-13T13:26:15Z',payload:{}}],task,experiment_ids:variant==='empty'?[]:['EXP-baseline']};
if(variant==='legacy'){delete detail.task;delete detail.experiment_ids;}
if(variant==='durable'){detail.task=null;detail.events.push({event_type:'runtime_log',created_at:'2026-11-13T13:26:20Z',payload:{message:'持久化日志恢复成功'}});}
window.errors=[];
const api={factorEvolutions:async()=>({items:[{...status,base_factor:'probe',base_factor_display_name:'60日温和上涨效率',model_name:'deepseek-cyber'}]}),factorEvolution:async()=>detail,jobs:async()=>({items:[task]}),experiments:async(query)=>({items:variant==='empty'||query?.improved?[]:[experiment]}),experiment:async()=>({experiment,events:[]}),experimentCompare:async()=>({comparison:{}})};
const root=document.querySelector('main');root.innerHTML=await(await fetch('/components/experiments/experiments.html')).text();
await mount(root,{bus:{on(){},emit(type,payload){if(type==='toast')window.errors.push(payload)}},api});
window.ready=true;
</script></body></html>`;
const server=createServer(async(req,res)=>{
  try{
    const url=new URL(req.url,'http://localhost');
    if(url.pathname==='/'){res.setHeader('Content-Type','text/html; charset=utf-8');res.end(html);return;}
    const file=await readFile(root+url.pathname);
    res.setHeader('Content-Type',url.pathname.endsWith('.css')?'text/css':url.pathname.endsWith('.js')?'text/javascript':'text/html; charset=utf-8');res.end(file);
  }catch{res.writeHead(404);res.end();}
});
await new Promise((resolve,reject)=>{server.once('error',reject);server.listen(0,'127.0.0.1',resolve)});
const base=`http://127.0.0.1:${server.address().port}`;
const chrome=spawn(process.env.QUANTLENS_CHROME || '/opt/google/chrome/chrome',['--headless=new','--no-sandbox','--disable-gpu','--remote-debugging-port=0','--user-data-dir=/tmp/quantlens-evolution-layout-browser','about:blank'],{stdio:['ignore','ignore','pipe']});
let socket;
try{
  let stderr='';
  const endpoint=await new Promise((resolve,reject)=>{
    const timeout=setTimeout(()=>reject(Error('Browser startup timed out: '+stderr)),10000);
    chrome.stderr.on('data',chunk=>{stderr+=chunk;const m=stderr.match(/DevTools listening on (ws:\/\/[^\s]+)/);if(m){clearTimeout(timeout);resolve(m[1]);}});
    chrome.once('error',reject);chrome.once('exit',code=>{clearTimeout(timeout);reject(Error('Browser exited '+code+': '+stderr));});
  });
  const port=new URL(endpoint).port;
  const target=await(await fetch(`http://127.0.0.1:${port}/json/new?about:blank`,{method:'PUT'})).json();
  socket=new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((resolve,reject)=>{socket.onopen=resolve;socket.onerror=reject});
  let sequence=0;const pending=new Map();
  socket.onmessage=event=>{const r=JSON.parse(event.data);if(r.id){const p=pending.get(r.id);pending.delete(r.id);r.error?p.reject(Error(JSON.stringify(r.error))):p.resolve(r.result);}};
  const call=(method,params={})=>new Promise((resolve,reject)=>{const id=++sequence;pending.set(id,{resolve,reject});socket.send(JSON.stringify({id,method,params}));});
  const evaluate=async expression=>{const r=await call('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true,replMode:true});assert(!r.exceptionDetails,JSON.stringify(r.exceptionDetails));return r.result.value;};
  async function waitFor(expression){for(let n=0;n<100;n++){try{if(await evaluate(expression))return}catch{}await delay(30)}assert.fail(expression)}
  await call('Page.enable');
  const report=[];
  for(const variant of ['running','legacy','failed','paused','completed','empty','durable']){
    await call('Page.navigate',{url:base+'?variant='+variant});
    let ready=false;for(let n=0;n<100;n++){try{ready=await evaluate('window.ready===true && document.querySelector("[data-task-log]")')}catch{}if(ready)break;await delay(50)}assert(ready,'Failed to mount '+variant);
    for(const theme of ['light','dark'])for(const width of [1440,900,390]){
      await call('Emulation.setDeviceMetricsOverride',{width,height:900,deviceScaleFactor:1,mobile:false});
      await evaluate(`document.documentElement.dataset.theme='${theme}';await new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)))`);
      const result=await evaluate(`(()=>{
        const table=document.querySelector('.evolution-sessions'), log=document.querySelector('[data-task-log]');
        const row=table.querySelector('tbody tr'),cellStyle=getComputedStyle(row.cells[row.cells.length-1]);
        return {width:innerWidth,theme:document.documentElement.dataset.theme,headers:[...table.querySelectorAll('th')].map(e=>e.textContent),
          whitespace:[...row.cells].map(e=>getComputedStyle(e).whiteSpace),rowHeight:row.getBoundingClientRect().height,
          twoLineRowHeight:2*parseFloat(cellStyle.lineHeight)+parseFloat(cellStyle.paddingTop)+parseFloat(cellStyle.paddingBottom)+2,
          badge:row.cells[3].firstElementChild.className,logOpen:log.open,logScrollable:log.querySelector('pre').scrollHeight>log.querySelector('pre').clientHeight,
          baseline:document.querySelector('[data-exp-id="EXP-baseline"]')?.textContent,bodyOverflow:document.documentElement.scrollWidth>innerWidth,
          text:document.querySelector('[data-list]').textContent,errors:window.errors};
      })()`);
      assert.deepEqual(result.headers,['基础因子','模型','启动时间','状态','研究进展']);
      assert(result.whitespace.slice(0,4).every(v=>v==='nowrap'),JSON.stringify(result));
      assert(result.rowHeight<=result.twoLineRowHeight,JSON.stringify(result));assert(!result.bodyOverflow,JSON.stringify(result));
      assert(result.badge.includes(variant==='failed'?'danger':variant==='completed'?'muted':'accent'),JSON.stringify(result));
      assert(result.logOpen,JSON.stringify(result));assert.deepEqual(result.errors,[]);
      if(variant==='empty'){assert(result.text.includes('研究进行中'));assert(!result.text.includes('失败或已中止'));}
      else assert(result.baseline,JSON.stringify(result));
      if(variant==='durable')assert(await evaluate('document.querySelector("[data-task-log]").textContent.includes("持久化日志恢复成功")'));
      report.push({variant,...result});
      if(variant==='running'&&theme==='light'&&[1440,390].includes(width)){
        const shot=await call('Page.captureScreenshot',{format:'png'});
        await writeFile(`/tmp/quantlens-evolution-${width}.png`,Buffer.from(shot.data,'base64'));
      }
    }
    if(variant==='running'){
      await evaluate('document.querySelector("[data-task-log] pre").scrollTop=120;document.querySelector("[data-task-log]").open=false');
      await delay(2300);
      assert.equal(await evaluate('document.querySelector("[data-task-log]").open'),false);
      assert.equal(await evaluate('document.querySelector("[data-task-log] pre").scrollTop'),120);
    }
  }
  await writeFile('/tmp/quantlens-evolution-layout-report.json',JSON.stringify(report,null,2));
  console.log(`PASS: ${report.length} layouts; restored task/durable logs, legacy baseline, empty/running/failed/paused/completed states, scroll and collapse retained.`);
}finally{socket?.close();chrome.kill();server.close();}
