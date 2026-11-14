import {newestFirst} from '../../core/sort.js';
import {hydrateIcons} from '../../core/icons.js';
import { renderState } from "../state-card/state-card.js";
import { formDialog } from "../confirm-dialog/form-dialog.js";
import { toastError } from "../../core/toast.js";
import { badge, fmtPct, fmtTime, fmtLogTime, signClass } from "../../core/fmt.js";
import { el, tableEl } from "../../core/dom.js";
import { captureLogScroll, updateTaskLog } from "../../core/task-log.js";
import { createResearchReport } from "../../core/research-report.js";
import {metricGrid, researchFacts, appendExperimentSummary} from '../../core/research-summary.js';
import {completedRuns, experimentOutcome} from './results.js';
import {createSessionFilter} from './session-filter.js';

const CLASSIFICATION_LABEL = {
  validation_generalized: "泛化有效", recently_decayed: "近期衰减",
  in_sample_only: "仅样本内有效", not_effective: "无效",
};
const CLASSIFICATION_TONE = {
  validation_generalized: 'safe', recently_decayed: 'warn',
  in_sample_only: 'warn', not_effective: 'danger',
};
const WINDOW_LABEL = {train: '训练', fixed_train: '训练', validation: '验证', validation_continuous: '连续验证', test: '测试', locked_test: '锁定测试'};
const EVENT_LABEL = {
  baseline_started: '基线实验已登记', baseline_completed: '基线回测完成',
  training_backtest_completed: '训练回测完成', window_completed: '窗口回测完成',
  candidate_created: '生成新候选因子', version_evaluated: '候选评估完成',
  round_completed: '一轮实验完成', round_failed: '本轮实验失败',
  session_finished: '实验结束', session_failed: '实验失败',
  locked_test_completed: '锁定测试完成', conditional_training_batch_completed: '训练筛选完成',
};
function windowText(value) {
  if (!value) return '';
  return typeof value === 'object' ? `${value.start} 至 ${value.end}` : String(value).replace('..', ' 至 ');
}
function pctNode(value) {
  return value == null ? '—' : el('span', signClass(value), fmtPct(value));
}
function latestPayload(events, type) {
  return [...events].reverse().find(event => event.event_type === type)?.payload || null;
}

export async function mount(root, ctx) {
  await hydrateIcons(root);
  const evolutionList = root.querySelector('[data-evolution-list]');
  const emptyHost = root.querySelector('[data-experiments-empty]');
  const evolutionDetail = root.querySelector('[data-evolution-detail]');
  const startButton = root.querySelector('[data-evolution-start]');
  let evolutionItems = [], records = [], selectedId = '', detailPoll, listRequest = 0, evolutionRequest = 0;
  const evolutionCache = new Map(), evolutionPending = new Map(), reportCache = new Map();
  const taskPolls = new Map(), polledResearchIds = new Set();
  const filter = createSessionFilter(root.querySelector('[data-session-filter]'), {
    ctx, key: 'factorExperimentFilter', allLabel: '全部因子', ariaLabel: '按因子筛选实验会话',
    onChange: () => { resetSelection(); renderEvolutions(true); },
  });
  startButton.addEventListener('click', startEvolution);
  ctx.bus.on('route', id => {
    if (id === 'experiments' || id === 'factors/experiments') loadEvolutions();
  });
  ctx.bus.on('factor:experiments', loadEvolutions);
  ctx.bus.on('subtab', data => { if (data.page === 'factors' && data.tab === 'experiments') loadEvolutions(); });
  root.closest('.view')?.querySelector('#factors-tab-experiments')?.addEventListener('click', loadEvolutions);
  await loadEvolutions();

  function resetSelection() {
    selectedId = ''; ++evolutionRequest; clearTimeout(detailPoll);
    evolutionDetail.replaceChildren();
    evolutionList.querySelector('.evolution-sessions')?.setPage(1);
  }

  async function loadEvolutions() {
    const request = ++listRequest;
    if (filter.set(ctx.factorExperimentFilter, ctx.factorExperimentFilterLabel)) resetSelection();
    try {
      const [sessions, experiments, factors] = await Promise.allSettled([
        ctx.api.factorEvolutions(), ctx.api.experiments({subject_type: 'factor'}), ctx.api.factors(),
      ]);
      if (request !== listRequest) return;
      if (sessions.status === 'rejected') throw sessions.reason;
      if (experiments.status === 'fulfilled') records = (experiments.value.items || []).filter(row => !row.subject_type || row.subject_type === 'factor');
      evolutionItems = sessions.value.items || [];
      // Keep standalone, manually registered experiments accessible through the same view.
      if (!evolutionItems.length) evolutionItems = records.filter(row => !row.parent_exp_id).map(row => ({
        research_id: row.exp_id, base_factor: row.subject_name,
        base_factor_display_name: row.subject_display_name || row.subject_name,
        profile_name: row.profile_name, profile_display_name: row.profile_display_name,
        started_at: row.created_at, state: row.status === 'registered' ? 'created' : row.status === 'aborted' ? 'failed' : 'completed',
        phase: row.status === 'registered' ? '已登记，尚未开始运行' : '', record: row,
      }));
      evolutionItems = newestFirst(evolutionItems, ['started_at', 'created_at', 'updated_at']);
      filter.update([
        ...(factors.status === 'fulfilled' ? factors.value.items || [] : []).map(row => [row.name, row.display_name || row.name]),
        ...evolutionItems.map(row => [row.base_factor, row.base_factor_display_name || row.base_factor]),
      ]);
      renderEvolutions();
    } catch (err) {
      if (request !== listRequest) return;
      emptyHost.hidden = true;
      evolutionList.hidden = false;
      const slot = el('div'); evolutionList.replaceChildren(slot);
      await renderState(slot, {kind: 'error', title: '实验会话加载失败', detail: err.message, retry: loadEvolutions});
    }
  }

  function renderEvolutions(resetPage = false) {
    const visible = evolutionItems.filter(item => !filter.value || item.base_factor === filter.value);
    emptyHost.hidden = visible.length > 0;
    evolutionList.hidden = !visible.length;
    emptyHost.querySelector('h3').textContent = filter.value ? '该因子暂无实验' : '暂无实验';
    if (!visible.length) {
      evolutionList.replaceChildren();
      evolutionDetail.replaceChildren();
      selectedId = ''; ++evolutionRequest; clearTimeout(detailPoll);
      return;
    }
    if (!visible.some(item => item.research_id === selectedId)) selectedId = visible[0].research_id;
    const pageState = evolutionList.querySelector('.evolution-sessions')?.pageState?.() || {};
    const table = tableEl([
      {key: 'base_factor_display_name', label: '实验'},
      {key: 'model_name', label: '模型', format: value => value || '—'},
      {key: 'started_at', label: '开始时间', format: fmtTime},
      {key: 'state', label: '状态', format: (_value, row) => badge(...stateBadgeArgs(row))},
      {key: 'phase', label: '进度', format: value => value || '—'},
    ], visible, {
      ...pageState, ...(resetPage ? {page: 1} : {}), pagination: true, onRowClick: item => selectEvolution(item.research_id),
      onRowRender: (row, item) => {
        row.dataset.researchId = item.research_id; row.title = item.research_id;
        row.classList.toggle('is-active', selectedId === item.research_id);
        row.setAttribute('aria-selected', String(selectedId === item.research_id));
      },
    });
    table.classList.add('evolution-sessions');
    evolutionList.replaceChildren(table);
    showEvolution(selectedId, true, false);
  }

  async function selectEvolution(id) {
    if (selectedId === id) return;
    selectedId = id; clearTimeout(detailPoll); ++evolutionRequest;
    evolutionList.querySelectorAll('[data-research-id]').forEach(row => {
      const active = row.dataset.researchId === id;
      row.classList.toggle('is-active', active); row.setAttribute('aria-selected', String(active));
    });
    if (!evolutionCache.has(id)) evolutionDetail.replaceChildren(el('p', '', '正在读取实验…'));
    await showEvolution(id, true, true);
  }

  function belongsToSession(data, row, id) {
    const ids = new Set([...(data.experiment_ids || []), ...(data.events || []).flatMap(event =>
      [event.payload?.experiment_id, event.payload?.result?.experiment_id]).filter(Boolean)]);
    if (ids.size) return ids.has(row.exp_id);
    const start = data.status?.started_at || data.events?.find(event => event.event_type === 'session_created')?.created_at;
    if (!start || row.subject_name !== data.config?.base_factor || row.profile_name !== data.config?.profile_name) return false;
    const later = evolutionItems.filter(item => item.research_id !== id && item.base_factor === row.subject_name && item.started_at > start)
      .map(item => item.started_at).sort()[0];
    const end = [later, data.status?.finished_at].filter(Boolean).sort()[0];
    return row.created_at >= start && (!end || row.created_at < end);
  }

  async function showEvolution(id, schedule = true, useCache = false) {
    if (selectedId !== id) return;
    const request = ++evolutionRequest;
    try {
      let data = useCache && evolutionCache.get(id);
      if (data?.task?.status === 'running') data = null;
      const session = evolutionItems.find(item => item.research_id === id) || {};
      if (!data) {
        if (!evolutionPending.has(id)) {
          const pending = (session.record ? ctx.api.experiment(session.record.exp_id).then(detail => ({
            status: {...session, research_id: id, state: detail.events?.some(event => event.event_type === 'abort') ? 'failed' : detail.events?.some(event => ['result', 'conclusion'].includes(event.event_type)) ? 'completed' : session.state}, config: {
              base_factor: detail.experiment.subject_name, profile_name: detail.experiment.profile_name,
              train_window: detail.experiment.train_window, validation_window: detail.experiment.valid_window,
              test_window: detail.experiment.test_window,
            }, events: detail.events || [], experiment_ids: [id], record: detail.experiment,
          })) : ctx.api.factorEvolution(id)).then(value => { evolutionCache.set(id, value); return value; })
            .finally(() => evolutionPending.delete(id));
          evolutionPending.set(id, pending);
        }
        data = await evolutionPending.get(id);
      }
      if (selectedId !== id || request !== evolutionRequest) return;
      const status = data.status || {}, events = data.events || [], config = data.config || {};
      const state = data.lifecycle_state === 'paused' || data.task?.status === 'interrupted' ? 'paused'
        : data.task?.status === 'failed' ? 'failed' : data.task?.progress?.state === 'queued' ? 'queued' : status.state;
      const displayStatus = {...status, state};
      Object.assign(session, {state, phase: status.phase || session.phase, decision: status.decision});
      const ended = ['completed', 'failed', 'cancelled', 'aborted'].includes(state);
      const linked = data.record ? [data.record] : records.filter(row => belongsToSession(data, row, id));
      const record = linked.find(row => !row.parent_exp_id) || linked[0] || {};
      const name = data.base_factor_display_name || session.base_factor_display_name || record.subject_display_name || config.base_factor || id;
      const row = [...evolutionList.querySelectorAll('[data-research-id]')].find(node => node.dataset.researchId === id);
      if (row) { row.cells[3].replaceChildren(badge(...stateBadgeArgs(displayStatus))); row.cells[4].textContent = status.phase || session.phase || '—'; }
      const previousLog = evolutionDetail.querySelector('[data-task-log]');
      const retainedLog = previousLog?.dataset.subject === id ? previousLog : null;
      const restoreScroll = captureLogScroll(retainedLog);
      const intro = el('article', 'card experiment-intro');
      const head = el('div', 'detail-card__head');
      head.append(el('h3', '', name)); intro.append(head);
      const description = config.hypothesis || config.description || record.change_reason || record.change_summary;
      if (description) intro.append(el('p', 'experiment-intro__description', description));
      intro.append(researchFacts([['训练区间', windowText(config.train_window)], ['验证区间', windowText(config.validation_window)],
        ['锁定测试区间', windowText(config.test_window)]]));
      const progress = el('article', 'card evolution-progress'); progress.dataset.sessionProgress = '';
      const progressHead = el('div', 'detail-card__head');
      progressHead.append(el('h4', '', '实验进度'), badge(...stateBadgeArgs(displayStatus)));
      if (state === 'paused') {
        const resume = el('button', 'btn btn--ghost', '继续实验'); resume.type = 'button';
        resume.onclick = async () => {
          resume.disabled = true;
          try { const started = await ctx.api.factorEvolutionResume(id); pollEvolution(id, started.task_id); }
          catch (err) { toastError(ctx.bus, err, '实验恢复失败'); resume.disabled = false; }
        };
        progressHead.append(resume);
      }
      progress.append(progressHead);
      if (!ended && status.phase) progress.append(el('p', 'experiment-phase', status.phase));
      if (state === 'running') {
        const track = el('div', 'evolution-progress__track'), fill = el('span', 'evolution-progress__fill');
        const percent = Math.max(0, Math.min(100, Number(status.progress || 0) * 100)); fill.style.width = `${percent}%`;
        track.setAttribute('role', 'progressbar'); track.setAttribute('aria-label', '实验进度');
        track.setAttribute('aria-valuemin', '0'); track.setAttribute('aria-valuemax', '100'); track.setAttribute('aria-valuenow', String(Math.round(percent)));
        track.append(fill); progress.append(track);
      }
      const eventLines = events.map(event => {
        const payload = event.payload || {}, runId = payload.run_id || payload.result?.run_id;
        return `[${fmtLogTime(event.created_at)}]  ${payload.message || EVENT_LABEL[event.event_type] || event.event_type}${runId ? ` · ${runId}` : ''}`;
      });
      const lines = data.task?.log?.length ? data.task.log : eventLines;
      const log = updateTaskLog(retainedLog, lines.length ? lines : [`[${fmtLogTime(status.updated_at || data.task?.created_at || status.started_at || session.started_at)}]  ${status.phase || session.phase || '等待实验开始'}`], {subject: id});
      if (log) progress.append(log);
      const error = data.task?.error || status.error;
      if (error && state !== 'completed') progress.append(el('p', 'experiment-error', String(error).split('\n')[0]));
      const result = ended || completedRuns(data, linked).length ? resultCard(data, linked) : null;
      const report = ended ? reportCard(data) : null;
      const backtests = backtestCard(data, linked);
      evolutionDetail.replaceChildren(...[intro, result, backtests, progress, report].filter(Boolean)); restoreScroll();
      clearTimeout(detailPoll);
      if (schedule && (['running', 'queued', 'created'].includes(state) || data.task?.status === 'running') && !session.record && !polledResearchIds.has(id)) {
        detailPoll = setTimeout(() => {
          if (root.closest('.view')?.hidden || document.hidden) return;
          showEvolution(id);
        }, 2000);
      }
    } catch (err) {
      if (selectedId !== id || request !== evolutionRequest) return;
      toastError(ctx.bus, err, '实验详情加载失败');
      const retry = el('button', 'btn btn--ghost', '重新读取实验'); retry.type = 'button'; retry.onclick = () => showEvolution(id);
      if (!evolutionDetail.querySelector('article')) evolutionDetail.replaceChildren(retry);
    }
  }

  function resultCard(data, linked) {
    const card = el('article', 'card experiment-results');
    const head = el('div', 'detail-card__head experiment-results__head');
    head.append(el('h4', '', '实验结果')); card.append(head);
    const outcome = experimentOutcome(data, linked), events = data.events || [];
    const assessment = outcome.assessment || {};
    const classification = latestPayload(events, 'locked_test_completed')?.generalization_classification || assessment.classification;
    if (classification) head.append(badge(CLASSIFICATION_LABEL[classification] || classification, CLASSIFICATION_TONE[classification] || 'accent'));
    appendExperimentSummary(card, outcome.summary);
    const metrics = el('div', 'experiment-results__metrics');
    for (const [label, value, group] of [['训练年化超额中位数', assessment.train_median_excess_return, 'returns'], ['验证年化超额中位数', assessment.validation_median_excess_return, 'returns'], ['泛化落差', assessment.generalization_gap, 'risk']]) {
      if (value == null) continue;
      const metric = el('div', 'metric-group metric'); metric.dataset.metricGroup = group;
      const number = el('span', `metric__value ${group === 'returns' ? signClass(value) : ''}`, fmtPct(value));
      metric.append(el('span', 'metric__label', label), number); metrics.append(metric);
    }
    if (metrics.childElementCount) card.append(metrics);
    else if (outcome.metrics) card.append(el('p', 'research-note', outcome.scope), metricGrid(outcome.metrics));
    return card;
  }

  function reportCard(data) {
    const id = data.research_report?.object_id;
    if (!id) return null;
    const retained = evolutionDetail.querySelector('.research-report');
    if (retained?.dataset.reportId === id) return retained;
    const card = createResearchReport('正在读取报告…'); card.dataset.reportId = id;
    const loadReport = async () => {
      const body = card.querySelector('.research-report__body');
      body.replaceChildren(el('p', '', '正在读取报告…'));
      if (!reportCache.has(id)) reportCache.set(id, ctx.api.researchReportContent(id).catch(err => { reportCache.delete(id); throw err; }));
      try {
        const content = await reportCache.get(id);
        card.replaceChildren(...createResearchReport(content.content).childNodes);
      } catch (err) {
        const retry = el('button', 'btn btn--ghost', '重新读取报告'); retry.type = 'button'; retry.onclick = loadReport;
        body.replaceChildren(el('p', 'experiment-error', err.message), retry);
      }
    };
    loadReport();
    return card;
  }

  function backtestCard(data, linked) {
    const rows = completedRuns(data, linked);
    if (!rows.length) return null;
    const card = el('article', 'card experiment-backtests'); card.append(el('h4', '', '已完成回测'));
    card.append(tableEl([
      {key: 'version', label: '方案', format: value => value || '—'},
      {key: 'window', label: '区间', format: (value, row) => [WINDOW_LABEL[value] || value, row.range || windowText(row.metrics?.window), row.fold].filter(Boolean).join(' · ') || '—'},
      {key: 'metrics', label: '年化收益', num: true, format: value => pctNode(value?.returns?.annualized_return)},
      {key: 'metrics', label: '年化超额', num: true, format: (value, row) => pctNode(value?.returns?.annualized_excess_return ?? row.annualized_excess_return)},
      {key: 'metrics', label: '最大回撤', num: true, format: value => value?.risk?.max_drawdown == null ? '—' : fmtPct(value.risk.max_drawdown)},
      {key: 'run_id', label: '回测', format: runLink},
    ], rows, {pagination: false}));
    return card;
  }
  async function startEvolution() {
    if (startButton.disabled) return;
    startButton.disabled = true;
    try {
      const factorData = await ctx.api.factors();
      const factors = (factorData.items || []).filter((item) => item.status !== "disabled");
      if (!factors.length) {
        throw new Error("暂无可研究的因子，请先在因子库中添加因子。");
      }
      const values = await formDialog({
        title: "新建实验",
        message:
          "选择一个因子，由项目配置的模型自动生成候选、回测和复盘。仅通过验证的改进版本才会更新原因子。",
        submitLabel: "开始自我进化",
        fields: [
          {
            key: "base_factor",
            label: "研究因子",
            kind: "select",
            required: true,
            value: factors.some(item => item.name === filter.value) ? filter.value : factors[0].name,
            options: factors.map((item) => ({
              value: item.name,
              label: item.display_name || item.name,
            })),
          },
        ],
      });
      if (!values) return;
      const started = await ctx.api.factorEvolutionStart({base_factor: values.base_factor});
      ctx.bus.emit("toast", { kind: "info", title: `自我进化已启动：${started.research_id}` });
      if (filter.value) filter.set(values.base_factor, factors.find(item => item.name === values.base_factor)?.display_name);
      selectedId = started.research_id;
      clearTimeout(detailPoll);
      ++evolutionRequest;
      evolutionCache.delete(selectedId);
      evolutionList.querySelector('.evolution-sessions')?.setPage(1);
      await loadEvolutions();
      pollEvolution(started.research_id, started.task_id);
    } catch (err) {
      toastError(ctx.bus, err, "自动进化启动失败");
    } finally {
      startButton.disabled = false;
    }
  }

  function pollEvolution(researchId, taskId) {
    clearTimeout(taskPolls.get(taskId)); polledResearchIds.add(researchId);
    async function tick() {
      try {
        const detail = await ctx.api.factorEvolution(researchId);
        const task = detail.task;
        if (!task || task.task_id !== taskId) {
          taskPolls.delete(taskId); polledResearchIds.delete(researchId);
          await loadEvolutions();
          return;
        }
        evolutionCache.set(researchId, detail);
        if (selectedId === researchId) {
          await showEvolution(researchId, false, true);
          if (selectedId === researchId && task.log?.length) {
            const host = evolutionDetail.querySelector('[data-session-progress]');
            const log = updateTaskLog(host?.querySelector('[data-task-log]'), task.log, {subject: researchId});
            if (host && log && log.parentNode !== host) host.append(log);
          }
        }
        if (task.status === 'running') { taskPolls.set(taskId, setTimeout(tick, 2000)); return; }
        taskPolls.delete(taskId); polledResearchIds.delete(researchId); evolutionCache.delete(researchId);
        await loadEvolutions();
      } catch (err) {
        taskPolls.delete(taskId); polledResearchIds.delete(researchId);
        toastError(ctx.bus, err, '实验状态查询失败');
      }
    }
    tick();
  }
  function stateText(state, decision) {
    if (state === "queued") return "排队中";
    if (state === "running") return "运行中";
    if (state === "created") return "待启动";
    if (state === "failed") return "失败";
    if (state === "cancelled") return "已中止";
    if (state === "paused") return "已暂停（可恢复）";
    if (state === "completed") return decision === "frozen" ? "已完成（达标）" : "已完成（未达标）";
    return state || "—";
  }

  function stateBadgeArgs(status) {
    if (status.decision === "cancelled") return ["已中止", "muted"];
    const state = status.state || "";
    if (state === "queued" || state === "created") return [stateText(state), "warn"];
    if (state === "running") return ["运行中", "accent"];
    if (state === "failed") return ["失败", "danger"];
    if (state === "cancelled") return ["已中止", "muted"];
    if (state === "paused") return ["已暂停（可恢复）", "accent"];
    if (state === "completed") {
      return status.decision === "frozen" ? ["已完成（达标）", "safe"] : ["已完成（未达标）", "muted"];
    }
    return [stateText(state, status.decision)];
  }

  function runLink(runId) {
    if (!runId) return null;
    const btn = el("button", "experiments__run-link", runId);
    btn.type = "button";
    btn.addEventListener("click", () => {
      ctx.bus.emit("backtest:open", runId);
      ctx.bus.emit("route", "backtest");
    });
    return btn;
  }

}
