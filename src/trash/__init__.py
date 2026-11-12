from __future__ import annotations

import secrets
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import yaml

from exceptions import QuantTrainingError
from paths import Workspace, workspace

VALID_CATEGORIES: frozenset[str] = frozenset(
    {
        "factor",
        "strategy_profile",
        "execution_template",
        "market_rule_candidate",
        "config_candidate",
        "data_mapping_candidate",
        "portfolio_account",
    }
)

def _utcnow() -> datetime:
    return datetime.now(tz=UTC)

@dataclass
class TrashEntry:

    category: str
    trashed_at: datetime
    original_path: str
    deleted_by: str
    reason: str
    bucket_dir: Path

    @property
    def content_path(self) -> Path:
        return self.bucket_dir / "content"

    @property
    def meta_path(self) -> Path:
        return self.bucket_dir / "meta.yaml"

class TrashStore:

    def __init__(self, ws: Workspace | None = None) -> None:
        self.ws = ws or workspace()

    def move(
        self,
        source: Path,
        *,
        category: str,
        deleted_by: str,
        reason: str = "",
    ) -> TrashEntry:
        if category not in VALID_CATEGORIES:
            raise QuantTrainingError(
                code="TRASH_CATEGORY_INVALID",
                message=f"非法 trash category：{category}",
                context={"valid": sorted(VALID_CATEGORIES)},
                blocking=True,
            )
        if not source.exists():
            raise QuantTrainingError(
                code="TRASH_SOURCE_MISSING",
                message=f"待回收对象不存在：{source}",
                blocking=True,
            )
        if not deleted_by.strip():
            raise QuantTrainingError(
                code="TRASH_DELETED_BY_REQUIRED",
                message="移入回收站需指定 deleted_by。",
                blocking=True,
            )

        ts = _utcnow().strftime("%Y%m%dT%H%M%SZ")
        bucket = self.ws.trash / category / f"{ts}__{source.name}__{secrets.token_hex(2)}"
        bucket.mkdir(parents=True, exist_ok=False)
        target = bucket / "content"
        shutil.move(str(source), str(target))

        meta: dict[str, Any] = {
            "category": category,
            "trashed_at": _utcnow().isoformat(),
            "original_path": str(source.resolve()),
            "deleted_by": deleted_by.strip(),
            "reason": reason,
        }
        (bucket / "meta.yaml").write_text(
            yaml.safe_dump(meta, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        return self._read_entry(bucket)

    def list(self, *, category: str | None = None) -> list[TrashEntry]:
        if not self.ws.trash.exists():
            return []
        out: list[TrashEntry] = []
        roots = (
            [self.ws.trash / category]
            if category
            else [p for p in self.ws.trash.iterdir() if p.is_dir()]
        )
        for root in roots:
            if not root.exists():
                continue
            for bucket in sorted(root.iterdir()):
                if bucket.is_dir() and (bucket / "meta.yaml").exists():
                    out.append(self._read_entry(bucket))
        out.sort(key=lambda e: e.trashed_at, reverse=True)
        return out

    def restore(
        self,
        bucket_name: str,
        *,
        target: Path | None = None,
        confirm: bool = False,
    ) -> Path:
        if not confirm:
            raise QuantTrainingError(
                code="TRASH_RESTORE_CONFIRM_REQUIRED",
                message="restore 需要 --confirm。",
                blocking=True,
            )
        bucket = self._find_bucket(bucket_name)
        entry = self._read_entry(bucket)
        dest = target or Path(entry.original_path)
        if dest.exists():
            raise QuantTrainingError(
                code="TRASH_RESTORE_TARGET_EXISTS",
                message=f"恢复目标已存在：{dest}",
                blocking=True,
                suggested_action="先确认是否真的要覆盖；如要覆盖，请手动移走再 restore。",
            )
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(entry.content_path), str(dest))
        shutil.rmtree(bucket)
        return dest

    def purge(
        self,
        bucket_name: str,
        *,
        confirm: bool = False,
    ) -> None:
        if not confirm:
            raise QuantTrainingError(
                code="TRASH_PURGE_CONFIRM_REQUIRED",
                message="purge 需要 --confirm。",
                blocking=True,
            )
        bucket = self._find_bucket(bucket_name)
        shutil.rmtree(bucket)

    def purge_older_than(
        self,
        days: int,
        *,
        confirm: bool = False,
    ) -> list[str]:
        
        if not confirm:
            raise QuantTrainingError(
                code="TRASH_PURGE_CONFIRM_REQUIRED",
                message="周期清理也需要 --confirm。",
                blocking=True,
            )
        cutoff = _utcnow() - timedelta(days=days)
        cleaned: list[str] = []
        for entry in self.list():
            if entry.trashed_at < cutoff:
                shutil.rmtree(entry.bucket_dir)
                cleaned.append(entry.bucket_dir.name)
        return cleaned

    def _find_bucket(self, bucket_name: str) -> Path:
        for entry in self.list():
            if entry.bucket_dir.name == bucket_name:
                return entry.bucket_dir
        raise QuantTrainingError(
            code="TRASH_BUCKET_NOT_FOUND",
            message=f"trash bucket 不存在：{bucket_name}",
            blocking=True,
        )

    @staticmethod
    def _read_entry(bucket: Path) -> TrashEntry:
        meta = yaml.safe_load((bucket / "meta.yaml").read_text(encoding="utf-8")) or {}
        return TrashEntry(
            category=meta.get("category", ""),
            trashed_at=datetime.fromisoformat(meta["trashed_at"]),
            original_path=meta.get("original_path", ""),
            deleted_by=meta.get("deleted_by", ""),
            reason=meta.get("reason", ""),
            bucket_dir=bucket,
        )

__all__ = ["TrashEntry", "TrashStore", "VALID_CATEGORIES"]
