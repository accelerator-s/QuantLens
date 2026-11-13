import numpy as np
import pandas as pd


def compute(panel):
    """60-day smooth uptrend efficiency.

    factor = cumulative return over lookback / sum(abs(daily returns)) over lookback
    High positive values indicate a smooth upward path; low-vol no-trend stocks are near zero.
    Uses only trailing data up to each date.
    """
    price = None
    for key in ["adj_close_nd", "adj_close_wd", "close"]:
        if key in panel:
            candidate = panel[key]
            if isinstance(candidate, pd.DataFrame):
                price = candidate
                break
    if price is None:
        raise KeyError("compute requires one of adj_close_nd, adj_close_wd, close as a DataFrame")

    lookback = 60
    min_obs = 45
    eps = 1e-8

    price = price.sort_index()
    daily_ret = price / price.shift(1) - 1.0
    cum_ret = price / price.shift(lookback) - 1.0
    abs_ret_sum = daily_ret.abs().rolling(lookback, min_periods=min_obs).sum()

    factor = cum_ret / (abs_ret_sum + eps)
    factor = factor.replace([np.inf, -np.inf], np.nan)
    factor = factor.reindex(index=price.index, columns=price.columns)
    return factor
