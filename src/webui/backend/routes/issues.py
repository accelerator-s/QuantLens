from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict

from issues import IssueStore
from issues.lifecycle import ALLOWED_TRANSITIONS, Status, assert_transition
from issues.models import CreatedBy, IssueType, Priority
from issues.templates import TEMPLATES
from paths import workspace
from snapshots import SnapshotStore

from ..schemas import TransitionPayload, confirm_preview, parse_enum
from ..serialize import json_safe

router = APIRouter(tags=["issues"])

def _summary(issue) -> dict:
    return json_safe(
        {
            "issue_id": issue.issue_id,
            "type": issue.type,
            "status": issue.status,
            "priority": issue.priority,
            "created_by": issue.created_by,
            "title": issue.title,
            "human_confirmed": issue.human_confirmed,
            "created_at": issue.created_at,
            "updated_at": issue.updated_at,
        }
    )

@router.get("/issues")
def list_issues(status: str = "", type: str = "") -> dict:
    store = IssueStore()
    items = store.list(
        status=parse_enum(Status, status, "status") if status else None,
        type=parse_enum(IssueType, type, "type") if type else None,
    )
    return {
        "items": [_summary(issue) for issue, _path in items],
        "statuses": [s.value for s in Status],
        "types": [t.value for t in IssueType],
        "priorities": [p.value for p in Priority],
    }

@router.get("/issues/templates")
def list_templates() -> dict:
    templates = {}
    for issue_type, tpl in TEMPLATES.items():
        templates[issue_type.value] = {
            "intro": tpl.intro,
            "fields": [asdict(f) for f in tpl.fields],
        }
    return json_safe({"templates": templates})

@router.get("/issues/{issue_id}")
def get_issue(issue_id: str) -> dict:
    issue, path = IssueStore().load(issue_id)
    allowed = ALLOWED_TRANSITIONS.get(issue.status, set())
    return json_safe(
        {
            "issue": issue,
            "path": str(path),
            "body": path.read_text(encoding="utf-8"),
            "allowed_transitions": sorted(s.value for s in allowed),
        }
    )

class IssueCreatePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: str
    title: str
    priority: str = "P2"
    owner: str = ""
    background: str = ""
    goal: str = ""
    scope: str = ""
    acceptance: str = ""
    risks: str = ""
    related_files: list[str] = []

@router.post("/issues")
def create_issue(payload: IssueCreatePayload) -> dict:
    issue, path = IssueStore().create(
        type=parse_enum(IssueType, payload.type, "type"),
        title=payload.title,
        priority=parse_enum(Priority, payload.priority, "priority"),
        created_by=CreatedBy.HUMAN,
        owner=payload.owner,
        background=payload.background,
        goal=payload.goal,
        scope=payload.scope,
        acceptance=payload.acceptance,
        risks=payload.risks,
        related_files=payload.related_files,
    )
    return json_safe({"issue": issue, "path": str(path)})

@router.post("/issues/{issue_id}/transition")
def transition_issue(issue_id: str, payload: TransitionPayload) -> dict:
    target = parse_enum(Status, payload.target, "target")
    needs_confirm = target in (Status.ACCEPTED, Status.SOLVED)
    if needs_confirm and not payload.confirm:
        issue, _path = IssueStore().load(issue_id)
        assert_transition(issue.status, target)
        return confirm_preview(
            {
                "操作": f"issue 状态流转 -> {target.value}",
                "issue_id": issue.issue_id,
                "标题": issue.title,
                "当前状态": issue.status.value,
                "目标状态": target.value,
                "说明": "该流转等价 CLI --confirm，将写入 human_confirmed=True。",
            }
        )
    issue, path = IssueStore().transition(
        issue_id,
        target,
        human_confirmed=payload.confirm,
        resolution_summary=payload.note,
    )
    return json_safe({"issue": issue, "path": str(path)})

@router.get("/snapshots")
def list_snapshots(issue: str = "") -> dict:
    store = SnapshotStore(workspace())
    return {"items": [json_safe(meta.to_dict()) for meta in store.list_snapshots(issue)]}
