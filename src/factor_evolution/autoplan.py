from __future__ import annotations

from datetime import date, timedelta

from exceptions import QuantTrainingError
from panel import cache_status
from strategy.models import StrategyStatus
from strategy.store import StrategyProfileStore

from .models import (
    EvolutionConfig,
    EvolutionTargets,
    ResearchWindow,
    WalkForwardConfig,
)

def _fail(code: str, message: str, suggested_action: str) -> QuantTrainingError:
    return QuantTrainingError(
        code=code,
        message=message,
        blocking=True,
        suggested_action=suggested_action,
    )

def choose_recent_windows(data_start: str, data_end: str) -> tuple[ResearchWindow, ...]:
    
    start = date.fromisoformat(data_start)
    end = date.fromisoformat(data_end)
    test_start = date(end.year - 1, 1, 1)
    validation_end = date(test_start.year - 1, 12, 31)
    validation_start = date(validation_end.year - 2, 1, 1)
    train_end = date(validation_start.year - 1, 12, 31)
    train_start = date(train_end.year - 5, 1, 1)
    if start > train_start or end < test_start:
        raise _fail(
            "EVOLUTION_AUTO_DATA_RANGE_INSUFFICIENT",
            f"当前数据区间 {data_start}..{data_end} 不足以生成 6 年训练、3 折验证和近期锁定测试。",
            "先更新或补齐历史数据，再重新启动。",
        )
    return (
        ResearchWindow(start=train_start.isoformat(), end=train_end.isoformat()),
        ResearchWindow(start=validation_start.isoformat(), end=validation_end.isoformat()),
        ResearchWindow(start=test_start.isoformat(), end=end.isoformat()),
    )

def _single_factor_profile(base_factor: str) -> tuple[str, int]:
    store = StrategyProfileStore()
    matches = []
    for doc in store.list(templates_only=False):
        if doc.status == StrategyStatus.DISABLED:
            continue
        resolved = store.resolve(doc.profile_name)
        names = [item.name for item in resolved.factors.items]
        if names == [base_factor]:
            matches.append(doc)
    if not matches:
        raise _fail(
            "EVOLUTION_AUTO_PROFILE_NOT_FOUND",
            f"没有找到只包含因子 {base_factor} 的策略。",
            "先配置一个严格单因子策略，再启动自动进化。",
        )
    
    matches.sort(key=lambda item: (item.updated_at, item.profile_name), reverse=True)
    selected = store.resolve(matches[0].profile_name)
    return selected.profile_name, selected.holding_period

def build_auto_config(base_factor: str) -> EvolutionConfig:
    
    base_factor = base_factor.strip()
    if not base_factor:
        raise _fail(
            "EVOLUTION_AUTO_FACTOR_REQUIRED",
            "基础因子不能为空。",
            "选择一个已注册因子后启动。",
        )
    status = cache_status().get("default")
    if status is None or status.state != "fresh" or not status.meta:
        state = status.state if status is not None else "missing"
        raise _fail(
            "EVOLUTION_AUTO_PANEL_NOT_READY",
            f"默认矩阵缓存状态为 {state}，不能自动确定时间切分。",
            "先运行数据更新和矩阵缓存重建。",
        )
    bounds = status.meta.get("build_params") or {}
    data_start = str(bounds.get("start") or "")
    data_end = str(bounds.get("end") or "")
    if not data_start or not data_end:
        raise _fail(
            "EVOLUTION_AUTO_PANEL_BOUNDS_MISSING",
            "矩阵缓存缺少起止日期，无法生成时间切分。",
            "重建默认矩阵缓存后重试。",
        )
    train, validation, test = choose_recent_windows(data_start, data_end)
    profile_name, embargo_days = _single_factor_profile(base_factor)
    train = ResearchWindow(
        start=train.start,
        end=(date.fromisoformat(train.end) - timedelta(days=embargo_days)).isoformat(),
    )
    return EvolutionConfig(
        base_factor=base_factor,
        profile_name=profile_name,
        train_window=train,
        validation_window=validation,
        test_window=test,
        targets=EvolutionTargets(
            min_annualized_excess_return=0.03,
            max_drawdown=0.25,
            max_annualized_turnover=12.0,
            min_oos_stability=0.50,
        ),
        walk_forward=WalkForwardConfig(
            enabled=True,
            mode="rolling",
            validation_years=1,
            step_years=1,
            embargo_days=embargo_days,
            min_positive_fold_ratio=2 / 3,
            min_target_pass_fold_ratio=2 / 3,
            recency_half_life_folds=1.0,
            require_latest_fold_pass=True,
            max_recent_degradation=0.05,
        ),
        max_iterations=8,
        no_improvement_rounds=3,
        max_runtime_seconds=3600,
        initial_capital=1_000_000,
        lookback_buffer_days=250,
        random_seed=42,
    )

__all__ = ["build_auto_config", "choose_recent_windows"]
