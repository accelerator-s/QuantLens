import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from exceptions import ContractViolation
from strategy.data_preparation import missing_window
from strategy.training import fold_windows


def test_missing_history_uses_warmup_and_stays_inside_requested_stage():
    days = pd.bdate_range('2020-01-01', periods=300).date
    start, end = str(days[0]), str(days[239])
    assert missing_window(days, [], min_history=80, start=start, end=end) == (str(days[80]), end)
    assert missing_window(days, days[80:240], min_history=80, start=start, end=end) is None
    present = list(days[80:120]) + list(days[121:180]) + list(days[181:])
    assert missing_window(days, present, min_history=80, start=start, end=end) == (str(days[120]), str(days[180]))


@pytest.mark.parametrize('count', [0, 239])
def test_genuine_short_history_has_actionable_error(count):
    with pytest.raises(ContractViolation) as error:
        fold_windows(pd.bdate_range('2020-01-01', periods=count))
    assert error.value.code == 'STRATEGY_TRAINING_HISTORY_SHORT'
    assert error.value.context['available_days'] == count


def test_minimum_history_still_preserves_three_disjoint_validation_folds():
    days = pd.bdate_range('2020-01-01', periods=240)
    folds = fold_windows(days)
    assert len(folds) == 3
    assert all(f['fit_end'] < f['start'] <= f['end'] for f in folds)
    assert folds[0]['end'] < folds[1]['start'] <= folds[1]['end'] < folds[2]['start']
