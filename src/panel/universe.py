from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from exceptions import PanelCacheError

_ST_STATUS_CODES: frozenset[str] | None = frozenset(
    {"2", "3", "5", "6", "8", "9", "11", "12", "14", "15", "16"}
)

@dataclass(frozen=True)
class UniverseConfig:

    min_listed_days: int = 0  # 0 = 不做次新过滤
    exclude_suspended: bool = True
    exclude_st: bool = False

def build_universe_mask(
    config: UniverseConfig,
    *,
    is_trading: pd.DataFrame,
    listed_days: pd.DataFrame,
    trading_status: pd.DataFrame | None = None,
) -> pd.DataFrame:
    
    mask = pd.DataFrame(True, index=is_trading.index, columns=is_trading.columns)

    if config.exclude_suspended:
        mask &= is_trading.astype(bool)

    if config.min_listed_days > 0:
        mask &= listed_days >= config.min_listed_days

    if config.exclude_st:
        if _ST_STATUS_CODES is None:
            raise PanelCacheError(
                code="PANEL_ST_FILTER_UNAVAILABLE",
                message="ST 过滤不可用：Trdsta 交易状态编码尚未人工核对。",
                context={"required": "universe.py 中 _ST_STATUS_CODES"},
                blocking=True,
                suggested_action=(
                    "登录 CSMAR 核对 Trdsta 编码表并填入 panel/universe.py 的 "
                    "_ST_STATUS_CODES；核对前请使用 exclude_st=False。"
                ),
            )
        if trading_status is None:
            raise PanelCacheError(
                code="PANEL_ST_FILTER_UNAVAILABLE",
                message="ST 过滤不可用：缓存中无 trading_status 字段（returns_daily 未接入）。",
                blocking=True,
                suggested_action=(
                    "核对 returns_daily 字段映射并重新发布 DWD 批次。"
                ),
            )
        mask &= ~trading_status.isin(sorted(_ST_STATUS_CODES))

    return mask
