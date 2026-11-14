from __future__ import annotations

from enum import StrEnum

from exceptions import LifecycleError

from .models import ObjectType

class ActorType(StrEnum):
    AI = "ai"
    SYSTEM = "system"
    BROKER_ADAPTER = "broker_adapter"
    HUMAN = "human"
    MIXED = "mixed"

INITIAL_STATES: dict[ObjectType, str] = {
    ObjectType.DATA_SNAPSHOT: "ready",
    ObjectType.RESEARCH_PROGRAM: "draft",
    ObjectType.FACTOR_VERSION: "draft",
    ObjectType.FACTOR_EVALUATION: "recorded",
    ObjectType.STRATEGY_VERSION: "proposed",
    ObjectType.EVALUATION_RUN: "planned",
    ObjectType.LOCKED_TEST_EXPOSURE: "reserved",
    ObjectType.CHAMPION_DECISION: "proposed",
    ObjectType.PORTFOLIO_PLAN: "proposed",
    ObjectType.EXECUTION_RECORD: "received",
    ObjectType.ATTRIBUTION_RECORD: "recorded",
    ObjectType.LEARNING_EVENT: "observed",
    ObjectType.RESEARCH_REPORT: "draft",
}

TRANSITIONS: dict[ObjectType, dict[str, set[str]]] = {
    ObjectType.DATA_SNAPSHOT: {
        "ready": {"quarantined", "superseded"},
        "quarantined": {"ready", "superseded"},
    },
    ObjectType.RESEARCH_PROGRAM: {
        "draft": {"queued", "cancelled"},
        "queued": {"running", "paused", "cancelled"},
        "running": {"paused", "frozen", "completed", "failed", "cancelled"},
        "paused": {"queued", "cancelled"},
        "frozen": {"completed", "failed"},
    },
    ObjectType.FACTOR_EVALUATION: {
        "recorded": {"reviewed", "invalidated"},
        "reviewed": {"invalidated"},
    },
    ObjectType.STRATEGY_VERSION: {
        "proposed": {"evaluating", "rejected"},
        "evaluating": {"frozen", "rejected"},
        "frozen": {"challenger", "rejected"},
        "challenger": {"champion", "watching", "rejected"},
        "champion": {"watching", "rolled_back", "retired"},
        "watching": {"challenger", "champion", "rolled_back", "retired"},
        "rolled_back": {"retired"},
    },
    ObjectType.EVALUATION_RUN: {
        "planned": {"running", "cancelled"},
        "running": {"completed", "failed", "cancelled"},
    },
    ObjectType.LOCKED_TEST_EXPOSURE: {
        "reserved": {"opened", "cancelled"},
        "opened": {"completed", "failed"},
    },
    ObjectType.CHAMPION_DECISION: {
        "proposed": {"approved", "rejected"},
        "approved": {"applied", "cancelled"},
    },
    ObjectType.PORTFOLIO_PLAN: {
        "proposed": {"confirmed", "rejected", "expired"},
        "confirmed": {"active", "cancelled", "expired"},
        "active": {"partially_executed", "executed", "expired", "cancelled"},
        "partially_executed": {"executed", "expired", "cancelled"},
    },
    ObjectType.EXECUTION_RECORD: {
        "received": {"reconciled", "disputed"},
        "disputed": {"reconciled"},
        "reconciled": {"attributed"},
    },
    ObjectType.ATTRIBUTION_RECORD: {
        "recorded": {"validated", "invalidated"},
        "validated": {"superseded"},
    },
    ObjectType.LEARNING_EVENT: {
        "observed": {"verified", "rejected"},
        "verified": {"accepted", "rejected"},
    },
    ObjectType.RESEARCH_REPORT: {
        "draft": {"validated", "rejected"},
        "validated": {"approved", "rejected"},
    },
}

HUMAN_GATED_TARGETS: set[tuple[ObjectType, str]] = {
    (ObjectType.STRATEGY_VERSION, "champion"),
    (ObjectType.CHAMPION_DECISION, "approved"),
    (ObjectType.PORTFOLIO_PLAN, "confirmed"),
    (ObjectType.LEARNING_EVENT, "accepted"),
    (ObjectType.RESEARCH_REPORT, "approved"),
}

def validate_transition(
    object_type: ObjectType | str,
    current: str,
    target: str,
    actor_type: ActorType | str,
) -> None:
    
    kind = ObjectType(object_type)
    actor = ActorType(actor_type)
    allowed = TRANSITIONS.get(kind, {}).get(current, set())
    if target not in allowed:
        raise LifecycleError(
            code="LIFECYCLE_INVALID_TRANSITION",
            message=f"{kind.value} 不允许从 {current} 转为 {target}",
            context={"object_type": kind.value, "current": current, "target": target},
            blocking=True,
            suggested_action=f"当前可用目标：{', '.join(sorted(allowed)) or '无'}。",
        )
    if (kind, target) in HUMAN_GATED_TARGETS and actor not in {
        ActorType.HUMAN,
        ActorType.MIXED,
    }:
        raise LifecycleError(
            code="LIFECYCLE_HUMAN_CONFIRMATION_REQUIRED",
            message=f"{kind.value} 进入 {target} 必须由人工确认",
            context={"actor_type": actor.value},
            blocking=True,
            suggested_action="由用户检查证据后，以 human 或 mixed 身份确认该状态流转。",
        )

__all__ = [
    "ActorType",
    "HUMAN_GATED_TARGETS",
    "INITIAL_STATES",
    "TRANSITIONS",
    "validate_transition",
]
