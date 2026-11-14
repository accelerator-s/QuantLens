import importlib.util
from pathlib import Path

import numpy as np
import pandas as pd

from factor_evolution.supervision import audit_factor


SOURCE = Path(__file__).resolve().parents[1] / 'factors/anti_lottery_max_21d.py'
spec = importlib.util.spec_from_file_location('anti_lottery_max_21d', SOURCE)
factor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(factor)


def panel():
    dates = pd.bdate_range('2019-01-01', periods=200)
    rng = np.random.default_rng(42)
    returns = pd.DataFrame(rng.normal(0, .02, (200, 4)), index=dates,
                           columns=['000001','000002','600001','600002'])
    return {'ret_wd': returns, 'is_trading': returns.notna(),
            'listed_days': returns * 0 + 500}


def test_low_max_direction_and_extreme_event_expiry():
    p = panel()
    p['ret_wd'].loc[:, :] = .001
    p['ret_wd'].iloc[25, 0] = .09
    scores = factor.compute(p)
    assert scores.iloc[25, 0] < scores.iloc[25, 1]
    assert scores.iloc[45, 0] == -.09
    assert scores.iloc[46, 0] == -.001
    assert scores.iloc[:20].isna().all(axis=None)


def test_missing_suspended_and_young_stocks_are_not_zero_filled():
    p = panel()
    p['is_trading'].iloc[50, 0] = False
    p['ret_wd'].iloc[70, 1] = np.nan
    p['listed_days'].iloc[:, 2] = 119
    scores = factor.compute(p)
    assert scores.iloc[50:71, 0].isna().all()
    assert scores.iloc[70:91, 1].isna().all()
    assert scores.iloc[:, 2].isna().all()
    assert scores.iloc[71, 0] == -p['ret_wd'].iloc[51:72, 0].max()


def test_causal_deterministic_and_chunk_invariant():
    assert audit_factor(SOURCE.read_text(), panel(), min_history=21)['passed']
