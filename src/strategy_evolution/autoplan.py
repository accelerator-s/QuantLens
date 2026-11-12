from __future__ import annotations

from datetime import date, timedelta

from exceptions import QuantTrainingError
from factor_evolution.autoplan import choose_recent_windows
from panel import cache_status
from strategy import StrategyProfileStore

from .models import StrategyEvolutionConfig, StrategyTargets
from factor_evolution.models import ResearchWindow, WalkForwardConfig

def build_strategy_auto_config(profile_name: str) -> StrategyEvolutionConfig:
    profile_name = profile_name.strip()
    if not profile_name:
        raise QuantTrainingError(
            code="STRATEGY_EVOLUTION_PROFILE_REQUIRED",
            message="策略 profile 不能为空。",
            blocking=True,
            suggested_action="在 WebUI 选择一个已解析的多因子策略。",
        )
    profile = StrategyProfileStore().resolve(profile_name)
    if len(profile.factors.items) < 2:
        raise QuantTrainingError(
            code="STRATEGY_EVOLUTION_MULTIFACTOR_REQUIRED",
            message=f"策略 {profile_name} 只有 {len(profile.factors.items)} 个因子。",
            blocking=True,
            suggested_action="先建立至少包含两个因子的 profile。",
        )
    status = cache_status().get("default")
    if status is None or status.state != "fresh" or not status.meta:
        raise QuantTrainingError(
            code="STRATEGY_EVOLUTION_PANEL_NOT_READY",
            message=f"默认矩阵缓存状态为 {status.state if status else 'missing'}。",
            blocking=True,
            suggested_action="先在数据页完成更新与矩阵重建。",
        )
    bounds = status.meta.get("build_params") or {}
    train, validation, test = choose_recent_windows(
        str(bounds.get("start") or ""), str(bounds.get("end") or "")
    )
    embargo = profile.holding_period
    train = ResearchWindow(
        start=train.start,
        end=(date.fromisoformat(train.end) - timedelta(days=embargo)).isoformat(),
    )
    return StrategyEvolutionConfig(
        profile_name=profile_name,
        train_window=train,
        validation_window=validation,
        test_window=test,
        targets=StrategyTargets(),
        walk_forward=WalkForwardConfig(
            enabled=True,
            mode="rolling",
            validation_years=1,
            step_years=1,
            embargo_days=embargo,
            min_positive_fold_ratio=2 / 3,
            min_target_pass_fold_ratio=2 / 3,
            recency_half_life_folds=1.0,
            require_latest_fold_pass=True,
            max_recent_degradation=0.05,
        ),
    )

__all__ = ["build_strategy_auto_config"]
