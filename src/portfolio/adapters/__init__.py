from __future__ import annotations

from .base import (
    CostBreakdown,
    InstrumentAdapter,
    get_adapter,
    list_adapters,
    register_adapter,
)

register_adapter(
    "CN-A",
    "stock",
    module_path="portfolio.adapters.cn_a_stock",
    class_name="CnAStockAdapter",
)

__all__ = [
    "CostBreakdown",
    "InstrumentAdapter",
    "get_adapter",
    "list_adapters",
    "register_adapter",
]
