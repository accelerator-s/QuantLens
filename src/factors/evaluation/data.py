from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from exceptions import QuantTrainingError

from .config import EvaluationConfig

@dataclass
class ChunkData:

    eval_dates: pd.DatetimeIndex
    returns: pd.DataFrame
    
    entry_ok: pd.DataFrame
    
    signal_mask: pd.DataFrame
    
    industry: pd.DataFrame | None = None
    log_mktcap: pd.DataFrame | None = None
    caliber_warnings: list[str] = field(default_factory=list)

def market_fields_for(config: EvaluationConfig) -> list[str]:
    
    fields = ["close", "high", "low", "is_trading", "listed_days"]
    if config.return_field != "close_raw":
        fields.append(config.return_field)
    if config.needs_industry():
        fields.append("industry_code")
    if config.needs_size():
        fields.append("mktcap_float")
    return fields

def compute_daily_returns(
    panel: dict[str, pd.DataFrame], config: EvaluationConfig
) -> tuple[pd.DataFrame, list[str]]:
    
    warnings: list[str] = []
    if config.return_field == "close_raw":
        close = panel["close"]

        filled = close.ffill()
        returns = filled.pct_change(fill_method=None)
        warnings.append(
            "return_caliber_warning: 收益使用未复权 close 口径（临时方案），"
            "除权除息日存在虚假跳变；returns_daily 字段核对落地后请改用 ret_wd。"
        )
    else:
        returns = panel[config.return_field].astype("float64")
    return returns, warnings

def forward_returns(returns: pd.DataFrame, horizon: int) -> pd.DataFrame:
    
    gross = (returns.fillna(0.0) + 1.0).to_numpy(dtype="float64")
    cum = np.cumprod(gross, axis=0)
    n = cum.shape[0]
    fwd = np.full_like(cum, np.nan)
    if horizon < n:
        fwd[: n - horizon] = cum[horizon:] / cum[: n - horizon] - 1.0
    return pd.DataFrame(fwd, index=returns.index, columns=returns.columns)

def entry_tradable_mask(panel: dict[str, pd.DataFrame], config: EvaluationConfig) -> pd.DataFrame:
    
    is_trading = panel["is_trading"].astype(bool)
    ok = pd.DataFrame(True, index=is_trading.index, columns=is_trading.columns)
    if config.exclude_suspended_entry:
        ok &= is_trading
    else:
        
        ok &= panel["listed_days"] > 0
    if config.exclude_limit_locked_entry:
        locked = (panel["high"] == panel["low"]) & is_trading
        ok &= ~locked
    
    shifted = ok.shift(-1)
    return shifted.fillna(False).astype(bool)

def signal_universe_mask(panel: dict[str, pd.DataFrame], config: EvaluationConfig) -> pd.DataFrame:
    
    from panel.universe import UniverseConfig, build_universe_mask

    return build_universe_mask(
        UniverseConfig(
            min_listed_days=config.min_listed_days,
            exclude_suspended=True,
            exclude_st=False,  
        ),
        is_trading=panel["is_trading"],
        listed_days=panel["listed_days"],
    )

def build_chunk_data(
    panel: dict[str, pd.DataFrame],
    *,
    config: EvaluationConfig,
    eval_start: str,
    eval_end: str,
) -> ChunkData:
    
    returns, warnings = compute_daily_returns(panel, config)
    index = returns.index
    eval_dates = index[(index >= pd.Timestamp(eval_start)) & (index <= pd.Timestamp(eval_end))]
    if len(eval_dates) == 0:
        raise QuantTrainingError(
            code="EVALUATION_EMPTY_CHUNK",
            message=f"评价分块 {eval_start} → {eval_end} 内没有交易日。",
            context={"eval_start": eval_start, "eval_end": eval_end},
            blocking=True,
            suggested_action="确认评价区间与缓存覆盖区间有交集。",
        )

    industry = panel.get("industry_code")
    log_mktcap: pd.DataFrame | None = None
    if config.needs_size():
        mktcap = panel["mktcap_float"]
        with np.errstate(divide="ignore", invalid="ignore"):
            log_mktcap = pd.DataFrame(
                np.log(mktcap.where(mktcap > 0).to_numpy(dtype="float64")),
                index=mktcap.index,
                columns=mktcap.columns,
            )

    return ChunkData(
        eval_dates=eval_dates,
        returns=returns,
        entry_ok=entry_tradable_mask(panel, config),
        signal_mask=signal_universe_mask(panel, config),
        industry=industry,
        log_mktcap=log_mktcap,
        caliber_warnings=warnings,
    )

def align_scores(
    raw_scores: pd.DataFrame,
    *,
    chunk: ChunkData,
) -> pd.DataFrame:
    
    scores = raw_scores.copy()
    scores.index = pd.to_datetime(scores.index)
    scores = scores.reindex(index=chunk.eval_dates, columns=chunk.returns.columns)
    scores = scores.astype("float64")
    mask = chunk.signal_mask.reindex(index=chunk.eval_dates, columns=scores.columns)
    return scores.where(mask.fillna(False).astype(bool))

__all__ = [
    "ChunkData",
    "align_scores",
    "build_chunk_data",
    "compute_daily_returns",
    "entry_tradable_mask",
    "forward_returns",
    "market_fields_for",
    "signal_universe_mask",
]
