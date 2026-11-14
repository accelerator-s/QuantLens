from copy import deepcopy
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from strategy.research import ResearchSpec
from strategy.training import Candidate, fold_windows, beats, search, choose_action
from strategy.capital_guard import band, check_account, assert_generation
from strategy.account_binding import account_snapshot, validate_binding_change
from exceptions import ContractViolation, PortfolioContractError


def test_spec_rejects_manual_capital_and_requires_account():
    base = dict(display_name='研究', factors=['a','b'], train_start='2016-01-01', train_end='2020-12-31', start='2021-01-01', end='2021-12-31')
    with pytest.raises(ValueError): ResearchSpec(**base)
    with pytest.raises(ValueError): ResearchSpec(**base, account_id='aa', capital=50000)
    assert ResearchSpec(**base, account_id='aa').model == 'auto'


def test_folds_are_chronological_and_bounded():
    days = pd.bdate_range('2016-01-01', periods=600)
    folds = fold_windows(days)
    assert len(folds) == 3
    for f in folds:
        assert f['fit_start'] < f['fit_end'] < f['start'] <= f['end'] <= str(days[-1].date())
    assert folds[0]['end'] < folds[1]['start']


def outcome(candidate, ret=.05, dd=-.1):
    return {'candidate': candidate.model_dump(mode='json'), 'comparison_hash': 'fixed',
            'folds': [{'window': {'start':'2019-01-01', 'end':'2019-12-31'},
                'metrics': {'returns': {'annualized_return': ret}, 'risk': {'max_drawdown': dd}, 'objective': {'eligible': True}},
                'diagnostics': {'overview': {'capital':50000}, 'annual': [], 'costs': {'commission':500}, 'drawdown': {'max_drawdown':dd}, 'signal': {'ic': .04}}}]}


def test_measured_gates_reject_return_risk_tradeoff_and_comparison_change():
    base = outcome(Candidate(method='equal',max_positions=20,frequency='monthly'))
    assert not beats(outcome(Candidate(method='model_ridge',max_positions=20,frequency='monthly'), .1, -.2),base)
    assert beats(outcome(Candidate(method='model_ridge',max_positions=20,frequency='monthly'), .06, -.09),base)
    changed = deepcopy(base); changed['comparison_hash']='other'
    with pytest.raises(ValueError): beats(changed,base)


def test_iterative_selection_uses_cited_training_only_evidence():
    calls=[]
    def respond(context, remaining):
        assert context['stage']=='strategy_inner_training_only'
        if not context['dialogue']: return {'action':'query','section':'annual'}
        cost=next(p for p in context['evidence'] if p['section']=='costs')
        return {'action':'candidate','candidate': {'method':'model_ridge','max_positions':20,'frequency':'monthly'},
                'observation':'权重相关', 'hypothesis':'收缩减少噪声', 'alternative':'因子无互补', 'falsification':'净收益下降或回撤变大',
                'evidence_refs':[{'evidence_id':cost['evidence_id'],'path':['candidate_0','0','commission'],'value':500}]}
    def evaluate(c):
        calls.append(c.method)
        return outcome(c, .06 if c.method=='model_ridge' else .05)
    result=search(SimpleNamespace(top_n=20, rebalance_frequency='monthly', max_candidates=1, model='auto'),
                  {'research_id':'test'}, evaluate, respond)
    assert calls==['equal','model_ridge'] and result['winner']==1
    assert len(result['dialogues'][0])==2 and result['freeze_hash']


@pytest.fixture
def bound(tmp_path, monkeypatch):
    monkeypatch.setenv('QUANTLENS_HOME',str(tmp_path))
    from portfolio.accounts import AccountRegistry
    from portfolio.models import Account, Cash
    from strategy import StrategyProfileDoc, StrategyProfileStore
    account=Account(account_id='test-account',alias='测试',market='CN-A',instrument_class='stock',reporting_currency='CNY',created_at=datetime.now(UTC),cash=[Cash(currency='CNY',total=Decimal('100000'),available=Decimal('100000'))])
    registry=AccountRegistry();registry.create(account)
    snapshot=account_snapshot(account.account_id)
    store=StrategyProfileStore()
    for name in ['alpha','beta']:
        store._save_doc(StrategyProfileDoc(profile_name=name,status='active',extra={'account_binding':{'account_id':account.account_id},'creation_account_snapshot':snapshot}))
    check_account(account.account_id)
    return registry,store,account


def test_exact_band_boundaries(tmp_path,monkeypatch):
    monkeypatch.setenv('QUANTLENS_HOME',str(tmp_path))
    assert [band(v) for v in ['0','49999.99','50000','499999.99','500000']] == ['small','small','medium','medium','large']


def test_cross_band_disables_every_strategy_and_does_not_auto_resume(bound):
    registry,store,account=bound
    initial=check_account(account.account_id)['generation']
    account.cash[0].available=account.cash[0].total=Decimal('49999')
    registry.update(account)
    for name in ['alpha','beta']:
        doc=store.get(name)
        assert doc.status.value=='disabled' and doc.extra['capital_suspension']['requires_manual_enable']
    with pytest.raises(ContractViolation): assert_generation(account.account_id,initial)
    account.cash[0].available=account.cash[0].total=Decimal('100000')
    registry.update(account)
    assert store.get('alpha').status.value=='disabled'
    from strategy import StrategyStatus
    stale=store.get('alpha');stale.status=StrategyStatus.ACTIVE
    with pytest.raises(ContractViolation):store._save_doc(stale)


def test_same_band_change_keeps_strategies_active(bound):
    registry,store,account=bound
    account.cash[0].available=account.cash[0].total=Decimal('90000')
    registry.update(account)
    assert store.get('alpha').status.value=='active'


def test_buy_updates_are_checked_after_cash_and_holdings_commit(bound,monkeypatch):
    registry,store,account=bound
    from portfolio.store import PortfolioStore
    from portfolio.trades import positions_to_holdings_df
    from portfolio.transaction import transaction
    monkeypatch.setattr('portfolio.recommend.latest_close_row',lambda:('2026-11-13',pd.Series({'000001':10.})))
    portfolio=PortfolioStore();scope={'market':'CN-A','instrument_class':'stock'}
    positions={('000001','long'):{'qty':Decimal('6000'),'available_qty':Decimal('6000'),'cost_price':Decimal('10'),'currency':'CNY','as_of':datetime.now(UTC)}}
    directory=registry.ws.portfolio_account('CN-A','stock',account.account_id)
    holdings=registry.ws.portfolio_holdings('CN-A','stock',account.account_id)/'current.parquet'
    with transaction([directory/'account.yaml',holdings,registry.ws.portfolio_index]):
        account.cash[0].available=account.cash[0].total=Decimal('39995')
        registry.update(account)  # Cash temporarily below 50k must not suspend.
        assert store.get('alpha').status.value=='active'
        portfolio.write_holdings(account.account_id,positions_to_holdings_df(positions,account=account),**scope)
    assert account_snapshot(account.account_id)['capital']=='99995.0000'
    assert store.get('alpha').status.value=='active'
    # Price-only depreciation crosses the band and is detected by monitoring.
    monkeypatch.setattr('portfolio.recommend.latest_close_row',lambda:('2026-11-14',pd.Series({'000001':1.})))
    check_account(account.account_id,reason='valuation_monitor')
    assert store.get('alpha').status.value=='disabled'


def test_guard_changes_roll_back_with_failed_account_transaction(bound):
    registry,store,account=bound
    from portfolio.transaction import transaction
    directory=registry.ws.portfolio_account('CN-A','stock',account.account_id)
    with pytest.raises(RuntimeError):
        with transaction([directory/'account.yaml',registry.ws.portfolio_index]):
            account.cash[0].available=account.cash[0].total=Decimal('40000')
            registry.update(account)
            check_account(account.account_id)
            assert store.get('alpha').status.value=='disabled'
            raise RuntimeError('failed write')
    assert store.get('alpha').status.value=='active'
    assert account_snapshot(account.account_id)['capital']=='100000'


def test_binding_cannot_be_replaced_removed_or_used_for_other_account(bound):
    registry,store,account=bound
    doc=store.get('alpha')
    doc.extra['account_binding']['account_id']='another'
    with pytest.raises(ContractViolation):store._save_doc(doc)
    doc=store.get('alpha');doc.extra.pop('account_binding')
    with pytest.raises(ContractViolation):store._save_doc(doc)
    with pytest.raises(PortfolioContractError):registry.delete(account.account_id,confirm=True)


def test_ridge_handles_collinear_factors():
    import torch
    from quantlens.gpu_worker import fit_ridge
    x=torch.tensor([[1.,1.],[2.,2.],[3.,3.]],dtype=torch.float64)
    y=torch.tensor([1.,2.,3.],dtype=torch.float64)
    coef=fit_ridge(x,y)
    assert torch.isfinite(coef).all() and abs(float(coef[0]-coef[1]))<1e-8
