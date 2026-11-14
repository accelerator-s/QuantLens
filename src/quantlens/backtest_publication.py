"""Validate completed research artifacts before publishing the current ADS contract."""
from __future__ import annotations

import json
import re
import uuid
import fcntl
import os
import threading
from contextlib import contextmanager
from functools import wraps

import numpy as np
import pandas as pd

from backtest.config import BacktestConfig
from backtest.protocol import BacktestProtocol, verify_protocol_or_raise
from backtest.report import BacktestRunStore
from exceptions import ContractViolation
from strategy import ResolvedStrategyProfile
from paths import workspace

_publication_thread_lock = threading.RLock()
_publication_depth = threading.local()


@contextmanager
def publication_lock():
    """Serialize host submissions through their final serving sync across processes."""
    with _publication_thread_lock:
        if getattr(_publication_depth, 'active', False):
            yield
            return
        path = workspace().data / 'backtest-publication.lock'
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(path, os.O_RDONLY | os.O_CREAT, 0o644)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            _publication_depth.active = True
            yield
        finally:
            _publication_depth.active = False
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)


def serialized_publication(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        with publication_lock():
            return function(*args, **kwargs)
    return wrapped


def completed_result(run_id: str) -> dict:
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}', run_id):
        raise ValueError('Invalid backtest run ID')
    store = BacktestRunStore.open_run(run_id)
    saved, status, metrics = store.read_config(), store.read_status(), store.read_metrics()
    config = BacktestConfig.model_validate(saved['config'])
    profile = ResolvedStrategyProfile.model_validate(saved['profile'])
    protocol = BacktestProtocol.model_validate(saved['protocol'])
    current = verify_protocol_or_raise()
    if (status.get('state') != 'completed' or not protocol.lock.locked
            or not saved.get('protocol_locked')
            or protocol.content_hash() != current.content_hash()
            or saved.get('protocol_hash') != current.content_hash()
            or saved.get('config_hash') != config.content_hash()
            or saved.get('research_standard') != 'cn_a_daily_v3'
            or metrics.get('research_standard') != 'cn_a_daily_v3'
            or (metrics.get('input_quality') or {}).get('passed') is not True):
        raise ContractViolation(code='BACKTEST_PUBLICATION_INVALID',
                                message=f'{run_id} 未通过当前回测口径校验', blocking=True)
    fingerprint = saved.get('data_fingerprint') or {}
    snapshot = fingerprint.get('data_snapshot')
    if not snapshot or snapshot == 'unpublished':
        raise ValueError(f'{run_id}: missing data snapshot')
    frames = {name: store.read_daily(name) for name in ('nav', 'trades', 'positions')}
    nav = frames['nav']
    required = {'date', 'nav', 'daily_return', 'benchmark_return', 'excess_return'}
    if nav.empty or not required.issubset(nav.columns):
        raise ValueError(f'{run_id}: incomplete NAV artifact')
    dates = pd.DatetimeIndex(nav['date'])
    if (dates.has_duplicates or not dates.is_monotonic_increasing
            or str(dates.min().date()) < config.start or str(dates.max().date()) > config.end
            or not np.isfinite(nav[list(required - {'date'})].to_numpy(dtype=float)).all()):
        raise ValueError(f'{run_id}: invalid NAV dates or values')
    from backtest.metrics import compute_metrics
    verified = compute_metrics(nav, frames['trades'], initial_capital=float(config.initial_capital),
                               trading_days_per_year=protocol.trading_days_per_year)
    for group in ('returns', 'risk', 'trading', 'orders'):
        for key, value in verified.get(group, {}).items():
            original = metrics.get(group, {}).get(key)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                if original is None or not np.isclose(value, original, rtol=1e-6, atol=1e-8, equal_nan=True):
                    raise ValueError(f'{run_id}: metric mismatch {group}.{key}')
            elif value != original:
                raise ValueError(f'{run_id}: metric mismatch {group}.{key}')
    for name, frame in frames.items():
        if frame.empty:
            continue
        if 'date' not in frame:
            raise ValueError(f'{run_id}: {name} has no date')
        frame['date'] = pd.to_datetime(frame['date']).dt.strftime('%Y-%m-%d')
        if not frame['date'].between(config.start, config.end).all():
            raise ValueError(f'{run_id}: {name} outside frozen window')
    return {'run_id': run_id, 'saved': saved, 'status': status, 'metrics': metrics,
            'config': config, 'profile': profile, 'snapshot': snapshot, 'frames': frames}


@serialized_publication
def publish_completed_runs(run_ids: list[str], reporter=None) -> str:
    if not run_ids or len(run_ids) != len(set(run_ids)):
        raise ValueError('Expected unique completed backtest IDs')
    # Validate on the submitting side as well; a bad artifact must never reach ADS.
    for run_id in run_ids:
        completed_result(run_id)
    from quantlens.compute import submit
    from quantlens.sync_ads import sync
    batch = 'backtest-publication-' + uuid.uuid4().hex
    submit('spark_publish_backtests', '--run-ids', json.dumps(run_ids), '--batch', batch,
           reporter=reporter)
    sync(batch)
    return batch


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--run-ids', required=True)
    args = parser.parse_args()
    print(publish_completed_runs(json.loads(args.run_ids), reporter=print), flush=True)
