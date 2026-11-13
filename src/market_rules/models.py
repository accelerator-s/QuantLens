from __future__ import annotations
from typing import Literal

import re
from datetime import datetime, time
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .exceptions import MarketRuleValidationError

# 枚举

class MarketRuleStatus(StrEnum):

    DRAFT = "draft"
    ACTIVE = "active"
    DISABLED = "disabled"

class TradeDirection(StrEnum):

    BUY = "buy"
    SELL = "sell"
    BOTH = "both"

class SessionType(StrEnum):

    CALL_AUCTION = "call_auction"
    CONTINUOUS = "continuous"
    CLOSING_AUCTION = "closing_auction"
    NIGHT = "night"

# 交易时段

class SessionDefinition(BaseModel):

    model_config = ConfigDict(extra="forbid")

    name: str
    start: time
    end: time
    session_type: SessionType
    notes: str = ""

# 涨跌停

class PriceLimitSpecialRule(BaseModel):

    model_config = ConfigDict(extra="forbid")

    condition: str
    limit_pct: Decimal
    notes: str = ""

class PriceLimitRule(BaseModel):

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    default_pct: Decimal = Decimal("10")
    special_rules: list[PriceLimitSpecialRule] = Field(default_factory=list)
    first_day_rule: str = ""
    notes: str = ""

# 费用

class BoardQuantityRule(BaseModel):

    model_config = ConfigDict(extra="forbid")

    board: str
    min_unit: int
    increment: int = 0
    notes: str = ""

class FeeItem(BaseModel):

    model_config = ConfigDict(extra="forbid")

    name: str
    rate: Decimal
    min_amount: Decimal | None = None
    max_amount: Decimal | None = None
    direction: TradeDirection = TradeDirection.BOTH
    notes: str = ""

class FeeSchedule(BaseModel):

    model_config = ConfigDict(extra="forbid")

    items: list[FeeItem] = Field(default_factory=list)
    notes: str = ""

# 税费

class TaxItem(BaseModel):

    model_config = ConfigDict(extra="forbid")

    name: str
    rate: Decimal
    direction: TradeDirection = TradeDirection.BOTH
    notes: str = ""

class TaxSchedule(BaseModel):

    model_config = ConfigDict(extra="forbid")

    items: list[TaxItem] = Field(default_factory=list)
    notes: str = ""

# 定期报告披露截止日

_MMDD_PATTERN = re.compile(r"^(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$")

class DisclosureDeadlineRule(BaseModel):

    model_config = ConfigDict(extra="forbid")

    period_end: str
    deadline: str
    year_offset: int = 0
    notes: str = ""

    @field_validator("period_end", "deadline")
    @classmethod
    def _validate_mmdd(cls, value: str) -> str:
        if not _MMDD_PATTERN.match(value):
            raise MarketRuleValidationError(
                code="MARKET_RULE_INVALID_MMDD",
                message=f"披露截止日规则的日期须为 MM-DD 格式，收到：{value}",
            )
        return value

class DisclosureDeadlineSchedule(BaseModel):

    model_config = ConfigDict(extra="forbid")

    items: list[DisclosureDeadlineRule] = Field(default_factory=list)
    notes: str = ""

    def deadline_for(self, report_period: str) -> str | None:
        
        period_end = report_period[5:]
        for rule in self.items:
            if rule.period_end == period_end:
                year = int(report_period[:4]) + rule.year_offset
                return f"{year}-{rule.deadline}"
        return None

# 主模型

_VALID_TRANSITIONS: dict[MarketRuleStatus, set[MarketRuleStatus]] = {
    MarketRuleStatus.DRAFT: {MarketRuleStatus.ACTIVE, MarketRuleStatus.DISABLED},
    MarketRuleStatus.ACTIVE: {MarketRuleStatus.DISABLED},
    MarketRuleStatus.DISABLED: {MarketRuleStatus.DRAFT},
}

def validate_transition(current: MarketRuleStatus, target: MarketRuleStatus) -> None:
    
    allowed = _VALID_TRANSITIONS.get(current, set())
    if target not in allowed:
        raise MarketRuleValidationError(
            code="MARKET_RULE_INVALID_TRANSITION",
            message=f"规则状态不允许从 {current.value} 转为 {target.value}",
        )

class MarketRuleConfig(BaseModel):

    model_config = ConfigDict(extra="forbid")

    # 基础标识
    name: str
    market: Literal["CN-A"]
    instrument_class: Literal["stock"]

    # 日历
    calendar_source: str = ""
    timezone: str = "Asia/Shanghai"

    # 交易时段
    sessions: list[SessionDefinition] = Field(default_factory=list)
    session_anchor: str = ""
    night_session: bool = False
    holidays_source: str = ""

    # 交易规则
    t_plus: int = 1
    min_unit: int = 100
    fractional_shares: bool = False
    price_limit: PriceLimitRule = Field(default_factory=PriceLimitRule)
    suspension_handling: str = ""

    # 板块级交易单位
    quantity_rules: list[BoardQuantityRule] = Field(default_factory=list)

    # 费用与税
    fees: FeeSchedule = Field(default_factory=FeeSchedule)
    taxes: TaxSchedule = Field(default_factory=TaxSchedule)

    disclosure_deadlines: DisclosureDeadlineSchedule = Field(
        default_factory=DisclosureDeadlineSchedule
    )

    # 结算与锚点
    settlement: str = ""
    price_anchor: str = ""
    backtest_anchor: str = ""
    live_window: str = ""

    # 样例测试
    sample_tests: str = ""

    # 验证来源
    verification_sources: list[str] = Field(default_factory=list)

    # 元数据
    status: MarketRuleStatus = MarketRuleStatus.DRAFT
    created_at: datetime | None = None
    updated_at: datetime | None = None
