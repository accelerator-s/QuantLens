from __future__ import annotations

from calendar import monthrange
from datetime import date, timedelta
from statistics import median

from .models import (
    EvolutionConfig,
    FoldResult,
    GeneralizationAssessment,
    ResearchWindow,
    WalkForwardFold,
    WalkForwardMode,
    WindowResult,
)

def _add_years(value: date, years: int) -> date:
    year = value.year + years
    day = min(value.day, monthrange(year, value.month)[1])
    return value.replace(year=year, day=day)

def build_folds(config: EvolutionConfig) -> list[WalkForwardFold]:
    
    if not config.walk_forward.enabled:
        return [
            WalkForwardFold(
                fold_id="wf001",
                train_window=config.train_window,
                validation_window=config.validation_window,
            )
        ]

    settings = config.walk_forward
    initial_train_start = date.fromisoformat(config.train_window.start)
    initial_train_end = date.fromisoformat(config.train_window.end)
    validation_start = date.fromisoformat(config.validation_window.start)
    validation_end = date.fromisoformat(config.validation_window.end)
    expected_train_end = validation_start - timedelta(days=settings.embargo_days + 1)
    if initial_train_end != expected_train_end:
        raise ValueError(
            "启用滚动验证时，首个训练窗终点必须位于首个验证日起点之前"
            f" {settings.embargo_days + 1} 天；期望 {expected_train_end.isoformat()}。"
        )

    folds: list[WalkForwardFold] = []
    offset_years = 0
    fold_validation_start = validation_start
    while fold_validation_start <= validation_end:
        fold_validation_end = min(
            _add_years(fold_validation_start, settings.validation_years) - timedelta(days=1),
            validation_end,
        )
        train_end = fold_validation_start - timedelta(days=settings.embargo_days + 1)
        train_start = (
            initial_train_start
            if settings.mode is WalkForwardMode.EXPANDING
            else _add_years(initial_train_start, offset_years)
        )
        folds.append(
            WalkForwardFold(
                fold_id=f"wf{len(folds) + 1:03d}",
                train_window=ResearchWindow(
                    start=train_start.isoformat(), end=train_end.isoformat()
                ),
                validation_window=ResearchWindow(
                    start=fold_validation_start.isoformat(),
                    end=fold_validation_end.isoformat(),
                ),
            )
        )
        offset_years += settings.step_years
        fold_validation_start = _add_years(validation_start, offset_years)
    return folds

def _value(metrics: dict, group: str, key: str) -> float:
    return float((metrics.get(group) or {}).get(key) or 0)

def aggregate_results(results: list[WindowResult], *, label: str) -> WindowResult:
    from quantlens.research_preflight import assess_input_quality

    qualities = [assess_input_quality(result.metrics)
                 for result in results]
    excess = [_value(result.metrics, "returns", "annualized_excess_return") for result in results]
    drawdowns = [_value(result.metrics, "risk", "max_drawdown") for result in results]
    turnovers = [
        _value(result.metrics, "trading", "annualized_turnover_oneside") for result in results
    ]
    net_returns = [(result.metrics.get('returns') or {}).get('annualized_return') for result in results]
    complete = all(value is not None for value in net_returns)
    calmar = median(float(value) for value in net_returns) / max(abs(min(drawdowns)), 1e-9) if complete else None
    metrics = {
        'research_standard':'cn_a_daily_v3',
        "input_quality": {
            "passed": all(quality['passed'] for quality in qualities),
            "failed_run_ids": [result.run_id for result, quality in zip(results, qualities)
                               if not quality['passed']],
        },
        "returns": {"annualized_excess_return": median(excess),
                    "annualized_return": median(net_returns) if complete else None},
        "risk": {"max_drawdown": min(drawdowns)},
        "trading": {"annualized_turnover_oneside": max(turnovers)},
        "objective": {"primary": "calmar", "value": calmar,
                      "eligible": complete and all(result.metrics.get('objective', {}).get('eligible', False) for result in results)},
        "walk_forward": {
            "aggregation": "median_return_worst_risk_turnover",
            "source_run_ids": [result.run_id for result in results],
            "fold_values": excess,
            "fold_net_returns": net_returns,
        },
    }
    return WindowResult(window=label, run_id=f"walk-forward:{label}", metrics=metrics)

def assess_generalization(
    config: EvolutionConfig, fold_results: list[FoldResult]
) -> GeneralizationAssessment:
    target = config.targets
    train_scores = [
        _value(fold.train.metrics, "returns", "annualized_excess_return") for fold in fold_results
    ]
    validation_scores = [
        _value(fold.validation.metrics, "returns", "annualized_excess_return")
        for fold in fold_results
    ]
    stability_values = []
    target_passes = 0
    for fold, train_score, validation_score in zip(
        fold_results, train_scores, validation_scores, strict=True
    ):
        if train_score > 0 and validation_score > 0:
            stability_values.append(
                min(validation_score / train_score, train_score / validation_score, 1.0)
            )
        else:
            stability_values.append(0.0)
        metrics = fold.validation.metrics
        if (
            validation_score >= target.min_annualized_excess_return
            and _value(metrics, "returns", "annualized_return") > 0
            and abs(_value(metrics, "risk", "max_drawdown")) <= target.max_drawdown
            and _value(metrics, "trading", "annualized_turnover_oneside")
            <= target.max_annualized_turnover
        ):
            target_passes += 1

    train_median = median(train_scores)
    validation_median = median(validation_scores)
    half_life = config.walk_forward.recency_half_life_folds
    weights = [
        0.5 ** ((len(validation_scores) - index - 1) / half_life)
        for index in range(len(validation_scores))
    ]
    recency_score = sum(
        score * weight for score, weight in zip(validation_scores, weights, strict=True)
    ) / sum(weights)
    latest_fold = fold_results[-1]
    latest_score = validation_scores[-1]
    latest_metrics = latest_fold.validation.metrics
    latest_passed = (
        latest_score >= target.min_annualized_excess_return
        and _value(latest_metrics, "returns", "annualized_return") > 0
        and abs(_value(latest_metrics, "risk", "max_drawdown")) <= target.max_drawdown
        and _value(latest_metrics, "trading", "annualized_turnover_oneside")
        <= target.max_annualized_turnover
    )
    prior_peak = max(validation_scores[:-1], default=latest_score)
    recent_degradation = max(0.0, prior_peak - latest_score)
    positive_ratio = sum(score > 0 for score in validation_scores) / len(validation_scores)
    pass_ratio = target_passes / len(validation_scores)
    median_stability = median(stability_values)
    validation_effective = (
        recency_score >= target.min_annualized_excess_return
        and positive_ratio >= config.walk_forward.min_positive_fold_ratio
        and pass_ratio >= config.walk_forward.min_target_pass_fold_ratio
        and median_stability >= target.min_oos_stability
        and (latest_passed or not config.walk_forward.require_latest_fold_pass)
        and recent_degradation <= config.walk_forward.max_recent_degradation
    )
    if validation_effective:
        classification = "validation_generalized"
    elif any(score >= target.min_annualized_excess_return for score in validation_scores[:-1]) and (
        not latest_passed or recent_degradation > config.walk_forward.max_recent_degradation
    ):
        classification = "recently_decayed"
    elif train_median >= target.min_annualized_excess_return:
        classification = "in_sample_only"
    else:
        classification = "not_effective"
    return GeneralizationAssessment(
        classification=classification,
        train_median_excess_return=train_median,
        validation_median_excess_return=validation_median,
        generalization_gap=train_median - validation_median,
        validation_positive_fold_ratio=positive_ratio,
        validation_target_pass_fold_ratio=pass_ratio,
        median_fold_stability=median_stability,
        recency_weighted_validation_excess_return=recency_score,
        latest_validation_excess_return=latest_score,
        latest_fold_passed=latest_passed,
        recent_degradation=recent_degradation,
        fold_count=len(fold_results),
    )

__all__ = ["aggregate_results", "assess_generalization", "build_folds"]
