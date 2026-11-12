def compute(panel):
    profit = panel["net_profit_ttm_parent"]
    assets = panel["total_assets"]
    market_value = panel["mktcap_total"] * 1000.0
    returns = panel["ret_wd"]

    earnings_yield = (profit / market_value).where(
        (profit > 0) & (market_value > 0)
    )
    roa = (profit / assets).where(assets > 0)
    low_volatility = -returns.rolling(60, min_periods=40).std()

    value_rank = earnings_yield.rank(axis=1, pct=True)
    roa_rank = roa.rank(axis=1, pct=True)
    volatility_rank = low_volatility.rank(axis=1, pct=True)
    return (value_rank + roa_rank + volatility_rank) / 3.0
