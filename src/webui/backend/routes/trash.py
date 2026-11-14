from __future__ import annotations

from datetime import timedelta

import yaml
from fastapi import APIRouter

from trash import VALID_CATEGORIES, TrashStore, _lock
from trash.retention import TRASH_RETENTION_DAYS, TrashRetention

from ..schemas import ConfirmPayload, confirm_preview
from ..serialize import json_safe

router = APIRouter(tags=["trash"])


def _trash_item(entry) -> dict:
    meta = yaml.safe_load(entry.meta_path.read_text(encoding="utf-8")) or {}
    return json_safe(
        {
            "name": meta.get("name", ""),
            "bucket": entry.bucket_dir.name,
            "category": entry.category,
            "trashed_at": entry.trashed_at,
            "original_path": entry.original_path,
            "deleted_by": entry.deleted_by,
            "reason": entry.reason,
            "expires_at": entry.trashed_at + timedelta(days=TRASH_RETENTION_DAYS),
        }
    )

@router.get("/trash")
def list_trash(category: str = "") -> dict:
    with _lock:
        retention = TrashRetention()
        retention.cleanup()
        entries = TrashStore().list(category=category or None)
        return {"items": [_trash_item(e) for e in entries]}

@router.post("/trash/empty")
def empty_trash(payload: ConfirmPayload) -> dict:
    store = TrashStore()
    if not payload.confirm:
        return confirm_preview({"操作": "清空回收站"})
    return {"count": store.empty(confirm=True)}

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
            }
        )
    store.purge(bucket, confirm=True)
    return {"purged": bucket}
