from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from exceptions import QuantTrainingError

from .config import EvaluationConfig
from .data import (
    align_scores,
    build_chunk_data,
    forward_returns,
    market_fields_for,
)
from .grouping import (
    assign_groups,
    group_daily_returns,
    long_short_stats,
    monotonicity_stats,
    turnover_series,
)
from .metrics import ic_half_life, rank_ic_series, summarize_ic, yearly_ic_table
from .neutralize import neutralize_scores
from .report import RunStore

ProgressFn = Callable[[str], None]

_TURNOVER_NOTE = "换手率为分组名单变化的单边近似，非成交模拟。"
_LIMIT_NOTE = (
    "入场日不可成交规则：t+1 日停牌（is_trading=False）或一字板（high==low）"
    "样本剔除；持有期内停牌日收益按 0 复合。"
)

@dataclass
class EvaluationOutcome:
    run_id: str
    run_dir: Path
    metrics: dict

def _progress(fn: ProgressFn | None, msg: str) -> None:
    if fn is not None:
        fn(msg)

def _year_segments(start: str, end: str) -> list[tuple[int, str, str]]:
    
    s, e = pd.Timestamp(start), pd.Timestamp(end)
    if s > e:
        raise QuantTrainingError(
            code="EVALUATION_BAD_RANGE",
            message=f"评价区间非法：start={start} 晚于 end={end}。",
            blocking=True,
            suggested_action="调整 --start / --end。",
        )
    segments: list[tuple[int, str, str]] = []
    for year in range(s.year, e.year + 1):
        seg_start = max(s, pd.Timestamp(year=year, month=1, day=1))
        seg_end = min(e, pd.Timestamp(year=year, month=12, day=31))
        segments.append((year, seg_start.strftime("%Y-%m-%d"), seg_end.strftime("%Y-%m-%d")))
    return segments

def _cache_bounds() -> tuple[str | None, str | None]:
    
    from panel import cache_status

    meta = cache_status()["default"].meta or {}
    params = meta.get("build_params") or {}
    return params.get("start"), params.get("end")

def _clamp(date: str, lo: str | None, hi: str | None) -> str:
    if lo is not None and date < lo:
        date = lo
    if hi is not None and date > hi:
        date = hi
    return date

def _labels_for(config: EvaluationConfig, dependencies: list[str]) -> tuple[list[str], list[str]]:
    
    notes: list[str] = []
    labels = ["default"]
    if config.strict_compare:
        from panel.contract import FieldCategory, get_field

        has_fundamental = any(
            get_field(name).category is FieldCategory.FUNDAMENTAL for name in dependencies
        )
        if has_fundamental:
            labels.append("strict")
        else:
            notes.append("因子不依赖基本面字段，strict（剔除 imputed 记录）对比无差异，已跳过。")
    return labels, notes

def _factor_scores(source, panel_payload, dependencies, config):
    from ai.execution import run_factor
    value = run_factor(source=source, panel_payload=panel_payload)
    if not isinstance(value, pd.DataFrame):
        raise QuantTrainingError(code='EVALUATION_SCORE_SHAPE_INVALID', message='横截面评价需要二维矩阵',
                                 suggested_action='检查 compute 函数返回值')
    return value

def _evaluate_chunk(
    *,
    source: str,
    dependencies: list[str],
    config: EvaluationConfig,
    label: str,
    seg_start: str,
    seg_end: str,
) -> tuple[dict[str, pd.DataFrame], list[str]]:
    
    from panel import load_panel

    cache_lo, cache_hi = _cache_bounds()
    load_start = _clamp(
        (pd.Timestamp(seg_start) - pd.Timedelta(days=config.lookback_buffer_days)).strftime(
            "%Y-%m-%d"
        ),
        cache_lo,
        cache_hi,
    )
    load_end = _clamp(
        (pd.Timestamp(seg_end) + pd.Timedelta(days=config.forward_buffer_days)).strftime(
            "%Y-%m-%d"
        ),
        cache_lo,
        cache_hi,
    )

    factor_panel = load_panel(
        dependencies,
        start=load_start,
        end=load_end,
        exclude_imputed=(label == "strict"),
    )
    market_panel = load_panel(
        market_fields_for(config),
        start=load_start,
        end=load_end,
        industry_classification_id=(
            config.industry_classification_id if config.needs_industry() else None
        ),
    )

    raw_scores = _factor_scores(source, factor_panel, dependencies, config)
    chunk = build_chunk_data(market_panel, config=config, eval_start=seg_start, eval_end=seg_end)
    scores = align_scores(raw_scores, chunk=chunk)

    fwd_by_h: dict[int, pd.DataFrame] = {}
    horizons = sorted({*config.decay_horizons, config.primary_horizon, 1})
    for h in horizons:
        fwd = forward_returns(chunk.returns, h).where(chunk.entry_ok)
        fwd_by_h[h] = fwd.reindex(index=chunk.eval_dates)

    ic_cols: dict[str, pd.Series] = {}
    group_cols: dict[str, pd.Series] = {}
    turnover_cols: dict[str, pd.Series] = {}
    for variant in config.variants():
        neutral = neutralize_scores(
            scores,
            variant,
            config=config,
            industry=chunk.industry,
            log_mktcap=chunk.log_mktcap,
        )
        for h in sorted({*config.decay_horizons, config.primary_horizon}):
            ic_cols[f"{variant.value}|h{h}"] = rank_ic_series(
                neutral, fwd_by_h[h], min_cross_section=config.min_cross_section
            )
        for n in config.group_counts:
            groups = assign_groups(neutral, n)
            daily = group_daily_returns(groups, fwd_by_h[1], n)
            turn = turnover_series(groups, n)
            for col in daily.columns:
                group_cols[f"{variant.value}|g{n}|{col}"] = daily[col]
            for col in turn.columns:
                turnover_cols[f"{variant.value}|g{n}|{col}"] = turn[col]

    notes = [*chunk.caliber_warnings, _TURNOVER_NOTE, _LIMIT_NOTE]
    frames = {
        "ic": pd.DataFrame(ic_cols),
        "groups": pd.DataFrame(group_cols),
        "turnover": pd.DataFrame(turnover_cols),
    }
    return frames, notes

def _direction_for(meta_direction: str, raw_ic_mean: float | None) -> tuple[int, bool]:
    
    if meta_direction == "positive":
        return 1, False
    if meta_direction == "negative":
        return -1, False
    if raw_ic_mean is not None and raw_ic_mean < 0:
        return -1, True
    return 1, True

def _summarize_label(
    store: RunStore,
    *,
    label: str,
    years: list[int],
    config: EvaluationConfig,
    meta_direction: str,
) -> tuple[dict, dict[str, pd.DataFrame]]:
    
    ppy = config.trading_days_per_year
    ic_all = store.read_chunks(label, years, "ic")
    groups_all = store.read_chunks(label, years, "groups")
    turnover_all = store.read_chunks(label, years, "turnover")

    raw_primary = ic_all[f"raw|h{config.primary_horizon}"]
    raw_summary = summarize_ic(raw_primary, periods_per_year=ppy, horizon=config.primary_horizon)
    direction, inferred = _direction_for(meta_direction, raw_summary["ic_mean"])

    variants_out: dict[str, dict] = {}
    nav_charts: dict[str, pd.Series] = {}
    for variant in config.variants():
        v = variant.value
        primary_ic = ic_all[f"{v}|h{config.primary_horizon}"]
        decay_means: dict[int, float | None] = {}
        for h in sorted(config.decay_horizons):
            s = summarize_ic(ic_all[f"{v}|h{h}"], periods_per_year=ppy, horizon=h)
            decay_means[h] = s["ic_mean"]

        groups_out: dict[str, dict] = {}
        for n in config.group_counts:
            cols = [f"{v}|g{n}|q{k}" for k in range(1, n + 1)]
            daily = groups_all[cols].copy()
            daily.columns = [f"q{k}" for k in range(1, n + 1)]
            turn = turnover_all[cols].copy()
            turn.columns = daily.columns
            ls_stats, nav = long_short_stats(daily, turn, direction=direction, periods_per_year=ppy)
            groups_out[f"g{n}"] = {
                "monotonicity": monotonicity_stats(daily, periods_per_year=ppy),
                "avg_turnover_oneside": {
                    col: (round(float(turn[col].mean()), 6) if turn[col].notna().any() else None)
                    for col in turn.columns
                },
                "long_short": ls_stats,
            }
            if n == max(config.group_counts):
                nav_charts[v] = nav

        variants_out[v] = {
            "ic": summarize_ic(primary_ic, periods_per_year=ppy, horizon=config.primary_horizon),
            "ic_decay": {f"h{h}": decay_means[h] for h in sorted(config.decay_horizons)},
            "ic_half_life_days": ic_half_life(decay_means),
            "yearly_ic": yearly_ic_table(
                primary_ic, periods_per_year=ppy, horizon=config.primary_horizon
            ),
            "groups": groups_out,
        }

    comparison = {
        v.value: {
            key: variants_out[v.value]["ic"][key]
            for key in ("ic_mean", "icir", "icir_annualized", "win_rate", "n_valid_days")
        }
        for v in config.variants()
    }

    label_metrics = {
        "direction": direction,
        "direction_inferred_from_sample": inferred,
        "variants": variants_out,
        "neutralization_comparison": comparison,
    }

    charts: dict[str, pd.DataFrame] = {
        f"ic_daily_{label}": ic_all[
            [f"{v.value}|h{config.primary_horizon}" for v in config.variants()]
        ],
        f"longshort_nav_{label}": pd.DataFrame(nav_charts),
        f"group_daily_{label}": groups_all,
    }
    return label_metrics, charts

def evaluate_factor(
    config: EvaluationConfig,
    *,
    resume_run_id: str | None = None,
    on_progress: ProgressFn | None = None,
) -> EvaluationOutcome:
    
    from ai.execution import syntax_check
    from factors.plugins import FactorPlugin
    from factors.registry import FactorRegistry

    registry = FactorRegistry()
    meta = registry.get(config.factor_name)
    source = FactorPlugin.default().read(config.factor_name)

    errors = syntax_check(source)
    if errors:
        raise QuantTrainingError(
            code="EVALUATION_SYNTAX_INVALID",
            message="因子源码未通过语法检查：" + "；".join(errors),
            blocking=True,
            suggested_action="修复因子源码后重试（因子库代码检查 可复现）。",
        )

    dependencies = list(meta.dependencies)
    labels, label_notes = _labels_for(config, dependencies)
    segments = _year_segments(config.start, config.end)
    years = [y for y, _, _ in segments]

    from factors.registry import hash_code

    if resume_run_id is not None:
        store = RunStore.open_resume(config, resume_run_id)
    else:
        store = RunStore.create(
            config,
            extra_meta={
                "factor_code_hash": hash_code(source),
                "labels": labels,
                "cache_bounds": list(_cache_bounds()),
            },
        )

    total = len(labels) * len(segments)
    done_n = 0
    for label in labels:
        for year, seg_start, seg_end in segments:
            done_n += 1
            if store.is_chunk_done(label, year):
                _progress(on_progress, f"[{done_n}/{total}] {label} {year}：已完成，跳过。")
                continue
            _progress(on_progress, f"[{done_n}/{total}] {label} {year}：{seg_start} → {seg_end}")
            frames, notes = _evaluate_chunk(
                source=source,
                dependencies=dependencies,
                config=config,
                label=label,
                seg_start=seg_start,
                seg_end=seg_end,
            )
            store.write_chunk(label, year, frames, notes=notes)
    store.update_status(state="aggregating")

    metrics: dict = {
        "schema_version": 1,
        "factor": config.factor_name,
        "config_hash": config.content_hash(),
        "period": {"start": config.start, "end": config.end},
        "return_field": config.return_field,
        "labels": {},
        "notes": [],
    }
    all_charts: dict[str, pd.DataFrame] = {}
    for label in labels:
        label_metrics, charts = _summarize_label(
            store,
            label=label,
            years=years,
            config=config,
            meta_direction=meta.direction,
        )
        metrics["labels"][label] = label_metrics
        all_charts.update(charts)

    if len(labels) > 1:
        metrics["strict_vs_default"] = {
            v.value: {
                "ic_mean_default": metrics["labels"]["default"]["variants"][v.value]["ic"][
                    "ic_mean"
                ],
                "ic_mean_strict": metrics["labels"]["strict"]["variants"][v.value]["ic"]["ic_mean"],
            }
            for v in config.variants()
        }

    notes = [*label_notes]
    for note in store.chunk_notes("default", years):
        if note not in notes:
            notes.append(note)
    metrics["notes"] = notes

    store.write_metrics(metrics)
    for name, frame in all_charts.items():
        store.write_chart(name, frame)
    store.update_status(state="completed", extra={"labels": labels, "years": years})

    meta.last_evaluated_at = datetime.now(tz=UTC)
    meta.decay_history = [
        *meta.decay_history,
        {
            "run_id": store.run_id,
            "period": f"{config.start}..{config.end}",
            "return_field": config.return_field,
            "primary_horizon": config.primary_horizon,
            "ic_mean_raw": metrics["labels"]["default"]["variants"]["raw"]["ic"]["ic_mean"],
            "neutralization_comparison": metrics["labels"]["default"]["neutralization_comparison"],
        },
    ]
    registry.upsert(meta, override=True)

    return EvaluationOutcome(run_id=store.run_id, run_dir=store.run_dir, metrics=metrics)

__all__ = ["EvaluationOutcome", "evaluate_factor"]
