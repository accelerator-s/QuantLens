from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import TYPE_CHECKING, Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from exceptions import StrategyProfileError

if TYPE_CHECKING:
    from panel.universe import UniverseConfig

def _utcnow() -> datetime:
    return datetime.now(tz=UTC)

# 枚举

class StrategyStatus(StrEnum):

    DRAFT = "draft"
    ACTIVE = "active"
    DISABLED = "disabled"

class Frequency(StrEnum):

    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"
    QUARTERLY = "quarterly"
    EVENT_DRIVEN = "event_driven"

class WeightMode(StrEnum):

    EQUAL = "equal"
    IC = "ic"
    ICIR = "icir"
    MODEL_LINEAR = "model_linear"
    MODEL_RIDGE = "model_ridge"
    MODEL_LOGISTIC = "model_logistic"

class FillnaMethod(StrEnum):

    DROP = "drop"
    ZERO = "zero"
    CROSS_MEDIAN = "cross_median"
    INDUSTRY_MEDIAN = "industry_median"

class RankMethod(StrEnum):

    AVERAGE = "average"
    MIN = "min"
    MAX = "max"
    DENSE = "dense"

# 因子清单

class FactorRef(BaseModel):

    model_config = ConfigDict(extra="forbid")

    name: str
    weight_override: Decimal | None = None

    direction_override: str = ""

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        v = v.strip()
        if not v.replace("_", "").isalnum():
            raise ValueError("因子名只允许 [A-Za-z0-9_]。")
        return v

class FactorSpec(BaseModel):

    model_config = ConfigDict(extra="forbid")

    weighting: WeightMode
    items: list[FactorRef] = Field(min_length=1)

    @model_validator(mode="after")
    def _weights_consistent(self) -> FactorSpec:
        names = [f.name for f in self.items]
        if len(names) != len(set(names)):
            raise ValueError("因子清单存在重复因子名。")
        return self

class WinsorizeStep(BaseModel):

    model_config = ConfigDict(extra="forbid")

    step: Literal["winsorize"]
    lower_pct: Decimal = Field(ge=0, lt=1)
    upper_pct: Decimal = Field(gt=0, le=1)

    @model_validator(mode="after")
    def _range(self) -> WinsorizeStep:
        if self.lower_pct >= self.upper_pct:
            raise ValueError("winsorize 要求 lower_pct < upper_pct。")
        return self

class ZScoreStep(BaseModel):

    model_config = ConfigDict(extra="forbid")

    step: Literal["zscore"]

class RankStep(BaseModel):

    model_config = ConfigDict(extra="forbid")

    step: Literal["rank"]
    method: RankMethod = RankMethod.AVERAGE

class IndustryNeutralStep(BaseModel):

    model_config = ConfigDict(extra="forbid")

    step: Literal["industry_neutral"]
    industry_source: str = "sw2021"

class SizeNeutralStep(BaseModel):

    model_config = ConfigDict(extra="forbid")

    step: Literal["size_neutral"]
    size_field: str = "log_mktcap"

class FillnaStep(BaseModel):

    model_config = ConfigDict(extra="forbid")

    step: Literal["fillna"]
    method: FillnaMethod

StandardizationStep = Annotated[
    WinsorizeStep | ZScoreStep | RankStep | IndustryNeutralStep | SizeNeutralStep | FillnaStep,
    Field(discriminator="step"),
]

# 其余子模型

class StrategyUniverse(BaseModel):

    model_config = ConfigDict(extra="forbid")

    min_listed_days: int = Field(ge=0)

    exclude_suspended: bool
    exclude_st: bool
    notes: str = ""

    def to_panel_config(self) -> UniverseConfig:
        
        from panel.universe import UniverseConfig

        return UniverseConfig(
            min_listed_days=self.min_listed_days,
            exclude_suspended=self.exclude_suspended,
            exclude_st=self.exclude_st,
        )

class TradingRulesRef(BaseModel):

    model_config = ConfigDict(extra="forbid")

    market_rule: str = Field(min_length=1)

class ExecutionAssumption(BaseModel):

    model_config = ConfigDict(extra="forbid")

    price_anchor: str = Field(min_length=1)

    fill_assumption: str = Field(min_length=1)

    notes: str = ""

class PositionConstraint(BaseModel):

    model_config = ConfigDict(extra="forbid")

    top_n: int = Field(gt=0)
    max_weight_per_name: Decimal = Field(gt=0, le=1)
    min_cash_ratio: Decimal = Field(default=Decimal("0"), ge=0, lt=1)

class FeesSlippage(BaseModel):

    model_config = ConfigDict(extra="forbid")

    fee_source: Literal["market_rule", "manual"]
    slippage_bps: Decimal = Field(ge=0)
    manual_fee_bps: Decimal | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _fee_consistent(self) -> FeesSlippage:
        if self.fee_source == "manual" and self.manual_fee_bps is None:
            raise ValueError("fee_source=manual 时必须给出 manual_fee_bps。")
        if self.fee_source == "market_rule" and self.manual_fee_bps is not None:
            raise ValueError("fee_source=market_rule 时不允许设置 manual_fee_bps。")
        return self

class RiskControl(BaseModel):

    model_config = ConfigDict(extra="forbid")

    max_single_name_pct: Decimal = Field(gt=0, le=1)
    max_industry_pct: Decimal = Field(gt=0, le=1)
    notes: str = ""

# 存储层文档

REQUIRED_FIELDS: tuple[str, ...] = (
    "market",
    "instruments",
    "timescale",
    "data_frequency",
    "universe",
    "factors",
    "standardization_chain",
    "trading_rules",
    "execution_assumption",
    "rebalance_frequency",
    "holding_period",
    "position_constraint",
    "fees_slippage",
    "risk_control",
    "price_deviation_threshold",
)

FIELD_LABELS: dict[str, str] = {
    "profile_name": "策略配置名称",
    "market": "市场",
    "instruments": "品种",
    "timescale": "时间尺度",
    "data_frequency": "数据频率",
    "universe": "universe 定义",
    "factors": "因子清单",
    "standardization_chain": "标准化链",
    "trading_rules": "交易规则",
    "execution_assumption": "执行假设",
    "rebalance_frequency": "调仓频率",
    "holding_period": "持仓期",
    "position_constraint": "仓位约束",
    "fees_slippage": "费用/滑点",
    "risk_control": "风控规则",
    "price_deviation_threshold": "价格偏差阈值",
    "inheritance_parent": "继承父级",
}

class PortfolioConstraints(BaseModel):
    model_config = ConfigDict(extra="forbid")
    max_weight: float = Field(default=.10, gt=0, le=1)
    max_cluster_weight: float = Field(default=.35, gt=0, le=1)
    max_industry_weight: float = Field(default=.30, gt=0, le=1)
    cash_reserve: float = Field(default=.10, ge=0, lt=1)
    max_positions: int = Field(default=30, ge=1)

class RegimeFilter(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = True
    breadth_lookback: int = Field(default=60, ge=1)
    breadth_low: float = Field(default=.40, ge=0, le=1)
    breadth_crisis: float = Field(default=.30, ge=0, le=1)
    exposure_low: float = Field(default=.50, ge=0, le=1)
    exposure_crisis: float = Field(default=.25, ge=0, le=1)

class PortfolioRisk(BaseModel):
    model_config = ConfigDict(extra="forbid")
    regime_filter: RegimeFilter = Field(default_factory=RegimeFilter)

class StrategyProfileDoc(BaseModel):

    model_config = ConfigDict(extra="forbid")

    # 结构字段
    profile_name: str
    display_name: str = ""

    is_template: bool = False

    inheritance_parent: str = ""
    status: StrategyStatus = StrategyStatus.DRAFT
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)

    market: Literal['CN-A'] | None = None
    instruments: list[Literal['stock']] | None = None
    timescale: Frequency | None = None
    data_frequency: Frequency | None = None
    universe: StrategyUniverse | None = None
    factors: FactorSpec | None = None
    standardization_chain: list[StandardizationStep] | None = None

    trading_rules: TradingRulesRef | None = None
    execution_assumption: ExecutionAssumption | None = None
    rebalance_frequency: Frequency | None = None
    holding_period: int | None = Field(default=None, gt=0)

    position_constraint: PositionConstraint | None = None
    fees_slippage: FeesSlippage | None = None
    risk_control: RiskControl | None = None
    price_deviation_threshold: Decimal | None = Field(default=None, ge=0)

    constraints: PortfolioConstraints = Field(default_factory=PortfolioConstraints)
    risk: PortfolioRisk = Field(default_factory=PortfolioRisk)
    benchmark: str = "model_selected"
    extra: dict[str, Any] = Field(default_factory=dict)

    @field_validator("profile_name")
    @classmethod
    def _profile_name(cls, v: str) -> str:
        v = v.strip()
        if not v.replace("_", "").isalnum():
            raise ValueError("策略配置名只允许 [A-Za-z0-9_]。")
        return v

    @field_validator("instruments")
    @classmethod
    def _instruments(cls, v: list[str] | None) -> list[str] | None:
        if v is not None and not v:
            raise ValueError("instruments 不允许为空列表；未确认请整体留空（null）。")
        return v

    def missing_required_fields(self) -> list[str]:
        
        return [name for name in REQUIRED_FIELDS if getattr(self, name) is None]

class ResolvedStrategyProfile(BaseModel):

    model_config = ConfigDict(extra="forbid")

    profile_name: str
    display_name: str
    status: StrategyStatus
    source_chain: list[str]

    market: Literal['CN-A']
    instruments: list[Literal['stock']] = Field(min_length=1)
    timescale: Frequency
    data_frequency: Frequency
    universe: StrategyUniverse
    factors: FactorSpec
    standardization_chain: list[StandardizationStep]
    trading_rules: TradingRulesRef
    execution_assumption: ExecutionAssumption
    rebalance_frequency: Frequency
    holding_period: int = Field(gt=0)
    position_constraint: PositionConstraint
    fees_slippage: FeesSlippage
    risk_control: RiskControl
    price_deviation_threshold: Decimal = Field(ge=0)
    constraints: PortfolioConstraints = Field(default_factory=PortfolioConstraints)
    risk: PortfolioRisk = Field(default_factory=PortfolioRisk)
    benchmark: str = "model_selected"
    extra: dict[str, Any] = Field(default_factory=dict)

# 继承解析与状态机

_MAX_INHERIT_DEPTH = 10

_VALID_TRANSITIONS: dict[StrategyStatus, set[StrategyStatus]] = {
    StrategyStatus.DRAFT: {StrategyStatus.ACTIVE, StrategyStatus.DISABLED},
    StrategyStatus.ACTIVE: {StrategyStatus.DISABLED},
    StrategyStatus.DISABLED: {StrategyStatus.DRAFT, StrategyStatus.ACTIVE},
}

def validate_transition(current: StrategyStatus, target: StrategyStatus) -> None:
    
    if target not in _VALID_TRANSITIONS.get(current, set()):
        raise StrategyProfileError(
            code="STRATEGY_PROFILE_INVALID_TRANSITION",
            message=f"策略配置状态不允许从 {current.value} 转为 {target.value}",
            blocking=True,
            suggested_action="合法流转：draft→active/disabled、active→disabled、disabled→draft/active。",
        )

def resolve_inheritance_chain(
    doc: StrategyProfileDoc,
    get_parent: Callable[[str], StrategyProfileDoc],
) -> list[StrategyProfileDoc]:
    
    chain: list[StrategyProfileDoc] = [doc]
    visited: set[str] = {doc.profile_name}
    cur = doc
    while cur.inheritance_parent:
        parent_name = cur.inheritance_parent
        if parent_name in visited:
            raise StrategyProfileError(
                code="STRATEGY_PROFILE_INHERIT_CYCLE",
                message=(
                    "策略配置继承链存在环："
                    + " -> ".join([*(d.profile_name for d in chain), parent_name])
                ),
                context={"chain": [d.profile_name for d in chain]},
                blocking=True,
                suggested_action="检查各配置的 inheritance_parent，消除循环引用。",
            )
        if len(chain) >= _MAX_INHERIT_DEPTH:
            raise StrategyProfileError(
                code="STRATEGY_PROFILE_INHERIT_CYCLE",
                message=f"策略配置继承链超过最大深度 {_MAX_INHERIT_DEPTH}。",
                context={"chain": [d.profile_name for d in chain]},
                blocking=True,
                suggested_action="精简继承层级；V1 建议不超过 2 层（模板 + 派生）。",
            )
        try:
            parent = get_parent(parent_name)
        except StrategyProfileError as exc:
            raise StrategyProfileError(
                code="STRATEGY_PROFILE_PARENT_NOT_FOUND",
                message=f"策略配置 {cur.profile_name} 的父级 {parent_name} 不存在。",
                context={"child": cur.profile_name, "parent": parent_name},
                blocking=True,
                suggested_action="先创建父级模板，或修正 inheritance_parent。",
            ) from exc
        chain.append(parent)
        visited.add(parent_name)
        cur = parent
    return chain

def resolve_profile(
    doc: StrategyProfileDoc,
    get_parent: Callable[[str], StrategyProfileDoc],
) -> ResolvedStrategyProfile:
    
    chain = resolve_inheritance_chain(doc, get_parent)

    merged: dict[str, Any] = {}
    for name in REQUIRED_FIELDS:
        for node in chain:
            value = getattr(node, name)
            if value is not None:
                merged[name] = value
                break

    extra: dict[str, Any] = {}
    for node in reversed(chain):
        extra.update(node.extra)

    missing = [name for name in REQUIRED_FIELDS if name not in merged]
    if missing:
        labels = [f"{name}（{FIELD_LABELS.get(name, name)}）" for name in missing]
        raise StrategyProfileError(
            code="STRATEGY_PROFILE_MISSING_FIELDS",
            message=(
                f"策略配置 {doc.profile_name} 合并继承链后仍缺失 "
                f"{len(missing)} 个必填字段：{'、'.join(labels)}"
            ),
            context={
                "profile": doc.profile_name,
                "chain": [d.profile_name for d in chain],
                "missing": missing,
            },
            blocking=True,
            suggested_action="补齐缺失字段后重新 create --override；程序不会自动填入未确认值。",
        )

    return ResolvedStrategyProfile(
        profile_name=doc.profile_name,
        display_name=doc.display_name or doc.profile_name,
        status=doc.status,
        source_chain=[d.profile_name for d in chain],
        extra=extra,
        constraints=doc.constraints, risk=doc.risk, benchmark=doc.benchmark,
        **merged,
    )

__all__ = [
    "FIELD_LABELS",
    "REQUIRED_FIELDS",
    "ExecutionAssumption",
    "FactorRef",
    "FactorSpec",
    "FeesSlippage",
    "FillnaMethod",
    "FillnaStep",
    "Frequency",
    "IndustryNeutralStep",
    "PositionConstraint",
    "RankMethod",
    "RankStep",
    "ResolvedStrategyProfile",
    "RiskControl",
    "SizeNeutralStep",
    "StandardizationStep",
    "StrategyProfileDoc",
    "StrategyStatus",
    "StrategyUniverse",
    "TradingRulesRef",
    "WeightMode",
    "WinsorizeStep",
    "ZScoreStep",
    "resolve_inheritance_chain",
    "resolve_profile",
    "validate_transition",
]
