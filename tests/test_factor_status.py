"""Factor availability is separate from recoverable deletion and research outcomes."""
from pathlib import Path
import sys

import pytest
import yaml
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from exceptions import QuantTrainingError
from factors.registry import FactorMetadata, FactorRegistry, FactorStatus

INVALID_STATUSES = ["retired", "unknown", "", "ACTIVE", " active ", None, 1, True, {}, []]


def test_factor_statuses_have_no_retirement_action():
    assert [status.value for status in FactorStatus] == ["draft", "active", "disabled"]
    with pytest.raises(ValueError):
        FactorStatus("retired")


@pytest.mark.parametrize("value", INVALID_STATUSES)
def test_invalid_status_is_rejected_in_factor_metadata(value):
    with pytest.raises(ValidationError):
        FactorMetadata.model_validate({"name": "probe", "status": value})


@pytest.mark.parametrize("value", INVALID_STATUSES)
def test_invalid_status_assignment_keeps_previous_status(value):
    factor = FactorMetadata(name="probe")
    with pytest.raises(ValidationError):
        factor.status = value
    assert factor.status == FactorStatus.DRAFT


@pytest.mark.parametrize("value", INVALID_STATUSES)
def test_invalid_transition_does_not_change_registry(tmp_path, value):
    registry = FactorRegistry(tmp_path / "factors.yaml")
    registry.upsert(FactorMetadata(name="probe"))
    before = registry.path.read_bytes()
    with pytest.raises(QuantTrainingError) as error:
        registry.transition("probe", target=value, confirm=True)
    assert error.value.code == "FACTOR_INVALID_STATUS"
    assert registry.path.read_bytes() == before


@pytest.mark.parametrize("value", INVALID_STATUSES)
def test_invalid_copied_status_is_rejected_before_save(tmp_path, value):
    registry = FactorRegistry(tmp_path / "factors.yaml")
    factor = FactorMetadata(name="probe")
    registry.upsert(factor)
    before = registry.path.read_bytes()
    invalid = factor.model_copy(update={"status": value})
    with pytest.raises(ValidationError):
        registry.upsert(invalid, override=True)
    assert registry.path.read_bytes() == before


@pytest.mark.parametrize("value", INVALID_STATUSES)
def test_invalid_persisted_status_is_rejected(tmp_path, value):
    registry = FactorRegistry(tmp_path / "factors.yaml")
    registry.path.write_text(yaml.safe_dump({"factors": [{"name": "probe", "status": value}]}))
    with pytest.raises(ValidationError):
        registry.load()


@pytest.mark.parametrize("value", ["unknown", "retired", "", "ACTIVE"])
def test_invalid_status_filter_is_rejected(tmp_path, value):
    registry = FactorRegistry(tmp_path / "factors.yaml")
    with pytest.raises(QuantTrainingError) as error:
        registry.list(status=value)
    assert error.value.code == "FACTOR_INVALID_STATUS"


@pytest.mark.parametrize("target", list(FactorStatus))
def test_valid_status_strings_can_be_used_for_transition_and_filter(tmp_path, target):
    registry = FactorRegistry(tmp_path / "factors.yaml")
    registry.upsert(FactorMetadata(name="probe"))
    factor = registry.transition("probe", target=target.value, confirm=True)
    assert factor.status == target
    assert [item.name for item in registry.list(status=target.value)] == ["probe"]


@pytest.mark.parametrize("target", [FactorStatus.DRAFT, FactorStatus.DISABLED])
def test_leaving_active_revokes_permission(tmp_path, target):
    registry = FactorRegistry(tmp_path / "factors.yaml")
    registry.upsert(FactorMetadata(name="probe", status="active", production_permission=True))
    registry.transition("probe", target=target)
    factor = registry.get("probe")
    assert factor.status == target
    assert not factor.production_permission


def test_enabling_does_not_require_approval_or_confirmation(tmp_path):
    registry = FactorRegistry(tmp_path / "factors.yaml")
    registry.upsert(FactorMetadata(name="probe"))
    factor = registry.transition("probe", target=FactorStatus.ACTIVE)
    assert factor.status == FactorStatus.ACTIVE
    assert "production_permission" not in factor.model_dump(mode="json")


def test_factor_approval_endpoint_is_removed(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from webui.backend.server import create_app

    monkeypatch.setenv("QUANTLENS_HOME", str(tmp_path))
    monkeypatch.setenv("QUANTLENS_RECOVERY", "1")
    with TestClient(create_app()) as client:
        response = client.post("/api/lifecycle/factor-versions/any/promote", json={"confirm": True})
        assert response.status_code in {404, 405}


def test_http_factor_status_changes_have_no_approval_step(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from webui.backend.server import create_app

    monkeypatch.setenv("QUANTLENS_HOME", str(tmp_path))
    monkeypatch.setenv("QUANTLENS_RECOVERY", "1")
    FactorRegistry().upsert(FactorMetadata(name="probe"))
    with TestClient(create_app()) as client:
        for target in ["active", "disabled", "draft"]:
            response = client.post("/api/factors/probe/transition", json={"target": target})
            assert response.status_code == 200, response.text
            assert response.json()["factor"]["status"] == target
            assert "production_permission" not in response.json()["factor"]


def test_research_ledger_uses_catalog_status_without_a_second_state_machine(tmp_path, monkeypatch):
    import json
    import sqlite3
    from lifecycle.models import ObjectType
    from lifecycle.states import HUMAN_GATED_TARGETS, TRANSITIONS
    from lifecycle.store import LifecycleStore

    monkeypatch.setenv("QUANTLENS_HOME", str(tmp_path))
    registry = FactorRegistry()
    registry.upsert(FactorMetadata(name="probe"))
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE lifecycle_objects (object_type TEXT, object_id TEXT, payload TEXT)")
    conn.execute("INSERT INTO lifecycle_objects VALUES (?,?,?)", ("factor_version", "v1", json.dumps({"factor_name": "probe"})))
    for state in ["draft", "active", "disabled"]:
        registry.transition("probe", target=state)
        assert LifecycleStore._current_state_with_conn(conn, ObjectType.FACTOR_VERSION, "v1") == state
    assert ObjectType.FACTOR_VERSION not in TRANSITIONS
    assert not any(kind == ObjectType.FACTOR_VERSION for kind, _ in HUMAN_GATED_TARGETS)
    conn.close()


def test_losing_model_comparison_disables_without_a_retirement_state(tmp_path, monkeypatch):
    from quantlens.model_comparison import apply_conclusions

    monkeypatch.setenv("QUANTLENS_HOME", str(tmp_path))
    registry = FactorRegistry()
    registry.upsert(FactorMetadata(name="model_gbt", status="active", production_permission=True))
    rows = [{"test_start": "2024-01-01", "method": method, "objective_value": score}
            for method, score in [("model_gbt", -.2), ("icir", .1), ("model_linear", .05)]]
    monkeypatch.setattr("quantlens.ads.query", lambda *args, **kwargs: rows)
    assert apply_conclusions() is True
    factor = registry.get("model_gbt")
    assert factor.status == FactorStatus.DISABLED
    assert not factor.production_permission
    assert apply_conclusions() is False
