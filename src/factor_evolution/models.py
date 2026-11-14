from __future__ import annotations

import hashlib
import json
from datetime import date
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

class EvolutionState(StrEnum):
    CREATED = "created"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

class EvolutionDecision(StrEnum):
    CONTINUE = "continue"
    FROZEN = "frozen"
    STOPPED = "stopped"
    FAILED = "failed"
    CANCELLED = "cancelled"

class ResearchWindow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start: str
    end: str

    @field_validator("start", "end")
    @classmethod
    def _date(cls, value: str) -> str:
        date.fromisoformat(value)
        return value

    @model_validator(mode="after")
    def _ordered(self) -> ResearchWindow:
        if self.start > self.end:
            raise ValueError(f"区间起点晚于终点：{self.start}..{self.end}")
        return self

    def text(self) -> str:
        return f"{self.start}..{self.end}"

class EvolutionTargets(BaseModel):
    model_config = ConfigDict(extra="forbid")

    min_annualized_excess_return: float
    max_drawdown: float = Field(gt=0, le=1)
    max_annualized_turnover: float = Field(gt=0)
    min_oos_stability: float = Field(ge=0, le=1)

class WalkForwardMode(StrEnum):
    ROLLING = "rolling"
    EXPANDING = "expanding"

class WalkForwardConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    mode: WalkForwardMode = WalkForwardMode.ROLLING
    validation_years: int = Field(default=1, ge=1, le=10)
    step_years: int = Field(default=1, ge=1, le=10)
    embargo_days: int = Field(default=0, ge=0, le=365)
    min_positive_fold_ratio: float = Field(default=0.60, ge=0, le=1)
    min_target_pass_fold_ratio: float = Field(default=0.50, ge=0, le=1)
    recency_half_life_folds: float = Field(default=1.0, gt=0, le=20)
    require_latest_fold_pass: bool = True
    max_recent_degradation: float = Field(default=0.05, ge=0, le=1)

class EvolutionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base_factor: str
    profile_name: str
    train_window: ResearchWindow
    validation_window: ResearchWindow
    test_window: ResearchWindow
    targets: EvolutionTargets
    walk_forward: WalkForwardConfig = Field(default_factory=WalkForwardConfig)
    max_iterations: int = Field(default=8, ge=1, le=100)
    no_improvement_rounds: int = Field(default=3, ge=1, le=100)
    max_runtime_seconds: int = Field(default=3600, ge=30, le=604800)
    initial_capital: float = Field(default=1_000_000, gt=0)
    lookback_buffer_days: int = Field(default=250, ge=0, le=2000)
    model_name: str = ""
    random_seed: int = 42
    run_locked_test: bool = True
    analysis_max_turns: int = Field(default=8, ge=1, le=20)
    prediction_horizons: list[int] = Field(default_factory=lambda: [1, 5, 10, 20], min_length=1)

    @field_validator('prediction_horizons')
    @classmethod
    def _horizons(cls, value):
        if len(set(value)) != len(value) or any(h not in {1, 5, 10, 20} for h in value):
            raise ValueError('预测期限必须是不重复的已注册 v1 标签期限：1、5、10、20')
        return sorted(value)

    @field_validator("base_factor", "profile_name")
    @classmethod
    def _required(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("字段不能为空")
        return value

    @model_validator(mode="after")
    def _isolated_windows(self) -> EvolutionConfig:
        if not (
            self.train_window.end < self.validation_window.start
            and self.validation_window.end < self.test_window.start
        ):
            raise ValueError("训练、验证、锁定测试区间必须按时间先后排列且互不重叠")
        return self

    def content_hash(self) -> str:
        raw = json.dumps(self.model_dump(mode="json"), ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]

class FactorVersion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version_id: str
    parent_version_id: str | None = None
    factor_name: str
    assumption: str
    direction: str
    timescale: str
    dependencies: list[str]
    parameters: dict[str, Any] = Field(default_factory=dict)
    standardization: list[str] = Field(default_factory=list)
    missing_handling: str = "drop"
    code: str
    change_type: str
    change_summary: str
    expected_impact: str = ""
    model_name: str = ""
    generation_attempts: int = Field(default=1, ge=1, le=10)
    proposal_id: str = ""
    research_plan: dict[str, Any] = Field(default_factory=dict)

    @property
    def code_hash(self) -> str:
        return hashlib.sha256(self.code.encode("utf-8")).hexdigest()[:16]

class WindowResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    window: str
    run_id: str
    metrics: dict[str, Any]

class WalkForwardFold(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fold_id: str
    train_window: ResearchWindow
    validation_window: ResearchWindow

class FoldResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fold_id: str
    train_window: ResearchWindow
    validation_window: ResearchWindow
    train: WindowResult
    validation: WindowResult

class GeneralizationAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    classification: str
    train_median_excess_return: float
    validation_median_excess_return: float
    generalization_gap: float
    validation_positive_fold_ratio: float
    validation_target_pass_fold_ratio: float
    median_fold_stability: float
    recency_weighted_validation_excess_return: float
    latest_validation_excess_return: float
    latest_fold_passed: bool
    recent_degradation: float
    fold_count: int

class VersionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version_id: str
    experiment_id: str = ""
    train: WindowResult | None = None
    validation: WindowResult | None = None
    test: WindowResult | None = None
    score: float | None = None
    stability: float | None = None
    constraints_passed: bool = False
    folds: list[FoldResult] = Field(default_factory=list)
    generalization: GeneralizationAssessment | None = None
    failure_code: str = ""
    failure_message: str = ""

class ReviewResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: str
    assessment: str
    failure_reason: str = ""
    next_change: str = ""
    model_name: str = ""
    generation_attempts: int = Field(default=1, ge=1, le=10)
    ai_generated: bool = True

class EvolutionStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")

    research_id: str
    state: EvolutionState = EvolutionState.CREATED
    decision: EvolutionDecision = EvolutionDecision.CONTINUE
    phase: str = "等待启动"
    progress: float = Field(default=0, ge=0, le=1)
    iteration: int = 0
    successful_rounds: int = 0
    failed_rounds: int = 0
    no_improvement_count: int = 0
    best_version_id: str = ""
    current_version_id: str = ""
    terminal_reason: str = ""
    error: str = ""
    elapsed_seconds: float = 0
    started_at: str | None = None
    updated_at: str
    finished_at: str | None = None
    cancel_requested: bool = False
    locked_test_runs: int = 0

__all__ = [
    "EvolutionConfig",
    "EvolutionDecision",
    "EvolutionState",
    "EvolutionStatus",
    "EvolutionTargets",
    "FoldResult",
    "FactorVersion",
    "GeneralizationAssessment",
    "ResearchWindow",
    "ReviewResult",
    "VersionResult",
    "WalkForwardConfig",
    "WalkForwardFold",
    "WalkForwardMode",
    "WindowResult",
]
