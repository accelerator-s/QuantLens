from __future__ import annotations
from dataclasses import dataclass
import numpy as np
import pandas as pd


@dataclass(frozen=True)
class Constraints:
    max_weight: float = .10
    max_cluster_weight: float = .35
    max_industry_weight: float = .30
    cash_reserve: float = .10
    max_positions: int = 30

    def __post_init__(self):
        if not all(0<x<=1 for x in [self.max_weight,self.max_cluster_weight,self.max_industry_weight]) or not 0<=self.cash_reserve<1 or self.max_positions<1:
            raise ValueError('Invalid portfolio constraints')


def construct(candidates, constraints=Constraints(), *, breadth=None, regime=None,
              star_market_enabled=True, capital=None):
    """Allocate positive score mass subject to overlapping group capacities."""
    frame=candidates.copy()
    if frame.code.duplicated().any(): raise ValueError('Duplicate portfolio code')
    allowed=pd.Series(True,index=frame.index)
    from portfolio.permissions import can_buy
    allowed &= frame.code.map(lambda code: can_buy(code, star_market_enabled=star_market_enabled))
    if capital is not None:
        from decimal import Decimal
        from backtest.tradability import board_of
        from market_rules import MarketRuleRegistry, RuleEngine
        engine = RuleEngine(MarketRuleRegistry().resolve('CN-A', 'stock'))
        def affordable(row):
            if pd.isna(row.close) or row.close <= 0:
                return False
            minimum, _ = engine.resolve_min_unit(board_of(str(row.code)))
            amount = Decimal(minimum) * Decimal(str(row.close))
            return amount <= Decimal(str(capital)) * Decimal(str(constraints.max_weight)) and amount + engine.calculate_total_cost(amount, 'buy').total <= Decimal(str(capital)) * Decimal(str(1-constraints.cash_reserve))
        allowed &= frame.apply(affordable, axis=1)
    for col in ['is_trading','in_universe']:
        if col in frame: allowed &= frame[col].fillna(False).astype(bool)
    for col in ['is_suspended','limit_up','is_delisting']:
        if col in frame: allowed &= ~frame[col].fillna(True).astype(bool)
    # Unknown classifications are one conservative group, never unconstrained.
    for col in ['cluster_id','industry_code']:
        if col not in frame: frame[col]='unknown'
        frame[col]=frame[col].fillna('unknown').astype(str)
    frame=frame.loc[allowed & np.isfinite(frame.score)].sort_values(['score','code'],ascending=[False,True],kind='stable').head(constraints.max_positions).copy()
    frame['weight']=0.0
    budget=1-constraints.cash_reserve
    if frame.empty: return frame
    scores=frame.score.to_numpy(dtype=float)
    # Shift negative scores into nonnegative proportional allocations.
    scores=np.maximum(scores-min(0,float(scores.min())),0)
    if scores.sum()==0: scores=np.ones(len(scores))
    for _ in range(20):
        remaining=budget-float(frame.weight.sum())
        if remaining<1e-12: break
        capacity=np.maximum(0,constraints.max_weight-frame.weight.to_numpy())
        for col,cap in [('cluster_id',constraints.max_cluster_weight),('industry_code',constraints.max_industry_weight)]:
            residual=cap-frame.groupby(col).weight.transform('sum').to_numpy()
            # Divide shared residual among eligible members by score mass.
            active=capacity>1e-12
            shares=pd.Series(np.where(active,scores,0),index=frame.index)
            group_total=shares.groupby(frame[col]).transform('sum').to_numpy()
            group_share=np.divide(scores,group_total,out=np.zeros_like(scores),where=group_total>0)
            capacity=np.minimum(capacity,np.maximum(0,residual)*group_share)
        active=capacity>1e-12
        mass=np.where(active,scores,0)
        if mass.sum()==0: break
        increment=np.minimum(capacity,remaining*mass/mass.sum())
        frame['weight']+=increment
    regime=regime or {}
    if regime.get('enabled',False):
        if breadth is None or not np.isfinite(breadth): raise ValueError('Enabled regime filter requires market breadth')
        if breadth<regime.get('breadth_crisis',.3): frame['weight']*=regime.get('exposure_crisis',.25)
        elif breadth<regime.get('breadth_low',.4): frame['weight']*=regime.get('exposure_low',.5)
    if frame.weight.sum()>budget+1e-9: raise AssertionError('Cash constraint violated')
    return frame.loc[frame.weight>1e-12].reset_index(drop=True)
