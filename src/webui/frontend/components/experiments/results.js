// Interpret actual completed stages without treating an unrun stage as zero.
function definedValues(value) {
  return Object.fromEntries(Object.entries(value).filter(([, item]) => item !== undefined && item !== null && item !== ''));
}

function windowKind(value) {
  if (typeof value !== 'string') return undefined;
  if (value === 'fixed_train' || /^wf\d+_train$/.test(value)) return 'train';
  if (/^wf\d+_validation$/.test(value)) return 'validation';
  return value;
}

export function runRange(value) {
  if (!value) return '';
  if (typeof value === 'object') return value.start && value.end ? `${value.start} 至 ${value.end}` : '';
  return /^\d{4}-\d{2}-\d{2}\.\.\d{4}-\d{2}-\d{2}$/.test(value) ? value.replace('..', ' 至 ') : '';
}

export function completedRuns(data, linked = []) {
  const rows = new Map();
  function add(result, context) {
    if (!result?.run_id || result.run_id.startsWith('walk-forward:')) return;
    const previous = rows.get(result.run_id) || {};
    rows.set(result.run_id, {...previous, ...definedValues(context), ...definedValues(result),
      window: windowKind(result.window_name || result.window) || context.window || previous.window,
      fold: result.fold_id || context.fold || previous.fold,
      range: runRange(result.metrics?.window) || runRange(result.window) || context.range || previous.range,
      metrics: Object.keys(result.metrics || {}).length ? result.metrics : previous.metrics || {}});
  }
  function visit(payload, context = {}) {
    if (!payload) return;
    const current = {...context, version: payload.version_id || context.version,
      window: windowKind(payload.window_name || payload.window) || context.window,
      fold: payload.fold_id || context.fold};
    add(payload, current);
    if (payload.result) visit(payload.result, current);
    for (const key of ['train', 'validation', 'test']) if (payload[key]) add(payload[key], {...current, window: key});
    for (const fold of payload.folds || []) {
      for (const key of ['train', 'validation']) add(fold[key], {...current, window: key, fold: fold.fold_id,
        range: runRange(fold[key + '_window'])});
    }
  }
  for (const event of data.events || []) visit(event.payload, {window: event.event_type === 'locked_test_completed' ? 'locked_test' : undefined});
  for (const exposure of data.locked_test_exposures || []) visit(exposure, {window: 'locked_test'});
  for (const record of linked) if (!rows.has(record.run_id)) add(record, {version: record.display_name || record.subject_display_name});
  return [...rows.values()];
}

export function completedStrategyRuns(result, spec = result?.spec || {}) {
  if (!result) return [];
  const rows = new Map();
  function add(run, context) {
    if (!run?.run_id || run.run_id.startsWith('walk-forward:')) return;
    const previous = rows.get(run.run_id) || {};
    rows.set(run.run_id, {...previous, ...definedValues(context), run_id: run.run_id,
      range: runRange(run.metrics?.window) || runRange(run.window) || previous.range || context.range,
      metrics: Object.keys(run.metrics || {}).length ? run.metrics : previous.metrics || {}});
  }
  function evaluation(value, context) {
    for (const [index, fold] of (value?.folds || []).entries()) {
      add(fold, {...context, candidate: value.candidate || context.candidate, fold: index + 1});
    }
  }
  for (const [index, value] of (result.search?.results || []).entries()) {
    evaluation(value, {stage: 'training', candidateIndex: index, baseline: index === 0});
  }
  evaluation(result.final_baseline, {stage: 'final_baseline', baseline: true});
  evaluation(result.final_evaluation, {stage: 'final', candidate: result.selected_candidate});
  // A top-level result is also a real run in older saved experiments. Merge it
  // by ID so that the final evaluation is never listed twice.
  add(result, {stage: 'final', candidate: result.selected_candidate,
    range: runRange({start: spec.start, end: spec.end})});
  return [...rows.values()];
}

export function experimentOutcome(data, linked = []) {
  const events = data.events || [], status = data.status || {};
  const evaluations = events.filter(e => ['version_evaluated', 'training_evaluated'].includes(e.event_type));
  const best = evaluations.filter(e => !status.best_version_id || e.payload?.version_id === status.best_version_id);
  const assessed = [...best].reverse().find(e => e.event_type === 'version_evaluated');
  const trained = [...best].reverse().find(e => e.event_type === 'training_evaluated');
  const rows = completedRuns(data, linked);
  const chosen = [...rows].reverse().find(r => r.metrics?.returns && (!status.best_version_id || r.version === status.best_version_id));
  const metrics = trained?.payload?.train?.metrics || chosen?.metrics;
  const terminalIndex = events.findLastIndex(e => ['session_finished', 'session_failed', 'session_created'].includes(e.event_type));
  const summary = events.slice(terminalIndex + 1).findLast(e => e.event_type === 'experiment_summary')?.payload;
  return {rows, assessment: assessed?.payload?.generalization, metrics,
    scope: trained ? '最佳训练方案 · 训练区间' : chosen ? ({train: '训练区间', validation: '验证区间', locked_test: '锁定测试区间', test: '测试区间'}[chosen.window] || '已完成回测') : '',
    summary: ['running', 'created'].includes(status.state) ? undefined : summary};
}
