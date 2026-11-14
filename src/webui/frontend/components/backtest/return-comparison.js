import {lineChart} from '../../core/charts.js';
import {el} from '../../core/dom.js';
import {fmtDate, fmtPct} from '../../core/fmt.js';

export function returnComparison({run, nav, benchmark, visibility = {}, onRetry}) {
  const node = el('article', 'card detail-card backtest__comparison');
  const head = el('div', 'backtest__comparison-head');
  const heading = el('div', 'backtest__comparison-heading');
  heading.append(el('h3', '', `净值曲线（${nav.total_rows} 个交易日）`));
  const note = el('p', 'backtest__index-note item-row__meta');
  note.setAttribute('aria-live', 'polite');
  heading.append(note);
  const controls = el('div', 'backtest__curve-controls');
  controls.setAttribute('role', 'group');
  controls.setAttribute('aria-label', '选择展示的净值曲线');
  head.append(heading, controls);
  const plot = el('div', 'backtest__comparison-plot');
  node.append(head, plot);
  const labels = nav.items.map(row => fmtDate(row.date));
  const capital = Number(run.config?.config?.initial_capital);
  // Use the same pre-window capital as the cumulative benchmark/index returns.
  const navBase = capital > 0 ? capital : Number(nav.items[0].nav) || 1;
  const series = [
    {key: 'strategy', label: '回测净值', color: 'var(--accent)',
      values: nav.items.map(row => row.nav == null ? null : Number(row.nav) / navBase)},
    {key: 'benchmark', label: benchmark.label, color: 'var(--warn)',
      values: nav.items.map(row => row.benchmark_nav == null ? null : Number(row.benchmark_nav))},
    {key: 'market', label: '市场指数（加载中）', color: 'var(--safe)', values: [], connectGaps: false},
  ];
  let chart;
  const inputs = new Map();
  for (const item of series) {
    const label = el('label', 'backtest__curve-toggle');
    const input = el('input');
    input.type = 'checkbox';
    input.dataset.curve = item.key;
    input.checked = visibility[item.key] !== false;
    input.disabled = !item.values.some(value => value != null);
    const marker = el('i', 'backtest__legend-marker');
    marker.style.background = item.color;
    marker.setAttribute('aria-hidden', 'true');
    const text = el('span', '', item.label);
    label.append(input, marker, text);
    controls.append(label);
    inputs.set(item.key, {input, text});
    input.addEventListener('change', () => {
      visibility[item.key] = input.checked;
      draw();
    });
  }
  note.textContent = '正在加载市场指数…';

  function draw() {
    chart?.dispose?.();
    const selected = series.filter(item => inputs.get(item.key).input.checked && !inputs.get(item.key).input.disabled);
    if (!selected.length) {
      plot.replaceChildren(el('p', 'backtest__curves-empty item-row__meta', '勾选上方曲线即可展示。'));
      chart = null;
      return;
    }
    chart = lineChart({series: selected, labels, height: 280, interactive: true});
    plot.replaceChildren(chart);
  }

  function updateReference(reference) {
    const market = series[2];
    market.label = reference.name ? `市场指数（${reference.name}）`
      : reference.status === 'error' ? '市场指数（加载失败）' : '市场指数（暂不可用）';
    const values = new Map((reference.items || []).map(row => [String(row.date).slice(0, 10), row.nav]));
    market.values = nav.items.map(row => values.get(String(row.date).slice(0, 10)) ?? null);
    // Preserve gaps even when the missing dates were omitted by NAV downsampling.
    const missing = reference.missing_dates || [];
    market.breakBefore = nav.items.flatMap((row, i) => i > 0 && missing.some(day =>
      day > String(nav.items[i - 1].date).slice(0, 10) && day <= String(row.date).slice(0, 10)) ? [i] : []);
    const {input, text} = inputs.get('market');
    text.textContent = market.label;
    input.disabled = !market.values.some(value => value != null);
    note.replaceChildren();
    if (reference.total_return != null) {
      const stats = el('span', 'backtest__index-stats', `指数收益 ${fmtPct(reference.total_return)}`);
      const subject = run.config?.profile?.extra?.purpose === 'factor_research_only' ? '因子' : '策略';
      if (reference.excess_return != null) stats.append(document.createTextNode(
        ` · ${subject}超额 ${(reference.excess_return * 100).toFixed(2)} 个百分点`));
      note.append(stats);
    } else {
      note.textContent = reference.message || '指数行情暂不可用。';
      if (reference.status === 'error' && onRetry) {
        const retry = el('button', 'btn btn--ghost btn--sm', '重试');
        retry.type = 'button';
        retry.dataset.referenceRetry = '';
        retry.addEventListener('click', onRetry);
        note.append(' ', retry);
      }
    }
    draw();
  }
  draw();
  return {node, updateReference, dispose: () => chart?.dispose?.()};
}
