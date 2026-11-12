from __future__ import annotations

from fastapi import APIRouter

from ai.execution import syntax_check
from factors.plugins import FactorPlugin
from factors.registry import FactorRegistry, FactorStatus
from paths import workspace
from presentation import factor_display_name
from trash import TrashStore

from ..schemas import DecidePayload, TransitionPayload, confirm_preview, parse_enum
from ..serialize import json_safe

router = APIRouter(tags=["factors"])

@router.get("/factors")
def list_factors(status: str = "", market: str = "", instrument_class: str = "") -> dict:
    registry = FactorRegistry()
    items = registry.list(
        status=parse_enum(FactorStatus, status, "status") if status else None,
        market=market or None,
        instrument_class=instrument_class or None,
    )
    return {
        "items": [
            {**json_safe(f), "display_name": f.display_name or factor_display_name(f.name)}
            for f in items
        ],
        "statuses": [s.value for s in FactorStatus],
    }

@router.get("/factors/{name}")
def get_factor(name: str) -> dict:
    factor = FactorRegistry().get(name)
    code = ""
    code_path = ""
    if factor.plugin_path:
        path = workspace().root / factor.plugin_path
        code_path = str(path)
        if path.exists():
            code = path.read_text(encoding="utf-8")
    payload = json_safe(
        {
            "factor": factor,
            "code": code,
            "code_path": code_path,
        }
    )
    payload["factor"]["display_name"] = factor.display_name or factor_display_name(factor.name)
    return payload

@router.post("/factors/{name}/transition")
def transition_factor(name: str, payload: TransitionPayload) -> dict:
    registry = FactorRegistry()
    target = parse_enum(FactorStatus, payload.target, "target")
    if not payload.confirm:
        factor = registry.get(name)
        return confirm_preview(
            {
                "操作": f"因子状态流转 -> {target.value}",
                "因子": factor.name,
                "当前状态": factor.status.value,
                "目标状态": target.value,
                "生产权限": "启用后自动获得" if target == FactorStatus.ACTIVE else "将被收回",
            }
        )
    factor = registry.transition(name, target=target, confirm=True)
    return json_safe({"factor": factor})

@router.post("/factors/{name}/remove")
def remove_factor(name: str, payload: DecidePayload) -> dict:
    registry = FactorRegistry()
    factor = registry.get(name)
    if not payload.confirm:
        return confirm_preview(
            {
                "操作": "删除因子（源码移入回收站）",
                "因子": factor.name,
                "状态": factor.status.value,
                "源码路径": factor.plugin_path,
                "说明": "数据表条目删除后可在 trash/factor/ 恢复源码，但元数据不可恢复。",
            }
        )
    plugin_file = FactorPlugin.default().path_for(name)
    if plugin_file.exists():
        TrashStore().move(
            plugin_file,
            category="factor",
            deleted_by=payload.decided_by,
            reason=payload.note,
        )
    removed = registry.remove(name, confirm=True)
    return json_safe({"removed": removed.name})

@router.post("/factors/{name}/code-check")
def check_factor_code(name: str) -> dict:
    factor = FactorRegistry().get(name)
    code = FactorPlugin.default().read(name)
    errors = syntax_check(code)
    return json_safe(
        {
            "factor": factor.name,
            "code_hash": factor.code_hash,
            "ok": not errors,
            "errors": errors,
        }
    )
