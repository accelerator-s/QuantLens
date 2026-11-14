import {el} from './dom.js';
import {fmtTime} from './fmt.js';

export function createModelReviews(ctx, kind, name) {
  const host = el('section', 'model-reviews');
  host.setAttribute('aria-label', '模型评价记录');
  const head = el('div', 'model-reviews__head');
  const generate = el('button', 'btn btn--ghost', '生成评价');
  generate.type = 'button';
  head.append(el('h4', '', '评价记录'), generate);
  const content = el('div', 'model-reviews__content');
  const status = el('p', 'item-row__meta'); status.setAttribute('role', 'status');
  host.append(head, status, content);
  let running = false, loading = false, reload = false;

  function entry(record) {
    const node = el('article', 'model-reviews__entry');
    const meta = el('p', 'model-reviews__meta');
    const time = el('time', '', fmtTime(record.created_at));
    if (record.created_at) time.dateTime = record.created_at;
    meta.append(el('span', '', record.model_name || '模型未记录'), time);
    node.append(meta, el('p', 'model-reviews__conclusion', record.conclusion));
    for (const [label, values] of [['风险', record.risks], ['后续建议', record.next_steps]]) {
      if (!values?.length) continue;
      const list = el('ul');
      values.forEach(value => list.append(el('li', '', value)));
      node.append(el('h4', '', label), list);
    }
    return node;
  }

  async function refresh() {
    if (!host.isConnected) return;
    if (loading) { reload = true; return; }
    loading = true;
    try {
      const data = await ctx.api[`${kind}Reviews`](name);
      if (!host.isConnected) return;
      running = data.task?.status === 'running';
      generate.disabled = running;
      generate.textContent = running ? '评价生成中…' : '生成评价';
      status.classList.toggle('ql-error', ['failed', 'interrupted'].includes(data.task?.status));
      status.textContent = running ? '正在生成模型评价…' : data.task?.error || '';
      const records = data.items || [];
      const historyOpen = content.querySelector('details')?.open || false;
      content.replaceChildren();
      if (records.length) {
        content.append(entry(records[0]));
        if (records.length > 1) {
          const history = el('details', 'model-reviews__history');
          history.open = historyOpen;
          const summary = el('summary');
          summary.append(el('span', '', '历史评价'), el('span', 'model-reviews__count', String(records.length - 1)));
          const entries = el('div', 'model-reviews__history-list');
          records.slice(1).forEach(record => entries.append(entry(record)));
          history.append(summary, entries);
          content.append(history);
        }
      }
    } catch (error) {
      if (!host.isConnected) return;
      status.classList.add('ql-error'); status.textContent = `模型评价加载失败：${error.message}`;
    } finally {
      loading = false;
      if (reload) { reload = false; refresh(); }
      else if (running && host.isConnected) setTimeout(refresh, 1500);
    }
  }

  generate.onclick = async () => {
    generate.disabled = true;
    try {
      await ctx.api[`${kind}ReviewGenerate`](name);
      running = true;
      await refresh();
    } catch (error) {
      status.classList.add('ql-error'); status.textContent = `模型评价生成失败：${error.message}`;
    } finally { generate.disabled = running; }
  };
  queueMicrotask(refresh);
  return host;
}
