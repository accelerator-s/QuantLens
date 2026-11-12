from __future__ import annotations

from enum import StrEnum

from exceptions import IssueStateError

class Status(StrEnum):
    OPEN = "open"
    IN_REVIEW = "in_review"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    SOLVED = "solved"

ALLOWED_TRANSITIONS: dict[Status, set[Status]] = {
    Status.OPEN: {Status.IN_REVIEW, Status.REJECTED},
    Status.IN_REVIEW: {Status.ACCEPTED, Status.REJECTED, Status.OPEN},
    Status.ACCEPTED: {Status.SOLVED, Status.IN_REVIEW},
    Status.REJECTED: {Status.SOLVED, Status.OPEN},
    Status.SOLVED: set(),
}

def assert_transition(current: Status, target: Status) -> None:
    
    if target == current:
        return
    if target not in ALLOWED_TRANSITIONS.get(current, set()):
        raise IssueStateError(
            code="ISSUE_STATE_INVALID",
            message=f"非法状态迁移：{current.value} -> {target.value}",
            context={"from": current.value, "to": target.value},
            blocking=True,
            suggested_action="参考 ALLOWED_TRANSITIONS 选择合法目标状态。",
        )
