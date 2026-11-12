from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from market_rules.models import MarketRuleConfig

def _lazy_cn_a() -> MarketRuleConfig:
    from .cn_a.presets import build_cn_a_stock

    return build_cn_a_stock()

_PRESETS: dict[str, Callable[[], MarketRuleConfig]] = {
    "CN-A-stock": _lazy_cn_a,
}

def list_presets() -> list[str]:
    
    return sorted(_PRESETS.keys())

def load_preset(name: str) -> MarketRuleConfig:
    
    factory = _PRESETS.get(name)
    if factory is None:
        available = ", ".join(list_presets()) or "（无）"
        msg = f"未知预置：{name}。可用预置：{available}"
        raise KeyError(msg)
    return factory()
