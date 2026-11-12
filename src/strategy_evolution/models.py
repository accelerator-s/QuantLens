from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator

from factor_evolution.models import ResearchWindow, WalkForwardConfig

class StrategyTargets(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    min_annualized_excess_return: float = 0.03
    min_sharpe: float = 0.50
    max_drawdown: float = Field(default=0.25, gt=0, le=1)
    max_annualized_turnover: float = Field(default=12.0, gt=0)
    min_positive_fold_ratio: float = Field(default=2 / 3, ge=0, le=1)
    min_oos_stability: float = Field(default=0.50, ge=0, le=1)

class StrategyEvolutionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    profile_name: str
    train_window: ResearchWindow
    validation_window: ResearchWindow
    test_window: ResearchWindow
    targets: StrategyTargets = Field(default_factory=StrategyTargets)
    walk_forward: WalkForwardConfig
    max_iterations: int = Field(default=6, ge=1, le=30)
    no_improvement_rounds: int = Field(default=3, ge=1, le=10)
    max_runtime_seconds: int = Field(default=7200, ge=60, le=604800)
    initial_capital: float = Field(default=1_000_000, gt=0)
    lookback_buffer_days: int = Field(default=250, ge=0, le=2000)
    model_name: str = ""
    random_seed: int = 42
    trigger_learning_event_id: str = ""

    @model_validator(mode="after")
    def _windows(self) -> StrategyEvolutionConfig:
        if not (
            self.train_window.end < self.validation_window.start
            and self.validation_window.end < self.test_window.start
        ):
            raise ValueError("训练、验证、锁定测试必须按时间排列且互不重叠")
        return self

class StrategyRunResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    purpose: str
    window: ResearchWindow
    metrics: dict
    evaluation_run_id: str = ""
    locked_test_exposure_id: str = ""

class StrategyCandidateResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    strategy_version_id: str
    train_runs: list[StrategyRunResult] = Field(default_factory=list)
    validation_runs: list[StrategyRunResult] = Field(default_factory=list)
    aggregate_metrics: dict = Field(default_factory=dict)
    positive_fold_ratio: float = 0
    oos_stability: float = 0
    constraints_passed: bool = False
    score: float = 0
    ablation_strategy_version_id: str = ""
    ablation_run_ids: list[str] = Field(default_factory=list)
    ablation_delta: dict = Field(default_factory=dict)

__all__ = [
    "StrategyCandidateResult",
    "StrategyEvolutionConfig",
    "StrategyRunResult",
    "StrategyTargets",
]
