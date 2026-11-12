from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from exceptions import FxRateUnavailableError

def _unavailable(base: str, quote: str) -> FxRateUnavailableError:
    return FxRateUnavailableError(
        code="fx.unavailable",
        message=(f"FX 模块未启用：无法获取 {base} -> {quote} 汇率"),
        context={"base": base, "quote": quote},
        blocking=True,
        suggested_action=(
            "请新建 framework issue 启用 FX 模块并绑定数据源；在数据源接入前，仅允许单币种账户"
        ),
    )

def convert(
    amount: Decimal,
    *,
    base: str,
    quote: str,
    asof: datetime | None = None,
) -> Decimal:
    
    if base == quote:
        return amount
    raise _unavailable(base, quote)

def get_rate(
    base: str,
    quote: str,
    *,
    asof: datetime | None = None,
) -> Decimal:
    
    if base == quote:
        return Decimal("1")
    raise _unavailable(base, quote)

def snapshot_rates(
    currencies: list[str],
    *,
    reporting_currency: str,
    asof: datetime | None = None,
) -> dict[str, Decimal]:
    
    out: dict[str, Decimal] = {}
    for cur in currencies:
        if cur == reporting_currency:
            out[cur] = Decimal("1")
        else:
            raise _unavailable(cur, reporting_currency)
    return out

__all__ = ["convert", "get_rate", "snapshot_rates"]
