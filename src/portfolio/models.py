from __future__ import annotations
from typing import Literal

import re
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{1,31}$")
_TRADE_ID_PATTERN = re.compile(r"^TRD-(?:\d{8}-[0-9a-f]{6}|[0-9a-f]{32})$")
_PLAN_ID_PATTERN = re.compile(r"^PLN-\d{8}-[0-9a-f]{6}$")
_SNAP_ID_PATTERN = re.compile(r"^SNP-\d{8}-\d{6}Z-[0-9a-f]{6}$")
_CURRENCY_PATTERN = re.compile(r"^[A-Z]{3}$")

# 枚举

class Side(StrEnum):

    LONG = "long"
    SHORT = "short"

class Action(StrEnum):

    OPEN = "open"  # 新开仓
    CLOSE = "close"  # 平仓
    INCREASE = "increase"  # 加仓
    DECREASE = "decrease"  # 减仓

class TradeStatus(StrEnum):

    PLANNED = "planned"
    PARTIAL = "partial"
    FAILED = "failed"
    SKIPPED = "skipped"
    UNPLANNED = "unplanned"
    PLANNED_WITH_LARGE_DEVIATION = "planned_with_large_deviation"

class PlanState(StrEnum):

    ACTIVE = "active"
    EXECUTED = "executed"
    STALE_FOR_EXECUTION = "stale_for_execution"
    ARCHIVED = "archived"
    CANCELLED = "cancelled"

class SnapshotMode(StrEnum):

    DETAILED = "detailed"
    UNCHANGED = "unchanged"  

# 基础模型

class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

class Cash(_StrictModel):

    currency: str
    total: Decimal = Decimal("0")
    available: Decimal = Decimal("0")
    withdrawable: Decimal = Decimal("0")
    frozen: Decimal = Decimal("0")

    @field_validator("currency")
    @classmethod
    def _check_currency(cls, value: str) -> str:
        if not _CURRENCY_PATTERN.match(value):
            raise ValueError(f"currency 必须为 ISO 4217 三字母大写，收到 {value!r}")
        return value

class Account(_StrictModel):

    account_id: str
    alias: str = ""
    market: Literal["CN-A"]
    instrument_class: Literal["stock"]
    reporting_currency: str
    broker: str = ""
    star_market_enabled: bool = False
    cash: list[Cash] = Field(default_factory=list)
    created_at: datetime
    reinit_count: int = 0
    notes: str = ""

    @field_validator("account_id")
    @classmethod
    def _check_account_id(cls, value: str) -> str:
        if not _ID_PATTERN.match(value):
            raise ValueError(f"account_id 必须匹配 ^[a-z][a-z0-9_-]{{1,31}}$，收到 {value!r}")
        return value

    @field_validator("reporting_currency")
    @classmethod
    def _check_reporting_currency(cls, value: str) -> str:
        if not _CURRENCY_PATTERN.match(value):
            raise ValueError(f"reporting_currency 必须为 ISO 4217 三字母大写，收到 {value!r}")
        return value

    @field_validator("market", "instrument_class")
    @classmethod
    def _check_required(cls, value: str) -> str:
        if not value:
            raise ValueError("market / instrument_class 不能为空")
        return value

class Holding(_StrictModel):

    account_id: str
    market: Literal["CN-A"]
    instrument_class: Literal["stock"]
    instrument_id: str
    side: Side
    qty: Decimal
    available_qty: Decimal
    cost_price: Decimal
    currency: str
    as_of: str  

    @field_validator("currency")
    @classmethod
    def _check_currency(cls, value: str) -> str:
        if not _CURRENCY_PATTERN.match(value):
            raise ValueError(f"currency 非法：{value!r}")
        return value

class Trade(_StrictModel):

    trade_id: str
    account_id: str
    executed_at: datetime
    market: Literal["CN-A"]
    instrument_class: Literal["stock"]
    instrument_id: str
    code: str
    side: Side
    action: Action
    qty: Decimal
    price: Decimal
    fee: Decimal = Decimal("0")
    tax: Decimal = Decimal("0")
    other_cost: Decimal = Decimal("0")
    currency: str
    status: TradeStatus = TradeStatus.UNPLANNED
    related_plan_id: str | None = None
    reason: str = ""
    note: str = ""

    @field_validator("trade_id")
    @classmethod
    def _check_trade_id(cls, value: str) -> str:
        if not _TRADE_ID_PATTERN.match(value):
            raise ValueError(f"trade_id 必须使用系统生成的编号，收到 {value!r}")
        return value

    @field_validator("currency")
    @classmethod
    def _check_currency(cls, value: str) -> str:
        if not _CURRENCY_PATTERN.match(value):
            raise ValueError(f"currency 非法：{value!r}")
        return value

# 快照

class SnapshotPosition(_StrictModel):

    market: Literal["CN-A"]
    instrument_class: Literal["stock"]
    instrument_id: str
    side: Side
    qty: Decimal
    available_qty: Decimal
    cost_price: Decimal
    current_price: Decimal
    currency: str
    market_value_native: Decimal
    market_value_reporting: Decimal
    pnl_native: Decimal
    pnl_reporting: Decimal

class CurrencyTotals(_StrictModel):

    total_assets: Decimal
    available_cash: Decimal
    floating_pnl: Decimal

class ReportingTotals(_StrictModel):

    total_assets: Decimal
    total_market_value: Decimal
    available_cash: Decimal
    floating_pnl: Decimal

class Snapshot(_StrictModel):

    snapshot_id: str
    account_id: str
    mode: SnapshotMode = SnapshotMode.DETAILED
    taken_at: datetime
    reporting_currency: str
    fx_snapshot: dict[str, str] = Field(default_factory=dict)
    totals_reporting: ReportingTotals
    totals_by_currency: dict[str, CurrencyTotals] = Field(default_factory=dict)
    positions: list[SnapshotPosition] = Field(default_factory=list)
    source: str = "manual"
    reconcile_status: str = "ok"  
    reconcile_note: str = ""

    @field_validator("snapshot_id")
    @classmethod
    def _check_snapshot_id(cls, value: str) -> str:
        if not _SNAP_ID_PATTERN.match(value):
            raise ValueError(f"snapshot_id 必须形如 SNP-YYYYMMDD-HHMMSSZ-xxxxxx，收到 {value!r}")
        return value

# 调仓计划

class EffectiveWindow(_StrictModel):
    start: datetime
    end: datetime

class TargetPosition(_StrictModel):

    market: Literal["CN-A"]
    instrument_class: Literal["stock"]
    instrument_id: str
    side: Side
    qty: Decimal
    weight: Decimal | None = None

class SuggestedTrade(_StrictModel):

    market: Literal["CN-A"]
    instrument_class: Literal["stock"]
    instrument_id: str
    side: Side
    action: Action
    qty: Decimal
    reference_price: Decimal
    currency: str
    estimated_cost: Decimal = Decimal("0")

class Plan(_StrictModel):

    plan_id: str
    account_ids: list[str]
    generated_at: datetime
    effective_window: EffectiveWindow
    data_manifest_ref: str = ""
    strategy_profile_ref: str = ""
    market_rule_refs: list[str] = Field(default_factory=list)
    current_holdings_digest: str = ""
    fx_snapshot: dict[str, str] = Field(default_factory=dict)
    target_holdings: list[TargetPosition] = Field(default_factory=list)
    suggested_trades: list[SuggestedTrade] = Field(default_factory=list)
    trigger_reason: str = ""
    caveats: list[str] = Field(default_factory=list)
    estimated_fees_by_currency: dict[str, str] = Field(default_factory=dict)
    state: PlanState = PlanState.ACTIVE

    @field_validator("plan_id")
    @classmethod
    def _check_plan_id(cls, value: str) -> str:
        if not _PLAN_ID_PATTERN.match(value):
            raise ValueError(f"plan_id 必须形如 PLN-YYYYMMDD-xxxxxx，收到 {value!r}")
        return value

    @field_validator("account_ids")
    @classmethod
    def _check_accounts(cls, value: list[str]) -> list[str]:
        if not value:
            raise ValueError("account_ids 不能为空（计划至少关联一个账户）")
        return value

HOLDING_COLUMNS: tuple[str, ...] = tuple(Holding.model_fields.keys())
TRADE_COLUMNS: tuple[str, ...] = tuple(Trade.model_fields.keys())

def dump_decimals(model: BaseModel) -> dict[str, Any]:

    raw = model.model_dump(mode="json")

    def _convert(value: Any) -> Any:
        if isinstance(value, Decimal):
            return str(value)
        if isinstance(value, dict):
            return {k: _convert(v) for k, v in value.items()}
        if isinstance(value, list):
            return [_convert(v) for v in value]
        return value

    return _convert(raw)
