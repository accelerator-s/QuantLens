from decimal import Decimal
from copy import deepcopy
import numpy as np
import pandas as pd
import pytest

from strategy.execution_policy import policy_targets, ExecutionPolicy
from strategy.training import Candidate, beats, selection_score
from portfolio.construct import Constraints, construct
from factor_evolution.prediction import screen


def result(ret, dd, value=None, eligible=True):
    return {'comparison_hash':'same', 'folds':[{'window':{'start':'2020','end':'2021'},
        'metrics':{'returns':{'annualized_return':ret},'risk':{'max_drawdown':dd},
            'objective':{'primary':'calmar','value':ret/abs(dd) if value is None else value,'eligible':eligible}}}]}


def test_tradeoff_uses_registered_objective_and_keeps_hard_risk_limits():
    baseline=result(.083, -.1052)
    assert beats(result(.0801, -.0415), baseline)
    assert not beats(result(.15, -.21, eligible=False), baseline)
    assert not beats(result(.01, -.1), baseline)
    assert not beats(result(.08, -.04, value=float('nan')), baseline)
    with pytest.raises(ValueError):
        bad=deepcopy(baseline);bad['folds'][0]['metrics']['objective']['primary']='sharpe';beats(bad,baseline)


def test_cv_does_not_require_every_fold_to_dominate():
    baseline=result(.1,-.1);baseline['folds']*=2
    candidate=result(.09,-.1);candidate['folds']+=result(.13,-.1)['folds']
    assert beats(candidate,baseline)
    candidate['folds'][1]['metrics']['objective']['eligible']=False
    assert selection_score(candidate) is None


def test_execution_budget_cannot_delay_exits_or_increase_risk():
    limits=Constraints(max_weight=.1,max_industry_weight=.3)
    desired={'a':.08,'b':.08,'c':.08}
    current={'a':.09,'b':.07,'exit':.1}
    out=policy_targets(desired,current,{'min_trade_weight':.02,'max_buy_turnover':.03},constraints=limits,
        industries={k:k for k in desired},clusters={k:k for k in desired})
    assert 'exit' not in out
    assert out['a']==Decimal('.08')  # Reducing overall exposure wins over the band.
    assert out['b']==Decimal('.07')
    assert out['c']==Decimal('.03')
    assert sum(out.values())<=sum(map(Decimal,map(str,desired.values())))
    assert desired['a']==.08 and current['a']==.09


def test_relative_band_and_group_caps():
    desired={'a':.1,'b':.1};current={'a':.105,'b':.095}
    out=policy_targets(desired,current,{'no_trade_band':.1},constraints=Constraints(),
        industries={'a':'same','b':'same'},clusters={'a':'x','b':'y'})
    assert out['a']==Decimal('.1')  # Cannot retain a single-name cap breach.
    assert out['b']==Decimal('.095')
    with pytest.raises(ValueError):ExecutionPolicy(max_buy_turnover=0)
    with pytest.raises(ValueError):Candidate(method='equal',max_positions=10,frequency='monthly',min_trade_weight=float('nan'))


def test_final_allocation_must_afford_a_lot_with_slippage():
    frame=pd.DataFrame({'code':['000001','000002','000003'], 'score':[3.,2.,1.],
        'close':[50.,5.,5.], 'cluster_id':['x','y','z'], 'industry_code':['a','b','c']})
    limits=Constraints(max_positions=2,max_weight=.1)
    out=construct(frame,limits,capital=10000)
    assert '000001' not in set(out.code)
    assert set(out.code)=={'000002','000003'}
    assert (out.weight*10000 >= out.close*100*1.001).all()
    assert (out.weight<=.1+1e-9).all()


def test_prediction_screen_rejects_wrong_direction_without_claiming_significance():
    positive={'valid_ic_days':100,'rank_ic_mean':.02,'top_minus_bottom':.001}
    assert screen([positive])['passed']
    assert not screen([{**positive,'rank_ic_mean':-.02}])['passed']
    assert not screen([{**positive,'valid_ic_days':20}])['passed']
    assert not screen([{**positive,'top_minus_bottom':None}])['passed']
    assert not screen([positive])['statistical_significance_test']


def test_stock_attribution_reconciles_round_trip_and_industries():
    from factor_evolution.diagnostics import summarize_training
    dates=pd.to_datetime(['2020-01-02','2020-01-03','2020-01-06'])
    nav=pd.DataFrame({'date':dates,'nav':[994.,1004.,987.],'cash':[494.,494.,987.],
        'position_value':[500.,510.,0.],'daily_return':[-.006,1004/994-1,987/1004-1],
        'benchmark_return':[0.,.01,-.01],'cost_paid':[6.,0.,7.]})
    trades=pd.DataFrame({'date':dates[[0,2]],'instrument':['a','a'],'side':['buy','sell'],'status':['filled']*2,
        'amount':[501.,498.],'commission':[5.,5.],'slippage_cost':[1.,2.],
        'transfer_fee':[0.,0.],'stamp_tax':[0.,0.],'other_fees':[0.,0.]})
    positions=pd.DataFrame({'date':dates[:2],'instrument':['a','a'],'market_value':[500.,510.],'industry_code':['bank']*2})
    a=summarize_training(nav,trades,positions,start='2020-01-01',end='2020-12-31',capital=1000,benchmark={})['attribution']
    assert a['reconciled'] and a['net_pnl']==-13
    assert a['stocks'][0]['gross_pnl']==0 and a['stocks'][0]['cost']==13
    assert a['industries'][0]['net_pnl']==-13
