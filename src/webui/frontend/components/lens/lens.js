import {fmtTime} from "../../core/fmt.js";
import {fetchApi} from '../../core/page-cache.js';
import { labelText, displayText } from "../../core/labels.js";
import {lineChart,areaChart,barChart,candleChart} from '../../core/charts.js';

const pct=v=>v==null?'—':`${(Number(v)*100).toFixed(2)}%`;
const num=v=>v==null?'—':Number(v).toLocaleString('zh-CN',{maximumFractionDigits:2});
const esc=v=>displayText(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const day=v=>String(v??'').slice(0,10);
const state={running:'运行中',completed:'已完成',failed:'失败',interrupted:'已中断',incomplete:'尚未完成'};

async function api(path,body){
  const response=await fetchApi(`/api/lens${path}`,body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  const value=await response.json();
  if(!response.ok)throw new Error(value.detail?.message||JSON.stringify(value.detail||value));
  return value;
}

function table(rows,cols){
  if(!rows.length)return '<div class="ql-empty">暂无记录</div>';
  return `<div class="ql-table-wrap"><table class="ql-table"><thead><tr>${cols.map(c=>`<th>${c[1]}</th>`).join('')}</tr></thead><tbody>${rows.map(r=>`<tr>${cols.map(c=>`<td>${c[2]?c[2](r[c[0]],r):esc(labelText(r[c[0]]))}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`;
}

function card(title,content){return `<section class="ql-card"><header><h3>${title}</h3></header>${content}</section>`;}
function field(label,name,type='text',value='',attrs=''){return `<label>${label}<input name="${name}" type="${type}" value="${esc(value)}" ${attrs}></label>`;}
function emptyChart(root,key,rows,value,label){
  const target=root.querySelector(`[data-chart="${key}"]`);
  if(target&&rows.length)target.append(lineChart({series:[{label,values:rows.map(r=>r[value]),color:'var(--accent)'}],labels:rows.map(r=>day(r.date)),height:250}));
}

export async function mount(root,ctx){
  const route=root.dataset.pane;
  let coverage=null;
  let loading=false;
  const load=async()=>{
    if(loading)return;
    loading=true;
    root.innerHTML='<div class="ql-loading">正在读取数据…</div>';
    try{
      coverage=(await api('/datasets')).coverage;
      const handlers={dashboard,market,stocks,screen,models,watch,assistant,journal,system};
      await handlers[route]();
    }catch(error){root.innerHTML=card('读取失败',`<p class="ql-error">${esc(error.message)}</p><button data-retry>重试</button>`);root.querySelector('[data-retry]').onclick=load;}
    finally{loading=false;}
  };
  function notify(error){ctx.bus.emit('toast',{kind:'error',title:'操作失败',detail:error.message});}
  function bind(selector,action){root.querySelectorAll(selector).forEach(el=>el.onclick=async()=>{el.disabled=true;try{await action(el);}catch(e){notify(e);}finally{el.disabled=false;}});}
  function form(selector,action){const f=root.querySelector(selector);if(f)f.onsubmit=async e=>{e.preventDefault();const b=f.querySelector('button[type="submit"]');b.disabled=true;try{await action(Object.fromEntries(new FormData(f)));}catch(e){notify(e);}finally{b.disabled=false;}};}
  function stockLinks(){bind('[data-stock]',async el=>{ctx.stock=el.dataset.stock;ctx.bus.emit('route','stocks');});}
  function banner(){return '';}
  async function task(value){
    root.querySelector('[data-job]')?.remove();
    const box=document.createElement('section');box.className='ql-card';box.dataset.job=value.task_id;root.prepend(box);
    const poll=async()=>{
      if(!box.isConnected)return;
      const response=await fetchApi(`/api/tasks/${value.task_id}`);const job=await response.json();
      if(!response.ok)throw new Error(job.detail?.message||'读取任务失败');
      box.innerHTML=`<h3>${esc(job.name)} · ${state[job.status]||job.status}</h3><progress class="ql-progress" max="100" value="${job.progress?.percent||0}"></progress><pre class="ql-log">${esc(job.log?.slice(-14).join('\n'))}</pre>${job.error?`<p class="ql-error">${esc(job.error)}</p>`:''}`;
      if(job.status==='running')setTimeout(()=>poll().catch(notify),2000);
      else if(job.status==='completed')box.innerHTML+='<p>结果已保存，可刷新列表查看。</p>';
    };await poll();
  }

  async function dashboard(){
    const d=await api('/overview');const m=d.market[0];
    root.innerHTML=banner()+`<div class="ql-head"><div><h2>市场与研究概览</h2></div><div class="ql-actions"><button data-go="data">更新数据</button><button data-go="screen">条件选股</button><button data-go="models">新建实验</button></div></div>
      <div class="ql-stats">${[['最新交易日',day(d.coverage.end)],['市场平均涨跌',pct(m?.equal_weight_return)],['行情记录',num(d.coverage.rows)+' 行'],['覆盖证券',num(d.coverage.instruments)+' 只'],['研究完成',d.models.length+' 次'],['关注标的',d.watch.items.length+' 只']].map(([a,b])=>`<div class="ql-stat"><span>${a}</span><strong>${b}</strong></div>`).join('')}</div>
      <div class="ql-grid">${card('市场广度',`<p>${day(m?.date)} · 上涨 ${num(m?.advancing)} / 下跌 ${num(m?.declining)}</p><div data-chart="breadth"></div>`)}
      ${card('需要处理',`<ul class="ql-list"><li>${d.coverage.age_days>5?'行情距今 '+d.coverage.age_days+' 天。可在数据源页面同步。':"请核对行情日期是否为最新交易日。"}</li><li>模型和数据连接可在设置页检查。</li><li>${d.jobs.filter(j=>j.status==='failed').length} 个任务失败，可查看日志和恢复条件。</li></ul><button data-go="system">查看任务</button>`)}</div>
      ${card('最近研究',table(d.models.map(r=>r.body),[['run_id','实验'],['counts','评估样本',v=>num(v.test)],['metrics',"秩相关系数",v=>num(v.rank_ic)],['completed_at','完成时间',v=>esc(day(v))]]))}`;
    emptyChart(root,'breadth',d.market.slice().reverse(),'equal_weight_return','市场等权日收益');
    bind('[data-go]',el=>ctx.bus.emit('route',el.dataset.go));
  }

  async function market(){
    const d=await api('/market');const rows=d.items.slice().reverse();
    root.innerHTML=banner()+card('市场观察',"<p>按当日有收益记录的证券等权统计，不代表可交易指数。</p><div data-chart=\"market\"></div>")+
      card('交易日明细',table(d.items.slice(0,35),[['date','日期',day],['stocks','证券数',num],['advancing','上涨',num],['declining','下跌',num],['equal_weight_return','等权收益',pct],['median_return','收益中位数',pct]]));
    emptyChart(root,'market',rows,'equal_weight_return','等权日收益');
  }

  async function stocks(){
    const code=ctx.stock||'000001';
    root.innerHTML=banner()+card('股票研究档案',`<form data-stock-form class="ql-form">${field('证券代码','code','text',code,'pattern="[0-9]{6}" required')}${field('历史日期','as_of','date',day(coverage.end))}<button type="submit">查看档案</button></form>`)+ '<div data-detail></div>';
    form('[data-stock-form]',async f=>{ctx.stock=f.code;await showStock(f.code,f.as_of);});
    await showStock(code,day(coverage.end));
  }

  async function showStock(code,date){
    const d=await api(`/stocks/${encodeURIComponent(code)}?as_of=${date}`);const host=root.querySelector('[data-detail]');const p=d.prices.at(-1);
    host.innerHTML=`<div class="ql-stats">${[['证券',code],['收盘价',num(p.close)],['当日回报',pct(p.ret_wd)],['成交额',num(p.amount)]].map(([a,b])=>`<div class="ql-stat"><span>${a}</span><strong>${b}</strong></div>`).join('')}</div>`+
      card('价格与成交',`<div data-chart="price"></div><p>${esc(d.pit_note)}</p>`)+
      `<div class="ql-grid">${card('财务与经营',fundamentals(d.events))}${card('预期与机构',eventsTable(d.events))}</div>`+
      card('研究记录',`<form data-watch-form class="ql-form">${field('关注依据','thesis','text','','required')}${field('失效条件','invalidation')}<button type="submit">加入观察列表</button></form>`+
        table(d.timeline,[['created_at','记录时间',fmtTime],['body','研究依据',v=>esc(v.thesis)],['body','失效条件',v=>esc(v.invalidation)]]));
    root.querySelector('[data-chart="price"]').append(candleChart(d.prices.slice(-120)));
    await stockModel(code,date,host);
    form('[data-watch-form]',async f=>{await api('/watch',{...f,instrument_id:code,as_of:date,action:'watch'});await showStock(code,date);});
  }

  function fundamentals(events){
    const defs={fs_income:[['revenue','营业收入'],['net_profit_parent','归母净利润']],fs_balance:[['total_assets','总资产'],['total_liabilities','总负债']],fs_cashflow:[['cfo_net','经营现金流']],fundamental_ttm:[['roe_ttm',"近一年净资产收益率"],['gross_margin_ttm',"近一年毛利率"]]};
    const rows=[];for(const [key,fields] of Object.entries(defs)){const e=events[key][0];if(e)for(const [f,label] of fields)rows.push({label,value:e[f],period:day(e.report_period),visible:day(e.effective_date)});}
    return table(rows,[['label','指标'],['value','值',num],['period','报告期'],['visible','可见日期']]);
  }
  function eventsTable(events){const rows=[];for(const [key,label,field] of [['consensus_daily',"下一财年净利润一致预期",'consensus_fy1_mean'],['holder_longline','长线机构比例','inst_longline_ratio'],['industry_classification','行业','industry_name']]){const e=events[key][0];if(e)rows.push({label,value:e[field],date:day(e.effective_date)});}return table(rows,[['label','项目'],['value','最新可见值'],['date','生效日期']]);}

  async function screen(){
    root.innerHTML=banner()+card('条件选股',`<form data-natural class="ql-form"><label class="ql-wide">描述筛选条件<textarea name="question" rows="2" required placeholder="40元以下，近20日不再下跌，处于60日价格区间下半部"></textarea></label><button type="submit">填写筛选条件</button></form><p>核对条件后再筛选。缺少所选财务指标的股票会被排除。</p>
      <form data-screen class="ql-form">${field('历史日期','as_of','date',day(coverage.end))}${field('最高价格','max_price','number',40,'min="0.01" step="0.01"')}${field('最低成交额（元）','min_amount','number',10000000,'min="0"')}${field('20日最低涨幅（比例）','min_momentum','number',-1,'min="-1" step="0.01"')}${field('60日最高价格位置','max_position','number',1,'min="0" max="1" step="0.05"')}${field("最低净资产收益率（可空）",'min_roe','number','','step="0.01"')}${field('最高负债率（可空）','max_debt','number','','min="0" max="1" step="0.01"')}${field('行业（可空）','industry')}<button type="submit">筛选</button></form>`)+card('候选证券','<div data-results></div>');
    form('[data-natural]',async f=>{const d=await api('/screen/interpret',{...f,as_of:root.querySelector('[name=as_of]').value});for(const [k,v] of Object.entries(d.criteria)){const input=root.querySelector(`[data-screen] [name="${k}"]`);if(input)input.value=v??'';}ctx.bus.emit('toast',{kind:'success',title:'条件已填入',detail:'请核对后执行筛选'});});
    form('[data-screen]',async f=>{for(const k of ['max_price','min_amount','min_momentum','max_position','min_roe','max_debt'])f[k]=f[k]===''?null:Number(f[k]);await showScreen(f);});
    await showScreen({as_of:day(coverage.end)});
  }
  async function showScreen(criteria){
    const d=await api('/screen/advanced',criteria);
    root.querySelector('[data-results]').innerHTML=`<p>${d.items.length} 个结果 · ${d.notes.map(esc).join(' ')}</p>`+table(d.items,[['instrument_id','证券',v=>`<button class="ql-link" data-stock="${esc(v)}">${esc(v)}</button>`],['industry_name','行业'],['close','收盘价',num],['momentum_20','20日涨幅',pct],['position_60','60日价格位置',pct],['roe_ttm',"净资产收益率",pct],['debt_ratio','负债率',pct],['amount','成交额',num]]);stockLinks();
  }

  async function stockModel(code,date,host){
    const d=await api(`/stocks/${code}/signals?as_of=${date}`);
    host.insertAdjacentHTML('beforeend',card('模型信号与后续结果',table(d.items,[['as_of','信号日期'],['run_id','模型'],['prediction','预测收益',pct],['realized','事后收益',pct],['mode','记录类型',v=>v==='shadow'?"盘后跟踪":'历史重放'],['forecast_id','因子',v=>`<button data-explain="${esc(v)}">查看</button>`]]))+'<div data-explanation></div>');
    bind('[data-explain]',async el=>{const r=await api(`/forecasts/${el.dataset.explain}/stocks/${code}`);root.querySelector('[data-explanation]').innerHTML=card('预测所用因子',`<p>预测收益 ${pct(r.prediction)} · 验证残差区间 ${pct(r.lower)} 至 ${pct(r.upper)}</p><p>${esc(r.note)}</p>`+table(r.features,[['label','因子'],['value','当日值',num],['percentile','市场分位',pct],['importance','模型重要性',pct]]));});
  }

  async function models(){
    const d=await api('/research');
    root.innerHTML=banner()+card("模型研究",`<p>计算并筛选 30 项价量因子，训练梯度提升树，检验样本外表现并模拟五日调仓。参数提交后不能修改。</p>
      <form data-research class="ql-form">${field('研究名称','name','text','A股五日收益研究','required')}${field('开始日期','start','date','2016-01-01')}${field('训练截止','train_end','date','2022-12-31')}${field('验证截止','valid_end','date','2024-12-31')}${field('评估截止','end','date','2025-12-31')}${field('计算核数','cores','number',4,'min="1" max="12"')}${field('树数量','trees','number',30,'min="5" max="150"')}${field('树深度','depth','number',3,'min="2" max="6"')}${field("股票数量上限（0 为全部）",'stock_limit','number',0,'min="0" max="7000"')}${field('组合股票数','top_n','number',30,'min="5" max="100"')}${field("单边成本（基点）",'cost_bps','number',15,'min="0" max="100"')}<button type="submit">创建研究任务</button></form>`)+
      card('实验版本',table(d.items,[['body','名称',v=>esc(v.config.name)],['state','状态',v=>state[v]||v],['metrics',"秩相关系数",v=>v?num(v.rank_ic):'—'],['id','操作',v=>`<button data-run="${esc(v)}">查看结果</button>`]]))+'<div data-rolling></div><div data-run-detail></div><div data-forecast-center></div>';
    await rollingCenter();
    await forecastCenter();
    form('[data-research]',async f=>{for(const k of ['cores','trees','depth','stock_limit','top_n','cost_bps'])f[k]=Number(f[k]);await task(await api('/research',f));});
    bind('[data-run]',el=>showRun(el.dataset.run));
  }

  async function rollingCenter(){
    const d=await api('/rolling');root.querySelector('[data-rolling]').innerHTML=card('滚动年度验证',`<p>提交后逐年训练。每轮按所选年数训练，再分别用一年验证和评估。</p><form data-rolling-form class="ql-form">${field('首个评估年','first_test_year','number',2023,'min="2005"')}${field('年度数量','folds','number',3,'min="2" max="6"')}${field('训练年数','train_years','number',3,'min="1" max="10"')}${field("股票数量上限（0 为全部）",'stock_limit','number',0,'min="0" max="7000"')}<button type="submit">创建滚动研究</button></form>`+'<div class="content-scroll rolling-results" tabindex="0" aria-label="滚动研究记录">'+d.items.map(r=>`<h4>${esc(r.config.name)} · ${r.completed}/${r.items.length} 折完成</h4><p>${esc(r.note)} · 平均秩相关系数 ${num(r.mean_rank_ic)}</p>`+table(r.items,[['config','评估年份',v=>esc(v.end.slice(0,4))],['metrics',"秩相关系数",v=>num(v?.rank_ic)],['simulation','累计收益',v=>pct(v?.total_return)],['simulation','最大回撤',v=>pct(v?.max_drawdown)],['run_id','详情',v=>`<button data-run="${v}">查看结果</button>`]])+(r.state!=='completed'?`<button data-rolling-resume="${r.batch_id}">继续剩余窗口</button>`:'')).join('')+'</div>');
    form('[data-rolling-form]',async f=>{for(const k of Object.keys(f))f[k]=Number(f[k]);await task(await api('/rolling',f));});
    bind('[data-rolling-resume]',async el=>task(await api(`/rolling/${el.dataset.rollingResume}/resume`,{})));
  }

  async function showRun(id){
    const d=await api(`/research/${id}`);const h=root.querySelector('[data-run-detail]');const m=d.metrics;
    h.innerHTML=card('实验配置',`<p>${esc(id)} · ${esc(d.config.dataset_id)}</p>${table(Object.entries(d.config.config).map(([key,value])=>({key:labelText(key),value})),[['key','设置'],['value','值']])}<a href="/api/lens/research/${id}/export" download>导出结果 JSON</a> <button data-print>打印 / 保存 PDF</button>${d.resumable&&!m?` <button data-resume="${id}">恢复未完成任务</button>`:''}`);
    bind('[data-print]',()=>window.print());bind('[data-resume]',async el=>task(await api(`/research/${el.dataset.resume}/resume`,{})));
    if(!m){h.innerHTML+=card("实验未完成","<p>请查看任务日志。进入评估阶段后不能重跑。</p>");return;}
    h.innerHTML+=`<div class="ql-stats">${[["秩相关系数",num(m.metrics.rank_ic)],["预测均方根误差",pct(m.metrics.rmse)],["零收益预测误差",pct(m.metrics.zero_rmse)],['区间覆盖率',pct(m.metrics.interval_coverage)]].map(([a,b])=>`<div class="ql-stat"><span>${a}</span><strong>${b}</strong></div>`).join('')}</div>`+
      `<div class="ql-grid">${card('每日排序能力','<div data-chart="ic"></div>')}${card('模拟净值','<div data-chart="nav"></div>')}</div>`+
      card('预测分组',table(m.groups,[['bucket','组别（低→高）'],['mean_return','平均五日收益',pct],['samples','样本数',num]]))+
      card('因子训练期表现',table(m.factors,[['label','因子'],['rank_ic',"平均秩相关系数",num],['ic_std',"相关系数标准差",num],['dates','有效日期',num]]))+
      (d.selection?card('因子筛选记录',table(d.selection.scores,[['label','因子'],['rank_ic',"训练期相关系数",num],['sign_stability','季度同向比例',pct],['selected','入选',v=>v?'是':'否'],['reason','依据']])):'')+
      card("模型因子重要性",table(m.importance.sort((a,b)=>b.importance-a.importance),[['label','因子'],['importance','重要性',pct]]))+
      card("生成预测",`<form data-infer class="ql-form">${field('预测日期','as_of','date',day(coverage.end))}<button type="submit">保存全市场预测</button></form><button data-drift>查看跟踪结果</button><div data-drift-detail></div>`)+
      card('历史预测',`<form data-predictions class="ql-form">${field('日期','as_of','date',d.config.config.end)}<button type="submit">查询预测</button></form><div data-predictions-list></div>`)+
      card("模拟假设与局限",`<ul>${[...m.notes,...(d.simulation?.assumptions||[])].map(s=>`<li>${esc(s)}</li>`).join('')}</ul><p>最大回撤 ${pct(m.simulation.max_drawdown)} · 综合费用 ${num(m.simulation.fees)} · 未成交 ${num(m.simulation.unfilled)}</p>${table(Object.entries(m.timings).map(([key,value])=>({key:labelText(key),value})),[['key','计算阶段'],['value','耗时（秒）']])}`);
    emptyChart(root,'ic',m.daily,'rank_ic',"每日秩相关系数");
    const chart=root.querySelector('[data-chart="nav"]');const nav=d.simulation.nav;
    chart.append(lineChart({series:[{label:'组合',values:nav.map(x=>x.nav),color:'var(--accent)'},{label:'市场等权',values:nav.map(x=>d.simulation.benchmark.find(b=>b.date===x.date)?.nav??null),color:'var(--fg-faint)'}],labels:nav.map(x=>x.date),height:250}));
    form('[data-predictions]',async f=>{const p=await api(`/research/${id}/predictions?as_of=${f.as_of}`);root.querySelector('[data-predictions-list]').innerHTML=table(p.items,[['instrument_id','证券',v=>`<button data-stock="${v}">${v}</button>`],['phase','用途'],['prediction','预测收益',pct],['lower','区间下界',pct],['upper','区间上界',pct],['label','事后实际收益',pct]]);stockLinks();});
    form('[data-infer]',async f=>task(await api(`/research/${id}/forecast`,f)));
    bind('[data-drift]',async()=>{const r=await api(`/research/${id}/drift`);root.querySelector('[data-drift-detail]').innerHTML=`<p>当日记录已到期 ${r.observations} 次 / 至少 ${r.minimum_observations} 次 · ${r.alert?'近期持续失效，请重新评估':'继续累计观察'} · 近期 秩相关系数 ${num(r.recent_rank_ic)}</p>`+table(r.items,[['as_of','日期'],['mode','类型',v=>v==='shadow'?'盘后跟踪':'历史重放'],['samples','样本数',num],['rank_ic',"秩相关系数",num],['mean_return','候选平均收益',pct]]);});
    bind('[data-print]',()=>window.print());
  }

  async function forecastCenter(){
    const [d,t,runs]=await Promise.all([api('/forecasts'),api('/tracking'),api('/research')]);const h=root.querySelector('[data-forecast-center]');
    h.innerHTML=card('每日模型跟踪',`<p>工作日按北京时间运行，程序需保持打开。无新行情时复用预测，只保存研究记录。</p><form data-tracking class="ql-form"><label>跟踪模型<select name="run_id"><option value="">请选择</option>${runs.items.filter(r=>r.state==='completed').map(r=>`<option value="${r.id}" ${t.config.run_id===r.id?'selected':''}>${esc(r.body.config.name)}</option>`).join('')}</select></label>${field('运行时间','daily_time','time',t.config.daily_time)}<label>自动运行<select name="enabled"><option value="false" ${!t.config.enabled?'selected':''}>关闭</option><option value="true" ${t.config.enabled?'selected':''}>启用</option></select></label><label>运行前更新数据<select name="sync_data"><option value="true" ${t.config.sync_data?'selected':''}>更新</option><option value="false" ${!t.config.sync_data?'selected':''}>使用已有数据</option></select></label><button type="submit">保存设置</button></form><button data-track-now>立即跟踪一次</button>`+table(t.results,[['created_at','运行时间',fmtTime],['body','数据日期',v=>esc(v.as_of)],['body','本次跟踪',v=>v.reused?'复用已有预测':'已保存新预测'],['body','补齐结果数',v=>num(v.observed)]]))+card('预测记录',`<button data-observe>补齐已到期结果</button> <button data-refresh-forecasts>刷新</button>`+table(d.items,[['body','日期',v=>esc(v.as_of)],['body','模型',v=>esc(v.run_id)],['body','类型',v=>v.mode==='shadow'?"盘后跟踪":'历史重放'],['id','操作',v=>`<button data-forecast="${v}">查看与组合测算</button>`]]))+'<div data-allocation></div>';
    form('[data-tracking]',async f=>{await api('/tracking',{...f,enabled:f.enabled==='true',sync_data:f.sync_data==='true'});ctx.bus.emit('toast',{kind:'success',title:'跟踪设置已保存'});});
    bind('[data-track-now]',async()=>task(await api('/tracking/run',{})));
    bind('[data-observe]',async()=>task(await api('/forecasts/observe',{})));bind('[data-refresh-forecasts]',forecastCenter);
    bind('[data-forecast]',async el=>{const f=await api(`/records/${el.dataset.forecast}`);const a=root.querySelector('[data-allocation]');a.innerHTML=card('预测排名与组合测算',`<p>按信号日收盘价估算股数，实际成交请另行录入。</p>`+table(f.body.items.slice(0,30),[['instrument_id','证券'],['close','参考价格',num],['prediction','预测收益',pct]])+`<form data-allocation-form class="ql-form">${field('资金','capital','number',1000000,'min="1"')}${field('单票最高权重','max_weight','number',0.1,'min="0.01" max="1" step="0.01"')}${field('现金保留比例','reserve','number',0.1,'min="0" max="0.99" step="0.01"')}${field('候选数量','top_n','number',20,'min="1" max="100"')}<button type="submit">计算组合</button></form><div data-targets></div>`);
      form('[data-allocation-form]',async v=>{for(const k of Object.keys(v))v[k]=Number(v[k]);const r=await api('/allocation',{...v,forecast_id:f.id});root.querySelector('[data-targets]').innerHTML=`<p>剩余现金 ${num(r.body.cash)} · ${esc(r.body.note)}</p>`+table(r.body.targets,[['instrument_id','证券'],['quantity','参考股数',num],['weight','权重',pct],['prediction','预测收益',pct]]);});});
  }

  async function watch(){const d=await api('/watch');root.innerHTML=banner()+card('我的观察列表',table(d.items,[['body','证券',v=>`<button data-stock="${esc(v.instrument_id)}">${esc(v.instrument_id)}</button>`],['body','关注依据',v=>esc(v.thesis)],['body','失效条件',v=>esc(v.invalidation)],['body','观察日期',v=>esc(v.as_of)],['body','操作',v=>`<button data-remove="${esc(v.instrument_id)}">移出</button>`]]));stockLinks();bind('[data-remove]',async el=>{await api('/watch',{instrument_id:el.dataset.remove,action:'remove',as_of:day(coverage.end),thesis:'用户移出观察列表'});await watch();});}

  async function assistant(){
    root.innerHTML=banner()+card('研究助手',`<p>结合股票数据或实验结果回答，保留原始依据，不操作账户。</p><form data-assistant class="ql-form">${field('证券代码（可选）','instrument_id')}${field('实验编号（可选）','run_id')}<label class="ql-wide">问题<textarea name="question" required rows="4" placeholder="这家公司的经营情况有哪些变化？哪些依据还不充分？"></textarea></label><button type="submit">分析</button></form><div data-answer></div>`);
    form('[data-assistant]',async f=>{root.querySelector('[data-answer]').textContent='正在分析…';const a=await api('/assistant',f);root.querySelector('[data-answer]').innerHTML=`<h3>模型解读</h3><pre class="ql-log">${esc(a.body.answer)}</pre><p>模型 ${esc(a.body.model)} · 证据 ${esc(a.body.evidence_id)}</p><button data-evidence="${a.body.evidence_id}">查看原始依据</button>`;bind('[data-evidence]',async el=>{const e=await api(`/records/${el.dataset.evidence}`);root.querySelector('[data-answer]').insertAdjacentHTML('beforeend',`<pre class="ql-log">${esc(JSON.stringify(e.body,null,2))}</pre>`);});});
  }

  async function journal(){const d=await api('/journal');root.innerHTML=card('研究时间线',table(d.items.sort((a,b)=>b.created_at.localeCompare(a.created_at)),[['created_at','时间',fmtTime],['kind','类型'],['body','记录',v=>esc(v.thesis||v.question)],['id','证据编号']]));}

  async function system(){
    const [d,c]=await Promise.all([api('/jobs'),api('/compute')]);
    root.innerHTML=card('任务中心',`<p>查看任务进度和错误。中断的研究可从模型详情继续；已进入评估阶段的实验除外。</p><button data-refresh>刷新</button>`+table(d.items,[['name','任务'],['status','状态',v=>state[v]||v],['created_at','启动时间',fmtTime],['task_id','日志',v=>`<button data-task="${v}">查看</button>`]]))+
      card('数据与完整性',`<button data-quality>检查数据质量</button> <button data-cache>重建缓存</button><div data-quality-result></div>`)+
      card("计算集群",`<p>在本机运行一个主节点、两个工作节点和历史服务，仅验证分布式计算。</p><button data-cluster-start ${c.nodes.some(n=>n.running)?'disabled':''}>启动集群</button> <button data-cluster-stop>停止集群</button> <a href="http://127.0.0.1:8090" target="_blank" rel="noreferrer">集群界面 ↗</a> <a href="http://127.0.0.1:8093" target="_blank" rel="noreferrer">任务历史 ↗</a>`+table(c.nodes,[['name','节点'],['pid','进程'],['running','状态',v=>v?'运行中':'未运行'],['name','日志',v=>`<button data-node-log="${v}">查看日志</button>`]])+'<pre data-node-log-text tabindex="0" aria-label="节点日志"></pre>')+
      card('服务错误日志','<button data-errors>查看最近错误</button><pre data-error-log tabindex="0" aria-label="服务错误日志"></pre>');
    bind('[data-node-log]',async el=>{const r=await api(`/compute/${el.dataset.nodeLog}/log`);root.querySelector('[data-node-log-text]').textContent=r.lines.join('\n')||'暂无日志';});
    bind('[data-errors]',async()=>{const r=await api('/errors');root.querySelector('[data-error-log]').textContent=r.lines.join('\n')||'暂无错误记录';});
    bind('[data-refresh]',system);bind('[data-task]',el=>task({task_id:el.dataset.task}));bind('[data-quality]',async()=>task(await api('/quality',{})));bind('[data-cache]',async()=>task(await api('/cache',{})));
    bind('[data-cluster-start]',async()=>task(await api('/compute/start',{})));bind('[data-cluster-stop]',async()=>{await api('/compute/stop',{});await system();});
    const q=await api('/quality/latest');root.querySelector('[data-quality-result]').innerHTML=q?`<p>最近检查：${q.body.passed?'通过':'有异常'} · ${num(q.body.rows)} 行 · 重复 ${num(q.body.duplicates)} · 价格异常 ${num(q.body.invalid_ohlc)} · 未匹配 ${num(q.body.unmatched)}</p>`:'<p>尚未检查</p>';
  }

  ctx.bus.on('route',id=>{if(id===route)return load();});
  ctx.bus.on('stock:selected',()=>{if(route==='stocks'&&!root.hidden)load();});
  if(!root.hidden)await load();
}
