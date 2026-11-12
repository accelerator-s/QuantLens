from __future__ import annotations

from fastapi import APIRouter, Query
from pydantic import BaseModel, ConfigDict

from exceptions import LifecycleError
from lifecycle.models import ObjectType, ResearchReport
from lifecycle.snapshots import DataSnapshotService
from lifecycle.reporting import ResearchReportService
from lifecycle.store import LifecycleStore
from lifecycle.states import ActorType

from ..schemas import confirm_preview

from ..serialize import json_safe

router = APIRouter(tags=["lifecycle"])

class CreateSnapshotPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    require_strict: bool = False

class ReportApprovalPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirm: bool = False
    decided_by: str = ""
    reason: str = ""

@router.get("/lifecycle/summary")
def get_summary() -> dict:
    return json_safe(LifecycleStore().summary())

@router.get("/lifecycle/objects")
def list_objects(object_type: str = "", limit: int = Query(default=200, ge=1, le=5000)) -> dict:
    kind = ObjectType(object_type) if object_type else None
    return json_safe({"items": LifecycleStore().list_objects(kind, limit=limit)})

@router.get("/lifecycle/objects/{object_type}/{object_id}")
def get_object(object_type: ObjectType, object_id: str) -> dict:
    store = LifecycleStore()
    record = store.get(object_type, object_id)
    return json_safe(
        {
            "object_type": object_type.value,
            "state": store.current_state(object_type, object_id),
            "record": record,
            "events": store.events(object_type, object_id),
        }
    )

@router.get("/lifecycle/integrity")
def verify_integrity() -> dict:
    return json_safe(LifecycleStore().verify_integrity())

@router.get("/lifecycle/events")
def list_lifecycle_events(limit: int = Query(default=100, ge=1, le=5000)) -> dict:
    return json_safe({"items": LifecycleStore().list_events(limit=limit)})

@router.post("/lifecycle/data-snapshots")
def create_data_snapshot(payload: CreateSnapshotPayload) -> dict:
    snapshot = DataSnapshotService().create(require_strict=payload.require_strict)
    return json_safe(
        {
            "snapshot": snapshot,
            "state": LifecycleStore().current_state(
                ObjectType.DATA_SNAPSHOT, snapshot.snapshot_id
            ),
        }
    )

@router.get("/lifecycle/data-snapshots")
def list_data_snapshots(limit: int = Query(default=50, ge=1, le=5000)) -> dict:
    store = LifecycleStore()
    rows = store.list_objects(ObjectType.DATA_SNAPSHOT, limit=limit)
    return json_safe(
        {
            "items": [
                {
                    **row,
                    "snapshot": store.get(ObjectType.DATA_SNAPSHOT, row["object_id"]),
                }
                for row in rows
            ]
        }
    )

@router.post("/lifecycle/research-programs/{program_id}/report")
def generate_research_report(program_id: str) -> dict:
    return json_safe({"report": ResearchReportService().generate(program_id)})

@router.get("/lifecycle/research-reports")
def list_research_reports(limit: int = Query(default=200, ge=1, le=5000)) -> dict:
    store = LifecycleStore()
    return json_safe(
        {
            "items": [
                {
                    **row,
                    "research_report": store.get(
                        ObjectType.RESEARCH_REPORT, row["object_id"]
                    ),
                }
                for row in store.list_objects(ObjectType.RESEARCH_REPORT, limit=limit)
            ]
        }
    )

@router.get("/lifecycle/research-reports/{report_id}/content")
def get_research_report_content(report_id: str) -> dict:
    from exceptions import LifecycleError
    from lifecycle.models import ResearchReport
    from paths import workspace

    record = LifecycleStore().get(ObjectType.RESEARCH_REPORT, report_id)
    assert isinstance(record, ResearchReport)
    path = (workspace().root / record.report_artifact_ref).resolve()
    root = workspace().root.resolve()
    if root not in path.parents or not path.is_file():
        raise LifecycleError(
            code="RESEARCH_REPORT_ARTIFACT_MISSING",
            message=f"报告 artifact 不存在或越界：{record.report_artifact_ref}",
            blocking=True,
            suggested_action="通过 ResearchReportService 按原指标 artifact 重新生成。",
        )
    return {"report_id": report_id, "content": __import__("json").loads(path.read_text(encoding="utf-8"))["content"]}

@router.post("/lifecycle/research-reports/{report_id}/approve")
def approve_research_report(report_id: str, payload: ReportApprovalPayload) -> dict:
    store = LifecycleStore()
    report = store.get(ObjectType.RESEARCH_REPORT, report_id)
    assert isinstance(report, ResearchReport)
    if not payload.confirm:
        return confirm_preview(
            {
                "操作": "批准研究报告",
                "报告": report_id,
                "研究任务": report.research_program_id,
                "数值引用数": str(len(report.metric_citations)),
                "说明": "批准只确认报告已人工审阅，不代表策略晋升。",
            }
        )
    actor = payload.decided_by.strip()
    if not actor:
        raise LifecycleError(
            code="RESEARCH_REPORT_APPROVER_REQUIRED",
            message="批准研究报告必须填写决策人。",
            blocking=True,
            suggested_action="填写可审计的姓名或内部账号。",
        )
    verification = ResearchReportService(store).verify(report_id)
    if store.current_state(ObjectType.RESEARCH_REPORT, report_id) == "approved":
        return {"report_id": report_id, "state": "approved", **verification}
    store.transition(
        ObjectType.RESEARCH_REPORT,
        report_id,
        "approved",
        actor_type=ActorType.HUMAN,
        actor_ref=actor,
        reason=payload.reason.strip() or "人工核对指标引用、局限和收益免责后批准",
        payload=verification,
    )
    return {"report_id": report_id, "state": "approved", **verification}

@router.post("/lifecycle/factor-versions/{factor_version_id}/promote")
def promote_factor_version(
    factor_version_id: str, payload: ReportApprovalPayload
) -> dict:
    store = LifecycleStore()
    factor = store.get(ObjectType.FACTOR_VERSION, factor_version_id)
    state = store.current_state(ObjectType.FACTOR_VERSION, factor_version_id)
    if not payload.confirm:
        return confirm_preview(
            {
                "操作": "批准因子使用权限",
                "因子版本": factor_version_id,
                "因子": factor.factor_name,
                "当前状态": state,
                "说明": "仅 shadow 版本且研究报告已批准时可进入 production。",
            }
        )
    actor = payload.decided_by.strip()
    if not actor:
        raise LifecycleError(
            code="FACTOR_PRODUCTION_APPROVER_REQUIRED",
            message="批准因子使用权限必须填写决策人。",
            blocking=True,
            suggested_action="填写可审计的姓名或内部账号。",
        )
    if state == "production":
        return {"factor_version_id": factor_version_id, "state": state}
    if state != "shadow":
        raise LifecycleError(
            code="FACTOR_VERSION_NOT_SHADOW",
            message=f"因子版本 {factor_version_id} 当前为 {state}，不能进入生产。",
            blocking=True,
            suggested_action="只能批准已通过锁定测试并进入模拟观察的因子。",
        )
    store.transition(
        ObjectType.FACTOR_VERSION,
        factor_version_id,
        "production",
        actor_type=ActorType.HUMAN,
        actor_ref=actor,
        reason=payload.reason.strip() or "人工核验研究报告和锁定测试后批准正式使用",
    )
    return {"factor_version_id": factor_version_id, "state": "production"}
