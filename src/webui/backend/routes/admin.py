from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict

_GITKEEP_DIRS = []
from exceptions import ContractViolation
from factors import FactorRegistry
from market_rules import MarketRuleRegistry, MarketRuleStatus
from market_rules.markets import list_presets, load_preset
from memory_service import MemoryAdapter, get_default_adapter
from paths import workspace
from providers import ProviderRegistry, ProviderStatus
from session import load_session, save_session
from trash import TrashStore

from ..schemas import ConfirmPayload, confirm_preview
from ..serialize import json_safe

router = APIRouter(tags=["admin"])

class MemoryConfigurePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirm: bool = False
    pinned_version: str
    package_name: str = "mem0ai"

@router.post("/memory/configure")
def configure_memory(payload: MemoryConfigurePayload) -> dict:
    pinned = payload.pinned_version.strip()
    if not pinned:
        raise ContractViolation(
            code="WEBUI_MEMORY_PIN_REQUIRED",
            message="请填写固定版本。",
        )
    current = get_default_adapter()
    if not payload.confirm:
        return confirm_preview(
            {
                "操作": "配置记忆服务 固定版本",
                "package": f"{current.package_name} → {payload.package_name}",
                "pinned_version": f"{current.pinned_version or '（未配置）'} → {pinned}",
                "说明": "仅写入配置，不会自动升级依赖包。",
            }
        )
    adapter = MemoryAdapter(pinned_version=pinned, package_name=payload.package_name)
    adapter.save_config()
    return {"config_path": str(adapter.config_path), "pinned_version": pinned}

class TrashPurgePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirm: bool = False
    days: int

@router.post("/trash/purge-older-than")
def trash_purge_older_than(payload: TrashPurgePayload) -> dict:
    if payload.days < 0:
        raise ContractViolation(
            code="WEBUI_TRASH_INVALID_DAYS",
            message=f"天数 {payload.days} 非法，必须 >= 0。",
        )
    store = TrashStore()
    cutoff = datetime.now(tz=UTC) - timedelta(days=payload.days)
    stale = [e for e in store.list() if e.trashed_at < cutoff]
    if not payload.confirm:
        sample = "、".join(e.bucket_dir.name for e in stale[:10])
        if len(stale) > 10:
            sample += f" 等 {len(stale)} 条"
        return confirm_preview(
            {
                "操作": "永久清理回收站（不可撤销）",
                "阈值": f"超过 {payload.days} 天未恢复",
                "命中条目": str(len(stale)),
                "明细": sample or "（无命中，本次为空操作）",
            }
        )
    cleaned = store.purge_older_than(payload.days, confirm=True)
    return {"cleaned": cleaned, "count": len(cleaned)}

@router.post("/doctor/init")
def doctor_init(payload: ConfirmPayload) -> dict:
    ws = workspace()
    missing_dirs = [d for d in ws.all_dirs() if not d.exists()]
    missing_keeps = [
        ws.root / rel / ".gitkeep"
        for rel in _GITKEEP_DIRS
        if not (ws.root / rel / ".gitkeep").exists()
    ]
    if not payload.confirm:
        rels = "、".join(str(d.relative_to(ws.root)) for d in missing_dirs)
        return confirm_preview(
            {
                "操作": "补齐工作区目录（幂等）",
                "缺失目录": rels or "（无，本次为空操作）",
                "待补 .gitkeep": str(len(missing_keeps)),
            }
        )
    created = []
    for d in missing_dirs:
        d.mkdir(parents=True, exist_ok=True)
        created.append(str(d.relative_to(ws.root)))
    kept = 0
    for keep in missing_keeps:
        keep.parent.mkdir(parents=True, exist_ok=True)
        keep.touch()
        kept += 1
    return {"created_dirs": created, "gitkeep_written": kept}

class MarketPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirm: bool = False
    market: str = ""
    instrument_class: str = ""

def _active_rule_options(registry: MarketRuleRegistry) -> list[dict]:
    return [
        {
            "market": r.market,
            "instrument_class": r.instrument_class,
            "rule": r.name,
            "t_plus": r.t_plus,
        }
        for r in registry.list(status=MarketRuleStatus.ACTIVE)
    ]

@router.post("/session/market")
def set_session_market(payload: MarketPayload) -> dict:
    mr_registry = MarketRuleRegistry()
    options = _active_rule_options(mr_registry)
    presets = list_presets()

    if not payload.confirm:
        preview = {
            "操作": "切换交易市场",
            "可选市场": "、".join(f"{o['market']} {o['instrument_class']}" for o in options)
            or "（无活跃规则）",
        }
        if not options:
            preview["首跑初始化"] = (
                f"确认后将自动加载并激活 {len(presets)} 个预置市场规则。"
                if presets
                else "无可用预置，需先通过 issue 添加市场规则。"
            )
        result = confirm_preview(preview)
        result["options"] = options
        return result

    if not options:
        if not presets:
            raise ContractViolation(
                code="WEBUI_MARKET_NO_PRESET",
                message="没有活跃的市场规则，也没有可用预置。",
                suggested_action="先通过 issue 添加市场规则。",
            )
        for preset_name in presets:
            cfg = load_preset(preset_name)
            mr_registry.upsert(cfg, override=True)
            mr_registry.transition(preset_name, target=MarketRuleStatus.ACTIVE, confirm=True)
        options = _active_rule_options(mr_registry)

    market = payload.market.strip()
    instrument_class = payload.instrument_class.strip()
    if not market or not instrument_class:
        if len(options) == 1:
            market = options[0]["market"]
            instrument_class = options[0]["instrument_class"]
        else:
            raise ContractViolation(
                code="WEBUI_MARKET_REQUIRED",
                message="存在多个可选市场，需指定 market 与 instrument_class。",
            )
    if not any(
        o["market"] == market and o["instrument_class"] == instrument_class for o in options
    ):
        raise ContractViolation(
            code="WEBUI_MARKET_NOT_AVAILABLE",
            message=f"市场 {market} {instrument_class} 不在活跃规则中。",
        )

    session = load_session()
    session.active_market = market
    session.active_instrument_class = instrument_class
    session.last_login = datetime.now(tz=UTC).isoformat()
    save_session(session)

    rule = mr_registry.resolve(market, instrument_class)
    providers = ProviderRegistry().list(
        status=ProviderStatus.ACTIVE, market=market, instrument_class=instrument_class
    )
    factor_count = len(FactorRegistry().list(market=market, instrument_class=instrument_class))
    return json_safe(
        {
            "market": market,
            "instrument_class": instrument_class,
            "rule": rule.name,
            "t_plus": rule.t_plus,
            "providers": [p.name for p in providers],
            "factor_count": factor_count,
        }
    )

class FeePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirm: bool = False
    rate: str

def _normalize_fee_rate(raw: str) -> str:
    try:
        val = Decimal(raw)
    except InvalidOperation as exc:
        raise ContractViolation(
            code="WEBUI_INVALID_FEE_RATE",
            message=f"无法解析为数字：{raw!r}",
        ) from exc
    if val <= 0:
        raise ContractViolation(
            code="WEBUI_INVALID_FEE_RATE",
            message="佣金费率必须大于 0。",
        )
    if val >= Decimal("0.01"):
        raise ContractViolation(
            code="WEBUI_INVALID_FEE_RATE",
            message=f"佣金费率 {val} 过高（应小于 1%）。",
            suggested_action="输入小数形式，如万0.8 = 0.00008。",
        )
    return str(val.normalize())

@router.post("/session/fee")
def set_session_fee(payload: FeePayload) -> dict:
    rate = _normalize_fee_rate(payload.rate.strip())
    session = load_session()
    if not payload.confirm:
        return confirm_preview(
            {
                "操作": "设置券商佣金费率",
                "当前费率": session.commission_rate or "0.0003（预置默认：万三）",
                "新费率": rate,
            }
        )
    session.commission_rate = rate
    save_session(session)
    return {"commission_rate": rate}
