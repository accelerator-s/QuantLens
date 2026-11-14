import assert from 'node:assert/strict';
import {completedRuns, completedStrategyRuns, experimentOutcome} from '../src/webui/frontend/components/experiments/results.js';
const metrics = {returns: {total_return: .1, annualized_return: .02}, risk: {max_drawdown: -.1}};
const run = {run_id: 'BT-train', metrics};
const training = {status: {best_version_id: 'v0', terminal_reason: '未通过训练筛选'}, events: [
  {event_type: 'window_completed', payload: {version_id: 'v0', window_name: 'train', ...run}},
  {event_type: 'training_evaluated', payload: {version_id: 'v0', train: run, validation: null, generalization: null}},
  {event_type: 'baseline_completed', payload: {version_id: 'v0', result: {version_id: 'v0', train: run}}},
]};
assert.equal(completedRuns(training).length, 1);
assert.deepEqual(experimentOutcome(training).metrics, metrics);
assert.equal(experimentOutcome(training).assessment, undefined);
assert.equal(experimentOutcome(training).summary, undefined);
const summarized = structuredClone(training);
const summary = {conclusion: '训练候选未优于基线，未运行验证和锁定测试。', ai_generated: true, model_name: 'configured-model'};
summarized.events.push({event_type: 'session_finished', payload: {}}, {event_type: 'experiment_summary', payload: summary});
assert.deepEqual(experimentOutcome(summarized).summary, summary);
summarized.events.push({event_type: 'session_failed', payload: {}});
assert.equal(experimentOutcome(summarized).summary, undefined);
const validation = structuredClone(training);
validation.events.push({event_type: 'version_evaluated', payload: {version_id: 'v0',
  validation: {run_id: 'walk-forward:validation_aggregate', metrics}, generalization: {classification: 'not_effective'},
  folds: [{fold_id: 'f1', validation: {run_id: 'BT-valid', metrics}}]}});
assert.equal(completedRuns(validation).length, 2);
assert.equal(experimentOutcome(validation).assessment.classification, 'not_effective');
validation.events.push({event_type: 'version_evaluated', payload: {version_id: 'other', generalization: {classification: 'validation_generalized'}}});
assert.equal(experimentOutcome(validation).assessment.classification, 'not_effective');
assert.deepEqual(experimentOutcome({events: [{event_type: 'result', payload: run}]}).metrics, metrics);
assert.equal(experimentOutcome({status: {state: 'failed'}, events: []}).metrics, undefined);
assert.equal(experimentOutcome({events: []}).summary, undefined);

// Incremental events and final aggregates must describe the same physical run,
// without losing the version, fold, stage or trading dates on a later event.
const factorFold = {run_id: 'BT-fold', window: 'wf001_validation', metrics: {
  window: {start: '2022-01-04', end: '2022-12-30'}, ...metrics}};
const factorRows = completedRuns({events: [
  {event_type: 'window_completed', payload: {version_id: 'v1', fold_id: 'wf001', window_name: 'validation', ...factorFold}},
  {event_type: 'version_evaluated', payload: {version_id: 'v1', folds: [
    {fold_id: 'wf001', validation_window: {start: '2022-01-01', end: '2022-12-31'}, validation: factorFold}]}},
  {event_type: 'locked_test_completed', payload: {version_id: 'v1', run_id: 'BT-test', metrics}},
], locked_test_exposures: [{run_id: 'BT-test'}]});
assert.equal(factorRows.length, 2);
assert.equal(factorRows[0].version, 'v1');
assert.equal(factorRows[0].window, 'validation');
assert.equal(factorRows[0].fold, 'wf001');
assert.equal(factorRows[0].range, '2022-01-04 至 2022-12-30');
assert.equal(factorRows[1].version, 'v1');
assert.equal(factorRows[1].window, 'locked_test');
assert.deepEqual(factorRows[1].metrics, metrics);

// Same method with different frequencies is a different strategy candidate.
// Every training fold, final baseline, and selected final run must survive.
const monthly = {method: 'equal', max_positions: 20, frequency: 'monthly'};
const quarterly = {...monthly, frequency: 'quarterly'};
const fold = (run_id, year, annualized_return) => ({run_id,
  window: {fit_start: '2016-01-01', fit_end: `${year - 1}-12-31`, start: `${year}-01-01`, end: `${year}-12-31`},
  metrics: {returns: {annualized_return}, risk: {max_drawdown: -.1}}});
const final = fold('BT-final', 2022, -.072464);
final.metrics.window = {start: '2022-01-04', end: '2022-12-30'};
const strategyResult = {run_id: final.run_id, metrics: final.metrics, selected_candidate: quarterly,
  search: {results: [
    {candidate: monthly, folds: [fold('BT-train-a', 2020, .01), fold('BT-train-b', 2021, 0)]},
    {candidate: quarterly, folds: [fold('BT-train-c', 2020, .03), fold('BT-train-d', 2021, -.01)]},
  ]},
  final_evaluation: {candidate: quarterly, folds: [final]},
  final_baseline: {candidate: monthly, folds: [fold('BT-baseline', 2022, -.1)]}};
const strategyRows = completedStrategyRuns(strategyResult, {start: '2022-01-01', end: '2022-12-31'});
assert.equal(strategyRows.length, 6);
assert.deepEqual(strategyRows.map(row => row.run_id), ['BT-train-a','BT-train-b','BT-train-c','BT-train-d','BT-baseline','BT-final']);
assert.deepEqual(strategyRows.map(row => row.stage), ['training','training','training','training','final_baseline','final']);
assert.deepEqual(strategyRows.slice(0, 4).map(row => row.fold), [1,2,1,2]);
assert.equal(strategyRows[1].metrics.returns.annualized_return, 0);
assert.equal(strategyRows[2].candidate.frequency, 'quarterly');
assert.equal(strategyRows.at(-1).range, '2022-01-04 至 2022-12-30');
assert.equal(strategyRows.at(-1).metrics.returns.annualized_return, -.072464);
const withoutBaseline = structuredClone(strategyResult); withoutBaseline.final_baseline = null;
assert.equal(completedStrategyRuns(withoutBaseline).length, 5);
assert.equal(completedStrategyRuns(null).length, 0);
assert.equal(completedStrategyRuns({}).length, 0);
assert.equal(completedStrategyRuns({run_id: 'BT-old', metrics}).length, 1);
console.log('PASS: all candidate/fold/baseline/final runs, deduplication, stage/date/metric alignment, incremental factor events and old records');
