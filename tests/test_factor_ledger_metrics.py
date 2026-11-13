"""Completed walk-forward evidence must survive canonical ledger registration."""
import sys
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from lifecycle.factor_bridge import FactorEvolutionBridge
from lifecycle.factor_research import FactorResearchIndex
from lifecycle.models import FactorVersion


def test_walk_forward_aggregate_registers_scalar_metrics_and_preserves_missing_costs():
    factor = FactorVersion(
        factor_version_id='FV-session-v000', factor_name='probe', research_program_id='RP-session',
        data_snapshot_id='snapshot', hypothesis='test', dependencies=['close'], code_ref='probe.json',
        code_hash='hash', change_dimension='baseline', change_summary='baseline', generated_by='test',
    )
    metrics = {
        'returns': {'annualized_excess_return': -.064592},
        'risk': {'max_drawdown': -.066263},
        'trading': {'annualized_turnover_oneside': 2.486287},
        'walk_forward': {'aggregation': 'median', 'run_ids': ['BT-1', 'BT-2']},
    }
    legacy = {'status': {'iteration': 3}, 'versions': [{'version_id': 'v000'}], 'events': [{
        'event_type': 'version_evaluated', 'payload': {
            'version_id': 'v000', 'score': -.071691, 'validation': {'metrics': metrics},
            'generalization': {'classification': 'recently_decayed'}, 'constraints_passed': False,
        },
    }]}
    store = Mock()
    store.get.return_value = factor
    store.current_state.return_value = 'evaluated'
    bridge = FactorEvolutionBridge(store)
    with patch.object(FactorResearchIndex, 'assess_similarity', return_value={}), \
         patch.object(FactorResearchIndex, 'multiple_testing', return_value={'fold_count': 3}):
        assert bridge._register_evaluations('session', 'snapshot', legacy, {'v000': ['ER-1', 'ER-2']}) == ['FE-session-v000']
    registered = store.add.call_args.args[0]
    assert registered.primary_metrics['returns.annualized_excess_return'] == -.064592
    assert registered.primary_metrics['risk.max_drawdown'] == -.066263
    assert registered.primary_metrics['walk_forward.aggregation'] == 'median'
    assert 'walk_forward.run_ids' not in registered.primary_metrics
    assert registered.evaluation_run_ids == ['ER-1', 'ER-2']
    assert registered.incremental_value['annualized_turnover'] == 2.486287
    assert 'cost_total' not in registered.incremental_value


def test_incremental_value_uses_actual_backtest_trading_costs_and_keeps_zero():
    result = FactorResearchIndex.incremental_value(score=.05, parent_score=.03, validation_metrics={
        'returns': {'annualized_excess_return': .05},
        'trading': {'annualized_turnover_oneside': 0, 'total_cost_paid': 0},
    })
    assert result['annualized_turnover'] == 0
    assert result['cost_total'] == 0
    assert abs(result['score_delta'] - .02) < 1e-12
