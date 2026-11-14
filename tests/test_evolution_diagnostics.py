import copy

import pandas as pd
import pytest

from exceptions import ContractViolation
from factor_evolution.analysis import analyze
from factor_evolution.diagnostics import summarize_training, signal_summary, comparison_contract, digest
from factor_evolution.selection import training_noninferiority


@pytest.fixture
def frames():
    dates = pd.to_datetime(['2020-01-02', '2020-01-03', '2020-01-06'])
    nav = pd.DataFrame({'date': dates, 'nav': [995., 1005., 990.], 'cash': [495., 495., 990.],
        'position_value': [500., 510., 0.], 'daily_return': [-.005, 1005/995-1, 990/1005-1],
        'benchmark_return': [0., .02, -.02], 'cost_paid': [5., 0., 5.]})
    trades = pd.DataFrame({'date': dates[[0, 2]], 'status': ['filled']*2,
        'amount': [500., 500.], 'commission': [5., 5.], 'transfer_fee': [0., 0.],
        'stamp_tax': [0., 0.], 'other_fees': [0., 0.], 'slippage_cost': [0., 0.]})
    positions = pd.DataFrame({'date': dates[:2], 'instrument': ['000001']*2, 'market_value': [500., 510.]})
    return nav, trades, positions


def summarize(frames):
    return summarize_training(*frames, start='2020-01-01', end='2020-12-31', capital=1000,
                              benchmark={'return_benchmark': {'kind': 'index', 'code': 'H00985'}, 'market_reference': '000985'})


def test_actual_costs_and_cash_reconcile(frames):
    result = summarize(frames)
    assert result['overview']['reconciled']
    assert result['costs']['commission_at_five_orders'] == 2
    assert result['annual'][0]['net_pnl'] == -10
    assert result['annual'][0]['holdings_pnl_before_cost'] == 0
    assert result['drawdown']['net_pnl'] == -15
    assert result['drawdown']['cost'] == 5


@pytest.mark.parametrize('corruption', ['future', 'cost', 'holdings', 'nav', 'duplicate', 'nan'])
def test_bad_evidence_fails_closed(frames, corruption):
    nav, trades, positions = frames
    if corruption == 'future': trades.loc[0, 'date'] = pd.Timestamp('2025-01-01')
    if corruption == 'cost': trades.loc[0, 'commission'] = 0
    if corruption == 'holdings': positions.loc[0, 'market_value'] = 400
    if corruption == 'nav': nav.loc[0, 'nav'] = 1000
    if corruption == 'duplicate': positions = pd.concat([positions, positions.iloc[:1]])
    if corruption == 'nan': trades.loc[0, 'commission'] = float('nan')
    with pytest.raises(ContractViolation): summarize((nav, trades, positions))


def packet(frames):
    sections = summarize(frames)
    sections['signal'] = {'rank_ic': .02}
    return {'run_id': 'BT-train', 'definition_hash': 'factor', 'comparison_hash': 'same', 'sections': sections}


def hypothesis(context):
    cost = next(p for p in context['evidence'] if p['section'] == 'costs')
    return {'action': 'hypothesis', 'observation': '存在最低佣金',
            'hypothesis': '信号短期噪音导致排名波动', 'alternative': '可能是资金过小而非信号问题',
            'falsification': '预测与净收益没有改善则拒绝', 'change_type': 'lookback', 'primary_metric': 'net_return',
            'evidence_refs': [{'evidence_id': cost['evidence_id'], 'path': ['total_cost'], 'value': 10.0}]}


def test_multiturn_queries_and_development_share_contract(frames):
    seen = []
    def responder(context, remaining):
        seen.append(context)
        if len(seen) == 1: return {'action': 'query', 'section': 'annual'}
        assert any(p['section'] == 'annual' for p in context['evidence'])
        assert all(p['scope'] == 'training_only' for p in context['evidence'])
        return hypothesis(context)
    result = analyze(packet(frames), responder, max_turns=3, timeout_seconds=5)
    assert result['decision']['action'] == 'hypothesis' and len(result['dialogue']) == 2


@pytest.mark.parametrize('field,value', [('value', 0), ('evidence_id', 'from-test'), ('path', ['missing'])])
def test_unverifiable_citations_rejected(frames, field, value):
    def responder(context, _):
        action = hypothesis(context)
        action['evidence_refs'][0][field] = value
        return action
    with pytest.raises(ContractViolation): analyze(packet(frames), responder, max_turns=2, timeout_seconds=5)


def test_repeated_queries_are_bounded(frames):
    with pytest.raises(ContractViolation):
        analyze(packet(frames), lambda *_: {'action': 'query', 'section': 'annual'}, max_turns=2, timeout_seconds=5)


def test_lower_net_return_is_not_redeemed_by_calmar():
    parent = {'returns': {'annualized_return': .06}, 'risk': {'max_drawdown': -.16}, 'research_comparison_hash': 'same'}
    candidate = {'returns': {'annualized_return': .05}, 'risk': {'max_drawdown': -.10}, 'research_comparison_hash': 'same'}
    assert not training_noninferiority(candidate, parent)
    candidate['returns']['annualized_return'] = .06
    assert training_noninferiority(candidate, parent)
    candidate['research_comparison_hash'] = 'other-capital'
    with pytest.raises(ContractViolation): training_noninferiority(candidate, parent)


def test_benchmark_reference_is_frozen_independently_of_factor():
    from factor_evolution.models import EvolutionConfig
    config = EvolutionConfig(base_factor='a', profile_name='a', train_window={'start':'2016-01-01','end':'2021-12-31'},
        validation_window={'start':'2022-01-01','end':'2024-12-31'},test_window={'start':'2025-01-01','end':'2026-01-01'},
        targets={'min_annualized_excess_return':.03,'max_drawdown':.2,'max_annualized_turnover':6,'min_oos_stability':.5})
    profile = {'factors': {'name': 'a'}, 'constraints': {'max_positions': 30}, 'extra': {'benchmark_plan': {
        'return_benchmark': {'kind':'index','code':'H00985'}, 'market_reference':'000985','rationale':'境内全市场'}}}
    original = digest(comparison_contract(config, {}, profile))
    profile['factors']['name'] = 'b'
    assert digest(comparison_contract(config, {}, profile)) == original
    profile['extra']['benchmark_plan']['market_reference'] = '000300'
    assert digest(comparison_contract(config, {}, profile)) != original
    profile['extra'] = {}
    with pytest.raises(ContractViolation): comparison_contract(config, {}, profile)


def test_signal_summary_rejects_future_and_constant_signal():
    daily = pd.DataFrame({'date':['2020-01-02'], 'label_max_end':['2020-01-10'], 'horizon':[5], 'rank_ic':[.02], 'coverage':[.9], 'q5_ret':[.01], 'q1_ret':[0.]})
    assert signal_summary(daily,start='2020-01-01',end='2020-12-31',horizons=[5])['horizons'][0]['rank_ic_mean']==.02
    with pytest.raises(ContractViolation): signal_summary(daily,start='2019-01-01',end='2019-12-31',horizons=[5])
    future = daily.assign(label_max_end='2021-01-05')
    with pytest.raises(ContractViolation): signal_summary(future,start='2020-01-01',end='2020-12-31',horizons=[5])
    daily['rank_ic'] = float('nan')
    with pytest.raises(ContractViolation): signal_summary(daily,start='2020-01-01',end='2020-12-31',horizons=[5])


@pytest.mark.parametrize('development', [True, False])
def test_production_evidence_dialogue_and_tamper_detection(tmp_path, monkeypatch, frames, development):
    from types import SimpleNamespace
    from factor_evolution.models import EvolutionConfig, FactorVersion
    from factor_evolution.services import ProductionEvolutionServices
    from backtest.report import BacktestRunStore
    monkeypatch.setenv('QUANTLENS_HOME', str(tmp_path))
    config = EvolutionConfig(base_factor='probe',profile_name='probe',initial_capital=1000,
        train_window={'start':'2020-01-01','end':'2020-12-31'},
        validation_window={'start':'2021-01-01','end':'2021-12-31'},
        test_window={'start':'2022-01-01','end':'2022-12-31'},
        targets={'min_annualized_excess_return':.03,'max_drawdown':.2,'max_annualized_turnover':6,'min_oos_stability':.5})
    version = FactorVersion(version_id='base',factor_name='probe',assumption='probe',direction='positive',timescale='daily',
        dependencies=['close'],code='def compute(p): return p["close"]',change_type='baseline',change_summary='baseline')
    profile = {'constraints': {'max_positions':30},'extra':{'benchmark_plan':{
        'return_benchmark':{'kind':'universe_equal_weight'},'market_reference':'000985','rationale':'全A'}}}
    wrapped = SimpleNamespace(model_dump=lambda **_: profile)
    protocol = SimpleNamespace(model_dump=lambda **_: {'benchmark':'model_selected'})
    record = BacktestRunStore.create(profile_name='probe',run_id='BT-fixture',
                                    config_payload={'data_fingerprint':{'data_snapshot':'snapshot'}})
    for name, frame in zip(['nav','trades','positions'],frames): record.write_daily(name,frame)
    record.update_status(state='completed')
    def respond(context, _):
        return {'action':'query','section':'annual'} if not context['dialogue'] else hypothesis(context)
    service = ProductionEvolutionServices(analyst=respond if development else None)
    monkeypatch.setattr(service,'_context',lambda _: (protocol,wrapped))
    class Router:
        def __init__(self, **kwargs):
            assert not development, 'Development analysis must not invoke a configured model'
        def run_task(self, **kwargs):
            import json
            return {'config_kind':'factor_training_analysis','proposed_value':respond(json.loads(kwargs['prompt']),1)}
    monkeypatch.setattr('ai.router.AIRouter',Router)
    service._write_training_evidence(config,version,record,protocol,wrapped,{'rank_ic':.02},{})
    memory = [{'version_id':'base','metrics':{'train':{'run_id':'BT-fixture'},'validation':{}}}]
    result = service._diagnose(config,version,memory,timeout_seconds=10)
    assert len(result['dialogue']) == 2
    assert result['analyst'] == ('development' if development else 'configured_ai')
    leak = copy.deepcopy(memory); leak[0]['metrics']['validation'] = {'run_id':'BT-test'}
    with pytest.raises(ContractViolation): service._diagnose(config,version,leak,10)
    frames[0].loc[0,'nav'] = 999
    record.write_daily('nav',frames[0])
    with pytest.raises(ContractViolation): service._diagnose(config,version,memory,10)
