from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, field_validator

from .lifecycle import Status

class IssueType(StrEnum):

    DATA_SOURCE = "data_source"
    FACTOR = "factor"
    MARKET_RULE = "market_rule"
    CLI = "cli"
    MEMORY = "memory"
    BUG = "bug"
    FRAMEWORK = "framework"
    STRATEGY_PROFILE = "strategy_profile"
    PORTFOLIO = "portfolio"
    PROVIDER_ACCEPTANCE_FAIL = "provider_acceptance_fail"
    DATA_QUALITY_CONFLICT = "data_quality_conflict"

class Priority(StrEnum):
    P0 = "p0"
    P1 = "p1"
    P2 = "p2"
    P3 = "p3"

class CreatedBy(StrEnum):

    HUMAN = "human"
    HUMAN_VIA_AI_SUGGESTION = "human_via_ai_suggestion"

def _utcnow() -> datetime:
    return datetime.now(tz=UTC)

class Issue(BaseModel):

    issue_id: str
    type: IssueType
    status: Status = Status.OPEN
    priority: Priority = Priority.P2
    created_by: CreatedBy = CreatedBy.HUMAN
    owner: str = ""
    human_confirmed: bool = False

    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)

    title: str
    background: str = ""
    goal: str = ""
    scope: str = ""
    acceptance: str = ""

    constraint_index: list[str] = Field(default_factory=list)
    risks: str = ""
    related_files: list[str] = Field(default_factory=list)
    related_runs: list[str] = Field(default_factory=list)
    resolution_summary: str = ""

    extra: dict[str, Any] = Field(default_factory=dict)

    @field_validator("title")
    @classmethod
    def _title_not_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("title 不能为空")
        return v

    def touch(self) -> None:
        
        self.updated_at = _utcnow()
