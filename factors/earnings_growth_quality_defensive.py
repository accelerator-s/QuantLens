import numpy as np

def compute(panel):
    profit=panel['net_profit_ttm_parent'].astype('float64').shift(1)
    assets=panel['total_assets'].astype('float64').shift(1)
    valid=panel['is_trading'].fillna(False).astype(bool)&panel['listed_days'].ge(120)
    growth=((profit-profit.shift(63))/assets).where(assets>0)
    roa=(profit/assets).where(assets>0)
    volatility=panel['ret_wd'].astype('float64').rolling(60,min_periods=40).std(ddof=0)
    defensive=(-volatility).where(valid).rank(axis=1,pct=True)
    raw=(growth.where(valid).rank(axis=1,pct=True)+roa.where(valid).rank(axis=1,pct=True)+defensive)/3
    return raw.replace([np.inf,-np.inf],np.nan).where(valid)
