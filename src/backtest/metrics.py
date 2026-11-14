from __future__ import annotations

import math

import pandas as pd

def _r6(value: float) -> float:
    return round(float(value), 6)

def _drawdown(nav: pd.Series, initial_capital: float) -> tuple[float, str, str]:
    
    peak = nav.cummax().clip(lower=initial_capital)
    dd = nav / peak - 1.0
    trough_pos = int(dd.to_numpy().argmin())
    max_dd = float(dd.iloc[trough_pos])
    if max_dd >= 0:
        return 0.0, "", ""
    peak_pos = int(nav.iloc[: trough_pos + 1].to_numpy().argmax())
    peak_date = (str(nav.index[0].date()) if float(nav.iloc[:trough_pos+1].max()) < initial_capital
                 else str(nav.index[peak_pos].date()))
    return max_dd, peak_date, str(nav.index[trough_pos].date())

def _annualized(total_growth: float, n_days: int, days_per_year: int) -> float:
    if n_days <= 0 or total_growth <= 0:
        return 0.0
    return total_growth ** (days_per_year / n_days) - 1.0

def compute_metrics(
    nav_frame: pd.DataFrame,
    trades_frame: pd.DataFrame,
    *,
    initial_capital: float,
    trading_days_per_year: int,
) -> dict:
    
    nav = nav_frame.set_index(pd.to_datetime(nav_frame["date"]))["nav"].astype(float)
    daily_ret = nav_frame["daily_return"].astype(float)
    bench_ret = nav_frame["benchmark_return"].astype(float).fillna(0.0)
    excess_ret = nav_frame["excess_return"].astype(float)

    n_days = len(nav)
    years = n_days / trading_days_per_year if trading_days_per_year > 0 else 0.0
    total_growth = float(nav.iloc[-1]) / initial_capital
    bench_growth = float((1.0 + bench_ret).prod())

    ann_return = _annualized(total_growth, n_days, trading_days_per_year)
    ann_bench = _annualized(bench_growth, n_days, trading_days_per_year)

    ret_std = float(daily_ret.std(ddof=1)) if n_days > 1 else 0.0
    sharpe = (
        float(daily_ret.mean()) / ret_std * math.sqrt(trading_days_per_year) if ret_std > 0 else 0.0
    )
    ex_std = float(excess_ret.std(ddof=1)) if n_days > 1 else 0.0
    info_ratio = (
        float(excess_ret.mean()) / ex_std * math.sqrt(trading_days_per_year) if ex_std > 0 else 0.0
    )

    max_dd, dd_peak, dd_trough = _drawdown(nav, initial_capital)

    mean_nav = float(nav.mean())
    total_buy = float(nav_frame["buy_amount"].astype(float).sum())
    total_sell = float(nav_frame["sell_amount"].astype(float).sum())
    total_cost = float(nav_frame["cost_paid"].astype(float).sum())
    turnover_annual = total_buy / mean_nav / years if mean_nav > 0 and years > 0 else 0.0
    cost_drag_annual = total_cost / mean_nav / years if mean_nav > 0 and years > 0 else 0.0

    status_counts = trades_frame["status"].value_counts().to_dict() if len(trades_frame) else {}
    buys = trades_frame[trades_frame["side"] == "buy"] if len(trades_frame) else trades_frame
    intended_buy = float(buys["amount"].astype(float).sum()) if len(buys) else 0.0
    unaffordable = buys[buys["status"] == "rejected_lot_unaffordable"] if len(buys) else buys
    unaffordable_amount = (
        float(unaffordable["amount"].astype(float).sum()) if len(unaffordable) else 0.0
    )

    return {
        "window": {
            "start": str(nav.index[0].date()),
            "end": str(nav.index[-1].date()),
            "trading_days": n_days,
            "trading_days_per_year": trading_days_per_year,
        },
        "returns": {
            "final_nav": _r6(float(nav.iloc[-1])),
            "total_return": _r6(total_growth - 1.0),
            "annualized_return": _r6(ann_return),
            "benchmark_total_return": _r6(bench_growth - 1.0),
            "benchmark_annualized_return": _r6(ann_bench),
            "annualized_excess_return": _r6(ann_return - ann_bench),
        },
        "risk": {
            "sharpe": _r6(sharpe),
            "information_ratio": _r6(info_ratio),
            "annualized_volatility": _r6(ret_std * math.sqrt(trading_days_per_year)),
            "max_drawdown": _r6(max_dd),
            "max_drawdown_peak_date": dd_peak,
            "max_drawdown_trough_date": dd_trough,
            "daily_win_rate": _r6(float((daily_ret > 0).mean())) if n_days else 0.0,
            "excess_win_rate": _r6(float((excess_ret > 0).mean())) if n_days else 0.0,
        },
        "trading": {
            "total_buy_amount": _r6(total_buy),
            "total_sell_amount": _r6(total_sell),
            "annualized_turnover_oneside": _r6(turnover_annual),
            "total_cost_paid": _r6(total_cost),
            "annualized_cost_drag": _r6(cost_drag_annual),
            "avg_holdings": _r6(float(nav_frame["n_holdings"].astype(float).mean()))
            if n_days
            else 0.0,
        },
        "orders": {
            "status_counts": {str(k): int(v) for k, v in sorted(status_counts.items())},
            "lot_unaffordable_count": int(len(unaffordable)),
            "lot_unaffordable_amount": _r6(unaffordable_amount),
            "lot_unaffordable_amount_ratio": _r6(
                unaffordable_amount / intended_buy if intended_buy > 0 else 0.0
            ),
        },
    }

__all__ = ["compute_metrics"]
