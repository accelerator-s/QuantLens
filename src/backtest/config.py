from __future__ import annotations

import hashlib
import json
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from exceptions import BacktestError
from strategy import ResolvedStrategyProfile, StrategyProfileStore

from .protocol import BacktestProtocol

class BacktestConfig(BaseModel):

    model_config = ConfigDict(extra="forbid")

    profile_name: str = Field(min_length=1)
    start: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    end: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    initial_capital: Decimal = Field(default=Decimal("1000000"), gt=0)
    account_id: str | None = None
    account_name: str | None = None
    star_market_enabled: bool = True

    lookback_buffer_days: int = Field(default=180, ge=0)

    @model_validator(mode="after")
    def _range(self) -> BacktestConfig:
        if self.start > self.end:
            raise ValueError(f"回测区间非法：start={self.start} 晚于 end={self.end}。")
        return self

    def content_hash(self) -> str:
        
        payload = self.model_dump(mode="json")
        canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]

def _profile_protocol_diffs(
    profile: ResolvedStrategyProfile, protocol: BacktestProtocol
) -> list[str]:
    
    diffs: list[str] = []

    anchor = profile.execution_assumption.price_anchor
    if anchor != protocol.price_anchor:
        diffs.append(
            f"价格锚：profile.execution_assumption.price_anchor={anchor} "
            f"≠ protocol.price_anchor={protocol.price_anchor}"
        )

    fee_source = profile.fees_slippage.fee_source
    if fee_source != protocol.fee_source:
        diffs.append(
            f"费用来源：profile.fees_slippage.fee_source={fee_source} "
            f"≠ protocol.fee_source={protocol.fee_source}"
        )

    slippage = profile.fees_slippage.slippage_bps
    if slippage != protocol.slippage_bps:
        diffs.append(
            f"滑点：profile.fees_slippage.slippage_bps={slippage} "
            f"≠ protocol.slippage_bps={protocol.slippage_bps}"
        )

    rule_name = profile.trading_rules.market_rule
    if rule_name != protocol.market_rule_name:
        diffs.append(
            f"市场规则：profile.trading_rules.market_rule={rule_name} "
            f"≠ protocol.market_rule_name={protocol.market_rule_name}"
        )

    return diffs

def resolve_and_check(
    config: BacktestConfig, protocol: BacktestProtocol
) -> ResolvedStrategyProfile:
    
    resolved = StrategyProfileStore().resolve(config.profile_name)

    diffs = _profile_protocol_diffs(resolved, protocol)
    if diffs:
        raise BacktestError(
            code="BACKTEST_PROTOCOL_CONFLICT",
            message=(
                f"策略配置 {config.profile_name} 与考试口径存在 {len(diffs)} 项冲突：\n- "
                + "\n- ".join(diffs)
            ),
            context={"diffs": diffs},
            blocking=True,
            suggested_action=(
                "修改策略配置使其与考试口径一致（口径为准）；若确需变更口径，"
                "走人工评审解锁流程，禁止静默覆盖。"
            ),
            related_subjects={"strategy_profile": config.profile_name},
        )
    return resolved

__all__ = ["BacktestConfig", "resolve_and_check"]
