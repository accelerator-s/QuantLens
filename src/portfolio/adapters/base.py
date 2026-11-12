from __future__ import annotations

import importlib
from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING, Literal, Protocol, runtime_checkable

from exceptions import PortfolioAdapterMissingError

if TYPE_CHECKING:
    import pandas as pd

    from ..models import Action, Side

class CostBreakdown(Protocol):

    fee: Decimal
    tax: Decimal
    other_cost: Decimal

@runtime_checkable
class InstrumentAdapter(Protocol):

    market: str
    instrument_class: str

    def compute_available_qty(
        self,
        *,
        total_qty: Decimal,
        recent_trades: pd.DataFrame,
        as_of: date,
    ) -> Decimal:
        pass

    def compute_costs(
        self,
        *,
        side: Side,
        action: Action,
        qty: Decimal,
        price: Decimal,
        currency: str,
    ) -> CostBreakdown:
        pass

    def validate_qty(self, qty: Decimal, *, action: Action, instrument_id: str) -> None:
        pass

    def update_cost_price(
        self,
        *,
        current_qty: Decimal,
        current_cost: Decimal,
        trade_action: Action,
        trade_qty: Decimal,
        trade_price: Decimal,
    ) -> Decimal:
        pass

    def contract_multiplier(self) -> Decimal:
        pass

    def margin_requirement(self, *, side: Side, qty: Decimal, price: Decimal) -> Decimal:
        pass

    def position_layout(self) -> Literal["spot_long_only", "long_short", "lockable"]:
        pass

# 注册表

_ADAPTER_FACTORIES: dict[tuple[str, str], tuple[str, str]] = {}

def register_adapter(
    market: str, instrument_class: str, *, module_path: str, class_name: str
) -> None:
    
    _ADAPTER_FACTORIES[(market, instrument_class)] = (module_path, class_name)

def list_adapters() -> list[tuple[str, str]]:
    
    return list(_ADAPTER_FACTORIES.keys())

def get_adapter(market: str, instrument_class: str) -> InstrumentAdapter:
    
    key = (market, instrument_class)
    entry = _ADAPTER_FACTORIES.get(key)
    if entry is None:
        raise PortfolioAdapterMissingError(
            code="portfolio.adapter_missing",
            message=f"未注册的 (market, instrument_class)：{key!r}",
            context={"market": market, "instrument_class": instrument_class},
            blocking=True,
            suggested_action=(
                "请创建 framework 或 market_rule 类 issue，敲定该 "
                "(market, instrument_class) 的 InstrumentAdapter 实现后再操作"
            ),
            can_open_issue=True,
        )
    module_path, class_name = entry
    module = importlib.import_module(module_path)
    cls = getattr(module, class_name)
    instance = cls()
    if not isinstance(instance, InstrumentAdapter):
        raise PortfolioAdapterMissingError(
            code="portfolio.adapter_invalid",
            message=f"{module_path}.{class_name} 未实现 InstrumentAdapter 协议",
            context={"market": market, "instrument_class": instrument_class},
            blocking=True,
            suggested_action="请检查账户适配器实现",
        )
    return instance

__all__ = [
    "CostBreakdown",
    "InstrumentAdapter",
    "get_adapter",
    "list_adapters",
    "register_adapter",
]
