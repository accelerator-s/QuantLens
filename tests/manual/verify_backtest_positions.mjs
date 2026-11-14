// Exercise snapshot calendars through the real app with isolated API fixtures.
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {readFile, writeFile, mkdtemp} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {spawn} from 'node:child_process';
import {setTimeout as delay} from 'node:timers/promises';

const root = fileURLToPath(new URL('../../src/webui/frontend', import.meta.url));
const output = await mkdtemp(join(tmpdir(), 'quantlens-positions-'));
const dates = ['2026-10-29','2026-10-30','2026-11-02','2026-11-03','2026-11-04','2026-11-05'];
const requests = [];
let failed = false;
async function api(url) {
  const path = url.pathname;
  if (path === '/api/status') return {server:{instance_id:'fixture'},workspace:{missing:0}};
  if (path === '/api/backtest/runs') return {items:['run','empty'].map(run_id => ({run_id,profile:'fixture',state:'completed',start:dates[0],end:dates.at(-1)}))};
  if (/^\/api\/backtest\/runs\/[^/]+$/.test(path)) return {profile:'fixture',status:{state:'completed'},config:{},metrics:{}};
  if (path.endsWith('/positions')) {
    const date = url.searchParams.get('date') || dates.at(-1);
    requests.push(url.search);
    if (path.includes('/empty/')) return {items:[],total_rows:0,dates:[],date:'',adjustment_dates:[]};
    if (date === '2026-11-04' && !failed) {failed = true; throw Error('持仓读取测试错误');}
    if (date === '2026-11-02' || date === '2026-11-06') await delay(200);
    const snapshot_date = dates.filter(day=>day<=date).at(-1) || '';
    const rows = Array.from({length:date === '2026-10-29' ? 0 : 12}, (_, i) => ({instrument:String(i + 1).padStart(6,'0'),stock_name:i === 0?'平安银行':i === 11?'':'测试股票',weight:.05,market_value:Number(snapshot_date.slice(-2))*1000,shares_nominal:100}));
    const limit = Number(url.searchParams.get('limit') || 10), offset = Number(url.searchParams.get('offset') || 0);
    return {items:rows.slice(offset,offset+limit),total_rows:rows.length,dates,date,snapshot_date,
      date_range:{start:'2026-10-29',end:'2026-11-07'},adjustment_dates:['2026-10-30','2026-11-03']};
  }
  return {items:[],dates:[],total_rows:0};
}
const server = createServer(async (req,res) => {
  try {
    const url = new URL(req.url,'http://localhost');
    if (url.pathname.startsWith('/api/')) {
      res.setHeader('Content-Type','application/json');
      try {res.end(JSON.stringify(await api(url)));}
      catch (error) {res.writeHead(500);res.end(JSON.stringify({detail:error.message}));}
      return;
    }
    const file = url.pathname === '/' ? '/index.html' : url.pathname;
    res.setHeader('Content-Type',file.endsWith('.css')?'text/css':file.endsWith('.js')?'text/javascript':file.endsWith('.svg')?'image/svg+xml':'text/html; charset=utf-8');
    res.end(await readFile(root+file));
  } catch {res.writeHead(404);res.end();}
});
await new Promise(resolve => server.listen(0,'127.0.0.1',resolve));
const chrome = spawn(process.env.QUANTLENS_CHROME_BIN || 'google-chrome', ['--headless=new','--no-sandbox','--disable-gpu','--remote-debugging-port=0',`--user-data-dir=${join(output,'browser')}`,'about:blank'], {stdio:['ignore','ignore','pipe']});
let socket;
try {
  const endpoint = await new Promise((resolve,reject) => {
    let log = '';
    const timer = setTimeout(() => reject(Error(log)),10000);
    chrome.stderr.on('data', chunk => {log += chunk; const match = log.match(/DevTools listening on (ws:\/\/\S+)/); if (match) {clearTimeout(timer);resolve(match[1]);}});
    chrome.once('error',reject);
  });
  const target = await (await fetch(`http://127.0.0.1:${new URL(endpoint).port}/json/new?about:blank`,{method:'PUT'})).json();
  socket = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise(resolve => {socket.onopen = resolve;});
  let serial = 0;
  const pending = new Map(), errors = [];
  socket.onmessage = event => {
    const result = JSON.parse(event.data);
    if (result.method === 'Runtime.exceptionThrown') errors.push(result.params);
    if (result.id) {const item = pending.get(result.id); pending.delete(result.id); result.error ? item.reject(Error(JSON.stringify(result.error))) : item.resolve(result.result);}
  };
  const call = (method,params={}) => new Promise((resolve,reject) => {const id = ++serial;pending.set(id,{resolve,reject});socket.send(JSON.stringify({id,method,params}));});
  async function js(expression) {const result = await call('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true});assert(!result.exceptionDetails,JSON.stringify(result.exceptionDetails));return result.result.value;}
  async function wait(expression) {for (let i=0;i<160;i++) {if (await js(expression)) return;await delay(40);}throw Error(expression+'\n'+await js('document.body.innerText'));}
  const card = '.backtest__positions', input = `${card} input[type=date]`;
  const click = selector => js(`document.querySelector(${JSON.stringify(selector)}).click()`);
  const open = async () => {
    await js('new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))');
    if (await js(`document.querySelector('.date-picker').hidden`)) await click(`${card} .date-input__toggle`);
    await wait(`!document.querySelector('.date-picker').hidden`);
  };
  const choose = async date => {await open();await click(`[data-date="${date}"]`);};
  const loaded = () => wait(`document.querySelector('${card} [aria-busy]')?.getAttribute('aria-busy')==='false' && !!document.querySelector('${card} tbody tr')`);
  async function checkTableLayout() {
    const layout = await js(`(()=>{
      const card=document.querySelector('${card}'),detail=card.parentElement;
      const bounds=card.getBoundingClientRect(),parent=detail.getBoundingClientRect();
      const wrap=card.querySelector('.table-wrap').getBoundingClientRect(),style=getComputedStyle(card);
      const reference=getComputedStyle(document.querySelector('[data-list] tbody td'));
      const stock=card.querySelector('.backtest__stock');
      return {left:Math.abs(bounds.left-parent.left),right:Math.abs(bounds.right-parent.right),
        tableRight:Math.abs(wrap.right-(bounds.right-parseFloat(style.paddingRight)-parseFloat(style.borderRightWidth))),
        font:reference.fontSize,fonts:[stock,stock.firstElementChild,stock.lastElementChild,stock.closest('td'),card.querySelector('td.is-num')].map(node=>getComputedStyle(node).fontSize),
        padding:getComputedStyle(stock.closest('td')).paddingTop,referencePadding:reference.paddingTop};
    })()`);
    assert(layout.left<=1 && layout.right<=1 && layout.tableRight<=1,JSON.stringify(layout));
    assert(layout.fonts.every(size=>size===layout.font),JSON.stringify(layout));
    assert.equal(layout.padding,layout.referencePadding);
  }
  await call('Page.enable');await call('Runtime.enable');
  await call('Emulation.setDeviceMetricsOverride',{width:1440,height:1000,deviceScaleFactor:1,mobile:false});
  await call('Page.navigate',{url:`http://127.0.0.1:${server.address().port}/#backtest`});
  await wait(`document.querySelector('${input}')?.value==='2026-11-05'`);await loaded();
  assert.equal(await js(`document.querySelector('${card} tbody td').textContent`),'平安银行000001');
  assert.equal(await js(`(()=>{const stock=document.querySelector('${card} .backtest__stock'),name=stock.firstElementChild.getBoundingClientRect(),code=stock.lastElementChild.getBoundingClientRect();return code.left>name.right && Math.abs(name.top-code.top)<5 && stock.closest('tr').getBoundingClientRect().height<40})()`),true);
  await checkTableLayout();
  await click(`${card} [data-page=next]`);
  await wait(`document.querySelector('${card} .table-pagination__current').textContent==='2 / 2 页'`);
  assert.equal(await js(`document.querySelector('${card} tbody tr:last-child td').innerText`),'000012');
  await open();
  assert.equal(await js(`document.querySelector('[data-date="2026-11-03"]').classList.contains('is-highlighted')`),true);
  assert.equal(await js(`document.querySelector('[data-date="2026-11-05"]').classList.contains('is-highlighted')`),false);
  assert.equal(await js(`document.querySelector('[data-date="2026-11-01"]').disabled`),false);
  assert.equal(await js(`Array.from(document.querySelectorAll('.date-picker [data-date]')).every(button=>button.disabled === (button.dataset.date<'2026-10-29'||button.dataset.date>'2026-11-07'))`),true);
  assert.equal(await js(`getComputedStyle(document.querySelector('[data-clear]')).display`),'none');
  assert.notEqual(await js(`getComputedStyle(document.querySelector('[data-date-legend]')).display`),'none');
  await click('[data-month-step="-1"]');
  assert.equal(await js(`document.querySelector('[data-date="2026-10-30"]').classList.contains('is-highlighted')`),true);
  assert.equal(await js(`document.querySelector('[data-month-step="-1"]').disabled`),true);
  await click('[data-date="2026-10-30"]');await loaded();
  assert.equal(await js(`document.querySelector('${input}').value`),'2026-10-30');
  assert.equal(await js(`document.querySelector('${card} .table-pagination__current').textContent`),'1 / 2 页');
  await open();await click('[data-month-step="1"]');await click('[data-date="2026-11-03"]');await loaded();
  await wait(`document.querySelector('${input}').value==='2026-11-03'`);
  for (const [width,mobile] of [[1440,false],[390,true]]) {
    await call('Emulation.setDeviceMetricsOverride',{width,height:900,deviceScaleFactor:1,mobile});
    for (const theme of ['light','dark']) {
      await js(`document.documentElement.dataset.theme='${theme}';document.querySelector('${card}').scrollIntoView({block:'center'});new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))`);
      await checkTableLayout();
      await open();
      const colors=await js(`(()=>{const style=date=>getComputedStyle(document.querySelector('[data-date="'+date+'"]'));return {current:style('2026-11-02').color,adjacent:style('2026-10-29').color,disabled:style('2026-10-28').color,marked:document.querySelector('[data-date="2026-10-30"]').classList.contains('is-highlighted')}})()`);
      assert.notEqual(colors.adjacent,colors.current);assert.notEqual(colors.adjacent,colors.disabled);assert.equal(colors.marked,true);
      const bounds = await js(`(()=>{const r=document.querySelector('.date-picker').getBoundingClientRect();return {left:r.left,right:r.right,top:r.top,bottom:r.bottom}})()`);
      assert(bounds.left>=8 && bounds.right<=width-8 && bounds.top>=8 && bounds.bottom<=892,JSON.stringify(bounds));
      assert.equal(await js(`document.querySelector('[data-date="2026-11-03"]').getAttribute('aria-selected')`),'true',JSON.stringify(await js(`({value:document.querySelector('${input}').value, selected:document.querySelector('.date-picker [aria-selected=true]')?.dataset.date, width:innerWidth})`)));
      await writeFile(join(output,`${width}-${theme}.png`),Buffer.from((await call('Page.captureScreenshot',{format:'png'})).data,'base64'));
      await click(`${card} .date-input__toggle`);
    }
  }
  await choose('2026-11-02');
  // Weekends are selectable with the keyboard; keep the latest request's snapshot.
  await open();await call('Input.dispatchKeyEvent',{type:'keyDown',key:'ArrowLeft',code:'ArrowLeft'});
  assert.equal(await js('document.activeElement.dataset.date'),'2026-11-01');
  await call('Input.dispatchKeyEvent',{type:'keyDown',key:'Enter',code:'Enter',windowsVirtualKeyCode:13,text:'\r'});
  await call('Input.dispatchKeyEvent',{type:'keyUp',key:'Enter',code:'Enter',windowsVirtualKeyCode:13});
  await loaded();await delay(250);
  assert.equal(await js(`document.querySelector('${input}').value`),'2026-11-01');
  assert.equal(await js(`document.querySelector('${card} tbody td:nth-child(3)').textContent`),'30,000');
  assert.equal(await js(`document.querySelector('${card} .backtest__snapshot-note').textContent`),'非交易日，显示 2026-10-30 收盘持仓');
  await click(`${card} [data-page=next]`);
  await wait(`document.querySelector('${card} .table-pagination__current').textContent==='2 / 2 页'`);
  assert.equal(await js(`document.querySelector('${input}').value`),'2026-11-01');
  // Native typed dates outside the available snapshots restore the current day.
  const before = requests.length;
  await js(`(()=>{const input=document.querySelector('${input}');input.value='2026-11-08';input.dispatchEvent(new Event('change',{bubbles:true}))})()`);
  assert.equal(await js(`document.querySelector('${input}').value`),'2026-11-01');assert.equal(requests.length,before);
  await choose('2026-11-04');
  await wait(`document.querySelector('${card} .state-card--error')`);
  await click(`${card} .state-card__retry`);await loaded();
  assert.equal(await js(`document.querySelector('${input}').value`),'2026-11-04');
  // Date changes at the page bottom must not collapse the document while loading.
  for (const [width,mobile] of [[1440,false],[390,true]]) {
    await call('Emulation.setDeviceMetricsOverride',{width,height:700,deviceScaleFactor:1,mobile});
    await js(`window.scrollTo(0,document.documentElement.scrollHeight);new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))`);
    for (const date of ['2026-11-06','2026-10-29','2026-11-05']) {
      await open();
      const before=await js('window.scrollY');
      assert(before>20,'The scroll regression must run below the page top');
      await js(`window.scrollSamples=[];window.sampleScroll=true;requestAnimationFrame(function sample(){scrollSamples.push(scrollY);if(sampleScroll)requestAnimationFrame(sample)})`);
      await click(`[data-date="${date}"]`);
      await loaded();await delay(250);
      const samples=await js('window.sampleScroll=false;scrollSamples');
      assert(samples.every(value=>Math.abs(value-before)<=1),JSON.stringify({width,date,before,samples}));
      assert.equal(await js(`document.querySelector('${input}').value`),date);
    }
  }
  await click('[data-run-id=empty]');
  await wait(`document.querySelector('${input}')?.disabled && !!document.querySelector('${card} .table__empty')`);
  assert.equal(await js(`document.querySelector('${card} .date-input__toggle').disabled`),true);
  assert.deepEqual(errors,[]);
  console.log(`PASS: names/code fallback, pagination, marked dates, month navigation, keyboard, races, retry, empty run, light/dark and mobile. ${output}`);
} finally {socket?.close();chrome.kill();server.close();}
