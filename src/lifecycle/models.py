from __future__ import annotations

from datetime import UTC, date, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

def utcnow() -> datetime:
    return datetime.now(tz=UTC)

class ObjectType(StrEnum):
    DATA_SNAPSHOT = "data_snapshot"
    RESEARCH_PROGRAM = "research_program"
    FACTOR_VERSION = "factor_version"
    FACTOR_EVALUATION = "factor_evaluation"
    STRATEGY_VERSION = "strategy_version"
    EVALUATION_RUN = "evaluation_run"
    LOCKED_TEST_EXPOSURE = "locked_test_exposure"
    CHAMPION_DECISION = "champion_decision"
    PORTFOLIO_PLAN = "portfolio_plan"
    EXECUTION_RECORD = "execution_record"
    ATTRIBUTION_RECORD = "attribution_record"
    LEARNING_EVENT = "learning_event"
    RESEARCH_REPORT = "research_report"

class SubjectType(StrEnum):
    FACTOR = "factor"
    STRATEGY = "strategy"
    PORTFOLIO_POLICY = "portfolio_policy"

class ResearchLoop(StrEnum):
    FACTOR = "factor_research"
    STRATEGY = "strategy_research"
    LIVE_FEEDBACK = "live_feedback"

class RunPurpose(StrEnum):
    TRAIN = "train"
    VALIDATION = "validation"
    WALK_FORWARD = "walk_forward"
    LOCKED_TEST = "locked_test"
    SHADOW = "shadow"
    LIVE_OBSERVATION = "live_observation"

class RunOutcome(StrEnum):
    PENDING = "pending"
    PASSED = "passed"
    FAILED = "failed"
    INVALID = "invalid"

class QualityStatus(StrEnum):
    PASSED = "passed"
    WARNING = "warning"
    BLOCKED = "blocked"

class DecisionAction(StrEnum):
    PROMOTE = "promote"
    KEEP = "keep"
    ROLLBACK = "rollback"
    REJECT = "reject"

class StrategyChangeDimension(StrEnum):
    INITIAL = "initial"
    FACTOR_SET = "factor_set"
    FACTOR_WEIGHT = "factor_weight"
    STANDARDIZATION = "standardization"
    COMBINATION_POLICY = "combination_policy"
    REGIME_WEIGHT = "regime_weight"
    SELECTION_POLICY = "selection_policy"
    REBALANCE_POLICY = "rebalance_policy"
    EXPOSURE_LIMIT = "exposure_limit"
    TURNOVER_POLICY = "turnover_policy"
    RISK_BUDGET = "risk_budget"
    CASH_POLICY = "cash_policy"
    EXECUTION_POLICY = "execution_policy"

class AttributionLayer(StrEnum):
    SIGNAL = "signal"
    CONSTRAINT = "constraint"
    EXECUTION = "execution"
    USER_OVERRIDE = "user_override"
    MARKET_REGIME = "market_regime"

class ImmutableRecord(BaseModel):

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    created_at: datetime = Field(default_factory=utcnow)
    schema_version: int = Field(default=1, ge=1)

class TimeWindow(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    start: date
    end: date

    @model_validator(mode="after")
    def _ordered(self) -> TimeWindow:
        if self.start > self.end:
            raise ValueError("时间区间起点不能晚于终点")
        return self

class DomainObjectRef(BaseModel):

    model_config = ConfigDict(extra="forbid", frozen=True)

    object_type: ObjectType
    object_id: str = Field(min_length=1)

class ResearchBudget(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    max_iterations: int = Field(gt=0)
    max_model_calls: int = Field(gt=0)
    max_runtime_seconds: int = Field(gt=0)
    max_cost: float | None = Field(default=None, ge=0)
    cost_currency: str = ""

    @model_validator(mode="after")
    def _cost_currency(self) -> ResearchBudget:
        if self.max_cost is not None and not self.cost_currency:
            raise ValueError("声明 max_cost 时必须声明 cost_currency")
        return self

class DataSnapshot(ImmutableRecord):

    snapshot_id: str
    as_of: datetime
    market: str
    instrument_class: str
    manifest_refs: list[str] = Field(min_length=1)
    dataset_fingerprints: dict[str, str] = Field(min_length=1)
    pit_cutoffs: dict[str, datetime] = Field(default_factory=dict)
    universe_ref: str
    market_rule_ref: str
    quality_status: QualityStatus
    quality_event_refs: list[str] = Field(default_factory=list)
    parent_snapshot_id: str | None = None

class ResearchProgram(ImmutableRecord):

    program_id: str
    loop: ResearchLoop
    objective: str
    hypothesis_scope: list[str] = Field(min_length=1)
    data_snapshot_id: str
    protocol_hash: str
    train_window: TimeWindow | None = None
    validation_window: TimeWindow | None = None
    locked_test_window: TimeWindow | None = None
    embargo_days: int = Field(default=0, ge=0)
    targets: dict[str, float | int | str] = Field(min_length=1)
    configuration: dict[str, Any] = Field(default_factory=dict)
    
    budget: ResearchBudget
    random_seed: int
    parent_program_id: str | None = None

    @model_validator(mode="after")
    def _isolated_windows(self) -> ResearchProgram:
        windows = (self.train_window, self.validation_window, self.locked_test_window)
        if self.loop is ResearchLoop.LIVE_FEEDBACK:
            if any(window is not None for window in windows) and not all(
                window is not None for window in windows
            ):
                raise ValueError("实盘反馈 program 若声明研究切分，必须同时声明三个区间")
            return self
        if any(window is None for window in windows):
            raise ValueError("因子/策略研究必须声明训练、验证和锁定测试区间")
        assert self.train_window and self.validation_window and self.locked_test_window
        if not (
            self.train_window.end < self.validation_window.start
            and self.validation_window.end < self.locked_test_window.start
        ):
            raise ValueError("训练、验证和锁定测试区间必须按时间排列且互不重叠")
        return self

class FactorVersion(ImmutableRecord):

    factor_version_id: str
    factor_name: str
    parent_factor_version_id: str | None = None
    research_program_id: str
    data_snapshot_id: str
    hypothesis: str
    dependencies: list[str] = Field(min_length=1)
    code_ref: str
    code_hash: str
    parameters: dict[str, Any] = Field(default_factory=dict)
    transformations: list[str] = Field(default_factory=list)
    applicable_regimes: list[str] = Field(default_factory=list)
    change_dimension: str
    change_summary: str
    generated_by: str

    @model_validator(mode="after")
    def _unique_dependencies(self) -> FactorVersion:
        if len(self.dependencies) != len(set(self.dependencies)):
            raise ValueError("因子依赖字段不能重复")
        return self

class FactorEvaluation(ImmutableRecord):

    factor_evaluation_id: str
    factor_version_id: str
    data_snapshot_id: str
    evaluation_run_ids: list[str] = Field(min_length=1)
    primary_metrics: dict[str, float | int | str] = Field(min_length=1)
    annual_stability: dict[str, float | int | str] = Field(default_factory=dict)
    decay_metrics: dict[str, float | int | str] = Field(default_factory=dict)
    similarity: dict[str, float | int | str] = Field(default_factory=dict)
    multiple_testing: dict[str, float | int | str] = Field(default_factory=dict)
    incremental_value: dict[str, float | int | str] = Field(default_factory=dict)
    verdict: Literal["continue", "freeze", "eliminate", "invalid"]
    reason: str

class StrategyFactorInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    factor_version_id: str
    weight: float = Field(gt=0)
    transformations: list[str] = Field(default_factory=list)
    regime_weights: dict[str, float] = Field(default_factory=dict)

class StrategyVersion(ImmutableRecord):

    strategy_version_id: str
    strategy_name: str
    parent_strategy_version_id: str | None = None
    research_program_id: str
    data_snapshot_id: str
    factor_inputs: list[StrategyFactorInput] = Field(min_length=1)
    combination_policy: dict[str, Any]
    portfolio_policy: dict[str, Any]
    risk_policy: dict[str, Any]
    execution_policy: dict[str, Any]
    change_dimension: StrategyChangeDimension
    change_summary: str
    hypothesis: str

    @model_validator(mode="after")
    def _one_dimension(self) -> StrategyVersion:
        if self.parent_strategy_version_id and self.change_dimension is StrategyChangeDimension.INITIAL:
            raise ValueError("有父版本的策略必须声明本轮唯一变更维度")
        if not self.parent_strategy_version_id and self.change_dimension is not StrategyChangeDimension.INITIAL:
            raise ValueError("首个策略版本的 change_dimension 必须为 initial")
        ids = [item.factor_version_id for item in self.factor_inputs]
        if len(ids) != len(set(ids)):
            raise ValueError("策略版本不能重复引用同一因子版本")
        if abs(sum(item.weight for item in self.factor_inputs) - 1.0) > 1e-8:
            raise ValueError("策略版本的因子权重合计必须为 1")
        return self

class EvaluationRun(ImmutableRecord):

    evaluation_run_id: str
    subject_type: SubjectType
    subject_id: str
    research_program_id: str
    data_snapshot_id: str
    purpose: RunPurpose
    window: TimeWindow
    protocol_hash: str
    artifact_ref: str
    artifact_hash: str = ""
    outcome: RunOutcome = RunOutcome.PENDING
    metrics_digest: str = ""
    failure_code: str = ""

    @model_validator(mode="after")
    def _outcome_fields(self) -> EvaluationRun:
        if self.outcome is RunOutcome.FAILED and not self.failure_code:
            raise ValueError("失败运行必须记录 failure_code")
        if self.outcome is RunOutcome.PASSED and not self.metrics_digest:
            raise ValueError("通过的运行必须记录 metrics_digest")
        return self

class LockedTestExposure(ImmutableRecord):

    exposure_id: str
    subject_type: SubjectType
    subject_id: str
    research_program_id: str
    data_snapshot_id: str
    evaluation_run_id: str
    window: TimeWindow
    protocol_hash: str
    candidate_frozen_at: datetime

class ChampionDecision(ImmutableRecord):

    decision_id: str
    champion_strategy_version_id: str
    challenger_strategy_version_id: str
    action: DecisionAction
    evidence_run_ids: list[str] = Field(min_length=1)
    shadow_window: TimeWindow | None = None
    rationale: str
    risk_delta: dict[str, float | int | str] = Field(default_factory=dict)
    rollback_strategy_version_id: str
    proposed_by: str

    @model_validator(mode="after")
    def _different_versions(self) -> ChampionDecision:
        if self.champion_strategy_version_id == self.challenger_strategy_version_id:
            raise ValueError("champion 与 challenger 必须是不同策略版本")
        if self.action is DecisionAction.PROMOTE and self.shadow_window is None:
            raise ValueError("晋升决定必须声明同期影子观察区间")
        return self

class PortfolioTarget(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    instrument_id: str
    target_quantity: float
    target_weight: float = Field(ge=0, le=1)

class ProposedOrder(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    instrument_id: str
    side: Literal["buy", "sell"]
    quantity: float = Field(gt=0)
    price_window: str
    reference_price: float = Field(gt=0)
    estimated_cost: float = Field(ge=0)
    reason: str

class PortfolioPlan(ImmutableRecord):

    portfolio_plan_id: str
    trading_date: date
    account_ids: list[str] = Field(min_length=1)
    strategy_version_id: str
    data_snapshot_id: str
    current_holding_snapshot_refs: list[str] = Field(min_length=1)
    signal_as_of: datetime
    execution_window: str
    reference_portfolio_value: float = Field(default=0, ge=0)
    targets: list[PortfolioTarget]
    proposed_orders: list[ProposedOrder]
    factor_contributions: dict[str, dict[str, float]] = Field(default_factory=dict)
    constraint_adjustments: dict[str, float | int | str] = Field(default_factory=dict)
    estimated_costs: dict[str, float] = Field(default_factory=dict)
    caveats: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _portfolio_consistency(self) -> PortfolioPlan:
        instruments = [item.instrument_id for item in self.targets]
        if len(instruments) != len(set(instruments)):
            raise ValueError("目标持仓不能重复出现同一标的")
        if sum(item.target_weight for item in self.targets) > 1 + 1e-9:
            raise ValueError("目标持仓权重合计不能超过 1")
        return self

class ExecutionFill(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    execution_id: str
    instrument_id: str
    side: Literal["buy", "sell"]
    quantity: float = Field(gt=0)
    price: float = Field(gt=0)
    executed_at: datetime
    fees: float = Field(ge=0)
    match_status: Literal["planned", "partial", "unplanned", "large_deviation"]

class ExecutionRecord(ImmutableRecord):

    execution_record_id: str
    portfolio_plan_id: str
    account_id: str
    fills: list[ExecutionFill] = Field(default_factory=list)
    unfilled_order_refs: list[str] = Field(default_factory=list)
    source: Literal["manual", "broker_adapter"]
    holding_snapshot_ref: str
    reconciliation_summary: dict[str, float | int | str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _has_execution_facts(self) -> ExecutionRecord:
        if not self.fills and not self.unfilled_order_refs:
            raise ValueError("成交记录必须包含成交或未成交事实")
        return self

class AttributionComponent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    layer: AttributionLayer
    contribution: float
    evidence: dict[str, float | int | str] = Field(default_factory=dict)
    explanation: str

class AttributionRecord(ImmutableRecord):

    attribution_record_id: str
    portfolio_plan_id: str
    execution_record_id: str
    strategy_version_id: str
    label_window: TimeWindow
    realized_return: float
    benchmark_return: float | None = None
    components: list[AttributionComponent] = Field(min_length=1)
    residual: float
    metric_artifact_ref: str

    @model_validator(mode="after")
    def _unique_layers(self) -> AttributionRecord:
        layers = [item.layer for item in self.components]
        if len(layers) != len(set(layers)):
            raise ValueError("一条归因记录中每个归因层只能出现一次")
        explained = sum(item.contribution for item in self.components) + self.residual
        if abs(explained - self.realized_return) > 1e-8:
            raise ValueError("各层贡献加残差必须与实际收益一致")
        return self

class LearningEvent(ImmutableRecord):

    learning_event_id: str
    source_object_refs: list[DomainObjectRef] = Field(min_length=1)
    category: Literal[
        "factor_decay",
        "strategy_decay",
        "execution_drift",
        "constraint_drag",
        "user_override",
        "regime_change",
        "research_success",
        "research_failure",
    ]
    observation: str
    evidence_refs: list[str] = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)
    recommended_action: str
    trigger_research_loop: ResearchLoop | None = None

class MetricCitation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    metric_name: str
    rendered_value: str
    artifact_ref: str
    artifact_hash: str
    value_path: str

class ResearchReport(ImmutableRecord):

    research_report_id: str
    research_program_id: str
    subject_refs: list[DomainObjectRef] = Field(min_length=1)
    report_kind: Literal["factor", "strategy", "portfolio", "daily", "decay"]
    report_artifact_ref: str
    metric_citations: list[MetricCitation] = Field(min_length=1)
    trial_count: int = Field(ge=0)
    locked_test_exposure_count: int = Field(ge=0)
    failed_candidate_count: int = Field(ge=0)
    limitations: list[str] = Field(min_length=1)
    future_return_disclaimer: str

Record = (
    DataSnapshot
    | ResearchProgram
    | FactorVersion
    | FactorEvaluation
    | StrategyVersion
    | EvaluationRun
    | LockedTestExposure
    | ChampionDecision
    | PortfolioPlan
    | ExecutionRecord
    | AttributionRecord
    | LearningEvent
    | ResearchReport
)

RECORD_MODELS: dict[ObjectType, type[ImmutableRecord]] = {
    ObjectType.DATA_SNAPSHOT: DataSnapshot,
    ObjectType.RESEARCH_PROGRAM: ResearchProgram,
    ObjectType.FACTOR_VERSION: FactorVersion,
    ObjectType.FACTOR_EVALUATION: FactorEvaluation,
    ObjectType.STRATEGY_VERSION: StrategyVersion,
    ObjectType.EVALUATION_RUN: EvaluationRun,
    ObjectType.LOCKED_TEST_EXPOSURE: LockedTestExposure,
    ObjectType.CHAMPION_DECISION: ChampionDecision,
    ObjectType.PORTFOLIO_PLAN: PortfolioPlan,
    ObjectType.EXECUTION_RECORD: ExecutionRecord,
    ObjectType.ATTRIBUTION_RECORD: AttributionRecord,
    ObjectType.LEARNING_EVENT: LearningEvent,
    ObjectType.RESEARCH_REPORT: ResearchReport,
}

_MODEL_TO_TYPE = {model: object_type for object_type, model in RECORD_MODELS.items()}

_ID_FIELDS: dict[ObjectType, str] = {
    ObjectType.DATA_SNAPSHOT: "snapshot_id",
    ObjectType.RESEARCH_PROGRAM: "program_id",
    ObjectType.FACTOR_VERSION: "factor_version_id",
    ObjectType.FACTOR_EVALUATION: "factor_evaluation_id",
    ObjectType.STRATEGY_VERSION: "strategy_version_id",
    ObjectType.EVALUATION_RUN: "evaluation_run_id",
    ObjectType.LOCKED_TEST_EXPOSURE: "exposure_id",
    ObjectType.CHAMPION_DECISION: "decision_id",
    ObjectType.PORTFOLIO_PLAN: "portfolio_plan_id",
    ObjectType.EXECUTION_RECORD: "execution_record_id",
    ObjectType.ATTRIBUTION_RECORD: "attribution_record_id",
    ObjectType.LEARNING_EVENT: "learning_event_id",
    ObjectType.RESEARCH_REPORT: "research_report_id",
}

def object_type_of(record: Record) -> ObjectType:
    return _MODEL_TO_TYPE[type(record)]

def object_id_of(record: Record) -> str:
    object_type = object_type_of(record)
    return str(getattr(record, _ID_FIELDS[object_type]))

def parse_record(object_type: ObjectType | str, payload: dict[str, Any]) -> Record:
    kind = ObjectType(object_type)
    return RECORD_MODELS[kind].model_validate(payload)  

def record_references(record: Record) -> list[tuple[ObjectType, str]]:
    
    refs: list[tuple[ObjectType, str]] = []
    if isinstance(record, DataSnapshot):
        if record.parent_snapshot_id:
            refs.append((ObjectType.DATA_SNAPSHOT, record.parent_snapshot_id))
    elif isinstance(record, ResearchProgram):
        refs.append((ObjectType.DATA_SNAPSHOT, record.data_snapshot_id))
        if record.parent_program_id:
            refs.append((ObjectType.RESEARCH_PROGRAM, record.parent_program_id))
    elif isinstance(record, FactorVersion):
        refs.extend(
            [
                (ObjectType.RESEARCH_PROGRAM, record.research_program_id),
                (ObjectType.DATA_SNAPSHOT, record.data_snapshot_id),
            ]
        )
        if record.parent_factor_version_id:
            refs.append((ObjectType.FACTOR_VERSION, record.parent_factor_version_id))
    elif isinstance(record, FactorEvaluation):
        refs.extend(
            [
                (ObjectType.FACTOR_VERSION, record.factor_version_id),
                (ObjectType.DATA_SNAPSHOT, record.data_snapshot_id),
                *((ObjectType.EVALUATION_RUN, run_id) for run_id in record.evaluation_run_ids),
            ]
        )
    elif isinstance(record, StrategyVersion):
        refs.extend(
            [
                (ObjectType.RESEARCH_PROGRAM, record.research_program_id),
                (ObjectType.DATA_SNAPSHOT, record.data_snapshot_id),
                *((ObjectType.FACTOR_VERSION, item.factor_version_id) for item in record.factor_inputs),
            ]
        )
        if record.parent_strategy_version_id:
            refs.append((ObjectType.STRATEGY_VERSION, record.parent_strategy_version_id))
    elif isinstance(record, EvaluationRun):
        refs.extend(
            [
                (ObjectType.RESEARCH_PROGRAM, record.research_program_id),
                (ObjectType.DATA_SNAPSHOT, record.data_snapshot_id),
                (
                    ObjectType.FACTOR_VERSION
                    if record.subject_type is SubjectType.FACTOR
                    else ObjectType.STRATEGY_VERSION,
                    record.subject_id,
                ),
            ]
        )
    elif isinstance(record, LockedTestExposure):
        refs.extend(
            [
                (ObjectType.RESEARCH_PROGRAM, record.research_program_id),
                (ObjectType.DATA_SNAPSHOT, record.data_snapshot_id),
                (ObjectType.EVALUATION_RUN, record.evaluation_run_id),
                (
                    ObjectType.FACTOR_VERSION
                    if record.subject_type is SubjectType.FACTOR
                    else ObjectType.STRATEGY_VERSION,
                    record.subject_id,
                ),
            ]
        )
    elif isinstance(record, ChampionDecision):
        refs.extend(
            [
                (ObjectType.STRATEGY_VERSION, record.champion_strategy_version_id),
                (ObjectType.STRATEGY_VERSION, record.challenger_strategy_version_id),
                (ObjectType.STRATEGY_VERSION, record.rollback_strategy_version_id),
                *((ObjectType.EVALUATION_RUN, run_id) for run_id in record.evidence_run_ids),
            ]
        )
    elif isinstance(record, PortfolioPlan):
        refs.extend(
            [
                (ObjectType.STRATEGY_VERSION, record.strategy_version_id),
                (ObjectType.DATA_SNAPSHOT, record.data_snapshot_id),
            ]
        )
    elif isinstance(record, ExecutionRecord):
        refs.append((ObjectType.PORTFOLIO_PLAN, record.portfolio_plan_id))
    elif isinstance(record, AttributionRecord):
        refs.extend(
            [
                (ObjectType.PORTFOLIO_PLAN, record.portfolio_plan_id),
                (ObjectType.EXECUTION_RECORD, record.execution_record_id),
                (ObjectType.STRATEGY_VERSION, record.strategy_version_id),
            ]
        )
    elif isinstance(record, LearningEvent):
        refs.extend((ref.object_type, ref.object_id) for ref in record.source_object_refs)
    elif isinstance(record, ResearchReport):
        refs.append((ObjectType.RESEARCH_PROGRAM, record.research_program_id))
        refs.extend((ref.object_type, ref.object_id) for ref in record.subject_refs)
    return refs

__all__ = [
    "AttributionLayer",
    "AttributionRecord",
    "ChampionDecision",
    "DataSnapshot",
    "DecisionAction",
    "DomainObjectRef",
    "EvaluationRun",
    "ExecutionRecord",
    "FactorEvaluation",
    "FactorVersion",
    "LearningEvent",
    "LockedTestExposure",
    "MetricCitation",
    "ObjectType",
    "PortfolioPlan",
    "QualityStatus",
    "RECORD_MODELS",
    "Record",
    "ResearchBudget",
    "ResearchLoop",
    "ResearchProgram",
    "ResearchReport",
    "RunOutcome",
    "RunPurpose",
    "StrategyChangeDimension",
    "StrategyFactorInput",
    "StrategyVersion",
    "SubjectType",
    "TimeWindow",
    "object_id_of",
    "object_type_of",
    "parse_record",
    "record_references",
]
