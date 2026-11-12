import numpy as np
import pandas as pd


def compute(panel):
    """下影线 × 量能偏度 × 价格偏离（研究基线）。

    输入是 QuantLens 的宽表面板：索引为交易日，列为证券代码。
    因子内部先按“过去 20 个交易日平均成交额前 500”构建动态股池，
    再在池内做逐日截面排名；池外返回 NaN，评价与回测都会跳过。
    """
    open_ = panel["open"].astype("float64")
    high = panel["high"].astype("float64")
    low = panel["low"].astype("float64")
    close = panel["close"].astype("float64")
    volume = panel["volume"].astype("float64")
    amount = panel["amount"].astype("float64")
    adj_close = panel["adj_close_wd"].astype("float64")
    is_trading = panel["is_trading"].astype(bool)
    listed_days = panel["listed_days"].astype("float64")

    # 动态股池：可交易、上市满 120 日、当日有真实振幅，按 20 日均成交额取前 500。
    # 一字板（high == low）不参与，因为其下影线机械为 0，会污染截面。
    eligible = is_trading & (listed_days >= 120) & high.gt(low)
    amt20 = amount.where(eligible).rolling(20, min_periods=20).mean()
    pool = amt20.rank(axis=1, ascending=False, method="first").le(500) & eligible

    # 下影线长度（元），再做个股 30 日时序 z-score。
    lower_shadow = (np.minimum(open_, close) - low).clip(lower=0.0)
    ls_mean = lower_shadow.rolling(30, min_periods=15).mean()
    ls_std = lower_shadow.rolling(30, min_periods=15).std()
    z_ls = (lower_shadow - ls_mean) / (ls_std + 1e-12)

    # 成交量变化率，滞后 1 日避免同日量价互相解释；极端值先截断再算 30 日偏度。
    vol_chg = (
        volume.pct_change(fill_method=None)
        .replace([np.inf, -np.inf], np.nan)
        .shift(1)
        .clip(lower=-0.95, upper=5.0)
    )
    vol_skew = vol_chg.rolling(30, min_periods=15).skew()

    # 价格偏离 30 日均线；使用复权收盘价，避免除权除息造成假偏离。
    adj_ma30 = adj_close.rolling(30, min_periods=15).mean()
    price_dev = adj_close / adj_ma30 - 1.0

    raw = (z_ls * (-vol_skew) * price_dev).replace([np.inf, -np.inf], np.nan)
    raw = raw.where(pool)

    # 只在股池内做逐日截面排名；池外保持 NaN。
    return raw.rank(axis=1, pct=True)
