from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from exceptions import SnapshotError
from paths import Workspace

_COUNTER_FILE = ".snapshot_counter"

def _generate_snapshot_id(snapshots_dir: Path) -> str:
    
    counter_file = snapshots_dir / _COUNTER_FILE
    today = datetime.now(UTC).strftime("%Y%m%d")

    counter = 1
    if counter_file.exists():
        try:
            data = yaml.safe_load(counter_file.read_text(encoding="utf-8")) or {}
            if data.get("date") == today:
                counter = data.get("counter", 0) + 1
        except Exception:
            pass

    snapshots_dir.mkdir(parents=True, exist_ok=True)
    counter_file.write_text(
        yaml.dump({"date": today, "counter": counter}, default_flow_style=False),
        encoding="utf-8",
    )
    return f"SNAP-{today}-{counter:04d}"

@dataclass
class SnapshotMeta:

    snapshot_id: str
    issue_id: str
    files: list[str]
    reason: str = ""
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def to_dict(self) -> dict[str, Any]:
        return {
            "snapshot_id": self.snapshot_id,
            "issue_id": self.issue_id,
            "files": self.files,
            "reason": self.reason,
            "created_at": self.created_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SnapshotMeta:
        return cls(
            snapshot_id=data["snapshot_id"],
            issue_id=data["issue_id"],
            files=data.get("files", []),
            reason=data.get("reason", ""),
            created_at=datetime.fromisoformat(data["created_at"]),
        )

class SnapshotStore:

    def __init__(self, ws: Workspace) -> None:
        self._ws = ws
        self._base = ws.snapshots

    def create(
        self,
        issue_id: str,
        files_to_backup: list[str],
        reason: str = "",
    ) -> SnapshotMeta:
        
        raise SnapshotError(
            code="QL_BACKUP_DISABLED",
            message="已按工作区要求禁用文件备份快照",
            suggested_action="直接修改工作区文件，不创建备份副本。",
        )

    def list_snapshots(self, issue_id: str = "") -> list[SnapshotMeta]:
        
        results: list[SnapshotMeta] = []
        if not self._base.exists():
            return results

        for snap_dir in sorted(self._base.iterdir()):
            meta_path = snap_dir / "meta.yaml"
            if not meta_path.exists():
                continue
            try:
                data = yaml.safe_load(meta_path.read_text(encoding="utf-8"))
                meta = SnapshotMeta.from_dict(data)
                if issue_id and meta.issue_id != issue_id:
                    continue
                results.append(meta)
            except Exception:
                continue
        return results

    def get(self, snapshot_id: str) -> SnapshotMeta:
        
        snap_dir = self._base / snapshot_id
        meta_path = snap_dir / "meta.yaml"
        if not meta_path.exists():
            raise SnapshotError(
                code="SNAPSHOT_NOT_FOUND",
                message=f"快照不存在：{snapshot_id}",
                suggested_action="使用 issue solve snapshot-list 查看可用快照。",
            )
        data = yaml.safe_load(meta_path.read_text(encoding="utf-8"))
        return SnapshotMeta.from_dict(data)

    def restore(self, snapshot_id: str) -> list[str]:
        
        meta = self.get(snapshot_id)
        snap_dir = self._base / snapshot_id
        files_dir = snap_dir / "files"

        if not files_dir.exists():
            raise SnapshotError(
                code="SNAPSHOT_FILES_MISSING",
                message=f"快照文件目录不存在：{snapshot_id}",
            )

        restored: list[str] = []
        for rel_path in meta.files:
            src = files_dir / rel_path
            if src.exists():
                dest = self._ws.root / rel_path
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dest)
                restored.append(rel_path)

        return restored

    def delete(self, snapshot_id: str) -> None:
        
        snap_dir = self._base / snapshot_id
        if not snap_dir.exists():
            raise SnapshotError(
                code="SNAPSHOT_NOT_FOUND",
                message=f"快照不存在：{snapshot_id}",
            )
        shutil.rmtree(snap_dir)
