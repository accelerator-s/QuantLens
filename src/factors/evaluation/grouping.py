from __future__ import annotations

import math

import numpy as np
import pandas as pd

def assign_groups(scores: pd.DataFrame, n_groups: int) -> pd.DataFrame:
    
    pct = scores.rank(axis=1, pct=True)
    groups = np.ceil(pct.to_numpy(dtype="float64") * n_groups)
    out = pd.DataFrame(groups, index=scores.index, columns=scores.columns)
    return out.clip(lower=1.0, upper=float(n_groups)).where(pct.notna())

def group_daily_returns(groups: pd.DataFrame, fwd1: pd.DataFrame, n_groups: int) -> pd.DataFrame:
    
    fwd1 = fwd1.reindex(index=groups.index, columns=groups.columns)
    cols: dict[str, pd.Series] = {}
    for g in range(1, n_groups + 1):
        member = groups == float(g)
        cols[f"q{g}"] = fwd1.where(member).mean(axis=1)
    return pd.DataFrame(cols)

def turnover_series(groups: pd.DataFrame, n_groups: int) -> pd.DataFrame:
    
    prev = groups.shift(1)
    cols: dict[str, pd.Series] = {}
    for g in range(1, n_groups + 1):
        cur = groups == float(g)
        was = prev == float(g)
        size = cur.sum(axis=1).astype("float64")
        entries = (cur & ~was).sum(axis=1).astype("float64")
        s = entries / size.replace(0.0, np.nan)
        s.iloc[:1] = np.nan
        cols[f"q{g}"] = s
    return pd.DataFrame(cols)

def max_drawdown(nav: pd.Series) -> float | None:
    
    clean = nav.dropna()
    if clean.empty:
        return None
    peak = clean.cummax()
    dd = 1.0 - clean / peak
    return round(float(dd.max()), 6)

def annualized_return(daily: pd.Series, *, periods_per_year: int) -> float | None:
    clean = daily.dropna()
    if clean.empty:
        return None
    nav_end = float((1.0 + clean).prod())
    if nav_end <= 0:
        return None
    return round(nav_end ** (periods_per_year / len(clean)) - 1.0, 6)

def monotonicity_stats(group_daily: pd.DataFrame, *, periods_per_year: int) -> dict:
    
    n_groups = group_daily.shape[1]
    ann = [
        annualized_return(group_daily.iloc[:, i], periods_per_year=periods_per_year)
        for i in range(n_groups)
    ]
    valid = [(i + 1, a) for i, a in enumerate(ann) if a is not None]
    if len(valid) < 2:
        return {"group_annualized_returns": ann, "spearman": None, "adjacent_ordered_ratio": None}
    order = pd.Series([a for _, a in valid], index=[i for i, _ in valid])
    spearman = float(order.rank().corr(pd.Series(order.index, index=order.index).rank()))
    pairs = [(valid[i][1], valid[i + 1][1]) for i in range(len(valid) - 1)]
    up = sum(1 for a, b in pairs if b > a)
    down = sum(1 for a, b in pairs if b < a)
    ordered = max(up, down)  
    return {
        "group_annualized_returns": ann,
        "spearman": round(spearman, 6),
        "adjacent_ordered_ratio": round(ordered / len(pairs), 6),
    }

def long_short_stats(
    group_daily: pd.DataFrame,
    turnover: pd.DataFrame,
    *,
    direction: int,
    periods_per_year: int,
) -> tuple[dict, pd.Series]:
    
    top = group_daily.iloc[:, -1]
    bottom = group_daily.iloc[:, 0]
    ls = (top - bottom) * float(direction)
    ls_valid = ls.dropna()

    nav = (1.0 + ls.fillna(0.0)).cumprod()
    nav.name = "nav"

    ls_turnover = None
    if not turnover.empty:
        both = pd.concat([turnover.iloc[:, 0], turnover.iloc[:, -1]], axis=1).mean(axis=1)
        if both.notna().any():
            ls_turnover = round(float(both.mean()), 6)

    vol = None
    sharpe = None
    if len(ls_valid) > 1:
        std = float(ls_valid.std(ddof=1))
        if std > 0:
            vol = round(std * math.sqrt(periods_per_year), 6)
            mean_ann = float(ls_valid.mean()) * periods_per_year
            sharpe = round(mean_ann / vol, 4)

    stats = {
        "direction": direction,
        "n_days": int(len(ls_valid)),
        "annualized_return": annualized_return(ls, periods_per_year=periods_per_year),
        "annualized_volatility": vol,
        "sharpe_like": sharpe,
        "max_drawdown": max_drawdown(nav),
        "avg_daily_turnover_oneside": ls_turnover,
        "turnover_note": "换手率为分组名单变化近似，非成交模拟。",
    }
    return stats, nav

__all__ = [
    "annualized_return",
    "assign_groups",
    "group_daily_returns",
    "long_short_stats",
    "max_drawdown",
    "monotonicity_stats",
    "turnover_series",
]
