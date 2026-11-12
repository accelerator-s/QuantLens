from __future__ import annotations

import re
from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, field_validator

from exceptions import ExperimentError

EventType = Literal["result", "comparison", "ai_analysis", "conclusion", "abort", "note"]

SubjectType = Literal["factor", "strategy", "portfolio_policy"]

SUBJECT_TYPES: tuple[str, ...] = ("factor", "strategy", "portfolio_policy")

EVENT_TYPES: tuple[str, ...] = (
    "result",
    "comparison",
    "ai_analysis",
    "conclusion",
    "abort",
    "note",
)

_WINDOW_RE = re.compile(r"^\d{4}-\d{2}-\d{2}\.\.\d{4}-\d{2}-\d{2}$")

def validate_window(value: str, *, field_name: str) -> str:
    
    text = value.strip()
    if not _WINDOW_RE.match(text):
        raise ExperimentError(
            code="EXPERIMENT_BAD_WINDOW",
            message=f"{field_name} 区间格式非法：{value!r}，应为 YYYY-MM-DD..YYYY-MM-DD。",
            blocking=True,
            suggested_action="示例：--train 2020-01-01..2022-12-31",
        )
    start, end = text.split("..")
    try:
        date.fromisoformat(start)
        date.fromisoformat(end)
    except ValueError as exc:
        raise ExperimentError(
            code="EXPERIMENT_BAD_WINDOW",
            message=f"{field_name} 区间含非法日期：{exc}。",
            blocking=True,
            suggested_action="使用真实存在的公历日期，格式 YYYY-MM-DD。",
        ) from exc
    if start > end:
        raise ExperimentError(
            code="EXPERIMENT_BAD_WINDOW",
            message=f"{field_name} 区间起始晚于结束：{text}。",
            blocking=True,
            suggested_action="交换起止日期后重试。",
        )
    return text

class ExperimentRecord(BaseModel):

    model_config = ConfigDict(extra="forbid")

    exp_id: str
    created_at: str
    subject_type: SubjectType = "factor"
    subject_name: str
    parent_exp_id: str | None = None
    change_summary: str
    change_reason: str
    train_window: str | None = None
    valid_window: str | None = None
    test_window: str | None = None
    profile_name: str | None = None
    schema_version: int = 2

    @field_validator("subject_name", "change_summary", "change_reason")
    @classmethod
    def _non_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("字段不能为空。")
        return v.strip()

class ExperimentEvent(BaseModel):

    model_config = ConfigDict(extra="forbid")

    event_id: int
    exp_id: str
    event_type: EventType
    created_at: str
    payload: dict[str, Any]

__all__ = [
    "EVENT_TYPES",
    "SUBJECT_TYPES",
    "EventType",
    "ExperimentEvent",
    "ExperimentRecord",
    "SubjectType",
    "validate_window",
]
