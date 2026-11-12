def compute(panel):
    profit = panel["net_profit_ttm_parent"]
    prior = profit.shift(1)
    update = (profit != prior) & profit.notna() & prior.notna() & (prior.abs() > 0)
    surprise = ((profit - prior) / prior.abs()).where(update)
    return surprise.ffill(limit=19)
