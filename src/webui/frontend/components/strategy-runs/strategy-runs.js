import {newestFirst} from '../../core/sort.js';
import {hydrateIcons} from '../../core/icons.js';
import {renderState} from '../state-card/state-card.js';
import {formDialog} from '../confirm-dialog/form-dialog.js';
import {toastError} from '../../core/toast.js';
import {badge, fmtPct, fmtNum, fmtTime, fmtLogTime, signClass} from '../../core/fmt.js';
import {el, tableEl} from '../../core/dom.js';
import {captureLogScroll, updateTaskLog} from '../../core/task-log.js';
import {createResearchReport} from '../../core/research-report.js';
import {metricGrid, researchFacts, factorWeights, appendExperimentSummary} from '../../core/research-summary.js';
import {createSessionFilter} from '../experiments/session-filter.js';
import {completedStrategyRuns} from '../experiments/results.js';

const MODEL = {auto: '自动研究', equal: '等权', icir: 'ICIR', model_linear: '线性回归', model_ridge: '岭回归', model_logistic: '逻辑回归'};
const STATE = {running: '进行中', completed: '已完成', failed: '失败', interrupted: '已中断'};
const tone = state => state === 'completed' ? 'safe' : state === 'running' ? 'accent' : 'danger';

export async function mount(root, ctx) {
  await hydrateIcons(root);
  const list = root.querySelector('[data-evolution-list]'), detail = root.querySelector('[data-evolution-detail]');
  const emptyHost = root.querySelector('[data-experiments-empty]');
  let revealSelection = !!ctx.strategyExperimentId;
  let items = [], selected = ctx.strategyExperimentId || '', request = 0, listRequest = 0, timer;
  const filter = createSessionFilter(root.querySelector('[data-session-filter]'), {
    ctx, key: 'strategyExperimentFilter', allLabel: '全部策略', ariaLabel: '按策略筛选实验会话',
    onChange: () => { selected = ''; revealSelection = false; ++request; clearTimeout(timer); detail.replaceChildren(); renderList(true); },
  });
  root.querySelector('[data-evolution-start]').onclick = newExperiment;
  ctx.bus.on('route', id => { if (id === 'strategy-research') load(); else clearTimeout(timer); });
  ctx.bus.on('subtab', data => { if (data.page === 'strategy-research' && data.tab === 'experiments') load(); });
  ctx.bus.on('strategy:experiment', id => { selected = id; revealSelection = true; load(); });
  root.closest('.view')?.querySelector('#strategy-research-tab-experiments')?.addEventListener('click', load);
  await load();

  async function load() {
    const id = ++listRequest;
    if (filter.set(ctx.strategyExperimentFilter, ctx.strategyExperimentFilterLabel)) {
      ++request; clearTimeout(timer); detail.replaceChildren();
      list.querySelector('.evolution-sessions')?.setPage(1);
    }
    try {
      const [data, strategies] = await Promise.allSettled([ctx.api.strategyExperiments(), ctx.api.strategies()]);
      if (id !== listRequest) return;
      if (data.status === 'rejected') throw data.reason;
      items = newestFirst(data.value.items || [], ['started_at', 'created_at', 'updated_at']);
      filter.update([
        ...(strategies.status === 'fulfilled' ? strategies.value.items || [] : []).map(row => [row.profile_name, row.display_name || row.profile_name]),
        ...items.map(row => [row.profile_name, row.display_name || row.profile_name]),
      ]);
      const requested = revealSelection && items.find(row => row.experiment_id === selected);
      if (requested && filter.value && requested.profile_name !== filter.value) filter.set(requested.profile_name, requested.display_name);
      await renderList();
    } catch (error) {
      if (id !== listRequest) return;
      emptyHost.hidden = true; list.hidden = false;
      await renderState(list, {kind: 'error', title: '实验会话加载失败', detail: error.message, retry: load});
    }
  }

  async function renderList(resetPage = false) {
    const visible = items.filter(row => !filter.value || row.profile_name === filter.value);
    emptyHost.hidden = visible.length > 0;
    list.hidden = !visible.length;
    emptyHost.querySelector('h3').textContent = filter.value ? '该策略暂无实验' : '暂无实验';
    if (!visible.length) {
      ++request; clearTimeout(timer); detail.replaceChildren();
      selected = ''; list.replaceChildren();
      return;
    }
    if (!visible.some(row => row.experiment_id === selected)) {
      selected = visible[0].experiment_id;
      revealSelection = true;
    }
    const pageState = list.querySelector('.evolution-sessions')?.pageState?.() || {};
    const table = tableEl([
      {key: 'display_name', label: '实验'},
      {key: 'model', label: '模型', format: value => MODEL[value] || value},
      {key: 'started_at', label: '开始时间', format: fmtTime},
      {key: 'state', label: '状态', format: value => badge(STATE[value] || value, tone(value))},
      {key: 'phase', label: '进度', format: value => value || '—'},
    ], visible, {...pageState, ...(resetPage ? {page: 1} : {}), pagination: true,
      ...(revealSelection ? {page: Math.floor(visible.findIndex(row => row.experiment_id === selected) / (pageState.pageSize || 10)) + 1} : {}),
      onRowClick: row => { selected = row.experiment_id; markSelection(); show(); },
      onRowRender: (row, item) => { row.dataset.researchId = item.experiment_id; row.title = item.experiment_id;
        const active = selected === item.experiment_id; row.classList.toggle('is-active', active); row.setAttribute('aria-selected', String(active));
      },
    });
    revealSelection = false;
    table.classList.add('evolution-sessions'); list.replaceChildren(table); markSelection(); await show();
  }

  function markSelection() {
    list.querySelectorAll('[data-research-id]').forEach(row => {
      row.classList.toggle('is-active', row.dataset.researchId === selected);
      row.setAttribute('aria-selected', String(row.dataset.researchId === selected));
    });
  }

  async function show() {
    clearTimeout(timer);
    const id = ++request, sessionId = selected;
    try {
      const data = await ctx.api.strategyExperiment(sessionId);
      if (id !== request || sessionId !== selected) return;
      const task = data.task, spec = data.spec, result = data.result;
      const previousLog = detail.querySelector('[data-task-log]');
      const retainedLog = previousLog?.dataset.subject === sessionId ? previousLog : null;
      const restoreScroll = captureLogScroll(retainedLog);
      const intro = el('article', 'card experiment-intro');
      intro.append(el('h3', '', data.display_name));
      const snapshot = result?.account_snapshot;
      intro.append(researchFacts([
        ['关联账户', snapshot?.alias], ['研究资金', snapshot ? `${fmtNum(snapshot.capital, 0)} 元` : null],
        ['配权', MODEL[result?.selected_candidate?.method || spec.model]],
        ['持仓上限', `${result?.selected_candidate?.max_positions ?? spec.top_n} 只`],
        ['调仓', {weekly: '每周', monthly: '每月', quarterly: '每季度'}[result?.selected_candidate?.frequency || spec.rebalance_frequency]],
        ['训练区间', `${spec.train_start} 至 ${spec.train_end}`], ['回测区间', `${spec.start} 至 ${spec.end}`],
      ]));
      const progress = el('article', 'card evolution-progress'); progress.dataset.sessionProgress = '';
      const head = el('div', 'detail-card__head');
      head.append(el('h4', '', '实验进度'), badge(STATE[task.status] || task.status, tone(task.status)));
      progress.append(head);
      if (task.status === 'running') {
        progress.append(el('p', 'experiment-phase', task.progress?.step || '等待计算资源'));
        const track = el('div', 'evolution-progress__track'), fill = el('span', 'evolution-progress__fill');
        const percent = Math.max(0, Math.min(100, Number(task.progress?.percent || 0)));
        track.setAttribute('role', 'progressbar'); track.setAttribute('aria-label', '实验进度');
        track.setAttribute('aria-valuemin', '0'); track.setAttribute('aria-valuemax', '100'); track.setAttribute('aria-valuenow', String(Math.round(percent)));
        fill.style.width = `${percent}%`; track.append(fill); progress.append(track);
      }
      const log = updateTaskLog(retainedLog, task.log?.length ? task.log : [`[${fmtLogTime(task.created_at)}]  ${task.progress?.step || '等待实验开始'}`], {subject: sessionId});
      if (log) progress.append(log);
      if (task.error) progress.append(el('p', 'experiment-error', task.error));
      const cards = [intro];
      if (result?.state === 'completed') {
        const summary = el('div', 'research-summary-row');
        summary.append(resultCard(result), factorWeights(result, data.factor_display_names));
        cards.push(summary);
        const backtests = backtestCard(result, spec);
        if (backtests) cards.push(backtests);
      }
      cards.push(progress);
      if (result?.state === 'completed') cards.push(createResearchReport(result.report));
      detail.replaceChildren(...cards); restoreScroll();
      const item = items.find(row => row.experiment_id === sessionId);
      if (item) Object.assign(item, {state: task.status, phase: task.progress?.step || item.phase});
      const row = [...list.querySelectorAll('[data-research-id]')].find(node => node.dataset.researchId === sessionId);
      if (row) { row.cells[3].replaceChildren(badge(STATE[task.status], tone(task.status))); row.cells[4].textContent = task.progress?.step || '—'; }
      if (task.status === 'running') timer = setTimeout(() => {
        if (!root.closest('.view')?.hidden && !root.closest('[role=tabpanel]')?.hidden && !document.hidden) show();
      }, 2000);
    } catch (error) {
      if (id !== request) return;
      await renderState(detail, {kind: 'error', title: '实验详情加载失败', detail: error.message, retry: show});
    }
  }

  function backtestCard(result, spec) {
    const rows = completedStrategyRuns(result, spec);
    if (!rows.length) return null;
    const card = el('article', 'card experiment-backtests'); card.append(el('h4', '', '已完成回测'));
    card.append(tableEl([
      {key: 'candidate', label: '方案', format: (value, row) => [
        value?.method ? MODEL[value.method] || value.method : null,
        value?.max_positions != null ? `${value.max_positions} 只` : null,
        {weekly: '每周', monthly: '每月', quarterly: '每季度'}[value?.frequency],
        row.baseline ? '基线' : null,
      ].filter(Boolean).join(' · ') || '—'},
      {key: 'stage', label: '阶段', format: (value, row) => value === 'training' ? `训练内第 ${row.fold} 段` : value === 'final_baseline' ? '最终基线评估' : '最终评估'},
      {key: 'range', label: '区间', format: value => value || '—'},
      {key: 'metrics', label: '年化收益', num: true, format: value => fmtPct(value?.returns?.annualized_return)},
      {key: 'metrics', label: '年化超额', num: true, format: value => fmtPct(value?.returns?.annualized_excess_return)},
      {key: 'metrics', label: '最大回撤', num: true, format: value => fmtPct(value?.risk?.max_drawdown)},
      {key: 'run_id', label: '回测', format: runId => {
        const link = el('button', 'experiments__run-link', runId); link.type = 'button';
        link.onclick = () => { ctx.bus.emit('backtest:open', runId); ctx.bus.emit('route', 'backtest'); }; return link;
      }},
    ], rows, {pagination: false}));
    return card;
  }

  function resultCard(result) {
    const card = el('article', 'card experiment-results');
    const head = el('div', 'detail-card__head experiment-results__head');
    head.append(el('h4', '', '实验结果')); card.append(head);
    head.append(badge(result.accepted ? '通过启用条件' : '未通过启用条件', result.accepted ? 'safe' : 'warn'));
    appendExperimentSummary(card, result.experiment_summary);
    card.append(metricGrid(result.metrics));
    return card;
  }

  async function newExperiment() {
    try {
      const data = await ctx.api.strategies();
      const eligible = data.items.filter(row => row.status !== 'active' && row.task?.status !== 'running');
      if (!eligible.length) { ctx.bus.emit('route', 'strategy-research/research'); return; }
      const values = await formDialog({title: '新建策略实验', submitLabel: '开始实验', fields: [
        {key: 'profile_name', label: '策略方案', kind: 'select', value: eligible[0].profile_name, options: eligible.map(row => ({value: row.profile_name, label: row.display_name}))},
      ]});
      if (!values) return;
      const started = await ctx.api.strategyResearch(values.profile_name); selected = started.task_id; revealSelection = true; await load();
    } catch (error) { toastError(ctx.bus, error, '策略实验启动失败'); }
  }
}
