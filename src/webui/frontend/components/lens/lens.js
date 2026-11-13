import {badge,fmtTime,compactRunId} from "../../core/fmt.js";
import {selectMarkup} from "../../core/dom.js";
import {createSelect} from "../../core/select.js";
import {attachStockPicker} from "../../core/stock-picker.js";
import {captureLogScroll,updateTaskLog} from '../../core/task-log.js';
import {fetchApi} from '../../core/page-cache.js';
import { labelText, displayText } from "../../core/labels.js";
import {lineChart,areaChart,barChart,candleChart} from '../../core/charts.js';

const pct=v=>v==null?'—':`${(Number(v)*100).toFixed(2)}%`;
const num=v=>v==null?'—':Number(v).toLocaleString('zh-CN',{maximumFractionDigits:2});
const esc=v=>displayText(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const day=v=>String(v??'').slice(0,10);
const state={running:'运行中',completed:'已完成',failed:'失败',interrupted:'已中断',incomplete:'尚未完成',cancelled:'已取消',queued:'排队中'};
const statusBadge=value=>badge(state[value]||labelText(value),({running:'accent',completed:'safe',failed:'danger',interrupted:'warn',incomplete:'warn',queued:'warn',cancelled:'muted'})[value]||'muted').outerHTML;

async function request(path,body){
  const response=await fetchApi(`/api${path}`,body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  const value=await response.json();
  if(!response.ok)throw new Error(value.detail?.message||JSON.stringify(value.detail||value));
  return value;
}

function table(rows,cols){
  if(!rows.length)return '<div class="ql-empty">暂无记录</div>';
  const value = raw => compactRunId(raw) !== String(raw ?? '')
    ? `<span class="run-id" title="${esc(raw)}" aria-label="${esc(raw)}">${esc(compactRunId(raw))}</span>` : esc(labelText(raw));
  return `<div class="ql-table-wrap"><table class="ql-table"><thead><tr>${cols.map(c=>`<th>${c[1]}</th>`).join('')}</tr></thead><tbody>${rows.map(r=>`<tr>${cols.map(c=>`<td>${c[2]?c[2](r[c[0]],r):value(r[c[0]])}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`;
}

function card(title,content,className=''){return `<section class="ql-card${className?` ${className}`:''}"><header><h3>${title}</h3></header>${content}</section>`;}
function field(label,name,type='text',value='',attrs=''){return `<label class="field"><span class="field__label">${label}</span><input class="input" name="${name}" type="${type}" value="${esc(value)}" ${attrs}></label>`;}
function emptyChart(root,key,rows,value,label){
  const target=root.querySelector(`[data-chart="${key}"]`);
  if(target&&rows.length)target.append(lineChart({series:[{label,values:rows.map(r=>r[value]),color:'var(--accent)'}],labels:rows.map(r=>day(r.date)),height:250}));
}

export async function mount(root,ctx) {
  const page=root.dataset.pane;
  let coverage={};let loading=false;let screenRequest=0;
  let taskDialog=null;
  let stockPickers=[];
  function bindStockPickers(){
    stockPickers.forEach(picker=>picker.destroy());
    stockPickers=[...root.querySelectorAll('input[name="code"], input[name="instrument_id"]')].map(input=>attachStockPicker(input));
  }
  let refreshClusterStatus=null;
  ctx.bus.on('health',()=>{
    if(refreshClusterStatus&&!root.hidden&&!root.closest('.view')?.hidden)refreshClusterStatus();
  });
  function closeTask(){taskDialog?.close();}
  function notify(error){ctx.bus.emit('toast',{kind:'error',title:'操作失败',detail:error.message});}
  function bind(selector,action){root.querySelectorAll(selector).forEach(el=>el.onclick=async()=>{el.disabled=true;try{await action(el);}catch(e){notify(e);}finally{el.disabled=false;}});}
  function form(selector,action){const f=root.querySelector(selector);if(f)f.onsubmit=async e=>{e.preventDefault();const b=f.querySelector('button[type="submit"]');b.disabled=true;try{await action(Object.fromEntries(new FormData(f)));}catch(e){notify(e);}finally{b.disabled=false;}};}
  function stockLinks(){bind('[data-stock]',async el=>{ctx.stock=el.dataset.stock;ctx.bus.emit('route','stocks');});}
  function banner(){return '';}
  async function task(value){
    closeTask();
    const trigger=document.activeElement;
    const dialog=document.createElement('dialog');dialog.className='ql-task-dialog';dialog.dataset.job=value.task_id;
    dialog.setAttribute('aria-label','任务详情');
    dialog.innerHTML='<div class="ql-card"><header><h3>任务详情</h3><button type="button" class="btn btn--ghost btn--sm" data-close-task autofocus>关闭</button></header><div data-job-content aria-live="polite">正在读取任务…</div></div>';
    const box=dialog.querySelector('[data-job-content]');
    let timer=null;
    dialog.addEventListener('close',()=>{clearTimeout(timer);dialog.remove();if(taskDialog===dialog)taskDialog=null;if(trigger?.isConnected)trigger.focus();},{once:true});
    dialog.querySelector('[data-close-task]').onclick=()=>dialog.close();
    document.body.append(dialog);taskDialog=dialog;dialog.showModal();
    const poll=async()=>{
      if(!dialog.open)return;
      try {
        const job=await request(`/tasks/${encodeURIComponent(value.task_id)}`);
        if(!dialog.open)return;
        const previousLog=box.querySelector('[data-running-log]');
        const restoreScroll=captureLogScroll(previousLog);
        box.innerHTML=`<div class="toolbar"><h3>${esc(job.name)}</h3>${statusBadge(job.status)}</div><p>任务编号：${esc(job.task_id)}</p><p>创建时间：${esc(fmtTime(job.created_at))} · 结束时间：${esc(fmtTime(job.finished_at))}</p><progress class="ql-progress" aria-label="任务进度" max="100" value="${Number(job.progress?.percent)||0}"></progress>${job.error?`<div class="ql-task-error" role="alert"><h4>失败或中断原因</h4><pre class="ql-log">${esc(job.error)}</pre></div>`:''}<h4>运行日志</h4><pre class="ql-log" data-running-log></pre>`;
        let log=box.querySelector('[data-running-log]');
        if(previousLog){log.replaceWith(previousLog);log=previousLog;}
        const content=job.log?.join('\n')||'暂无日志';
        if(log.textContent!==content)log.textContent=content;
        restoreScroll();
        if(job.status==='running')timer=setTimeout(poll,2000);
      } catch(error) {
        if(!dialog.open)return;
        box.innerHTML=`<p class="ql-task-error" role="alert">${esc(error.message)}</p><button type="button" class="btn" data-retry-task>重试</button>`;
        box.querySelector('[data-retry-task]').onclick=poll;
      }
    };await poll();
  }
  ctx.bus.on('route',closeTask);
  ctx.bus.on('subtab',detail=>{if(detail.page==='settings')closeTask();});

  async function dashboard() {
    const data=await request('/overview'),c=data.coverage,m=data.market[0];
    const attentionJobs=data.jobs.filter(job=>job.needs_attention!==false&&['running','queued','failed','interrupted','incomplete'].includes(job.status));
    root.innerHTML=`<div class="ql-head"><div><h2>市场与研究概览</h2></div><div class="ql-actions"><button data-go="settings/data">更新数据</button><button data-go="screen">条件选股</button><button data-go="factors">研究因子</button></div></div>`+
      `<div class="ql-stats">${[['最新交易日',day(c.end)||'尚未发布'],['市场平均涨跌',pct(m?.equal_weight_return)],['覆盖证券',num(c.instruments)],['因子评估数',num(c.factor_evaluations)],['待处理任务',num(attentionJobs.length)],['数据覆盖天数',num(c.days)]].map(([label,value])=>`<div class="ql-stat"><span>${label}</span><strong>${value}</strong></div>`).join('')}</div>`+
      `<div class="ql-dashboard-row ql-dashboard-row--market">${card('市场广度',`<p class="ql-market-summary"><span>60日均线上方 <strong>${pct(m?.breadth)}</strong></span><span class="ql-market-up">上涨 ${num(m?.advancing)}</span><span class="ql-market-down">下跌 ${num(m?.declining)}</span></p><div data-market-chart></div>`,'ql-dashboard-market')}${card('数据状态',`<div class="ql-dashboard-scroll">${data.quality.length?table(data.quality,[['table','表'],['passed','质检',v=>badge(v?'通过':'阻断',v?'safe':'danger').outerHTML]]):'<p>暂无质检报告</p>'}</div>`,'ql-dashboard-quality')}</div>`+
      `<div class="ql-dashboard-row ql-dashboard-row--activity">${card('最近研究',`<div class="ql-dashboard-scroll">${table(data.evaluations,[['factor','因子'],['rank_ic','Rank IC',num],['coverage','覆盖率',pct],['run_id','批次']])}</div>`)}${card('需要处理',`<div class="ql-dashboard-scroll">${table(attentionJobs,[['name','任务'],['status','状态',statusBadge],['created_at','时间',fmtTime]])}</div>`)}</div>`;
    const rows=data.market.slice().reverse();
    if(rows.length)root.querySelector('[data-market-chart]').append(lineChart({series:[{label:'市场等权收益',values:rows.map(r=>r.equal_weight_return),color:'var(--accent)'}],labels:rows.map(r=>day(r.date)),height:200,yFmt:pct,interactive:true,fillFirst:true}));
    bind('[data-go]',element=>ctx.bus.emit('route',element.dataset.go));
  }
  async function stocks(){
    const code=ctx.stock||'000001';
    root.innerHTML='<button data-back>返回上一页</button>'+card('股票研究档案',`<form data-stock-form class="ql-form">${field('证券代码','code','text',code,'pattern="[0-9]{6}" required')}${field('历史日期','as_of','date',day(coverage.end))}<button type="submit">查看档案</button></form>`)+ '<div data-detail></div>';
    bindStockPickers();
    form('[data-stock-form]',async f=>{ctx.stock=f.code;await showStock(f.code,f.as_of);});
    bind('[data-back]',()=>history.back());
    if(coverage.end) await showStock(code,day(coverage.end));
  }

  async function showStock(code,date){
    const d=await request(`/stocks/${encodeURIComponent(code)}?as_of=${date}`);const host=root.querySelector('[data-detail]');const p=d.prices.at(-1);
    host.innerHTML=`<div class="ql-stats">${[['证券',code],['收盘价',num(p.close)],['当日回报',pct(p.ret_wd)],['成交额',num(p.amount)]].map(([a,b])=>`<div class="ql-stat"><span>${a}</span><strong>${b}</strong></div>`).join('')}</div>`+
      card('价格与成交',`<div data-chart="price"></div>`)+
      `<div class="ql-grid">${card('财务与经营',fundamentals(d.events))}${card('预期与机构',eventsTable(d.events))}</div>`+
      card('研究记录',`<form data-watch-form class="ql-form">${field('关注依据','thesis','text','','required')}${field('失效条件','invalidation')}<button type="submit">加入观察列表</button></form>`+
        table(d.timeline,[['created_at','记录时间',fmtTime],['body','研究依据',v=>esc(v.thesis)],['body','失效条件',v=>esc(v.invalidation)]]));
    root.querySelector('[data-chart="price"]').append(candleChart(d.prices.slice(-120)));
    host.insertAdjacentHTML('beforeend',card('因子值',table(d.factors,[['factor','因子'],['value','原值',num],['std_value','标准化值',num],['run_id','批次']]))+card('最新策略信号',table(d.signals,[['strategy_id','策略'],['score','得分',num],['as_of','日期',day],['run_id','批次']])));
    form('[data-watch-form]',async f=>{await request('/watch',{...f,instrument_id:code,as_of:date,action:'watch'});await showStock(code,date);});
  }

  function fundamentals(events){
    const defs={fs_income:[['revenue','营业收入'],['net_profit_parent','归母净利润']],fs_balance:[['total_assets','总资产'],['total_liabilities','总负债']],fs_cashflow:[['cfo_net','经营现金流']],fundamental_ttm:[['roe_ttm',"近一年净资产收益率"],['gross_margin_ttm',"近一年毛利率"]]};
    const rows=[];for(const [key,fields] of Object.entries(defs)){const e=events[key][0];if(e)for(const [f,label] of fields)rows.push({label,value:e[f],period:day(e.report_period),visible:day(e.effective_date)});}
    return table(rows,[['label','指标'],['value','值',num],['period','报告期'],['visible','可见日期']]);
  }
  function eventsTable(events){const rows=[];for(const [key,label,field] of [['consensus_daily',"下一财年净利润一致预期",'consensus_fy1_mean'],['holder_longline','长线机构比例','inst_longline_ratio'],['industry_classification','行业','industry_name']]){const e=events[key][0];if(e)rows.push({label,value:e[field],date:day(e.effective_date)});}return table(rows,[['label','项目'],['value','最新可见值'],['date','生效日期']]);}

  async function screen(){
    const accountData=await request('/accounts');
    const accounts=accountData.items;
    root.innerHTML=card('条件选股',`<form data-natural class="ql-form"><label class="ql-wide">描述筛选条件<textarea name="question" rows="2" required placeholder="40元以下，近20日不再下跌，处于60日价格区间下半部"></textarea></label><button type="submit">填写筛选条件</button></form>
      <form data-screen class="ql-form"><label class="field"><span class="field__label">账户</span><div class="select" data-screen-account>${selectMarkup('选择账户')}</div></label><input type="hidden" name="account_id">${field('历史日期','as_of','date',day(coverage.end))}${field('最高价格','max_price','number',40,'min="0.01" step="0.01"')}${field('最低成交额（元）','min_amount','number',10000000,'min="0"')}${field('20日最低涨幅（比例）','min_momentum','number',-1,'min="-1" step="0.01"')}${field('60日最高价格位置','max_position','number',1,'min="0" max="1" step="0.05"')}${field("最低净资产收益率（可空）",'min_roe','number','','step="0.01"')}${field('最高负债率（可空）','max_debt','number','','min="0" max="1" step="0.01"')}${field('行业（可空）','industry')}<button type="submit">筛选</button></form>`)+card('候选证券','<div data-results></div>');
    const accountInput=root.querySelector('[data-screen] [name=account_id]');
    const accountSelect=createSelect(root.querySelector('[data-screen-account]'),{onChange:async value=>{accountInput.value=value;ctx.accountId=value;root.querySelector('[data-results]').innerHTML='';root.querySelector('[data-screen] button[type=submit]').click();}});
    accountSelect.setOptions(accounts.map(a=>({value:a.account_id,label:a.alias||a.account_id})),ctx.accountId||accountData.default_account_id);
    accountInput.value=accountSelect.value;
    ctx.accountId=accountSelect.value;
    form('[data-natural]',async f=>{const d=await request('/screen/interpret',{...f,as_of:root.querySelector('[name=as_of]').value});for(const [k,v] of Object.entries(d.criteria)){const input=root.querySelector(`[data-screen] [name="${k}"]`);if(input&&k!=='account_id')input.value=v??'';}ctx.bus.emit('toast',{kind:'success',title:'条件已填入'});});
    form('[data-screen]',async f=>{for(const k of ['max_price','min_amount','min_momentum','max_position','min_roe','max_debt'])f[k]=f[k]===''?null:Number(f[k]);await showScreen(f);});
    if(coverage.end) await showScreen({as_of:day(coverage.end),account_id:accountInput.value});
  }
  async function showScreen(criteria){
    const sequence=++screenRequest;
    const d=await request('/screen',criteria);
    if(sequence!==screenRequest || (criteria.account_id||'')!==(root.querySelector('[data-screen] [name=account_id]')?.value||''))return;
    root.querySelector('[data-results]').innerHTML=`<p>${d.items.length} 个结果</p>`+table(d.items,[['instrument_id','证券',v=>`<button class="ql-link" data-stock="${esc(v)}">${esc(v)}</button>`],['industry_name','行业'],['close','收盘价',num],['momentum_20','20日涨幅',pct],['position_60','60日价格位置',pct],['roe_ttm',"净资产收益率",pct],['debt_ratio','负债率',pct],['amount','成交额',num]]);stockLinks();
  }

  async function assistant(){
    root.innerHTML=banner()+card('研究助手',`<form data-assistant class="ql-form ql-assistant-form">${field('证券代码（可选）','instrument_id')}${field('实验编号（可选）','run_id')}<label class="field ql-wide"><span class="field__label">问题</span><textarea class="textarea" name="question" required rows="4" placeholder="这家公司的经营情况有哪些变化？哪些依据还不充分？"></textarea></label><button type="submit" class="btn btn--primary">分析</button></form><div data-answer></div>`);
    bindStockPickers();
    form('[data-assistant]',async f=>{root.querySelector('[data-answer]').textContent='正在分析…';const a=await request('/assistant',f);root.querySelector('[data-answer]').innerHTML=`<h3>模型解读</h3><pre class="ql-log">${esc(a.body.answer)}</pre><p>模型 ${esc(a.body.model)} · 证据 ${esc(a.body.evidence_id)}</p><button type="button" class="btn btn--ghost" data-evidence="${a.body.evidence_id}">查看原始依据</button>`;bind('[data-evidence]',async el=>{const e=await request(`/records/${el.dataset.evidence}`);root.querySelector('[data-answer]').insertAdjacentHTML('beforeend',`<pre class="ql-log">${esc(JSON.stringify(e.body,null,2))}</pre>`);});});
  }

  async function tasks() {
    const jobs=await request('/jobs');
    root.innerHTML=card('批次任务',`<div class="toolbar"><button type="button" class="btn btn--primary" data-pipeline>运行因子研究批次</button><button type="button" class="btn btn--ghost" data-refresh>刷新</button></div>`+
      table(jobs.items,[['name','任务'],['status','状态',statusBadge],['created_at','时间',fmtTime],['task_id','详情',value=>`<button type="button" class="btn btn--ghost btn--sm" data-task="${esc(value)}">查看</button>`]]));
    bind('[data-refresh]',tasks);bind('[data-pipeline]',async()=>task(await request('/pipeline',{})));bind('[data-task]',element=>task({task_id:element.dataset.task}));
  }
  async function cluster() {
    const data=await request('/settings/cluster');
    root.innerHTML=card('后台服务',`<p data-service-summary role="status"></p><p data-service-error class="ql-task-error" role="alert" hidden></p><div data-service-log></div><div class="toolbar"><button type="button" class="btn btn--ghost btn--sm" data-service-task hidden>查看启动任务</button></div>`)
      +card('集群',`<div class="toolbar"><button type="button" class="btn" data-start>启动后台服务</button><button type="button" class="btn btn--danger" data-stop>停止集群</button><button type="button" class="btn btn--ghost" data-refresh>刷新</button></div><p><a href="http://127.0.0.1:9870" target="_blank" rel="noreferrer">HDFS</a> · <a href="http://127.0.0.1:8088" target="_blank" rel="noreferrer">YARN</a> · <a href="http://127.0.0.1:16010" target="_blank" rel="noreferrer">HBase</a> · <a href="http://127.0.0.1:18080" target="_blank" rel="noreferrer">Spark History</a></p><div data-service-nodes></div>`);
    function render(data){
      const startup=data.service_startup;
      const running=startup?.status==='running';
      const failed=['failed','interrupted'].includes(startup?.status);
      root.querySelector('[data-service-summary]').textContent=running
        ? `后台服务启动中 · ${startup.phase||'正在准备'}`
        : failed ? '后台服务启动失败，可重试或查看日志。'
        : data.available&&data.training_ready ? '集群与训练服务已就绪' : '后台服务尚未全部就绪';
      const error=root.querySelector('[data-service-error]');
      error.hidden=!failed;error.textContent=startup?.error||'';
      const logHost=root.querySelector('[data-service-log]');
      const log=updateTaskLog(logHost.querySelector('[data-task-log]'),startup?.log,{limit:60,subject:startup?.task_id||''});
      if(log&&!log.isConnected)logHost.replaceChildren(log);
      if(!log)logHost.replaceChildren();
      const details=root.querySelector('[data-service-task]');
      details.hidden=!startup?.task_id;details.onclick=()=>task({task_id:startup.task_id});
      root.querySelector('[data-start]').disabled=running;
      root.querySelector('[data-stop]').disabled=running;
      const nodes=[...(data.nodes||[]),{name:'训练服务',state:data.training_ready?'running':'stopped',health:data.training_ready?'healthy':'未就绪'}];
      root.querySelector('[data-service-nodes]').innerHTML=table(nodes,[['name','服务'],['state','状态'],['health','健康']]);
    }
    let refreshing=false;
    refreshClusterStatus=async()=>{
      if(refreshing)return;refreshing=true;
      try{render(await request('/settings/cluster'));}
      catch(error){root.querySelector('[data-service-summary]').textContent=`读取服务状态失败：${error.message}`;}
      finally{refreshing=false;}
    };
    render(data);
    bind('[data-start]',async()=>{
      const value=await request('/settings/cluster/start',{});
      ctx.bus.emit('health:refresh');await refreshClusterStatus();await task(value);
    });
    bind('[data-stop]',async()=>{await request('/settings/cluster/stop',{});await refreshClusterStatus();ctx.bus.emit('health:refresh');});
    bind('[data-refresh]',refreshClusterStatus);
  }

  async function load(){
    if(loading)return;loading=true;stockPickers.forEach(picker=>picker.destroy());stockPickers=[];root.querySelectorAll('.chart--interactive').forEach(chart=>chart.dispose?.());root.innerHTML='<div class="ql-loading">正在读取数据…</div>';
    try {if(!['tasks','cluster','assistant'].includes(page))coverage=(await request('/datasets')).coverage;await ({dashboard,stocks,screen,assistant,tasks,cluster})[page]();}
    catch(error){root.innerHTML=card('读取失败',`<p class="ql-error">${esc(error.message)}</p><button type="button" class="btn" data-retry>重试</button>`);bind('[data-retry]',load);}
    finally{loading=false;}
  }
  ctx.bus.on('route',id=>{if(id===page&&!root.hidden)load();});
  await load();
}
