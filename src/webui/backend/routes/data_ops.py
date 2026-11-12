from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict

from exceptions import QuantTrainingError
from providers.registry import ProviderRegistry, ProviderStatus

from ..schemas import confirm_preview
from ..serialize import json_safe
from ..tasks import manager

router = APIRouter(tags=["data-ops"])

def _active_provider(name: str = ""):
    
    registry = ProviderRegistry()
    if not name:
        from session import has_active_market, load_session

        session = load_session()
        if has_active_market(session):
            matched = registry.list(
                status=ProviderStatus.ACTIVE,
                market=session.active_market,
                instrument_class=session.active_instrument_class,
            )
            if matched:
                name = matched[0].name
        if not name:
            actives = registry.list(status=ProviderStatus.ACTIVE)
            if not actives:
                raise QuantTrainingError(
                    code="WEBUI_NO_ACTIVE_PROVIDER",
                    message="没有已启用的数据源。",
                    blocking=True,
                    suggested_action="请恢复数据源配置。",
                )
            name = actives[0].name
    meta = registry.get(name)
    if meta.status != ProviderStatus.ACTIVE:
        raise QuantTrainingError(
            code="WEBUI_PROVIDER_NOT_ACTIVE",
            message=f"数据源 {name} 状态为 {meta.status.value}，只有已启用的数据源才能更新。",
            blocking=True,
        )
    from providers import get_provider

    return get_provider(name)

def _log_console(append_log):
    from rich.console import Console

    from .data import _LogWriter

    return Console(file=_LogWriter(append_log), width=100, color_system=None)

def _cache_states() -> list[dict]:
    from panel.api import cache_status
    return [{"variant":v,"state":str(st.state)} for v,st in cache_status().items()]

@router.get("/data/update-check")
def data_update_check() -> dict:
    
    active_sync = manager.latest(resource="data", running_only=True)
    active_summary = (
        {
            "task_id": active_sync["task_id"],
            "status": active_sync["status"],
            "name":active_sync["name"],
            "progress": active_sync["progress"],
        }
        if active_sync is not None
        else None
    )
    if active_sync is not None:
        return {
            "provider": str(active_sync["name"]).partition(":")[2],
            "needs_update": False,
            "note": "",
            "cache": [],
            "active_sync": active_summary,
        }
    try:
        dp = _active_provider("")
    except QuantTrainingError as exc:
        return {
            "provider": "",
            "needs_update": False,
            "note": exc.message,
            "cache": [],
            "active_sync": active_summary,
        }
    if not hasattr(dp, "update_all"):
        return {
            "provider": dp.name,
            "needs_update": False,
            "note": "该数据源不支持自动更新检测。",
            "cache": [],
            "active_sync": active_summary,
        }
    status = dp.check_update_needed()
    plan = dp.plan_update_all()
    from quantlens import store
    checks=store.records('data_update_check',1)
    last_check=checks[0]['body'] if checks else None
    pending_windows = [(table, window) for table, window in plan.windows if window is not None]
    audit_due = [table for table, window in pending_windows if window.mode == "audit"]
    return json_safe(
        {
            "provider": dp.name,
            "needs_update": status.needs_update or bool(pending_windows),
            "market_needs_update": status.needs_update,
            "last_check":last_check,
            "first_download": any(window.mode == "initial" for _, window in pending_windows),
            "last_date": status.last_date,
            "pending_days": status.pending_days,
            "start_date": status.start_date,
            "end_date": status.end_date,
            "estimated_rows": status.estimated_rows,
            "batch_count": status.batch_count,
            "cache": _cache_states(),
            "audit_due": audit_due,
            "schedules": getattr(plan, "schedules", []),
            "pending_tables": [
                {
                    "table": table,
                    "mode": window.mode,
                    "mode_label": window.mode_label,
                    "start_date": window.start_date,
                    "end_date": window.end_date,
                    "batches": window.batch_count,
                }
                for table, window in pending_windows
            ],
            "note": "",
            "active_sync": active_summary,
        }
    )

class SyncPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirm: bool = False

@router.post("/data/sync")
def data_sync(payload: SyncPayload) -> dict:
    dp = _active_provider("")
    if not hasattr(dp, "update_all"):
        raise QuantTrainingError(
            code="WEBUI_PROVIDER_UNSUPPORTED",
            message=f"数据源 {dp.name} 不支持数据表编排更新，无法一键同步。",
            blocking=True,
        )

    if not payload.confirm:
        plan = dp.plan_update_all()
        windows = []
        for table, window in plan.windows:
            if window is None:
                windows.append({"table": table, "mode": "按计划跳过", "window": "", "batches": 0})
            else:
                windows.append(
                    {
                        "table": table,
                        "mode": window.mode_label,
                        "window": f"{window.start_date} ~ {window.end_date}",
                        "batches": window.batch_count,
                        "uncovered_before": window.uncovered_before,
                    }
                )
        preview = {
            "操作": "按计划补齐数据，按需更新聚合表与计算缓存",
            "数据源": dp.name,
            "待更新表": "、".join(w["table"] for w in windows if w["batches"]) or "（本次无需下载）",
            "推迟审计": "、".join(plan.deferred_audits) or "（无）",
            "跳过未核验": "、".join(plan.skipped_unverified) or "（无）",
        }
        result = confirm_preview(preview)
        result["plan"] = json_safe({"windows": windows, "has_pending": plan.has_pending})
        return result

    def run(reporter) -> dict:
        from quantlens.sync import run_sync

        report = run_sync(
            dp,
            console=_log_console(reporter),
            yes=True,
            on_progress=reporter.progress,
        )
        if not report.ok:
            
            if report.error is not None:
                raise report.error
            raise QuantTrainingError(
                code="WEBUI_SYNC_FAILED",
                message=f"同步在步骤「{report.failed_step or '未知'}」失败。",
                blocking=True,
            )
        return {
            "provider": dp.name,
            "new_rows": report.new_rows,
            "steps": [{"name": n, "state": s} for n, s in report.steps],
        }

    task_id = manager.start(f"data-sync:{dp.name}", run, resource="data")
    return {"task_id": task_id}
