from datetime import date
from decimal import Decimal
import io
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest


def test_historical_costs_follow_effective_dates_and_market():
    from market_rules import MarketRuleRegistry, RuleEngine
    engine = RuleEngine(MarketRuleRegistry().get('CN-A-stock'))
    amount = Decimal('100000')
    def costs(day, instrument='000001'):
        return {item.name:item.amount for item in engine.calculate_total_cost(
            amount,'sell',as_of=day,instrument=instrument).items}
    assert costs(date(2022,4,28))['过户费'] == Decimal('2.00')
    assert costs(date(2022,4,28),'830001')['过户费'] == Decimal('2.50')
    assert costs(date(2022,4,29))['过户费'] == Decimal('1.00')
    assert costs(date(2023,8,27))['印花税'] == Decimal('100.00')
    assert costs(date(2023,8,28))['印花税'] == Decimal('50.00')
    buy = engine.calculate_taxes(amount,'buy',as_of=date(2016,1,1))
    assert buy.total == 0


def test_chinext_historical_limits_and_delisting_exceptions():
    from market_rules import MarketRuleRegistry, RuleEngine
    engine = RuleEngine(MarketRuleRegistry().get('CN-A-stock'))
    assert engine.price_limit_range(Decimal('10'),'创业板',as_of=date(2020,8,21))[1] == 11
    assert engine.price_limit_range(Decimal('10'),'创业板',as_of=date(2020,8,24))[1] == 12
    assert engine.price_limit_range(Decimal('10'),'创业板ST',as_of=date(2020,8,24),instrument='300216')[1] == 11


def test_drawdown_includes_initial_cash_peak():
    from backtest.metrics import _drawdown
    dates = pd.bdate_range('2020-01-01',periods=3)
    loss,_,trough = _drawdown(pd.Series([900.,910.,920.],index=dates),1000.)
    assert loss == pytest.approx(-.1)
    assert trough == '2020-01-01'


@pytest.mark.parametrize('failure_stage',[None,'execute_day','compute_metrics'])
def test_observed_delisting_risk_exits_next_close_and_respects_suspension(tmp_path,monkeypatch,failure_stage):
    from backtest.engine import run_backtest
    from backtest.config import BacktestConfig
    from backtest.protocol import verify_protocol_or_raise
    from backtest.signal import SignalResult
    from backtest.report import BacktestRunStore
    from strategy import StrategyProfileStore
    from market_rules import MarketRuleRegistry
    from rich.console import Console
    import backtest.engine
    import portfolio.construct
    profile = StrategyProfileStore().resolve('research_reversal_5')
    protocol = verify_protocol_or_raise().model_copy(update={"benchmark": "same_cluster_equal_weight"})
    rule = MarketRuleRegistry().get(protocol.market_rule_name)
    monkeypatch.setenv('QUANTLENS_HOME',str(tmp_path))
    monkeypatch.setattr(backtest.engine,'MarketRuleRegistry',lambda:SimpleNamespace(get=lambda _:rule))
    dates = pd.bdate_range('2020-01-02',periods=7)
    close = pd.DataFrame(10.,index=dates,columns=['000001','000002'])
    market = {'close':close,'high':close*1.01,'low':close*.99,'ret_wd':close*0,
        'is_trading':close.notna(),'listed_days':close*0+500,'trading_status':close*0+1,
        'is_delisting':close.notna() & False}
    market['is_delisting'].loc[dates[3]:,'000001'] = True
    market['is_trading'].loc[dates[4],'000001'] = False
    monkeypatch.setattr(backtest.engine,'_signal_days',lambda *args:{dates[0]})
    monkeypatch.setattr(portfolio.construct,'construct',lambda *args,**kwargs:
                        pd.DataFrame({'code':['000001'],'weight':[.5]}))
    payload = {'market':market,'signal':SignalResult(close,{},{}),'mask':close.notna(),
        'clusters':close*0,'industries':close*0,'breadth':pd.Series(.8,index=dates)}
    config=BacktestConfig(profile_name=profile.profile_name,
        start=str(dates[0].date()),end=str(dates[-1].date()))
    if failure_stage:
        def fail_execution(*args,**kwargs):
            raise ValueError('injected execution failure')
        monkeypatch.setattr(backtest.engine,failure_stage,fail_execution)
        with pytest.raises(ValueError,match='injected execution failure'):
            run_backtest(config,protocol=protocol,profile=profile,warehouse_inputs=payload,
                         console=Console(file=io.StringIO()))
        assert BacktestRunStore.list_runs()==[]
        assert not list(tmp_path.rglob('BT-*'))
        return
    result = run_backtest(config,protocol=protocol,
        profile=profile,warehouse_inputs=payload,console=Console(file=io.StringIO()))
    trades = BacktestRunStore.open_run(result.run_id).read_daily('trades')
    sells = trades.loc[trades.side=='sell']
    assert list(sells.status) == ['pending_suspension','filled']
    assert pd.Timestamp(sells.iloc[0].date) == dates[4]
    assert pd.Timestamp(sells.iloc[1].date) == dates[5]
    assert set(trades.status) <= {'filled','pending_suspension'}
    assert result.metrics['input_quality']['passed']


def test_old_protocol_and_bypass_fields_are_rejected():
    from backtest.protocol import BacktestProtocol
    from backtest.config import BacktestConfig
    from pydantic import ValidationError
    with pytest.raises(ValidationError): BacktestProtocol(schema_version=2)
    with pytest.raises(ValidationError): BacktestProtocol(benchmark_fallback='universe_equal_weight')
    with pytest.raises(ValidationError): BacktestConfig(profile_name='x',start='2020-01-01',
        end='2020-02-01',require_style_clusters=False)


def test_market_membership_and_missing_group_data_are_explicit():
    from quantlens.security_master import is_a_share
    from portfolio.construct import construct
    assert all(is_a_share(c) for c in ['000001','001872','300001','600000','688001','920001'])
    assert not any(is_a_share(c) for c in ['200001','201872','900901','00700','a'])
    with pytest.raises(ValueError,match='industry_code'):
        construct(pd.DataFrame({'code':['000001'],'score':[1.],'cluster_id':[1],
                                'industry_code':[None]}))
