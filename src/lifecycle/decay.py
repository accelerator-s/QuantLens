from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from statistics import mean
from typing import Any

from exceptions import LifecycleError

from .models import (
    AttributionLayer,
    AttributionRecord,
    DomainObjectRef,
    LearningEvent,
    ObjectType,
    ResearchLoop,
)
from .states import ActorType
from .store import LifecycleStore

def _hash(payload: Any) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()

class DecayMonitor:
    def __init__(self, store: LifecycleStore | None = None) -> None:
        self.store = store or LifecycleStore()

    def scan(self) -> dict[str, Any]:
        groups: dict[str, list[AttributionRecord]] = defaultdict(list)
        for row in reversed(
            self.store.list_objects(ObjectType.ATTRIBUTION_RECORD, limit=5000)
        ):
            if row["state"] != "validated":
                continue
            record = self.store.get(ObjectType.ATTRIBUTION_RECORD, row["object_id"])
            assert isinstance(record, AttributionRecord)
            groups[record.strategy_version_id].append(record)
        created = []
        for strategy_id, records in groups.items():
            if len(records) < 3:
                continue
            recent = records[-3:]
            realized = mean(item.realized_return for item in recent)
            execution = mean(
                next(
                    (
                        component.contribution
                        for component in item.components
                        if component.layer is AttributionLayer.EXECUTION
                    ),
                    0.0,
                )
                for item in recent
            )
            if realized >= 0 and execution >= -0.005:
                continue
            category = "strategy_decay" if realized < 0 else "execution_drift"
            event_id = f"LE-{_hash([category, strategy_id, [item.attribution_record_id for item in recent]])[:20].upper()}"
            try:
                self.store.get(ObjectType.LEARNING_EVENT, event_id)
                continue
            except LifecycleError as exc:
                if exc.code != "LIFECYCLE_REFERENCE_NOT_FOUND":
                    raise
            event = LearningEvent(
                learning_event_id=event_id,
                source_object_refs=[
                    DomainObjectRef(
                        object_type=ObjectType.ATTRIBUTION_RECORD,
                        object_id=item.attribution_record_id,
                    )
                    for item in recent
                ],
                category=category,
                observation=(
                    f"最近 3 期实际收益均值 {realized:.4%}，"
                    f"执行层均值 {execution:.4%}"
                ),
                evidence_refs=[
                    f"lifecycle://attribution_record/{item.attribution_record_id}"
                    for item in recent
                ],
                confidence=min(0.95, 0.55 + 0.1 * len(recent)),
                recommended_action=(
                    "自动启动新的策略研究任务，并在人工确认前保留当前 champion；"
                    "若风险超限，生成回滚建议。"
                ),
                trigger_research_loop=ResearchLoop.STRATEGY,
            )
            self.store.add(event)
            self.store.transition(
                ObjectType.LEARNING_EVENT,
                event_id,
                "verified",
                actor_type=ActorType.SYSTEM,
                actor_ref="decay_monitor",
                reason="三期已验证 AttributionRecord 达到衰减触发条件",
            )
            created.append(event)
        return {
            "items": created,
            "trigger_candidates": self._pending_trigger_events(),
            "scanned_strategies": len(groups),
        }

    def _pending_trigger_events(self) -> list[LearningEvent]:
        
        triggered_ids = set()
        for row in self.store.list_objects(ObjectType.RESEARCH_PROGRAM, limit=5000):
            if row["state"] == "cancelled":
                continue
            program = self.store.get(ObjectType.RESEARCH_PROGRAM, row["object_id"])
            event_id = str((program.configuration or {}).get("trigger_learning_event_id") or "")
            if event_id:
                triggered_ids.add(event_id)
        pending = []
        for row in self.store.list_objects(ObjectType.LEARNING_EVENT, limit=5000):
            if row["state"] not in {"verified", "accepted"}:
                continue
            event = self.store.get(ObjectType.LEARNING_EVENT, row["object_id"])
            assert isinstance(event, LearningEvent)
            if (
                event.trigger_research_loop is not None
                and event.learning_event_id not in triggered_ids
            ):
                pending.append(event)
        return pending

__all__ = ["DecayMonitor"]
