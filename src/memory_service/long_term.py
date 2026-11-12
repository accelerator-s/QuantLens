from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from paths import Workspace, workspace

from .candidates import CandidateRecord

def _utcnow() -> datetime:
    return datetime.now(tz=UTC)

class MemoryRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_id: str
    committed_at: datetime
    committed_by: str
    payload: dict[str, Any] = Field(default_factory=dict)

class LongTermMemory:

    def __init__(self, ws: Workspace | None = None) -> None:
        self.ws = ws or workspace()

    @property
    def root(self) -> Path:
        return self.ws.memory / "long_term"

    def append(self, candidate: CandidateRecord) -> Path:
        if candidate.committed_at is None:
            raise ValueError("候选必须先 commit 才能写入长期记忆。")
        rec = MemoryRecord(
            candidate_id=candidate.candidate_id,
            committed_at=candidate.committed_at,
            committed_by=candidate.decided_by,
            payload=candidate.payload,
        )
        path = self._month_file(rec.committed_at)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(rec.model_dump_json() + "\n")
        return path

    def iter_records(self) -> Iterator[MemoryRecord]:
        if not self.root.exists():
            return iter(())
        files = sorted(self.root.glob("*.jsonl"))

        def _gen() -> Iterator[MemoryRecord]:
            for f in files:
                with f.open(encoding="utf-8") as fh:
                    for line in fh:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            yield MemoryRecord.model_validate(json.loads(line))
                        except Exception:
                            continue

        return _gen()

    def _month_file(self, dt: datetime) -> Path:
        ym = dt.astimezone().strftime("%Y-%m")
        return self.root / f"{ym}.jsonl"

__all__ = ["LongTermMemory", "MemoryRecord"]
