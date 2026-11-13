import numpy as np
import pandas as pd


def compute(panel):
    """60-day smooth uptrend efficiency with trend-regime beta tilt.

    Base factor = cumulative return over lookback / sum(abs(daily returns)).
    In strong equal-weight market trend states, tilt the cross-section toward
    low-beta low-efficiency names by adding a beta-zscore term; in range states
    the tilt is near zero. Uses only trailing data up to each date.
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
    beta_lookback = 60
    beta_min_obs = 45
    regime_lookback = 20
    regime_vol_lookback = 60
    regime_vol_min_obs = 40
    regime_scale_lookback = 250
    regime_scale_min_obs = 120
    regime_weight = 0.15

    price = price.sort_index()
    daily_ret = price / price.shift(1) - 1.0
    cum_ret = price / price.shift(lookback) - 1.0
    abs_ret_sum = daily_ret.abs().rolling(lookback, min_periods=min_obs).sum()
    raw = cum_ret / (abs_ret_sum + eps)
    raw = raw.replace([np.inf, -np.inf], np.nan)

    mkt_ret = daily_ret.mean(axis=1, skipna=True)
    mkt_cum = mkt_ret.rolling(regime_lookback, min_periods=max(2, regime_lookback // 2)).sum()
    mkt_vol = mkt_ret.rolling(regime_vol_lookback, min_periods=regime_vol_min_obs).std()
    regime_strength = mkt_cum.abs() / (mkt_vol + eps)
    regime_scale = regime_strength.rolling(regime_scale_lookback, min_periods=regime_scale_min_obs).quantile(0.90)
    regime_score = (regime_strength / (regime_scale + eps)).clip(lower=0.0, upper=1.5) / 1.5
    regime_score = regime_score.clip(0.0, 1.0).fillna(0.0)

    mkt_mean = mkt_ret.rolling(beta_lookback, min_periods=beta_min_obs).mean()
    mkt_var = mkt_ret.rolling(beta_lookback, min_periods=beta_min_obs).var()
    stock_mean = daily_ret.rolling(beta_lookback, min_periods=beta_min_obs).mean()
    stock_mkt_mean = daily_ret.mul(mkt_ret, axis=0).rolling(beta_lookback, min_periods=beta_min_obs).mean()
    cov = stock_mkt_mean - stock_mean.mul(mkt_mean, axis=0)
    beta = cov.div(mkt_var + eps, axis=0)

    beta_mean = beta.mean(axis=1, skipna=True)
    beta_std = beta.std(axis=1, skipna=True, ddof=0)
    beta_z = beta.sub(beta_mean, axis=0).div(beta_std.replace(0.0, np.nan) + eps, axis=0)
    beta_z = beta_z.clip(-3.0, 3.0).fillna(0.0)

    tilt = beta_z.mul(regime_weight * regime_score, axis=0)
    adj = raw + tilt
    adj = adj.replace([np.inf, -np.inf], np.nan)
    adj = adj.reindex(index=price.index, columns=price.columns)
    return adj
