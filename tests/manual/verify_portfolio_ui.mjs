// Run against an isolated temporary workspace; no existing account data is touched.
import assert from 'node:assert/strict';
import {cp, mkdtemp, readFile, mkdir, writeFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {spawn} from 'node:child_process';
import {setTimeout as delay} from 'node:timers/promises';

const project=fileURLToPath(new URL('../../',import.meta.url));
const temporary=await mkdtemp(join(tmpdir(),'quantlens-portfolio-ui-'));
const output=process.env.QUANTLENS_PORTFOLIO_OUTPUT || '/tmp/quantlens-portfolio-ui';
await mkdir(output,{recursive:true});
await cp(join(project,'config'),join(temporary,'config'),{recursive:true});
const index=await readFile(join(project,'src/webui/frontend/index.html'),'utf8');
const styles=[...index.matchAll(/<link rel="stylesheet" href="([^"]+)"/g)].map(m=>`<link rel="stylesheet" href="${m[1]}">`).join('');
await writeFile(join(temporary,'fixture.html'),`<!doctype html><html lang="zh-CN" data-theme="light"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">${styles}<style>body{padding:16px}main{max-width:1640px;margin:auto}</style></head><body><main class="portfolio"></main><script type="module">
import {mount} from '/components/portfolio/portfolio.js';
import {api} from '/core/api.js';
window.events=[];window.ctx={accountId:'manual',bus:{on(){},emit(...args){events.push(args)}},api:{...api,
 strategies:async()=>({items:[{profile_name:'fixture',display_name:'验收策略',status:'active',market:'CN-A'}]}),
 recommendGenerate:async payload=>({plan:{strategy_profile_ref:payload.profile,generated_at:new Date().toISOString(),target_holdings:[{instrument_id:'000001',qty:'100',weight:'.25'}],suggested_trades:[{instrument_id:'000001',action:'open'}],caveats:[]},ai_ok:true})}};
const root=document.querySelector('main');root.innerHTML=await(await fetch('/components/portfolio/portfolio.html')).text();await mount(root,ctx);window.ready=true;
</script></body></html>`);
const python=process.env.QUANTLENS_PYTHON || 'python3';
const serverCode=`import os,sys,socket
from pathlib import Path
import json
sys.path.insert(0,${JSON.stringify(join(project,'src'))})
os.environ['QUANTLENS_HOME']=${JSON.stringify(temporary)}
os.environ['QUANTLENS_RECOVERY']='1'
from quantlens.bootstrap import prepare
prepare()
import pandas as pd
directory=Path(${JSON.stringify(temporary)})/'data/ads'
directory.mkdir(parents=True,exist_ok=True)
pd.DataFrame({'code':['000001','000002','000003','600000'],'name':['平安银行','万科A','测试股票','浦发银行']}).to_parquet(directory/'stocks.parquet',index=False)
(directory.parent/'ads_manifest.json').write_text(json.dumps({'tables':{'stock_prices':{'files':[{'path':'stocks.parquet'}]}}}))
from datetime import datetime,UTC
from decimal import Decimal
from portfolio import Account,AccountRegistry,Cash
AccountRegistry().create(Account(account_id='manual',alias='验收账户',market='CN-A',instrument_class='stock',reporting_currency='CNY',created_at=datetime.now(UTC),cash=[Cash(currency='CNY',total=Decimal('100000'),available=Decimal('100000'),withdrawable=Decimal('100000'))]))
from webui.backend.server import create_app
from starlette.routing import Route
from starlette.responses import HTMLResponse
app=create_app()
async def fixture(request):return HTMLResponse(Path(${JSON.stringify(join(temporary,'fixture.html'))}).read_text())
app.router.routes.insert(0,Route('/__portfolio_test',fixture))
sock=socket.socket();sock.bind(('127.0.0.1',0));sock.listen(128)
print('PORT='+str(sock.getsockname()[1]),flush=True)
import uvicorn
uvicorn.Server(uvicorn.Config(app,log_level='error')).run(sockets=[sock])`;
const server=spawn(python,['-W','ignore','-c',serverCode],{stdio:['ignore','pipe','pipe']});
let serverLog='';server.stderr.on('data',chunk=>serverLog+=chunk);
let browser,socket;
try{
 const port=await new Promise((resolve,reject)=>{const timer=setTimeout(()=>reject(Error(serverLog)),15000);let log='';server.stdout.on('data',chunk=>{log+=chunk;const match=log.match(/PORT=(\d+)/);if(match){clearTimeout(timer);resolve(match[1])}});server.once('exit',code=>reject(Error(`Fixture exited ${code}: ${serverLog}`)))});
 const base=`http://127.0.0.1:${port}`;
 for(let i=0;i<100;i++){try{if((await fetch(base+'/api/accounts')).ok)break}catch{}await delay(50)}
 browser=spawn(process.env.QUANTLENS_CHROME_BIN || 'google-chrome',['--headless=new','--no-sandbox','--disable-gpu','--remote-debugging-port=0',`--user-data-dir=${join(temporary,'browser')}`,'about:blank'],{stdio:['ignore','ignore','pipe']});
 const endpoint=await new Promise((resolve,reject)=>{let log='';const timer=setTimeout(()=>reject(Error(log)),10000);browser.stderr.on('data',chunk=>{log+=chunk;const match=log.match(/DevTools listening on (ws:\/\/\S+)/);if(match){clearTimeout(timer);resolve(match[1])}});browser.once('error',reject)});
 const debugPort=new URL(endpoint).port;
 const target=await(await fetch(`http://127.0.0.1:${debugPort}/json/new?about:blank`,{method:'PUT'})).json();
 socket=new WebSocket(target.webSocketDebuggerUrl);await new Promise((resolve,reject)=>{socket.onopen=resolve;socket.onerror=reject});
 let serial=0;const pending=new Map(),exceptions=[];
 socket.onmessage=event=>{const result=JSON.parse(event.data);if(result.method==='Runtime.exceptionThrown')exceptions.push(result.params);if(result.id){const item=pending.get(result.id);pending.delete(result.id);result.error?item.reject(Error(JSON.stringify(result.error))):item.resolve(result.result)}};
 const call=(method,params={})=>new Promise((resolve,reject)=>{const id=++serial;pending.set(id,{resolve,reject});socket.send(JSON.stringify({id,method,params}))});
 async function js(expression){const result=await call('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true,replMode:true});assert(!result.exceptionDetails,JSON.stringify(result.exceptionDetails));return result.result.value}
 async function wait(expression){for(let i=0;i<160;i++){try{if(await js(expression))return}catch{}await delay(30)}throw Error('Timed out: '+expression+'\n'+await js('document.body.innerText'))}
 async function click(selector){await js(`document.querySelector(${JSON.stringify(selector)}).click()`)}
 async function fill(values){await js(`for(const [name,value] of Object.entries(${JSON.stringify(values)})){const input=document.querySelector('.dialog [name="'+name+'"]');input.value=value;input.dispatchEvent(new Event('input',{bubbles:true}));}`)}
 async function formPreview(title){await click('.dialog__actions .btn--primary');await wait(`document.querySelector('.dialog__title')?.textContent===${JSON.stringify(title)}`)}
 async function confirm(){await click('.dialog__actions .btn--primary, .dialog__actions .btn--danger');await wait("!document.querySelector('.dialog') && !document.querySelector('[data-holding-add]').disabled")}
 async function balance(){const data=await(await fetch(base+'/api/accounts')).json();return Number(data.items.find(a=>a.account_id==='manual').cash[0].total)}
 async function positions(){return (await(await fetch(base+'/api/portfolio/manual/holdings')).json()).items}
 async function screenshot(name){const result=await call('Page.captureScreenshot',{format:'png'});await writeFile(join(output,name+'.png'),Buffer.from(result.data,'base64'))}
 await call('Page.enable');await call('Runtime.enable');await call('Page.navigate',{url:base+'/__portfolio_test'});await wait('window.ready');
 assert(await js("!document.querySelector('[role=tab], [data-tab]') && !document.body.innerText.includes('对账')"));
 await click('[data-holding-add]');await wait("document.querySelector('.dialog input[name=qty]')");
 assert(await js("!document.querySelector('.dialog .select, .dialog [name=side], .dialog [name=currency]')"));
 assert(await js("!document.querySelector('.dialog .confirm__message')"));
 await js("window.stockQueries=[];window.originalFetch=window.fetch;window.fetch=(url,options)=>{if(String(url).includes('/stocks/suggestions'))stockQueries.push(new URL(url,location.origin).searchParams.get('q'));return originalFetch(url,options)};window.stockInput=document.querySelector('.dialog input[name=instrument_id]');stockInput.focus();stockInput.value=''");
 for(const digit of '000001')await js(`stockInput.value+=${JSON.stringify(digit)};stockInput.dispatchEvent(new Event('input',{bubbles:true}))`);
 await wait("document.querySelector('.stock-picker__option[data-code=\"000001\"]') && document.querySelector('.stock-picker__name')?.textContent==='平安银行'");
 assert.deepEqual(await js('stockQueries.slice(-6)'),['0','00','000','0000','00000','000001']);
 await call('Emulation.setDeviceMetricsOverride',{width:390,height:568,deviceScaleFactor:1,mobile:false});
 await js("stockInput.value='0';stockInput.dispatchEvent(new Event('input',{bubbles:true}));window.dispatchEvent(new Event('resize'))");
 await wait("document.querySelectorAll('.stock-picker__option').length===3");
 for(const theme of ['light','dark']){
   await js(`document.documentElement.dataset.theme='${theme}';await new Promise(r=>requestAnimationFrame(r))`);
   const bounds=await js("(()=>{const r=document.querySelector('.stock-picker__menu').getBoundingClientRect();return {left:r.left,right:r.right,top:r.top,bottom:r.bottom}})()");
   assert(bounds.left>=0&&bounds.right<=390&&bounds.top>=0&&bounds.bottom<=568,JSON.stringify(bounds));
   await screenshot(`stock-candidates-390-${theme}`);
 }
 await js("stockInput.dispatchEvent(new KeyboardEvent('keydown',{key:'ArrowDown',bubbles:true,cancelable:true}));stockInput.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',bubbles:true,cancelable:true}))");
 assert.equal(await js('stockInput.value'),'000002');
 await js("stockInput.value='平安';stockInput.dispatchEvent(new Event('input',{bubbles:true}))");
 await wait("document.querySelector('.stock-picker__option[data-code=\"000001\"]') && document.querySelectorAll('.stock-picker__option').length===1");
 await click('.stock-picker__option[data-code="000001"]');
 assert.equal(await js('stockInput.value'),'000001');
 assert(await js("document.querySelector('.stock-picker__menu').hidden"));
 await js("window.staleInput=document.createElement('input');document.body.append(staleInput);window.stockResolvers=new Map();window.stalePicker=(await import('/core/stock-picker.js')).attachStockPicker(staleInput,{search:q=>new Promise(resolve=>stockResolvers.set(q,resolve))});staleInput.focus();staleInput.value='0';staleInput.dispatchEvent(new Event('input'));staleInput.value='00';staleInput.dispatchEvent(new Event('input'));stockResolvers.get('00')({items:[{code:'000002',name:'最新候选'}]})");
 await wait("document.querySelector('#'+staleInput.getAttribute('aria-controls')+' [data-code=\"000002\"]')");
 await js("stockResolvers.get('0')({items:[{code:'000001',name:'过时结果'}]});await new Promise(r=>requestAnimationFrame(r))");
 assert(await js("document.querySelector('#'+staleInput.getAttribute('aria-controls')).innerText.includes('最新候选') && !document.querySelector('#'+staleInput.getAttribute('aria-controls')).innerText.includes('过时结果')"));
 await js("stalePicker.destroy();staleInput.remove()");
 await fill({instrument_id:'000001',qty:'1000',cost_price:'8'});await formPreview('保存持仓');await confirm();
 assert.equal(await balance(),100000);assert.equal((await positions())[0].qty,'1000');
 await click('[data-trade-add]');await wait("document.querySelector('.dialog input[name=price]')");
 assert.equal(await js("document.querySelectorAll('.dialog input[type=radio]').length"),2);
 assert(await js("!document.querySelector('.dialog .select, .dialog [name=side], .dialog [name=currency]')"));
 assert(await js("!document.querySelector('.dialog [name=fee], .dialog [name=tax], .dialog .confirm__message')"));
 const report=[];
 for(const theme of ['light','dark'])for(const width of [1440,768,390,320]){
   await call('Emulation.setDeviceMetricsOverride',{width,height:568,deviceScaleFactor:1,mobile:false});
   await js(`document.documentElement.dataset.theme='${theme}';window.dispatchEvent(new Event('resize'));await new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)))`);
   const layout=await js(`(()=>{const d=document.querySelector('.dialog'),b=d.querySelector('.dialog__body'),a=d.querySelector('.dialog__actions');const r=d.getBoundingClientRect(),br=b.getBoundingClientRect(),ar=a.getBoundingClientRect();const children=[...b.children].map(e=>e.getBoundingClientRect().right);return {width:innerWidth,theme:document.documentElement.dataset.theme,left:r.left,right:r.right,top:r.top,bottom:r.bottom,padding:parseFloat(getComputedStyle(b).paddingRight),scrolls:b.scrollHeight>b.clientHeight,bodyBottom:br.bottom,actionsTop:ar.top,contentGap:br.right-Math.max(...children),horizontal:b.scrollWidth>b.clientWidth}})()`);
   assert(layout.left>=0&&layout.right<=width&&layout.top>=0&&layout.bottom<=568,JSON.stringify(layout));
   assert(layout.padding>=12&&layout.contentGap>=12&&!layout.horizontal&&layout.bodyBottom<=layout.actionsTop,JSON.stringify(layout));
   report.push(layout);
   if([1440,390].includes(width))await screenshot(`trade-dialog-${width}-${theme}`);
 }
 await fill({instrument_id:'000001',qty:'100',price:'10'});await formPreview('录入成交');
 assert(await js("document.querySelector('.dialog__body').innerText.includes('手续费') && document.querySelector('.dialog__body').innerText.includes('印花税')"));
 await confirm();
 assert.equal(await balance(),98994.99);let held=(await positions())[0];assert.equal(held.qty,'1100');assert.equal(held.frozen_qty,'100');assert.equal(held.available_qty,'1000');
 await wait("!document.querySelector('[data-trade-undo]').disabled");await click('[data-trade-undo]');await wait("document.querySelector('.dialog__title')?.textContent==='撤回上一笔交易'");await confirm();
 assert.equal(await balance(),100000);held=(await positions())[0];assert.equal(held.qty,'1000');assert.equal(held.frozen_qty,'0');
 assert(await js("document.querySelector('[data-trades]').innerText.includes('已撤回')"));
 await click('[data-trade-add]');await wait("document.querySelector('.dialog input[name=price]')");
 await click('.dialog input[value=sell]');await fill({instrument_id:'000001',qty:'100',price:'12'});await formPreview('录入成交');await confirm();
 assert.equal(await balance(),101194.39);assert.equal((await positions())[0].qty,'900');
 await click('[data-trade-undo]');await wait("document.querySelector('.dialog__title')?.textContent==='撤回上一笔交易'");await confirm();
 assert.equal(await balance(),100000);assert.equal((await positions())[0].qty,'1000');
 const history=await(await fetch(base+'/api/portfolio/manual/trades')).json();assert.equal(history.total_rows,2);assert(history.items.every(item=>item.is_reversed));
 assert.equal(Number(history.items[0].fee),5.01);assert.equal(Number(history.items[0].tax),0.6);
 assert.equal(Number(history.items[1].fee),5.01);assert.equal(Number(history.items[1].tax),0);
 assert(await js("document.querySelector('[data-trade-undo]').disabled"));
 await click('[data-recommend-generate]');await wait("document.querySelector('.portfolio__suggestion')");
 for(const width of [1440,390,320]){
   await call('Emulation.setDeviceMetricsOverride',{width,height:1050,deviceScaleFactor:1,mobile:false});
   await js("document.documentElement.dataset.theme='light';await new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)))");
   assert(await js('document.documentElement.scrollWidth<=innerWidth'));
   await screenshot(`portfolio-${width}`);
 }
 assert.deepEqual(exceptions,[]);
 await writeFile(join(output,'verification.json'),JSON.stringify(report,null,2));
 console.log(`PASS: stock candidates on every keystroke, real backend holdings, automatic fees/tax, T+1, buy/sell reversal, retained history, suggestions, and ${report.length} dialog layouts. ${output}`);
}finally{socket?.close();browser?.kill();server.kill();}
