from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from exceptions import BacktestError
from paths import workspace

class ProtocolLock(BaseModel):

    model_config = ConfigDict(extra="forbid")

    locked: bool = False
    protocol_hash: str = ""
    locked_at: str = ""
    locked_by: str = ""
    notes: str = ""

class BacktestProtocol(BaseModel):

    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1

    # -- 执行时序 --
    execution_delay_days: Literal[1] = 1

    price_anchor: Literal["next_open", "next_close"] = "next_close"

    fill_assumption: Literal["all_or_nothing"] = "all_or_nothing"

    block_buy_on_suspension: bool = True

    block_buy_on_limit_up_one_line: bool = True

    block_sell_on_suspension: bool = True

    block_sell_on_limit_down_one_line: bool = True

    # -- 成本模型 --
    fee_source: Literal["market_rule"] = "market_rule"

    commission_override_rate: Decimal | None = Field(default=Decimal("0.00008"))

    model_min_commission: bool = True

    slippage_bps: Decimal = Field(default=Decimal("10"), ge=0)

    return_field: Literal["ret_wd", "ret_nd"] = "ret_wd"

    valuation_mode: Literal["mv_drift"] = "mv_drift"

    sell_proceeds_available: Literal["same_day", "next_day"] = "same_day"

    unaffordable_lot: Literal["reject_keep_cash"] = "reject_keep_cash"

    # -- 退市处置 --
    delist_force_close: bool = True

    delist_recovery_rate: Decimal = Field(default=Decimal("0"), ge=0, le=1)

    # -- 基准 --
    benchmark: Literal["universe_equal_weight"] = "universe_equal_weight"

    # -- 环境引用 --
    market_rule_name: str = "CN-A-stock"
    trading_days_per_year: int = Field(default=244, gt=0)

    lock: ProtocolLock = Field(default_factory=ProtocolLock)

    def content_hash(self) -> str:
        
        payload = self.model_dump(mode="json")
        payload.pop("lock", None)
        canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]

# 落盘与加载

def protocol_path() -> Path:
    return workspace().config / "backtest_protocol.yaml"

def save_protocol(protocol: BacktestProtocol) -> Path:
    path = protocol_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(protocol.model_dump(mode="json"), allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    return path

def load_protocol(*, create_default: bool = False) -> BacktestProtocol:
    
    path = protocol_path()
    if not path.exists():
        if create_default:
            protocol = BacktestProtocol()
            save_protocol(protocol)
            return protocol
        raise BacktestError(
            code="BACKTEST_PROTOCOL_MISSING",
            message=f"回测考试口径文件不存在：{path}",
            blocking=True,
            suggested_action="先执行 `backtest protocol show` 生成默认口径文件并人工确认内容。",
            related_files=[str(path)],
        )
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    try:
        return BacktestProtocol.model_validate(raw)
    except ValidationError as exc:
        details = "；".join(
            f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}" for err in exc.errors()
        )
        raise BacktestError(
            code="BACKTEST_PROTOCOL_INVALID",
            message=f"回测口径文件校验失败：{details}",
            context={"file": str(path)},
            blocking=True,
            related_files=[str(path)],
            suggested_action="按报错修正 config/backtest_protocol.yaml；勿直接删除 lock 块。",
        ) from exc

# 锁定与校验

def lock_protocol(*, locked_by: str = "human", notes: str = "") -> BacktestProtocol:
    
    protocol = load_protocol(create_default=True)
    current = protocol.content_hash()
    if protocol.lock.locked:
        if protocol.lock.protocol_hash == current:
            return protocol  # 幂等：已锁且未变
        raise BacktestError(
            code="BACKTEST_PROTOCOL_TAMPERED",
            message=(
                "口径已锁定但内容与锁定时不一致，拒绝覆盖旧锁"
                f"（锁定哈希 {protocol.lock.protocol_hash}，当前 {current}）。"
            ),
            blocking=True,
            suggested_action=(
                "口径变更属于考试规则变更：先人工评审差异，确认后手动删除 lock 块"
                "并重新 lock（变更须留痕，建议记录到 issue 或提交说明）。"
            ),
            related_files=[str(protocol_path())],
        )
    protocol.lock = ProtocolLock(
        locked=True,
        protocol_hash=current,
        locked_at=datetime.now(tz=UTC).isoformat(),
        locked_by=locked_by,
        notes=notes,
    )
    save_protocol(protocol)
    return protocol

def verify_protocol_or_raise() -> BacktestProtocol:
    
    protocol = load_protocol()
    if protocol.lock.locked:
        current = protocol.content_hash()
        if current != protocol.lock.protocol_hash:
            raise BacktestError(
                code="BACKTEST_PROTOCOL_TAMPERED",
                message=(
                    "考试口径已锁定但内容被修改："
                    f"锁定哈希 {protocol.lock.protocol_hash}，当前哈希 {current}。"
                ),
                blocking=True,
                suggested_action=(
                    "还原 config/backtest_protocol.yaml 至锁定版本（git checkout），"
                    "或走人工评审流程解锁后重新锁定。禁止在因子研究过程中改动考试口径。"
                ),
                related_files=[str(protocol_path())],
            )
    return protocol

__all__ = [
    "BacktestProtocol",
    "ProtocolLock",
    "load_protocol",
    "lock_protocol",
    "protocol_path",
    "save_protocol",
    "verify_protocol_or_raise",
]
