import numpy as np

def compute(panel):
    profit=panel['net_profit_ttm_parent'].astype('float64').shift(1)
    assets=panel['total_assets'].astype('float64').shift(1)
    cap=panel['mktcap_total'].astype('float64')*1000
    valid=panel['is_trading'].fillna(False).astype(bool)&panel['listed_days'].ge(120)
    improvement=profit-profit.shift(63)
    raw=(improvement/assets).where(assets>0)
    return raw.replace([np.inf,-np.inf],np.nan).where(valid)
