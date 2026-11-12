from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict

from memory_service import MemoryCandidateStore, get_default_adapter
from memory_service.candidates import CandidateStatus
from memory_service.guard import scan_for_secrets
from proposals.models import ProposalStatus, assert_proposal_transition
from proposals.store import ProposalStore
from trash import VALID_CATEGORIES, TrashStore

from ..schemas import (
    ConfirmPayload,
    DecidePayload,
    DecideTransitionPayload,
    confirm_preview,
    parse_enum,
)
from ..serialize import json_safe

router = APIRouter(tags=["review"])

@router.get("/proposals")
def list_proposals(role: str = "", status: str = "") -> dict:
    store = ProposalStore()
    items = store.list(
        role=role or None,
        status=parse_enum(ProposalStatus, status, "status") if status else None,
    )
    return {
        "items": [json_safe(p) for p, _path in items],
        "statuses": [s.value for s in ProposalStatus],
    }

@router.get("/proposals/{proposal_id}")
def get_proposal(proposal_id: str) -> dict:
    proposal, path = ProposalStore().load(proposal_id)
    return json_safe({"proposal": proposal, "path": str(path)})

@router.post("/proposals/{proposal_id}/transition")
def transition_proposal(proposal_id: str, payload: DecideTransitionPayload) -> dict:
    store = ProposalStore()
    target = parse_enum(ProposalStatus, payload.target, "target")
    if not payload.confirm:
        proposal, _path = store.load(proposal_id)
        assert_proposal_transition(proposal.status, target)
        return confirm_preview(
            {
                "操作": f"候选评审 -> {target.value}",
                "候选": proposal.proposal_id,
                "角色": proposal.role,
                "当前状态": proposal.status.value,
                "目标状态": target.value,
                "说明": "评审必须填写决策人（等价 CLI --by）。",
            }
        )
    proposal, path = store.transition(
        proposal_id,
        target,
        decided_by=payload.decided_by,
        note=payload.note,
    )
    return json_safe({"proposal": proposal, "path": str(path)})

@router.get("/memory/status")
def memory_status() -> dict:
    adapter = get_default_adapter()
    return json_safe(
        {
            "package_name": adapter.package_name,
            "pinned_version": adapter.pinned_version,
            "status": adapter.status,
            "last_check": adapter.last_check,
        }
    )

@router.get("/memory/candidates")
def list_memory_candidates(status: str = "") -> dict:
    store = MemoryCandidateStore()
    items = store.list(status=parse_enum(CandidateStatus, status, "status") if status else None)
    return {
        "items": [json_safe(rec) for rec, _path in items],
        "statuses": [s.value for s in CandidateStatus],
    }

@router.get("/memory/candidates/{candidate_id}")
def get_memory_candidate(candidate_id: str) -> dict:
    rec, path = MemoryCandidateStore().load(candidate_id)
    text = path.read_text(encoding="utf-8")
    matches = scan_for_secrets(text)
    return json_safe(
        {
            "candidate": rec,
            "path": str(path),
            "secret_matches": [{"name": m.name, "excerpt": m.excerpt} for m in matches],
        }
    )

class MemoryAddPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    payload: dict[str, Any]

@router.post("/memory/candidates")
def add_memory_candidate(payload: MemoryAddPayload) -> dict:
    rec, path = MemoryCandidateStore().create(payload.payload)
    return json_safe({"candidate": rec, "path": str(path)})

@router.post("/memory/candidates/{candidate_id}/commit")
def commit_memory_candidate(candidate_id: str, payload: DecidePayload) -> dict:
    store = MemoryCandidateStore()
    if not payload.confirm:
        rec, path = store.load(candidate_id)
        matches = scan_for_secrets(path.read_text(encoding="utf-8"))
        preview = {
            "操作": "候选入长期记忆（commit）",
            "候选": rec.candidate_id,
            "当前状态": rec.status.value,
            "说明": "commit 后由记忆适配器写入长期记忆文件。",
        }
        if matches:
            preview["密钥扫描"] = f"命中 {len(matches)} 处疑似密钥，请先确认内容安全！"
        return confirm_preview(preview)
    rec, _path = store.commit(candidate_id, decided_by=payload.decided_by)
    file_path = get_default_adapter().commit_to_long_term(rec)
    return json_safe({"candidate": rec, "long_term_file": str(file_path)})

@router.post("/memory/candidates/{candidate_id}/discard")
def discard_memory_candidate(candidate_id: str, payload: DecidePayload) -> dict:
    store = MemoryCandidateStore()
    if not payload.confirm:
        rec, _path = store.load(candidate_id)
        return confirm_preview(
            {
                "操作": "丢弃记忆候选（discard）",
                "候选": rec.candidate_id,
                "当前状态": rec.status.value,
            }
        )
    rec, _path = store.discard(
        candidate_id,
        decided_by=payload.decided_by,
        reason=payload.note,
    )
    return json_safe({"candidate": rec})

def _trash_item(entry) -> dict:
    return json_safe(
        {
            "bucket": entry.bucket_dir.name,
            "category": entry.category,
            "trashed_at": entry.trashed_at,
            "original_path": entry.original_path,
            "deleted_by": entry.deleted_by,
            "reason": entry.reason,
        }
    )

@router.get("/trash")
def list_trash(category: str = "") -> dict:
    entries = TrashStore().list(category=category or None)
    return {"items": [_trash_item(e) for e in entries]}

@router.get("/trash/categories")
def trash_categories() -> dict:
    return {"categories": sorted(VALID_CATEGORIES)}

@router.post("/trash/{bucket}/restore")
def restore_trash(bucket: str, payload: ConfirmPayload) -> dict:
    store = TrashStore()
    if not payload.confirm:
        entry = store._read_entry(store._find_bucket(bucket))
        return confirm_preview(
            {
                "操作": "从回收站恢复",
                "条目": bucket,
                "类别": entry.category,
                "恢复到": entry.original_path,
                "说明": "目标路径已存在时会被拒绝，不会覆盖现有文件。",
            }
        )
    dest = store.restore(bucket, confirm=True)
    return {"restored_to": str(dest)}

@router.post("/trash/{bucket}/purge")
def purge_trash(bucket: str, payload: ConfirmPayload) -> dict:
    store = TrashStore()
    if not payload.confirm:
        entry = store._read_entry(store._find_bucket(bucket))
        return confirm_preview(
            {
                "操作": "永久清除回收站条目",
                "条目": bucket,
                "类别": entry.category,
                "原路径": entry.original_path,
                "说明": "清除后不可恢复。",
            }
        )
    store.purge(bucket, confirm=True)
    return {"purged": bucket}
