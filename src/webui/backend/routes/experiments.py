from __future__ import annotations

from fastapi import APIRouter

from experiments.compare import compare_with_parent
from experiments.models import EVENT_TYPES
from experiments.store import ExperimentStore
from presentation import (
    experiment_display_name,
    experiment_is_archived,
    factor_display_name,
    strategy_display_name,
)

from ..serialize import json_safe

router = APIRouter(tags=["experiments"])

def _decorate(row: dict) -> dict:
    result = dict(row)
    result["subject_display_name"] = (
        factor_display_name(row["subject_name"])
        if row["subject_type"] == "factor"
        else strategy_display_name(row["subject_name"])
    )
    result["profile_display_name"] = strategy_display_name(row.get("profile_name"))
    result["display_name"] = experiment_display_name(
        row["subject_type"], row["subject_name"]
    )
    result["archived"] = experiment_is_archived(
        row["subject_type"], row["subject_name"], row.get("profile_name")
    )
    return result

@router.get("/experiments")
def list_experiments(
    subject_type: str = "",
    subject_name: str = "",
    improved: str = "",
    include_archived: bool = False,
) -> dict:
    store = ExperimentStore()
    improved_flag = {"true": True, "false": False}.get(improved.lower()) if improved else None
    rows = store.list_experiments(
        subject_type=subject_type or None,
        subject_name=subject_name or None,
        improved=improved_flag,
    )
    decorated = [_decorate(row) for row in rows]
    if not include_archived:
        decorated = [row for row in decorated if not row["archived"]]
    return json_safe({"items": decorated, "event_types": list(EVENT_TYPES)})

@router.get("/experiments/{exp_id}")
def get_experiment(exp_id: str, event_type: str = "") -> dict:
    store = ExperimentStore()
    record = store.get(exp_id)
    events = store.events(exp_id, event_type or None)
    decorated = _decorate(record.model_dump(mode="json"))
    return json_safe(
        {
            "experiment": decorated,
            "events": events,
            "event_types": list(EVENT_TYPES),
        }
    )

@router.get("/experiments/{exp_id}/compare")
def get_compare(exp_id: str) -> dict:
    store = ExperimentStore()
    latest = store.latest_event(exp_id, "result")
    metrics = (latest.payload.get("metrics") or {}) if latest else {}
    comparison = compare_with_parent(store, exp_id, metrics)
    return json_safe({"exp_id": exp_id, "comparison": comparison})
