def compute(panel):
    price = panel["adj_close_wd"]
    industry = panel["industry_code"]
    momentum = price / price.shift(60) - 1
    stacked = momentum.stack().rename("momentum")
    groups = industry.stack().reindex(stacked.index).rename("industry")
    dates = stacked.index.get_level_values(0)
    result = stacked.groupby([dates, groups]).transform("mean")
    return result.unstack().reindex_like(momentum)
