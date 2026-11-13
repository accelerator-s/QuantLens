from __future__ import annotations

import secrets
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, field_validator

def _utcnow() -> datetime:
    return datetime.now(tz=UTC)

def generate_proposal_id() -> str:
    
    return f"PRO-{_utcnow().strftime('%Y%m%d')}-{secrets.token_hex(2).upper()}"

class ProposalSource(StrEnum):

    API_MODEL = "api_model"
    HUMAN = "human"

class ProposalStatus(StrEnum):
    PENDING = "pending"

    APPROVED = "approved"

    REJECTED = "rejected"
    SUPERSEDED = "superseded"

# 允许的状态迁移
_ALLOWED: dict[ProposalStatus, set[ProposalStatus]] = {
    ProposalStatus.PENDING: {
        ProposalStatus.APPROVED,
        ProposalStatus.REJECTED,
        ProposalStatus.SUPERSEDED,
    },
    ProposalStatus.APPROVED: set(),
    ProposalStatus.REJECTED: set(),
    ProposalStatus.SUPERSEDED: set(),
}

def assert_proposal_transition(current: ProposalStatus, target: ProposalStatus) -> None:
    from exceptions import QuantTrainingError

    if target == current:
        return
    if target not in _ALLOWED.get(current, set()):
        raise QuantTrainingError(
            code="PROPOSAL_STATE_INVALID",
            message=f"非法 proposal 状态迁移：{current.value} -> {target.value}",
            blocking=True,
        )

class Proposal(BaseModel):

    proposal_id: str = Field(default_factory=generate_proposal_id)
    schema_version: int = 1
    source: ProposalSource
    role: str

    model_name: str = ""

    status: ProposalStatus = ProposalStatus.PENDING
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)
    decided_by: str = ""

    decision_note: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)

    @field_validator("role")
    @classmethod
    def _role(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("role 不能为空")
        return v

    def touch(self) -> None:
        self.updated_at = _utcnow()
