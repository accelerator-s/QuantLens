import {createSelect} from '../../core/select.js';
import {hydrateIcons} from '../../core/icons.js';
import {toastError} from '../../core/toast.js';
import {badge, fmtNum, fmtPct} from '../../core/fmt.js';
import {el, kvEl, selectMarkup} from '../../core/dom.js';
import {createModelReviews} from '../../core/model-reviews.js';

const STATUS = {draft: '研究中', active: '已启用', disabled: '已停止'};
const STATUS_TONE = {draft: 'accent', active: 'safe', disabled: 'muted'};
const MODEL = {auto: '自动研究', equal: '等权', icir: 'ICIR', model_linear: '线性回归', model_ridge: '岭回归', model_logistic: '逻辑回归'};
const TASK = {running: '正在研究', failed: '研究失败', interrupted: '研究中断'};

export async function mount(root, ctx) {
  await hydrateIcons(root);
  const list = root.querySelector('[data-list]'), detail = root.querySelector('[data-detail]');
  const emptyHost = root.querySelector('[data-library-empty]'), split = root.querySelector('.split');
  const filterRoot = root.querySelector('[data-status-select]');
  filterRoot.innerHTML = selectMarkup('全部状态');
  const filter = createSelect(filterRoot, {onChange: load});
  filter.setOptions([{value: '', label: '全部状态'}, ...Object.entries(STATUS).map(([value,label]) => ({value,label}))], '');
  const researchOpenBtn = root.querySelector('[data-research-open]');
  researchOpenBtn.onclick = () => ctx.bus.emit('route', 'strategy-research/research');
  let selected = ctx.strategyName || '', generation = 0, timer, active = true, loading = false, reload = false;
  ctx.bus.on('route', id => {
    active = id === 'strategy-research';
    clearTimeout(timer);
    if (active) load();
  });
  ctx.bus.on('strategy:changed', name => { selected = name; load(); });
  root.closest('.view')?.querySelector('#strategy-research-tab-library')?.addEventListener('click', load);
  await load();

  async function load() {
    if (loading) { reload = true; return; }
    loading = true;
    clearTimeout(timer);
    try {
      const data = await ctx.api.strategies({status: filter.value});
      list.replaceChildren();
      emptyHost.hidden = data.items.length > 0;
      split.hidden = !data.items.length;
      if (!data.items.length) {
        selected = ''; ++generation;
        detail.replaceChildren();
        return;
      }
      if (!data.items.some(item => item.profile_name === selected)) selected = data.items[0].profile_name;
      for (const item of data.items) {
        const row = el('button', `item-row${item.profile_name === selected ? ' is-active' : ''}`);
        row.type = 'button'; row.dataset.name = item.profile_name;
        row.append(el('span', 'item-row__title', item.display_name));
        const meta = el('span', 'item-row__meta');
        meta.append(badge(STATUS[item.status], STATUS_TONE[item.status]), el('span', '', `${MODEL[item.model]} · ${item.factors.length} 个因子`));
        if (item.task?.status === 'failed' || item.task?.status === 'interrupted') meta.append(badge(TASK[item.task.status], 'danger'));
        row.append(meta);
        row.onclick = () => {
          selected = item.profile_name;
          list.querySelectorAll('.item-row').forEach(node => node.classList.toggle('is-active', node === row));
          show();
        };
        list.append(row);
      }
      await show();
      if (active && data.items.some(item => item.task?.status === 'running')) timer = setTimeout(load, 2500);
    } catch (error) { toastError(ctx.bus, error, '策略加载失败'); }
    finally { loading = false; if (reload) { reload = false; load(); } }
  }

  async function action(button, fn) {
    button.disabled = true;
    try { await fn(); await load(); }
    catch (error) { toastError(ctx.bus, error, '策略操作失败'); }
    finally { if (button.isConnected) button.disabled = false; }
  }

  async function show() {
    const id = ++generation, name = selected;
    try {
      const data = await ctx.api.strategy(name);
      if (id !== generation || name !== selected) return;
      detail.replaceChildren();
      const card = el('article', 'card detail-card strategy__overview');
      const head = el('div', 'detail-card__head research-detail__head');
      const title = el('div', 'research-detail__title');
      title.append(el('h3', '', data.display_name), badge(STATUS[data.status], STATUS_TONE[data.status]));
      if (TASK[data.task?.status]) title.append(badge(TASK[data.task.status],
        data.task.status === 'running' ? 'accent' : 'danger'));
      const experimentsBtn = el('button', 'btn btn--ghost', '查看实验');
      experimentsBtn.type = 'button'; experimentsBtn.dataset.strategyExperiments = name;
      experimentsBtn.onclick = () => {
        ctx.strategyName = name; ctx.strategyExperimentId = data.task?.task_id || '';
        ctx.strategyExperimentFilter = name; ctx.strategyExperimentFilterLabel = data.display_name;
        ctx.bus.emit('strategy:experiment', ctx.strategyExperimentId); ctx.bus.emit('route', 'strategy-research/experiments');
      };
      const controls = el('div', 'strategy__controls');
      head.append(title, controls);
      card.append(head);
      const result = data.result, complete = result?.state === 'completed', running = data.task?.status === 'running';
      const spec = data.spec;
      if (data.hypothesis) {
        const hypothesis = kvEl([['核心假设', data.hypothesis, {wide: true, scroll: true}]]);
        hypothesis.classList.add('strategy__hypothesis'); card.append(hypothesis);
      }
      const snapshot = result?.account_snapshot || data.account_snapshot;
      const facts = kvEl([['配权方法', MODEL[result?.selected_candidate?.method || spec.model]],
        ['关联账户', snapshot.alias], ['研究资金快照', `${fmtNum(snapshot.capital, 0)} 元`],
        ['选股数量上限', `${result?.selected_candidate?.max_positions ?? spec.top_n} 只`],
        ['调仓频率', {weekly: '每周', monthly: '每月', quarterly: '每季度'}[result?.selected_candidate?.frequency || spec.rebalance_frequency]]]);
      facts.classList.add('strategy__facts');
      card.append(facts);
      if (complete && Object.keys(result.weights || {}).length) {
        const weights = el('section', 'research-weights strategy__weights');
        weights.append(el('h4', '', '因子权重'));
        const items = el('div', 'strategy__weight-items');
        for (const [factor, weight] of Object.entries(result.weights)) {
          const item = el('div', 'strategy__weight');
          const label = el('div', 'strategy__weight-label');
          label.append(el('span', '', data.factor_display_names?.[factor] || factor),
            el('span', 'item-row__meta', result.factor_versions?.[factor] || '—'));
          item.append(label, el('strong', '', fmtPct(weight))); items.append(item);
        }
        weights.append(items); card.append(weights);
      }
      if (data.capital_suspension) {
        const bands = {small: '小资金', medium: '中资金', large: '大资金', unavailable: '估值不可用'};
        card.append(el('p', 'ql-error', `账户资金变化：${bands[data.capital_suspension.from_band]} → ${bands[data.capital_suspension.to_band]}。已自动停用，需人工重新启用；已有研究结果对应原资金规模。`));
      }
      const actions = el('div', 'strategy-actions');
      actions.setAttribute('role', 'group');
      actions.setAttribute('aria-label', '策略操作');
      const button = (label, fn, tone = 'ghost') => {
        const b = el('button', `btn btn--${tone}`, label); b.type = 'button';
        b.onclick = () => action(b, fn); actions.append(b); return b;
      };
      for (const target of Object.keys(STATUS)) {
        if (target === data.status) continue;
        const b = button(`转为${STATUS[target]}`, () => ctx.api.strategyStatus(name, target));
        if (target === 'active' && (!complete || !result.accepted || running)) {
          b.disabled = true; b.title = '完成研究并通过启用条件后可启用';
        }
      }
      const remove = button('删除', () => ctx.api.strategyDelete(name), 'danger');
      remove.disabled = running;
      if (running) remove.title = '研究任务运行中，完成后可删除';
      controls.append(actions, experimentsBtn);
      if (data.task) {
        if (running) card.append(el('p', '', data.task.progress?.step || '等待计算资源…'));
        if (data.task.error) card.append(el('p', 'ql-error', data.task.error));
      }
      if (result?.error && !data.task?.error) card.append(el('p', 'ql-error', result.error));
      detail.append(card);
      const reviews = el('article', 'card detail-card strategy__reviews');
      reviews.append(createModelReviews(ctx, 'strategy', name));
      detail.append(reviews);
    } catch (error) { if (id === generation) toastError(ctx.bus, error, '策略详情加载失败'); }
  }
}
