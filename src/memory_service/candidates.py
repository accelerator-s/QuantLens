from __future__ import annotations

import secrets
from collections.abc import Iterable
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

from ai.schemas import validate_ai_output
from exceptions import QuantTrainingError
from paths import Workspace, workspace

from .guard import scan_for_secrets

def _utcnow() -> datetime:
    return datetime.now(tz=UTC)

def _new_id() -> str:
    return f"MEM-{_utcnow().strftime('%Y%m%d')}-{secrets.token_hex(2).upper()}"

class CandidateStatus(StrEnum):
    PENDING = "pending"
    COMMITTED = "committed"
    DISCARDED = "discarded"

class CandidateRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_id: str = Field(default_factory=_new_id)
    schema_version: int = 1
    status: CandidateStatus = CandidateStatus.PENDING
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)
    committed_at: datetime | None = None
    decided_by: str = ""
    payload: dict[str, Any]

    @field_validator("payload")
    @classmethod
    def _validate_payload(cls, v: dict[str, Any]) -> dict[str, Any]:
        v = {**v, "task": "memory_candidate"}
        validated = validate_ai_output("memory_candidate", v)
        
        flat = yaml.safe_dump(v, allow_unicode=True, sort_keys=False)
        hits = scan_for_secrets(flat)
        if hits:
            names = ", ".join(h.name for h in hits)
            raise ValueError(f"memory 候选疑似含敏感信息：{names}")
        return validated.model_dump(mode="json")

class MemoryCandidateStore:

    def __init__(self, ws: Workspace | None = None) -> None:
        self.ws = ws or workspace()

    @property
    def root(self) -> Path:
        return self.ws.memory / "candidates"

    def create(self, payload: dict[str, Any]) -> tuple[CandidateRecord, Path]:
        rec = CandidateRecord(payload=payload)
        path = self._path_for(rec)
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():  # 同 ID 罕见但要防御
            raise QuantTrainingError(
                code="MEMORY_CANDIDATE_PATH_CONFLICT",
                message=f"候选路径冲突：{path}",
                blocking=True,
            )
        path.write_text(self._dump(rec), encoding="utf-8")
        return rec, path

    def load(self, candidate_id: str) -> tuple[CandidateRecord, Path]:
        for p in self._iter_files():
            try:
                rec = self._parse(p.read_text(encoding="utf-8"))
            except Exception:
                continue
            if rec.candidate_id == candidate_id:
                return rec, p
        raise QuantTrainingError(
            code="MEMORY_CANDIDATE_NOT_FOUND",
            message=f"未找到候选：{candidate_id}",
            blocking=True,
        )

    def list(self, *, status: CandidateStatus | None = None) -> list[tuple[CandidateRecord, Path]]:
        out: list[tuple[CandidateRecord, Path]] = []
        for p in self._iter_files():
            try:
                rec = self._parse(p.read_text(encoding="utf-8"))
            except Exception:
                continue
            if status and rec.status != status:
                continue
            out.append((rec, p))
        out.sort(key=lambda x: x[0].created_at, reverse=True)
        return out

    def commit(
        self,
        candidate_id: str,
        *,
        decided_by: str,
    ) -> tuple[CandidateRecord, Path]:
        if not decided_by.strip():
            raise QuantTrainingError(
                code="MEMORY_COMMIT_DECIDER_REQUIRED",
                message="commit 需要 --by 标识决策人。",
                blocking=True,
            )
        rec, path = self.load(candidate_id)
        if rec.status != CandidateStatus.PENDING:
            raise QuantTrainingError(
                code="MEMORY_COMMIT_INVALID_STATE",
                message=f"候选 {candidate_id} 当前状态={rec.status.value}，不能 commit。",
                blocking=True,
            )
        rec.status = CandidateStatus.COMMITTED
        rec.committed_at = _utcnow()
        rec.decided_by = decided_by.strip()
        rec.updated_at = _utcnow()
        path.write_text(self._dump(rec), encoding="utf-8")
        return rec, path

    def discard(
        self,
        candidate_id: str,
        *,
        decided_by: str,
        reason: str = "",
    ) -> tuple[CandidateRecord, Path]:
        if not decided_by.strip():
            raise QuantTrainingError(
                code="MEMORY_DISCARD_DECIDER_REQUIRED",
                message="discard 需要 --by。",
                blocking=True,
            )
        rec, path = self.load(candidate_id)
        rec.status = CandidateStatus.DISCARDED
        rec.decided_by = decided_by.strip()
        rec.updated_at = _utcnow()
        if reason:
            rec.payload = {**rec.payload, "discard_reason": reason}
        path.write_text(self._dump(rec), encoding="utf-8")
        return rec, path

    def _iter_files(self) -> Iterable[Path]:
        if not self.root.exists():
            return iter(())
        return self.root.rglob("*.yaml")

    def _path_for(self, rec: CandidateRecord) -> Path:
        ym = rec.created_at.astimezone().strftime("%Y-%m")
        return self.root / ym / f"{rec.candidate_id}.yaml"

    @staticmethod
    def _dump(rec: CandidateRecord) -> str:
        return yaml.safe_dump(
            rec.model_dump(mode="json"),
            allow_unicode=True,
            sort_keys=False,
        )

    @staticmethod
    def _parse(text: str) -> CandidateRecord:
        return CandidateRecord.model_validate(yaml.safe_load(text) or {})

def list_candidates(*, status: CandidateStatus | None = None) -> list[tuple[CandidateRecord, Path]]:
    return MemoryCandidateStore().list(status=status)

__all__ = [
    "CandidateRecord",
    "CandidateStatus",
    "MemoryCandidateStore",
    "list_candidates",
]
