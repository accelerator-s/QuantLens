// Standalone browser checks; serves only frontend files and temporary form fixtures.
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {readFile, mkdtemp, mkdir, writeFile, rm} from 'node:fs/promises';
import {join, extname, resolve, sep} from 'node:path';
import {tmpdir} from 'node:os';
import {fileURLToPath} from 'node:url';
import {spawn} from 'node:child_process';

const root=fileURLToPath(new URL('../../src/webui/frontend/',import.meta.url));
const index=await readFile(join(root,'index.html'),'utf8');
const styles=[...index.matchAll(/<link rel="stylesheet" href="([^"]+)"/g)].map(m=>m[0]+'>').join('');
const fixture=`<!doctype html><html lang="zh-CN" data-theme="light"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">${styles}</head><body>
<main class="workspace"><section class="ql-page"><article class="ql-card">
<form class="ql-form" id="fixture"><label class="field">日期<input class="input" name="date" type="date" value="2026-11-13"></label><label>条件<input class="input"></label><button type="button">筛选</button></form>
<p class="item-row__meta">说明</p><input type="checkbox"><div class="rule-detail__head">交易规则</div><hr></article></section></main>
<script type="module">import {initDatePickers} from '/core/date-picker.js';initDatePickers();window.changes=[];document.querySelector('input[type=date]').addEventListener('change',e=>changes.push(e.target.value));window.ready=true;</script></body></html>`;
const server=createServer(async(req,res)=>{
 try{
  const url=new URL(req.url,'http://localhost');
  if(url.pathname==='/__controls'){res.setHeader('Content-Type','text/html');res.end(fixture);return;}
  const path=resolve(root,'.'+decodeURIComponent(url.pathname));
  if(!path.startsWith(root.replace(/\/$/,'')+sep))throw Error('Invalid path');
  const body=await readFile(path);
  res.setHeader('Content-Type',({'.js':'text/javascript','.css':'text/css','.svg':'image/svg+xml'})[extname(path)]||'application/octet-stream');res.end(body);
 }catch{res.statusCode=404;res.end('Not found');}
});
await new Promise(r=>server.listen(0,'127.0.0.1',r));
const base=`http://127.0.0.1:${server.address().port}`;
const temporary=await mkdtemp(join(tmpdir(),'quantlens-controls-'));
const output=process.env.QUANTLENS_CONTROLS_OUTPUT||'/tmp/quantlens-controls-ui';
await mkdir(output,{recursive:true});
const browser=spawn(process.env.QUANTLENS_CHROME_BIN||'google-chrome',['--headless=new','--no-sandbox','--disable-gpu','--remote-debugging-port=0',`--user-data-dir=${temporary}`,'about:blank'],{stdio:['ignore','ignore','pipe']});
let socket;
try{
 const endpoint=await new Promise((resolve,reject)=>{let log='';const timer=setTimeout(()=>reject(Error(log)),10000);browser.stderr.on('data',data=>{log+=data;const match=log.match(/DevTools listening on (ws:\/\/\S+)/);if(match){clearTimeout(timer);resolve(match[1]);}});browser.once('error',reject);});
 const targets=await(await fetch(`http://127.0.0.1:${new URL(endpoint).port}/json`)).json();
 socket=new WebSocket(targets.find(t=>t.type==='page').webSocketDebuggerUrl);await new Promise(r=>socket.onopen=r);
 let serial=0;const pending=new Map(),errors=[];
 socket.onmessage=event=>{const data=JSON.parse(event.data);if(data.method==='Runtime.exceptionThrown')errors.push(data.params);if(data.id){const p=pending.get(data.id);pending.delete(data.id);data.error?p.reject(data.error):p.resolve(data.result);}};
 const call=(method,params={})=>new Promise((resolve,reject)=>{const id=++serial;pending.set(id,{resolve,reject});socket.send(JSON.stringify({id,method,params}));});
 const js=async expression=>{const data=await call('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true});if(data.exceptionDetails)throw Error(JSON.stringify(data.exceptionDetails));return data.result.value;};
 const wait=async expression=>{for(let i=0;i<100;i++){if(await js(expression))return;await new Promise(r=>setTimeout(r,30));}throw Error('Timed out: '+expression);};
 await call('Runtime.enable');await call('Page.enable');
 await call('Emulation.setDeviceMetricsOverride',{width:1440,height:900,deviceScaleFactor:1,mobile:false});
 await call('Page.navigate',{url:base+'/__controls'});await wait('window.ready && !!document.querySelector(".date-input")');
 assert.deepEqual(await js(`(()=>{const input=document.querySelector('input[type=date]');return {value:new FormData(document.querySelector('form')).get('date'),border:getComputedStyle(input).borderColor,weight:getComputedStyle(document.querySelector('form button:not(.date-input__toggle)')).fontWeight,line:getComputedStyle(document.querySelector('.rule-detail__head')).borderBottomWidth,padding:getComputedStyle(document.querySelector('.rule-detail__head')).paddingBottom,note:getComputedStyle(document.querySelector('.item-row__meta')).lineHeight,hr:getComputedStyle(document.querySelector('hr')).display}})()`),{value:'2026-11-13',border:'rgb(209, 213, 219)',weight:'700',line:'0px',padding:'0px',note:'18px',hr:'none'});
 await js('document.querySelector(".date-input__toggle").click()');
 assert.equal(await js('document.querySelectorAll("[data-date]").length'),42);
 assert.equal(await js(`document.querySelectorAll('.date-picker select').length`),0);
 await js(`document.querySelector('[data-year] [data-select-trigger]').click()`);
 assert.equal(await js(`document.querySelector('[data-year] [role=listbox]').hidden`),false);
 assert.equal(await js(`(()=>{const list=document.querySelector('[data-year] [role=listbox]'),selected=list.querySelector('[aria-selected=true]'),r=list.getBoundingClientRect(),s=selected.getBoundingClientRect();return s.top>=r.top && s.bottom<=r.bottom})()`),true);
 await js(`document.querySelector('[data-year] [data-value="2027"]').click();document.querySelector('[data-month] [data-select-trigger]').click();document.querySelector('[data-month] [data-value="0"]').click()`);
 assert.equal(await js(`document.querySelector('.date-picker__days').getAttribute('aria-label')`),'2027年1月');
 assert.equal(await js(`document.querySelector('input[type=date]').value`),'2026-11-13');
 await js(`document.querySelector('[data-year] [data-select-trigger]').click();document.querySelector('[data-year] [data-value="2026"]').click();document.querySelector('[data-month] [data-select-trigger]').focus()`);
 // Repeated arrows must advance within the open themed menu, then Enter commits.
 for(let i=0;i<2;i++)await call('Input.dispatchKeyEvent',{type:'keyDown',key:'ArrowDown',code:'ArrowDown'});
 assert.equal(await js(`document.querySelector('[data-month] .select__option.is-active').dataset.value`),'2');
 await call('Input.dispatchKeyEvent',{type:'keyDown',key:'Enter',code:'Enter',windowsVirtualKeyCode:13,text:'\r'});
 await call('Input.dispatchKeyEvent',{type:'keyUp',key:'Enter',code:'Enter',windowsVirtualKeyCode:13});
 assert.equal(await js(`document.querySelector('.date-picker__days').getAttribute('aria-label')`),'2026年3月');
 await js(`document.querySelector('[data-month] [data-select-trigger]').click();document.querySelector('[data-month] [data-value="10"]').click();document.querySelector('[data-year] [data-select-trigger]').focus();document.querySelector('[data-year] [data-select-trigger]').click()`);
 await call('Input.dispatchKeyEvent',{type:'keyDown',key:'Escape',code:'Escape'});
 assert.equal(await js(`document.querySelector('[data-year] [role=listbox]').hidden && !document.querySelector('.date-picker').hidden`),true);
 await js(`document.querySelector('[data-date="2026-11-18"]').click()`);
 assert.deepEqual(await js('({value:document.querySelector("input[type=date]").value,changes,closed:document.querySelector(".date-picker").hidden})'),{value:'2026-11-18',changes:['2026-11-18'],closed:true});
 await js(`document.querySelector('.date-input__toggle').click();document.querySelector('[data-month-step="1"]').click()`);
 assert.equal(await js(`document.querySelector('[data-month] [data-select-value]').textContent`),'12月');
 await js('document.querySelector("[data-clear]").click()');
 assert.equal(await js('document.querySelector("input[type=date]").value'),'');
 await js(`(()=>{const input=document.querySelector('input[type=date]');input.value='2026-11-13';input.min='2026-11-10';input.max='2026-11-20';input.required=true;document.querySelector('.date-input__toggle').click()})()`);
 assert.deepEqual(await js(`({before:document.querySelector('[data-date="2026-11-09"]').disabled,after:document.querySelector('[data-date="2026-11-21"]').disabled,clear:document.querySelector('[data-clear]').disabled})`),{before:true,after:true,clear:true});
 await call('Input.dispatchKeyEvent',{type:'keyDown',key:'ArrowRight',code:'ArrowRight'});
 assert.equal(await js('document.activeElement.dataset.date'),'2026-11-14');
 await call('Input.dispatchKeyEvent',{type:'keyDown',key:'Escape',code:'Escape'});
 assert.equal(await js('document.querySelector(".date-picker").hidden && document.activeElement.type === "date"'),true);
 await js(`(()=>{const input=document.querySelector('input[type=date]');input.min='2020-01-01';input.max='2026-12-03'})()`);
 // Dynamically mounted fields retain names, validation and native date values.
 await js(`(()=>{const label=document.createElement('label');label.innerHTML='<input class="input" name="extra" type="date" value="2026-12-31">';document.querySelector('form').append(label)})()`);
 await wait('document.querySelectorAll(".date-input").length === 2');
 assert.equal(await js('new FormData(document.querySelector("form")).get("extra")'),'2026-12-31');
 const layouts=[];
 for(const [width,height,mobile] of [[1440,900,false],[390,844,true],[320,568,true],[280,480,true],[667,300,true],[320,220,true]]){
  await call('Emulation.setDeviceMetricsOverride',{width,height,deviceScaleFactor:1,mobile});
  await js('new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))');
  for(const theme of ['light','dark']){
   for(const edge of ['top','bottom']){
    await js(`(()=>{document.documentElement.dataset.theme=${JSON.stringify(theme)};const form=document.querySelector('form');form.style.position='fixed';form.style.left='8px';form.style.right='8px';form.style.top=${edge==='top'?"'8px'":"'auto'"};form.style.bottom=${edge==='bottom'?"'8px'":"'auto'"};const toggle=document.querySelector('.date-input__toggle');if(toggle.getAttribute('aria-expanded')==='true')toggle.click();toggle.click()})()`);
    const bounds=await js(`(()=>{const p=document.querySelector('.date-picker'),r=p.getBoundingClientRect();return {hidden:p.hidden,left:r.left,top:r.top,right:r.right,bottom:r.bottom,width:innerWidth,height:innerHeight}})()`);
    assert.equal(bounds.hidden,false);
    assert.ok(bounds.left>=7 && bounds.top>=7 && bounds.right<=bounds.width-7 && bounds.bottom<=bounds.height-7,JSON.stringify({width,height,theme,edge,...bounds}));
    const colors=await js(`(()=>{const style=value=>getComputedStyle(document.querySelector('[data-date="'+value+'"]'));return {current:style('2026-11-12').color,adjacent:style('2026-10-26').color,disabled:style('2026-12-04').color,adjacentBackground:style('2026-10-26').backgroundColor,disabledBackground:style('2026-12-04').backgroundColor}})()`);
    assert.notEqual(colors.adjacent,colors.current);assert.notEqual(colors.adjacent,colors.disabled);assert.notEqual(colors.adjacentBackground,colors.disabledBackground);
    for(const period of ['year','month']){
     await js(`document.querySelector('[data-${period}] [data-select-trigger]').click()`);
     const menu=await js(`(()=>{const list=document.querySelector('[data-${period}] [role=listbox]'),r=list.getBoundingClientRect(),other=document.querySelector('[data-${period==='year'?'month':'year'}] [role=listbox]');return {hidden:list.hidden,left:r.left,right:r.right,top:r.top,bottom:r.bottom,background:getComputedStyle(list).backgroundColor,panelBackground:getComputedStyle(document.querySelector('.date-picker')).backgroundColor,otherClosed:other.hidden}})()`);
     assert.equal(menu.hidden,false);assert.equal(menu.otherClosed,true);assert.equal(menu.background,menu.panelBackground);
     assert(menu.left>=7 && menu.right<=width-7 && menu.top>=7 && menu.bottom<=height-7,JSON.stringify({width,height,theme,edge,period,...menu}));
     if(edge==='bottom'&&period==='year'&&[1440,320].includes(width)&&height>=568){const shot=await call('Page.captureScreenshot',{format:'png'});await writeFile(join(output,`${width}-${theme}-year.png`),Buffer.from(shot.data,'base64'));}
    }
    await js(`document.querySelector('[data-month] [data-select-trigger]').click()`);
    if(height===220){await js('document.querySelector(".date-picker").scrollTop=100');assert.equal(await js('document.querySelector(".date-picker").hidden'),false);}
    if(edge==='bottom'&&[1440,320].includes(width)&&height>=568){const shot=await call('Page.captureScreenshot',{format:'png'});await writeFile(join(output,`${width}-${theme}.png`),Buffer.from(shot.data,'base64'));}
    layouts.push({width,height,theme,edge});
   }
  }
 }
 assert.deepEqual(errors,[]);
 console.log(JSON.stringify({passed:true,layouts:layouts.length,screenshots:output}));
}finally{socket?.close();browser.kill();server.close();await new Promise(r=>browser.once('exit',r));await rm(temporary,{recursive:true,force:true,maxRetries:3});}
