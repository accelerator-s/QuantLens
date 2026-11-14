"""Spark adapter for strategy search; final dates never enter inner jobs."""
from __future__ import annotations

import uuid
from decimal import Decimal

import numpy as np

from factor_evolution.diagnostics import digest, summarize_training
from strategy.training import Candidate, fold_windows


def candidate_profile(profile, candidate, weights):
    payload = profile.model_dump(mode='json')
    payload['factors']['weighting'] = candidate.method
    for ref in payload['factors']['items']:
        ref['weight_override'] = str(weights[ref['name']])
    payload['constraints']['max_positions'] = candidate.max_positions
    payload['position_constraint']['top_n'] = candidate.max_positions
    payload['rebalance_frequency'] = candidate.frequency
    from strategy import ResolvedStrategyProfile
    return ResolvedStrategyProfile.model_validate(payload)


def evaluate(spark, context, profile, spec, protocol, config, candidate, stage, *, run_id, batch):
    from pyspark.sql import functions as F
    from backtest.engine import run_backtest
    from backtest.report import BacktestRunStore
    from backtest.signal import _resolve_direction
    from factors.registry import FactorRegistry
    from quantlens.spark_weight_fit import exposures, fit
    from quantlens.backtest_inputs import inputs
    from benchmarks.selection import plan_from_profile

    registry = FactorRegistry()
    names = [ref.name for ref in profile.factors.items]
    versions = profile.extra['factor_versions']
    directions = {r.name: _resolve_direction(r.name, r.direction_override, registry.get(r.name).direction)
                  for r in profile.factors.items}
    # Inner jobs load neither final features nor final labels.
    end = str(spec.train_end) if stage == 'inner' else str(spec.end)
    frame = exposures(spark, names, directions, versions).where(
        (F.col('date') >= str(spec.train_start)) & (F.col('date') <= end)).join(
        spark.table('dwd.universe').where('in_universe').select('code', 'date'), ['code', 'date']).persist()
    labels = spark.table('dwd.label_forward').where(
        (F.col('horizon') == spec.label_horizon) & (F.col('label_version') == 'v1') & F.col('valid') &
        (F.col('date') >= str(spec.train_start)) & (F.col('date') <= end) & (F.col('valid_until') <= end)
    ).select('code', 'date', 'ret', 'valid_until')
    try:
        dates = [str(r.date) for r in frame.where(F.col('date') <= str(spec.train_end))
                 .select('date').distinct().orderBy('date').collect()]
        windows = fold_windows(dates) if stage == 'inner' else [{
            'fit_start': str(spec.train_start), 'fit_end': str(spec.train_end),
            'start': str(spec.start), 'end': str(spec.end)}]
        invariant = profile.model_dump(mode='json')
        for key in ['display_name', 'source_chain', 'updated_at']:
            invariant.pop(key, None)
        comparison = digest({'profile': invariant, 'protocol': protocol.model_dump(mode='json'),
            'account_snapshot': profile.extra['research_account_snapshot'], 'windows': windows,
            'data_snapshot': context['data_snapshot'], 'pipeline': context['pipeline_code_hash'],
            'label_horizon': spec.label_horizon})
        folds = []
        for window in windows:
            train = frame.join(labels, ['code', 'date']).where(
                (F.col('date') >= window['fit_start']) & (F.col('date') <= window['fit_end']) &
                (F.col('valid_until') <= window['fit_end']) & (F.col('valid_until') < window['start'])
            ).dropna(subset=names+['ret']).persist()
            try:
                stats = train.agg(F.count('*').alias('rows'), F.countDistinct('date').alias('days'),
                                  F.max('valid_until').alias('latest_label')).first()
                if stats.days < 60 or stats.rows < max(200, len(names)*20):
                    raise ValueError('成熟标签的训练样本不足')
                weights = dict(zip(names, map(float, fit(spark, train, names, candidate.method))))
            finally:
                train.unpersist()
            fitted = candidate_profile(profile, candidate, weights)
            current = config.model_copy(update={'start': window['start'], 'end': window['end']})
            ident = run_id if stage == 'final' else 'BT-'+uuid.uuid4().hex
            if stage == 'final':
                from quantlens.spark_backtest import run as backtest
                outcome = backtest(spark, context, profile.profile_name, window['start'], window['end'],
                    float(config.initial_capital), ident, fitted.model_dump(mode='json'),
                    protocol.model_dump(mode='json'), current.model_dump(mode='json'))
            else:
                payload = inputs(fitted, window['start'], window['end'], spark)
                outcome = run_backtest(current, protocol=protocol, profile=fitted, run_id=ident, warehouse_inputs=payload)
            store = BacktestRunStore.open_run(outcome.run_id)
            diagnostics = summarize_training(store.read_daily('nav'), store.read_daily('trades'),
                store.read_daily('positions'), start=window['start'], end=window['end'],
                capital=float(config.initial_capital), benchmark=plan_from_profile(fitted.model_dump(mode='json')).model_dump(mode='json'))
            # Prediction diagnostics use only labels mature within this evaluation fold.
            scored = frame.join(labels, ['code', 'date']).where(
                (F.col('date') >= window['start']) & (F.col('date') <= window['end']) &
                (F.col('valid_until') <= window['end'])).withColumn(
                    'score', sum(F.col(n)*float(w) for n, w in weights.items()))
            from quantlens.model_comparison import summarize
            prediction = summarize(scored)
            diagnostics['signal'] = {'rank_ic': prediction['rank_ic'], 'horizon': spec.label_horizon,
                                     'scope': 'mature_labels_only', 'not_a_net_return': True}
            folds.append({'run_id': outcome.run_id, 'window': window, 'weights': weights,
                'training': {'rows': stats.rows, 'days': stats.days, 'latest_label': str(stats.latest_label)},
                'metrics': outcome.metrics, 'diagnostics': diagnostics})
        result = {'candidate': candidate.model_dump(mode='json'), 'folds': folds,
                  'comparison_hash': comparison, 'data_snapshot': context['data_snapshot'],
                  'pipeline_code_hash': context['pipeline_code_hash']}
        if stage == 'final':
            from quantlens.spark_ads import export_backtests
            export_backtests(spark, context, batch, str(spec.end))
            result['profile'] = fitted.model_dump(mode='json')
        return result
    finally:
        frame.unpersist()
