import {el, tableEl} from './dom.js';
import {fmtPct, fmtNum, signClass} from './fmt.js';

export function appendExperimentSummary(host, record) {
  if (record?.ai_generated !== true || !record.model_name || !record.conclusion?.trim()) return;
  const sentence = el('p', 'experiment-conclusion');
  sentence.append(el('strong', '', record.conclusion));
  host.append(sentence);
}

export function metricGrid(metrics = {}) {
  const grid = el('div', 'research-metrics');
  for (const [key, label, group, value, percent] of [
    ['total_return', '总收益', 'returns', metrics.returns?.total_return, true],
    ['annualized_return', '年化收益', 'returns', metrics.returns?.annualized_return, true],
    ['max_drawdown', '最大回撤', 'risk', metrics.risk?.max_drawdown, true],
    ['sharpe', '夏普比率', 'risk', metrics.risk?.sharpe, false],
  ]) {
    const item = el('div', 'metric-group metric'); item.dataset.metricGroup = group; item.dataset.metric = key;
    const present = value != null && Number.isFinite(Number(value));
    item.append(el('span', 'metric__label', label), el('span', `metric__value ${present && key !== 'max_drawdown' ? signClass(value) : ''}`,
      present ? (percent ? fmtPct(value) : fmtNum(value, 3)) : '—'));
    grid.append(item);
  }
  return grid;
}

export function researchFacts(entries) {
  const facts = el('dl', 'research-facts');
  for (const [label, value] of entries) {
    if (value == null || value === '') continue;
    const entry = el('div'); entry.append(el('dt', '', label), el('dd', '', value)); facts.append(entry);
  }
  return facts;
}

export function factorWeights(result, names = {}) {
  const card = el('article', 'card detail-card research-weights');
  card.append(el('h4', '', '因子权重'), tableEl([
    {key: 'name', label: '因子'}, {key: 'version', label: '版本'},
    {key: 'weight', label: '权重', num: true, format: value => fmtPct(value)},
  ], Object.entries(result.weights || {}).map(([name, weight]) => ({
    name: names[name] || name, version: result.factor_versions?.[name] || '—', weight,
  })), {pagination: false}));
  return card;
}
