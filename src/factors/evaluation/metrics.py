from __future__ import annotations

import math

import numpy as np
import pandas as pd

def rank_ic_series(
    scores: pd.DataFrame,
    fwd: pd.DataFrame,
    *,
    min_cross_section: int,
) -> pd.Series:
    
    fwd = fwd.reindex(index=scores.index, columns=scores.columns)
    common = scores.notna() & fwd.notna()
    n_valid = common.sum(axis=1)

    x = scores.where(common).rank(axis=1)
    y = fwd.where(common).rank(axis=1)
    xc = x.sub(x.mean(axis=1), axis=0)
    yc = y.sub(y.mean(axis=1), axis=0)
    num = (xc * yc).sum(axis=1)
    den = np.sqrt((xc.pow(2)).sum(axis=1) * (yc.pow(2)).sum(axis=1))

    ic = num / den.replace(0.0, np.nan)
    ic[n_valid < min_cross_section] = np.nan
    ic.name = "rank_ic"
    return ic

def summarize_ic(ic: pd.Series, *, periods_per_year: int, horizon: int) -> dict:
    
    valid = ic.dropna()
    n = int(len(valid))
    total = int(len(ic))
    if n == 0:
        return {
            "n_days": total,
            "n_valid_days": 0,
            "zero_variance_or_thin_days": total,
            "ic_mean": None,
            "ic_std": None,
            "icir": None,
            "icir_annualized": None,
            "t_stat": None,
            "win_rate": None,
        }
    mean = float(valid.mean())
    std = float(valid.std(ddof=1)) if n > 1 else 0.0
    icir = mean / std if std > 0 else None
    ann_factor = math.sqrt(max(periods_per_year / max(horizon, 1), 1.0))
    return {
        "n_days": total,
        "n_valid_days": n,
        "zero_variance_or_thin_days": total - n,
        "ic_mean": round(mean, 6),
        "ic_std": round(std, 6),
        "icir": round(icir, 6) if icir is not None else None,
        "icir_annualized": round(icir * ann_factor, 6) if icir is not None else None,
        "t_stat": round(mean / std * math.sqrt(n), 4) if std > 0 else None,
        "win_rate": round(float((valid > 0).mean()), 6),
    }

def ic_half_life(decay_means: dict[int, float | None]) -> float | None:
    
    horizons = sorted(h for h, v in decay_means.items() if v is not None)
    if not horizons:
        return None
    base = decay_means[horizons[0]]
    assert base is not None
    base_abs = abs(base)
    if base_abs == 0.0:
        return None
    target = base_abs / 2.0
    prev_h, prev_v = horizons[0], base_abs
    for h in horizons[1:]:
        val = decay_means[h]
        assert val is not None
        cur = abs(val)
        if cur <= target:
            if prev_v == cur:
                return float(h)
            frac = (prev_v - target) / (prev_v - cur)
            return round(prev_h + frac * (h - prev_h), 2)
        prev_h, prev_v = h, cur
    return None

def yearly_ic_table(ic: pd.Series, *, periods_per_year: int, horizon: int) -> list[dict]:
    
    out: list[dict] = []
    for year, chunk in ic.groupby(ic.index.year):
        row = summarize_ic(chunk, periods_per_year=periods_per_year, horizon=horizon)
        out.append({"year": int(year), **row})
    return out

__all__ = ["ic_half_life", "rank_ic_series", "summarize_ic", "yearly_ic_table"]
