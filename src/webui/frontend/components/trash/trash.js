import {newestFirst} from '../../core/sort.js';
import { el, tableEl } from '../../core/dom.js';
import { fmtTime } from '../../core/fmt.js';
import { toastError } from '../../core/toast.js';
import { renderState } from '../state-card/state-card.js';
import { runWithConfirm } from '../confirm-dialog/confirm-dialog.js';

const CATEGORIES = {
  factor: '因子', strategy_profile: '策略', execution_template: '执行模板',
  market_rule_candidate: '市场规则', config_candidate: '配置',
  data_mapping_candidate: '数据映射', portfolio_account: '账户',
};

export async function mount(root, ctx) {
  const host = root.querySelector('[data-trash-list]');
  const empty = root.querySelector('[data-trash-empty]');
  let loading = false;
  let busy = false;
  let count = 0;

  async function renderEmpty() {
    const main = el('div', 'trash__empty-main');
    host.append(main);
    await renderState(main, { kind: 'empty', title: '回收站为空' });
  }

  function updateButtons() {
    empty.disabled = loading || busy || count === 0;
    host.querySelectorAll('.trash__actions button').forEach(button => { button.disabled = busy; });
  }

  async function load() {
    if (loading) return;
    loading = true;
    updateButtons();
    await renderState(host, { kind: 'loading', title: '加载回收站…' });
    try {
      const { items } = await ctx.api.trashList();
      count = items.length;
      for (const value of root.querySelectorAll('[data-trash-count]')) {
        value.textContent = value.dataset.trashCount === 'all' ? String(count)
          : String(items.filter(item => item.category === value.dataset.trashCount).length);
      }
      host.replaceChildren();
      if (!count) {
        await renderEmpty();
        return;
      }
      host.append(tableEl([
        { key: 'name', label: '名称' },
        { key: 'category', label: '类型', format: value => CATEGORIES[value] },
        { key: 'trashed_at', label: '删除时间', format: fmtTime },
        { key: 'expires_at', label: '永久删除时间', format: fmtTime },
        { key: 'bucket', label: '操作', format: (bucket, item) => {
          const actions = el('div', 'trash__actions');
          const restore = el('button', 'btn btn--ghost', '恢复');
          restore.type = 'button';
          restore.onclick = () => act(() => ctx.api.trashRestore(bucket, { confirm: true }), '已恢复');
          const purge = el('button', 'btn btn--danger', '永久删除');
          purge.type = 'button';
          purge.onclick = () => act(() => runWithConfirm({
            execute: extra => ctx.api.trashPurge(bucket, extra),
            title: `永久删除 ${item.name}`, confirmLabel: '永久删除', danger: true, showPreview: false,
          }), '已永久删除');
          actions.append(restore, purge);
          return actions;
        } },
      ], newestFirst(items, ['trashed_at'])));
    } catch (error) {
      count = 0;
      root.querySelectorAll('[data-trash-count]').forEach(value => { value.textContent = '—'; });
      await renderState(host, { kind: 'error', title: '回收站加载失败', detail: error.message, retry: load });
    } finally {
      loading = false;
      updateButtons();
    }
  }

  async function act(execute, title) {
    if (busy || loading) return;
    busy = true;
    updateButtons();
    try {
      const result = await execute();
      if (!result) return;
      ctx.bus.emit('toast', { kind: 'success', title });
      await load();
    } catch (error) {
      toastError(ctx.bus, error, '操作失败');
    } finally {
      busy = false;
      updateButtons();
    }
  }

  empty.onclick = () => act(() => runWithConfirm({
    execute: extra => ctx.api.trashEmpty(extra), title: '清空回收站',
    confirmLabel: '清空回收站', danger: true, showPreview: false,
  }), '回收站已清空');
  ctx.bus.on('route', page => { if (page === 'trash') load(); });
  await load();
}
