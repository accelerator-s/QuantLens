from __future__ import annotations

import pandas as pd
from fastapi import APIRouter

from backtest.report import BacktestRunStore
from presentation import ARCHIVED_STRATEGIES, factor_display_name, strategy_display_name

from ..serialize import df_records, downsample_indices, json_safe

router = APIRouter(tags=["backtest"])

NAV_MAX_POINTS = 1500

@router.get("/backtest/runs")
def list_runs(profile: str = "") -> dict:
    rows = BacktestRunStore.list_runs(profile or None)
    for row in rows:
        row["profile_display_name"] = strategy_display_name(row["profile"])
        row["archived"] = row["profile"] in ARCHIVED_STRATEGIES
        config = BacktestRunStore.open_run(row["run_id"]).read_config()
        factors = ((config.get("profile") or {}).get("factors") or {}).get("items") or []
        row["factor_display_names"] = [
            factor_display_name(item.get("name", "")) for item in factors if item.get("name")
        ]
    return json_safe({"items": rows})

@router.get("/backtest/runs/{run_id}")
def get_run(run_id: str) -> dict:
    store = BacktestRunStore.open_run(run_id)
    profile = store.run_dir.parent.name
    config = store.read_config()
    factors = ((config.get("profile") or {}).get("factors") or {}).get("items") or []
    return json_safe(
        {
            "run_id": store.run_id,
            "profile": profile,
            "profile_display_name": strategy_display_name(profile),
            "archived": profile in ARCHIVED_STRATEGIES,
            "factor_display_names": {
                item.get("name", ""): factor_display_name(item.get("name", ""))
                for item in factors
                if item.get("name")
            },
            "status": store.read_status(),
            "config": config,
            "metrics": store.read_metrics(),
        }
    )

@router.get("/backtest/runs/{run_id}/nav")
def get_nav(run_id: str) -> dict:
    store = BacktestRunStore.open_run(run_id)
    nav = store.read_daily("nav").copy()
    nav["date"] = nav["date"].astype(str)

    nav_values = nav["nav"].astype(float)
    running_peak = nav_values.cummax()
    nav["drawdown"] = nav_values / running_peak - 1.0
    if "benchmark_return" in nav.columns:
        nav["benchmark_nav"] = (1.0 + nav["benchmark_return"].astype(float).fillna(0.0)).cumprod()

    monthly: list[dict] = []
    if "daily_return" in nav.columns:
        df = nav[["date", "daily_return"]].copy()
        df["month"] = df["date"].str[:7]
        grouped = (1.0 + df["daily_return"].astype(float).fillna(0.0)).groupby(df["month"]).prod()
        monthly = [{"month": m, "value": float(v) - 1.0} for m, v in grouped.items()]

    trough_idx = int(nav["drawdown"].idxmin()) if len(nav) else 0
    picked = downsample_indices(len(nav), NAV_MAX_POINTS, keep=[trough_idx])
    sampled = nav.iloc[picked]
    return {
        "total_rows": len(nav),
        "items": df_records(sampled),
        "monthly_returns": monthly,
    }

@router.get("/backtest/runs/{run_id}/trades")
def get_trades(run_id: str, limit: int = 200, offset: int = 0) -> dict:
    store = BacktestRunStore.open_run(run_id)
    trades = store.read_daily("trades")
    return {
        "total_rows": len(trades),
        "items": df_records(trades, limit=limit, offset=offset),
    }

@router.get("/backtest/runs/{run_id}/positions")
def get_positions(run_id: str, date: str = "", limit: int = 500) -> dict:
    store = BacktestRunStore.open_run(run_id)
    positions = store.read_daily("positions")
    dates = sorted(pd.unique(positions["date"].astype(str)).tolist())
    target = date or (dates[-1] if dates else "")
    day = positions[positions["date"].astype(str) == target] if target else positions.iloc[0:0]
    if "weight" in day.columns:
        day = day.sort_values("weight", ascending=False)
    return {
        "dates": dates,
        "date": target,
        "total_rows": len(day),
        "items": df_records(day, limit=limit),
    }
