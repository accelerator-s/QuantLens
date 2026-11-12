from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

import numpy as np
import pandas as pd

from exceptions import BacktestError
from strategy import PositionConstraint, ResolvedStrategyProfile, WeightMode
from strategy.models import (
    FillnaMethod,
    FillnaStep,
    IndustryNeutralStep,
    RankStep,
    SizeNeutralStep,
    StandardizationStep,
    WinsorizeStep,
    ZScoreStep,
)

@dataclass
class SignalResult:

    scores: pd.DataFrame

    factor_code_hashes: dict[str, str]

    directions: dict[str, int]

    notes: list[str] = field(default_factory=list)

@dataclass(frozen=True)
class RuntimeFactor:

    source: str
    dependencies: tuple[str, ...]
    direction: str

# 因子执行

def _resolve_direction(name: str, override: str, meta_direction: str) -> int:
    declared = override.strip() or meta_direction
    if declared == "positive":
        return 1
    if declared == "negative":
        return -1
    raise BacktestError(
        code="BACKTEST_FACTOR_DIRECTION_UNDECIDED",
        message=(
            f"因子 {name} 方向为 {declared or 'undecided'}，回测无法确定多空方向。"
            "禁止用样本内 IC 推断（前瞻信息）。"
        ),
        blocking=True,
        suggested_action=(
            "先用 `factor evaluate` 在样本外确认方向，然后在策略配置的因子清单里"
            "设置 direction_override（positive/negative），或更新注册表 direction。"
        ),
        related_subjects={"factor": name},
    )

def _factor_scores(name, source, panel_payload, dependencies):
    from ai.execution import run_factor
    value = run_factor(source=source, panel_payload=panel_payload)
    if not isinstance(value, pd.DataFrame):
        raise BacktestError(code='BACKTEST_FACTOR_SCORE_INVALID', message=f'因子 {name} 应返回二维矩阵',
                            suggested_action='检查 compute 函数返回值')
    value = value.copy()
    value.index = pd.to_datetime(value.index)
    return value

# 标准化链

def _winsorize(scores: pd.DataFrame, step: WinsorizeStep) -> pd.DataFrame:
    lo = scores.quantile(float(step.lower_pct), axis=1)
    hi = scores.quantile(float(step.upper_pct), axis=1)
    return scores.clip(lower=lo, upper=hi, axis=0)

def _size_neutral_row(row: pd.Series, size_row: pd.Series) -> pd.Series:
    from factors.evaluation.neutralize import _residual_on

    valid = row.notna() & size_row.notna()
    out = pd.Series(np.nan, index=row.index)
    if valid.sum() < 2:
        out[valid] = row[valid]
        return out
    centered = row.where(valid)
    centered[valid] = row[valid] - row[valid].mean()
    size_centered = size_row.where(valid)
    size_centered[valid] = size_row[valid] - size_row[valid].mean()
    return _residual_on(centered, size_centered)

def _fillna(
    scores: pd.DataFrame,
    step: FillnaStep,
    *,
    mask: pd.DataFrame,
    industry: pd.DataFrame | None,
) -> pd.DataFrame:
    if step.method is FillnaMethod.DROP:
        return scores
    if step.method is FillnaMethod.ZERO:
        return scores.mask(mask & scores.isna(), 0.0)
    if step.method is FillnaMethod.CROSS_MEDIAN:
        med = scores.median(axis=1)
        return scores.T.fillna(med).T.where(mask)
    
    assert industry is not None, "fillna(industry_median) 需要 industry_code 面板"
    out = scores.copy()
    for dt in scores.index:
        row = scores.loc[dt]
        med = row.groupby(industry.loc[dt]).transform("median")
        out.loc[dt] = row.fillna(med)
    return out.where(mask)

def apply_standardization_chain(
    scores: pd.DataFrame,
    chain: list[StandardizationStep],
    *,
    mask: pd.DataFrame,
    industry: pd.DataFrame | None = None,
    log_mktcap: pd.DataFrame | None = None,
) -> pd.DataFrame:
    
    from factors.evaluation.neutralize import _demean_within, zscore_cross_section

    out = scores
    for step in chain:
        if isinstance(step, WinsorizeStep):
            out = _winsorize(out, step)
        elif isinstance(step, ZScoreStep):
            out = zscore_cross_section(out)
        elif isinstance(step, RankStep):
            out = out.rank(axis=1, method=step.method.value, pct=True)
        elif isinstance(step, IndustryNeutralStep):
            assert industry is not None, "industry_neutral 需要 industry_code 面板"
            neu = pd.DataFrame(np.nan, index=out.index, columns=out.columns)
            for dt in out.index:
                neu.loc[dt] = _demean_within(out.loc[dt], industry.loc[dt])
            out = neu
        elif isinstance(step, SizeNeutralStep):
            assert log_mktcap is not None, "size_neutral 需要 mktcap_float 面板"
            neu = pd.DataFrame(np.nan, index=out.index, columns=out.columns)
            for dt in out.index:
                neu.loc[dt] = _size_neutral_row(out.loc[dt], log_mktcap.loc[dt])
            out = neu
        elif isinstance(step, FillnaStep):
            out = _fillna(out, step, mask=mask, industry=industry)
        else:  
            raise BacktestError(
                code="BACKTEST_UNKNOWN_STANDARDIZATION_STEP",
                message=f"未知标准化环节：{type(step).__name__}",
                blocking=True,
            )
    return out

# 面板需求与合成

def _chain_needs(profile: ResolvedStrategyProfile) -> tuple[bool, bool]:
    
    needs_industry = any(
        isinstance(s, IndustryNeutralStep)
        or (isinstance(s, FillnaStep) and s.method is FillnaMethod.INDUSTRY_MEDIAN)
        for s in profile.standardization_chain
    )
    needs_size = any(isinstance(s, SizeNeutralStep) for s in profile.standardization_chain)
    return needs_industry, needs_size

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

def compute_composite_scores(
    profile: ResolvedStrategyProfile,
    *,
    start: str,
    end: str,
    lookback_buffer_days: int,
    runtime_factors: dict[str, RuntimeFactor] | None = None,
) -> SignalResult:
    
    from factors.plugins import FactorPlugin
    from factors.registry import FactorRegistry, hash_code
    from panel import load_panel
    from panel.universe import build_universe_mask

    registry = FactorRegistry()
    plugin = FactorPlugin.default()

    runtime_factors = runtime_factors or {}
    metas = {ref.name: registry.get(ref.name) for ref in profile.factors.items}
    sources = {
        name: runtime_factors[name].source if name in runtime_factors else plugin.read(name)
        for name in metas
    }
    dependencies = {
        name: (
            list(runtime_factors[name].dependencies)
            if name in runtime_factors
            else list(meta.dependencies)
        )
        for name, meta in metas.items()
    }
    directions = {
        ref.name: _resolve_direction(
            ref.name,
            ref.direction_override if ref.name not in runtime_factors else "",
            (
                runtime_factors[ref.name].direction
                if ref.name in runtime_factors
                else metas[ref.name].direction
            ),
        )
        for ref in profile.factors.items
    }

    dep_union = sorted({dep for fields in dependencies.values() for dep in fields})
    if not dep_union:
        raise BacktestError(
            code="BACKTEST_FACTOR_NO_DEPENDENCIES",
            message="因子清单未声明任何面板字段依赖，无法取数。",
            blocking=True,
            suggested_action="检查注册表各因子的 dependencies 字段。",
        )

    cache_lo, cache_hi = _cache_bounds()
    load_start = _clamp(
        (pd.Timestamp(start) - pd.Timedelta(days=lookback_buffer_days)).strftime("%Y-%m-%d"),
        cache_lo,
        cache_hi,
    )
    load_end = _clamp(end, cache_lo, cache_hi)

    needs_industry, needs_size = _chain_needs(profile)
    aux_fields = ["is_trading", "listed_days"]
    if profile.universe.exclude_st:
        aux_fields.append("trading_status")
    if needs_industry:
        aux_fields.append("industry_code")
    if needs_size:
        aux_fields.append("mktcap_float")

    factor_panel = load_panel(dep_union, start=load_start, end=load_end)
    aux_panel = load_panel(aux_fields, start=load_start, end=load_end)

    mask_full = build_universe_mask(
        profile.universe.to_panel_config(),
        is_trading=aux_panel["is_trading"],
        listed_days=aux_panel["listed_days"],
        trading_status=aux_panel.get("trading_status"),
    )

    axis_index = aux_panel["is_trading"].index
    eval_index = axis_index[(axis_index >= pd.Timestamp(start)) & (axis_index <= pd.Timestamp(end))]
    if len(eval_index) == 0:
        raise BacktestError(
            code="BACKTEST_EMPTY_WINDOW",
            message=f"回测区间 {start} → {end} 内没有交易日。",
            blocking=True,
            suggested_action="确认区间与缓存覆盖范围（data cache-status）有交集。",
        )
    columns = aux_panel["is_trading"].columns
    mask = mask_full.reindex(index=eval_index, columns=columns).fillna(False).astype(bool)

    industry = None
    log_mktcap = None
    if needs_industry:
        industry = aux_panel["industry_code"].reindex(index=eval_index, columns=columns)
    if needs_size:
        mktcap = aux_panel["mktcap_float"].reindex(index=eval_index, columns=columns)
        with np.errstate(divide="ignore", invalid="ignore"):
            log_mktcap = pd.DataFrame(
                np.log(mktcap.where(mktcap > 0).to_numpy(dtype="float64")),
                index=mktcap.index,
                columns=mktcap.columns,
            )

    standardized: dict[str, pd.DataFrame] = {}
    for ref in profile.factors.items:
        name = ref.name
        runtime = runtime_factors.get(name)
        raw = _factor_scores(
            name,
            sources[name],
            factor_panel,
            dependencies[name],
        )
        aligned = (
            raw.reindex(index=eval_index, columns=columns).astype("float64") * directions[name]
        )
        masked = aligned.where(mask)
        standardized[name] = apply_standardization_chain(
            masked,
            profile.standardization_chain,
            mask=mask,
            industry=industry,
            log_mktcap=log_mktcap,
        )

    if profile.factors.weighting is WeightMode.EQUAL:
        weights = {name: Decimal(1) for name in standardized}
    else:
        weights = {ref.name: ref.weight for ref in profile.factors.items if ref.weight is not None}
    total: pd.DataFrame | None = None
    weight_sum = Decimal(0)
    for name, frame in standardized.items():
        contrib = frame * float(weights[name])
        total = contrib if total is None else total + contrib
        weight_sum += weights[name]
    assert total is not None
    composite = (total / float(weight_sum)).where(mask)

    return SignalResult(
        scores=composite,
        factor_code_hashes={name: hash_code(src) for name, src in sources.items()},
        directions=directions,
        notes=[
            f"信号样本池：min_listed_days={profile.universe.min_listed_days}，"
            f"exclude_suspended={profile.universe.exclude_suspended}，"
            f"exclude_st={profile.universe.exclude_st}。",
        ],
    )

# 目标权重

def target_weights(score_row: pd.Series, constraint: PositionConstraint) -> dict[str, Decimal]:
    
    valid = score_row.dropna()
    if valid.empty:
        return {}
    
    order = np.lexsort((valid.index.to_numpy(), -valid.to_numpy(dtype="float64")))
    picked = valid.index.to_numpy()[order][: constraint.top_n]
    per_name = min(
        (Decimal(1) - constraint.min_cash_ratio) / constraint.top_n,
        constraint.max_weight_per_name,
    )
    return {str(inst): per_name for inst in picked}

__all__ = [
    "SignalResult",
    "RuntimeFactor",
    "apply_standardization_chain",
    "compute_composite_scores",
    "target_weights",
]
