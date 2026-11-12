from __future__ import annotations

from typing import Any

from .store import ExperimentStore

_PRIMARY_METRIC = "annualized_excess_return"
_FALLBACK_METRIC = "annualized_return"

# 对比指标：(组, 键)
_DELTA_KEYS: tuple[tuple[str, str], ...] = (
    ("returns", "annualized_excess_return"),
    ("returns", "annualized_return"),
    ("risk", "sharpe"),
    ("risk", "max_drawdown"),
    ("trading", "annualized_turnover_oneside"),
)

_RULE_TEXT = (
    f"improved = 本次 returns.{_PRIMARY_METRIC} − 父版本同指标 > 0"
    f"（缺失时回退 returns.{_FALLBACK_METRIC}）"
)

def primary_metric(metrics: dict) -> tuple[str, float | None]:
    
    returns = metrics.get("returns") or {}
    value = returns.get(_PRIMARY_METRIC)
    if value is not None:
        return _PRIMARY_METRIC, float(value)
    value = returns.get(_FALLBACK_METRIC)
    if value is not None:
        return _FALLBACK_METRIC, float(value)
    return _PRIMARY_METRIC, None

def compare_with_parent(
    store: ExperimentStore, exp_id: str, metrics: dict
) -> dict[str, Any] | None:
    
    record = store.get(exp_id)
    if record.parent_exp_id is None:
        return None

    parent_result = store.latest_event(record.parent_exp_id, "result")
    if parent_result is None:
        return {
            "parent_exp_id": record.parent_exp_id,
            "parent_run_id": None,
            "deltas": {},
            "improved": None,
            "rule": _RULE_TEXT,
            "note": "父版本尚无回测结果，无法对比。",
        }

    parent_metrics = parent_result.payload.get("metrics") or {}
    deltas: dict[str, float | None] = {}
    for group, key in _DELTA_KEYS:
        cur = (metrics.get(group) or {}).get(key)
        par = (parent_metrics.get(group) or {}).get(key)
        deltas[f"{group}.{key}"] = (
            round(float(cur) - float(par), 6) if cur is not None and par is not None else None
        )

    name, cur_primary = primary_metric(metrics)
    _, par_primary = primary_metric(parent_metrics)
    improved: bool | None = None
    if cur_primary is not None and par_primary is not None:
        improved = cur_primary - par_primary > 0

    return {
        "parent_exp_id": record.parent_exp_id,
        "parent_run_id": parent_result.payload.get("run_id"),
        "primary_metric": name,
        "primary_current": cur_primary,
        "primary_parent": par_primary,
        "deltas": deltas,
        "improved": improved,
        "rule": _RULE_TEXT,
    }

__all__ = ["compare_with_parent", "primary_metric"]
