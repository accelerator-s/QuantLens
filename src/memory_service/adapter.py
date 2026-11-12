from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from importlib import metadata as _metadata
from pathlib import Path
from typing import Any

import yaml

from paths import Workspace, workspace

from .candidates import CandidateRecord
from .long_term import LongTermMemory

_ADAPTER_FILE = "adapter.yaml"

class MemoryAdapterStatus(StrEnum):
    READY = "ready"
    DEGRADED = "degraded"

@dataclass
class UpgradeNotice:
    current_version: str
    available_version: str
    notes: str = ""

@dataclass
class MemoryAdapter:

    ws: Workspace = field(default_factory=workspace)
    pinned_version: str = ""

    package_name: str = "mem0ai"
    long_term: LongTermMemory = field(init=False)
    status: MemoryAdapterStatus = field(init=False, default=MemoryAdapterStatus.DEGRADED)
    last_check: datetime | None = field(init=False, default=None)

    def __post_init__(self) -> None:
        self.long_term = LongTermMemory(self.ws)
        self._refresh_status()

    def _refresh_status(self) -> None:
        if not self.pinned_version:
            self.status = MemoryAdapterStatus.DEGRADED
            return
        try:
            installed = _metadata.version(self.package_name)
        except _metadata.PackageNotFoundError:
            self.status = MemoryAdapterStatus.DEGRADED
            return
        
        if installed == self.pinned_version:
            self.status = MemoryAdapterStatus.READY
        else:
            self.status = MemoryAdapterStatus.DEGRADED

    def check_upstream(self) -> UpgradeNotice | None:
        
        if not self.pinned_version:
            return None
        try:
            installed = _metadata.version(self.package_name)
        except _metadata.PackageNotFoundError:
            return None
        self.last_check = datetime.now(tz=UTC)
        if installed != self.pinned_version:
            return UpgradeNotice(
                current_version=self.pinned_version,
                available_version=installed,
                notes=(
                    "本地已安装版本与 pinned_version 不一致；"
                    "请先创建 memory issue (subaction=external_upgrade) 评审兼容性，"
                    "再决定是否更新 pin。"
                ),
            )
        return None

    def commit_to_long_term(self, candidate: CandidateRecord) -> Path:
        
        path = self.long_term.append(candidate)
        if self.status == MemoryAdapterStatus.READY:
            self._upload_external(candidate)
        return path

    def _upload_external(self, candidate: CandidateRecord) -> None:  

        return

    @property
    def config_path(self) -> Path:
        return self.ws.memory / _ADAPTER_FILE

    def save_config(self) -> Path:
        self.ws.memory.mkdir(parents=True, exist_ok=True)
        data: dict[str, Any] = {
            "package_name": self.package_name,
            "pinned_version": self.pinned_version,
        }
        self.config_path.write_text(
            yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        return self.config_path

    @classmethod
    def from_config(cls, ws: Workspace | None = None) -> MemoryAdapter:
        ws = ws or workspace()
        cfg = ws.memory / _ADAPTER_FILE
        if not cfg.exists():
            return cls(ws=ws)
        raw = yaml.safe_load(cfg.read_text(encoding="utf-8")) or {}
        return cls(
            ws=ws,
            pinned_version=raw.get("pinned_version", "") or "",
            package_name=raw.get("package_name", "mem0ai"),
        )

def get_default_adapter() -> MemoryAdapter:
    return MemoryAdapter.from_config()

__all__ = [
    "MemoryAdapter",
    "MemoryAdapterStatus",
    "UpgradeNotice",
    "get_default_adapter",
]
