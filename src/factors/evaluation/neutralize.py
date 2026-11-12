from __future__ import annotations

import numpy as np
import pandas as pd

from .config import EvaluationConfig, Neutralization

def zscore_cross_section(scores: pd.DataFrame) -> pd.DataFrame:
    
    mean = scores.mean(axis=1)
    std = scores.std(axis=1, ddof=0)
    centered = scores.sub(mean, axis=0)
    safe_std = std.replace(0.0, np.nan)
    out = centered.div(safe_std, axis=0)
    
    zero_rows = std == 0.0
    if bool(zero_rows.any()):
        out.loc[zero_rows] = centered.loc[zero_rows]
    return out

def _snap_noise(resid: np.ndarray, scale: float) -> np.ndarray:
    
    tol = 1e-12 * max(1.0, scale)
    return np.where(np.abs(resid) <= tol, 0.0, resid)

def _demean_within(values: pd.Series, groups: pd.Series) -> pd.Series:
    
    valid = values.notna() & groups.notna()
    out = pd.Series(np.nan, index=values.index)
    if valid.sum() == 0:
        return out
    v = values[valid]
    resid = (v - v.groupby(groups[valid]).transform("mean")).to_numpy(dtype="float64")
    out[valid] = _snap_noise(resid, float(v.abs().max()))
    return out

def _residual_on(values: pd.Series, regressor: pd.Series) -> pd.Series:
    
    valid = values.notna() & regressor.notna()
    out = pd.Series(np.nan, index=values.index)
    if valid.sum() < 2:
        out[valid] = values[valid]
        return out
    x = regressor[valid].to_numpy(dtype="float64")
    y = values[valid].to_numpy(dtype="float64")
    var = float(np.dot(x, x))
    if var <= 0.0:
        out[valid] = y
        return out
    beta = float(np.dot(x, y)) / var
    out[valid] = _snap_noise(y - beta * x, float(np.abs(y).max()))
    return out

def neutralize_scores(
    scores: pd.DataFrame,
    kind: Neutralization,
    *,
    config: EvaluationConfig,
    industry: pd.DataFrame | None = None,
    log_mktcap: pd.DataFrame | None = None,
) -> pd.DataFrame:
    
    base = zscore_cross_section(scores) if config.standardize == "zscore" else scores
    if kind is Neutralization.RAW:
        return base

    need_industry = kind in (Neutralization.INDUSTRY, Neutralization.INDUSTRY_SIZE)
    need_size = kind in (Neutralization.SIZE, Neutralization.INDUSTRY_SIZE)
    if need_industry:
        assert industry is not None, "行业中性化需要 industry_code 面板"
    if need_size:
        assert log_mktcap is not None, "市值中性化需要 mktcap_float 面板"

    ind_aligned = (
        industry.reindex(index=base.index, columns=base.columns) if need_industry else None
    )
    size_aligned = log_mktcap.reindex(index=base.index, columns=base.columns) if need_size else None

    out = pd.DataFrame(np.nan, index=base.index, columns=base.columns)
    for dt in base.index:
        row = base.loc[dt]
        if ind_aligned is not None:
            ind_row = ind_aligned.loc[dt]
            row = _demean_within(row, ind_row)
            if size_aligned is not None:
                size_row = _demean_within(size_aligned.loc[dt], ind_row)
                row = _residual_on(row, size_row)
        elif size_aligned is not None:
            size_row = size_aligned.loc[dt]
            valid = row.notna() & size_row.notna()
            centered = row.where(valid)
            centered[valid] = row[valid] - row[valid].mean()
            size_centered = size_row.where(valid)
            size_centered[valid] = size_row[valid] - size_row[valid].mean()
            row = _residual_on(centered, size_centered)
        out.loc[dt] = row
    return out

__all__ = ["neutralize_scores", "zscore_cross_section"]
