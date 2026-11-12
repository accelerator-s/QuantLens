from __future__ import annotations

import json
import secrets
from contextlib import contextmanager
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from exceptions import QuantTrainingError
from paths import Workspace, workspace

from .models import ResearchWindow

def _now() -> str:
    return datetime.now(tz=UTC).isoformat()

def _overlaps(left: ResearchWindow, right: ResearchWindow) -> bool:
    return not (
        date.fromisoformat(left.end) < date.fromisoformat(right.start)
        or date.fromisoformat(right.end) < date.fromisoformat(left.start)
    )

class LockedTestLedger:
    def __init__(self, ws: Workspace | None = None) -> None:
        self.ws = ws or workspace()

    @property
    def path(self) -> Path:
        return self.ws.experience / "locked_test_exposures.jsonl"

    @property
    def lock_path(self) -> Path:
        return self.ws.experience / ".locked_test_exposures.lock"

    @contextmanager
    def _locked(self):
        import fcntl

        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        with self.lock_path.open("a", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def list(self, *, base_factor: str = "") -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        records = [
            json.loads(line) for line in self.path.read_text(encoding="utf-8").splitlines() if line
        ]
        if base_factor:
            records = [row for row in records if row.get("base_factor") == base_factor]
        return records

    def assert_available(self, base_factor: str, window: ResearchWindow) -> None:
        conflicts = self._conflicts(window)
        if conflicts:
            first = conflicts[0]
            raise QuantTrainingError(
                code="LOCKED_TEST_ALREADY_EXPOSED",
                message=(
                    f"锁定测试区间 {window.text()} 与已暴露区间 "
                    f"{first['window']['start']}..{first['window']['end']} 重叠；"
                    f"首次用于因子 {first['base_factor']}。"
                ),
                context={
                    "base_factor": base_factor,
                    "requested_window": window.model_dump(mode="json"),
                    "conflict": first,
                },
                blocking=True,
                suggested_action=(
                    "选择一段全局从未暴露的新锁定测试区间；旧区间只能降级为训练或验证数据。"
                ),
            )

    def reserve(
        self,
        *,
        base_factor: str,
        research_id: str,
        version_id: str,
        code_hash: str,
        window: ResearchWindow,
    ) -> str:
        with self._locked():
            self.assert_available(base_factor, window)
            exposure_id = f"LTE-{datetime.now(tz=UTC):%Y%m%d}-{secrets.token_hex(3).upper()}"
            self._append(
                {
                    "exposure_id": exposure_id,
                    "event": "reserved",
                    "created_at": _now(),
                    "base_factor": base_factor,
                    "research_id": research_id,
                    "version_id": version_id,
                    "code_hash": code_hash,
                    "window": window.model_dump(mode="json"),
                    "run_id": "",
                    "passed": None,
                }
            )
        return exposure_id

    def complete(
        self,
        exposure_id: str,
        *,
        event: str,
        run_id: str = "",
        passed: bool | None = None,
        error_code: str = "",
    ) -> None:
        with self._locked():
            self._append(
                {
                    "exposure_id": exposure_id,
                    "event": event,
                    "created_at": _now(),
                    "run_id": run_id,
                    "passed": passed,
                    "error_code": error_code,
                }
            )

    def exposures(self, *, base_factor: str = "") -> list[dict[str, Any]]:
        records = self.list()
        reserved = {
            row["exposure_id"]: dict(row)
            for row in records
            if row["event"] == "reserved"
            and (not base_factor or row.get("base_factor") == base_factor)
        }
        for row in records:
            if row["event"] != "reserved" and row["exposure_id"] in reserved:
                reserved[row["exposure_id"]].update(
                    {
                        "result_event": row["event"],
                        "result_at": row["created_at"],
                        "run_id": row.get("run_id", ""),
                        "passed": row.get("passed"),
                        "error_code": row.get("error_code", ""),
                    }
                )
        return sorted(reserved.values(), key=lambda row: row["created_at"], reverse=True)

    def _conflicts(self, window: ResearchWindow) -> list[dict[str, Any]]:
        return [
            row
            for row in self.exposures()
            if _overlaps(ResearchWindow.model_validate(row["window"]), window)
        ]

    def _append(self, payload: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")

__all__ = ["LockedTestLedger"]
