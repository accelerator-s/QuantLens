// Run against the active local service and its published backtests; no API fixtures.
import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {mkdtemp, writeFile, access} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {setTimeout as delay} from 'node:timers/promises';

const base=process.env.QUANTLENS_TEST_URL || 'http://127.0.0.1:8610';
const output=await mkdtemp(join(tmpdir(),'quantlens-references-live-'));
const runs=(await(await fetch(base+'/api/backtest/runs')).json()).items;
let annotated;
for(const run of runs){try{await access(join('runs/market_references',run.run_id+'.json'));annotated=run;break}catch{}}
assert(annotated,'Expected a backtest with a separately configured display reference');
const browser=spawn(process.env.QUANTLENS_CHROME_BIN||'google-chrome',['--headless=new','--no-sandbox','--disable-gpu','--remote-debugging-port=0',`--user-data-dir=${join(output,'browser')}`,'about:blank'],{stdio:['ignore','ignore','pipe']});
let socket;
try{
 const endpoint=await new Promise((resolve,reject)=>{let log='';const timer=setTimeout(()=>reject(Error(log)),10000);browser.stderr.on('data',chunk=>{log+=chunk;const match=log.match(/DevTools listening on (ws:\/\/\S+)/);if(match){clearTimeout(timer);resolve(match[1])}});browser.once('error',reject)});
 const target=await(await fetch(`http://127.0.0.1:${new URL(endpoint).port}/json/new?about:blank`,{method:'PUT'})).json();
 socket=new WebSocket(target.webSocketDebuggerUrl);await new Promise(resolve=>socket.onopen=resolve);
 let serial=0;const pending=new Map(),errors=[],requests=[];
 socket.onmessage=event=>{const message=JSON.parse(event.data);if(message.method==='Runtime.exceptionThrown')errors.push(message.params);if(message.method==='Network.requestWillBeSent')requests.push(message.params.request.url);if(message.id){const promise=pending.get(message.id);pending.delete(message.id);message.error?promise.reject(message.error):promise.resolve(message.result)}};
 const call=(method,params={})=>new Promise((resolve,reject)=>{const id=++serial;pending.set(id,{resolve,reject});socket.send(JSON.stringify({id,method,params}))});
 async function js(expression){const result=await call('Runtime.evaluate',{expression,awaitPromise:true,returnByValue:true});assert(!result.exceptionDetails,JSON.stringify(result.exceptionDetails));return result.result.value}
 async function wait(expression){for(let i=0;i<200;i++){if(await js(expression))return;await delay(50)}throw Error(expression+'\n'+await js('document.body.innerText'))}
 const card='[data-view=backtest] .backtest__comparison';
 const lines=`document.querySelectorAll('${card} .chart__line').length`;
 await call('Page.enable');await call('Runtime.enable');await call('Network.enable');
 await call('Emulation.setDeviceMetricsOverride',{width:1440,height:1000,deviceScaleFactor:1,mobile:false});
 await call('Page.navigate',{url:base+'/#backtest'});
 await wait(`!!document.querySelector('${card} [data-curve=market]:enabled')&&${lines}===3`);
 // Visit the actual annotated record through the real table pagination.
 const row=`[data-view=backtest] [data-run-id="${annotated.run_id}"]`;
 for(let page=0;page<Math.ceil(runs.length/10)&&!await js(`!!document.querySelector('${row}')`);page++){
  await js("document.querySelector('[data-view=backtest] [data-list] [data-page=next]').click()");
  await delay(60);
 }
 await wait(`!!document.querySelector('${row}')`);
 await js(`document.querySelector('${row}').click()`);
 await wait(`document.querySelector('[data-view=backtest] tr.is-active')?.dataset.runId===${JSON.stringify(annotated.run_id)}&&!!document.querySelector('${card} [data-curve=market]:enabled')&&${lines}===3`);
 const reference=await(await fetch(`${base}/api/backtest/runs/${annotated.run_id}/references`)).json();
 assert.equal(reference.status,'available');assert.equal(reference.selection_timing,'after_backtest_display');
 const text=await js(`document.querySelector('${card} .backtest__index-note').textContent`);
 assert(text.includes(`指数收益 ${(reference.total_return*100).toFixed(2)}%`),text);
 assert(text.includes(`${(reference.excess_return*100).toFixed(2)} 个百分点`),text);
 assert(!await js(`document.querySelector('${card}').textContent.includes('暂不可用')`));
 await js(`document.querySelector('${card} [data-curve=market]').click()`);assert.equal(await js(lines),2);
 await js(`document.querySelector('${card} [data-curve=market]').click()`);assert.equal(await js(lines),3);
 for(const width of [1440,390]){
  await call('Emulation.setDeviceMetricsOverride',{width,height:1000,deviceScaleFactor:1,mobile:false});
  await js(`document.querySelector('${card}').scrollIntoView({block:'start'})`);await delay(100);
  const screenshot=await call('Page.captureScreenshot',{format:'png'});
  await writeFile(join(output,`reference-${width}.png`),Buffer.from(screenshot.data,'base64'));
 }
 assert(requests.some(url=>url.endsWith(`/api/backtest/runs/${annotated.run_id}/references`)));
 assert(!requests.some(url=>url.includes('/market-index')),'The page called the removed endpoint');
 assert.deepEqual(errors,[]);
 await writeFile(join(output,'report.json'),JSON.stringify({run_id:annotated.run_id,reference_code:reference.code,note:text,reference_requests:requests.filter(url=>url.endsWith('/references')),runtime_errors:errors},null,2));
 console.log(`PASS: real page, real historical backtest, CSMAR reference, 3 curves, exact stats, toggles, desktop/mobile, no removed endpoint requests. ${output}`);
}finally{socket?.close();browser.kill()}
