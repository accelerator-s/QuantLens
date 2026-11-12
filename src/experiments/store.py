from __future__ import annotations

import json
import secrets
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from exceptions import ExperimentError
from paths import workspace

from .models import EVENT_TYPES, ExperimentEvent, ExperimentRecord

_ID_ALPHABET = "ABCDEFGHJKMNPQRSTVWXYZ23456789"  

_SCHEMA = """
CREATE TABLE IF NOT EXISTS experiments (
  exp_id TEXT PRIMARY KEY,
  created_at TEXT NOT NULL,
  subject_type TEXT NOT NULL DEFAULT 'factor',
  subject_name TEXT NOT NULL,
  parent_exp_id TEXT REFERENCES experiments(exp_id),
  change_summary TEXT NOT NULL,
  change_reason TEXT NOT NULL,
  train_window TEXT,
  valid_window TEXT,
  test_window TEXT,
  profile_name TEXT,
  schema_version INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS experiment_events (
  event_id INTEGER PRIMARY KEY AUTOINCREMENT,
  exp_id TEXT NOT NULL REFERENCES experiments(exp_id),
  event_type TEXT NOT NULL CHECK(event_type IN
    ('result','comparison','ai_analysis','conclusion','abort','note')),
  created_at TEXT NOT NULL,
  payload TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_events_exp ON experiment_events(exp_id, event_id);
"""

_TRIGGERS: dict[str, str] = {
    "trg_exp_no_update": (
        "CREATE TRIGGER trg_exp_no_update BEFORE UPDATE ON experiments "
        "BEGIN SELECT RAISE(ABORT, 'EXPERIMENT_APPEND_ONLY'); END"
    ),
    "trg_exp_no_delete": (
        "CREATE TRIGGER trg_exp_no_delete BEFORE DELETE ON experiments "
        "BEGIN SELECT RAISE(ABORT, 'EXPERIMENT_APPEND_ONLY'); END"
    ),
    "trg_evt_no_update": (
        "CREATE TRIGGER trg_evt_no_update BEFORE UPDATE ON experiment_events "
        "BEGIN SELECT RAISE(ABORT, 'EXPERIMENT_APPEND_ONLY'); END"
    ),
    "trg_evt_no_delete": (
        "CREATE TRIGGER trg_evt_no_delete BEFORE DELETE ON experiment_events "
        "BEGIN SELECT RAISE(ABORT, 'EXPERIMENT_APPEND_ONLY'); END"
    ),
}

def _utcnow_iso() -> str:
    return datetime.now(tz=UTC).isoformat()

def generate_exp_id(now: datetime | None = None) -> str:
    
    moment = now or datetime.now(tz=UTC).astimezone()
    token = "".join(_ID_ALPHABET[secrets.randbelow(len(_ID_ALPHABET))] for _ in range(4))
    return f"EXP-{moment:%Y%m%d}-{token}"

class ExperimentStore:

    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = db_path or workspace().factor_experiments_db
        self._init_db()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path)
        try:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            yield conn
        finally:
            conn.close()

    def _init_db(self) -> None:
        existed = self.db_path.exists()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            if existed:
                self._verify_triggers(conn)
                self._migrate(conn)
                return
            conn.executescript(_SCHEMA)
            for ddl in _TRIGGERS.values():
                conn.execute(ddl)
            conn.commit()

    @staticmethod
    def _migrate(conn: sqlite3.Connection) -> None:
        
        cols = {row["name"] for row in conn.execute("PRAGMA table_info(experiments)").fetchall()}
        if "base_factor" in cols:
            conn.execute("ALTER TABLE experiments RENAME COLUMN base_factor TO subject_name")
        if "subject_type" not in cols:
            conn.execute(
                "ALTER TABLE experiments ADD COLUMN subject_type TEXT NOT NULL DEFAULT 'factor'"
            )
        conn.commit()

    def _verify_triggers(self, conn: sqlite3.Connection) -> None:
        rows = conn.execute("SELECT name FROM sqlite_master WHERE type='trigger'").fetchall()
        present = {row["name"] for row in rows}
        missing = sorted(set(_TRIGGERS) - present)
        if missing:
            raise ExperimentError(
                code="EXPERIMENT_DB_TAMPERED",
                message=f"实验记录本 append-only 触发器缺失：{', '.join(missing)}，"
                "数据库可能被篡改。",
                blocking=True,
                suggested_action="核查 experience/factor_experiments.sqlite 的改动来源；"
                "确认无恶意改写后可人工重建触发器，历史记录的可信性需另行评估。",
                related_files=[str(self.db_path)],
            )

    def register(
        self,
        *,
        subject_name: str,
        change_summary: str,
        change_reason: str,
        subject_type: str = "factor",
        parent_exp_id: str | None = None,
        train_window: str | None = None,
        valid_window: str | None = None,
        test_window: str | None = None,
        profile_name: str | None = None,
    ) -> ExperimentRecord:
        
        with self._connect() as conn:
            if parent_exp_id is not None:
                self._require_exp(conn, parent_exp_id)
            exp_id = generate_exp_id()
            while conn.execute(
                "SELECT 1 FROM experiments WHERE exp_id=?", (exp_id,)
            ).fetchone():  # 同日随机碰撞，重新生成
                exp_id = generate_exp_id()
            record = ExperimentRecord(
                exp_id=exp_id,
                created_at=_utcnow_iso(),
                subject_type=subject_type,
                subject_name=subject_name,
                parent_exp_id=parent_exp_id,
                change_summary=change_summary,
                change_reason=change_reason,
                train_window=train_window,
                valid_window=valid_window,
                test_window=test_window,
                profile_name=profile_name,
            )
            conn.execute(
                "INSERT INTO experiments (exp_id, created_at, subject_type, subject_name,"
                " parent_exp_id, change_summary, change_reason, train_window, valid_window,"
                " test_window, profile_name, schema_version) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    record.exp_id,
                    record.created_at,
                    record.subject_type,
                    record.subject_name,
                    record.parent_exp_id,
                    record.change_summary,
                    record.change_reason,
                    record.train_window,
                    record.valid_window,
                    record.test_window,
                    record.profile_name,
                    record.schema_version,
                ),
            )
            conn.commit()
            return record

    def append_event(self, exp_id: str, event_type: str, payload: dict) -> int:
        
        if event_type not in EVENT_TYPES:
            raise ExperimentError(
                code="EXPERIMENT_BAD_EVENT_TYPE",
                message=f"未知事件类型：{event_type!r}。",
                blocking=True,
                suggested_action=f"可用类型：{', '.join(EVENT_TYPES)}。",
            )
        with self._connect() as conn:
            self._require_exp(conn, exp_id)
            cursor = conn.execute(
                "INSERT INTO experiment_events (exp_id, event_type, created_at, payload)"
                " VALUES (?,?,?,?)",
                (exp_id, event_type, _utcnow_iso(), json.dumps(payload, ensure_ascii=False)),
            )
            conn.commit()
            return int(cursor.lastrowid or 0)

    def get(self, exp_id: str) -> ExperimentRecord:
        with self._connect() as conn:
            row = self._require_exp(conn, exp_id)
            return ExperimentRecord(**dict(row))

    def events(self, exp_id: str, event_type: str | None = None) -> list[ExperimentEvent]:
        
        with self._connect() as conn:
            self._require_exp(conn, exp_id)
            if event_type:
                rows = conn.execute(
                    "SELECT * FROM experiment_events WHERE exp_id=? AND event_type=?"
                    " ORDER BY event_id",
                    (exp_id, event_type),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM experiment_events WHERE exp_id=? ORDER BY event_id",
                    (exp_id,),
                ).fetchall()
        return [
            ExperimentEvent(
                event_id=row["event_id"],
                exp_id=row["exp_id"],
                event_type=row["event_type"],
                created_at=row["created_at"],
                payload=json.loads(row["payload"]),
            )
            for row in rows
        ]

    def latest_event(self, exp_id: str, event_type: str) -> ExperimentEvent | None:
        found = self.events(exp_id, event_type)
        return found[-1] if found else None

    def list_experiments(
        self,
        *,
        subject_type: str | None = None,
        subject_name: str | None = None,
        improved: bool | None = None,
    ) -> list[dict]:
        
        with self._connect() as conn:
            clauses: list[str] = []
            params: list[str] = []
            if subject_type:
                clauses.append("subject_type=?")
                params.append(subject_type)
            if subject_name:
                clauses.append("subject_name=?")
                params.append(subject_name)
            where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
            exp_rows = conn.execute(
                f"SELECT * FROM experiments{where} ORDER BY exp_id DESC", params
            ).fetchall()
            summaries: list[dict] = []
            for row in exp_rows:
                exp_id = row["exp_id"]
                type_rows = conn.execute(
                    "SELECT event_type, payload FROM experiment_events WHERE exp_id=?"
                    " ORDER BY event_id",
                    (exp_id,),
                ).fetchall()
                seen = {r["event_type"]: json.loads(r["payload"]) for r in type_rows}
                status = "registered"
                if "abort" in seen:
                    status = "aborted"
                elif "conclusion" in seen:
                    status = "concluded"
                elif "result" in seen:
                    status = "attached"
                row_improved = (seen.get("conclusion") or {}).get("improved")
                primary = ((seen.get("result") or {}).get("metrics") or {}).get("returns") or {}
                summaries.append(
                    {
                        "exp_id": exp_id,
                        "subject_type": row["subject_type"],
                        "subject_name": row["subject_name"],
                        "parent_exp_id": row["parent_exp_id"],
                        "profile_name": row["profile_name"],
                        "status": status,
                        "improved": row_improved,
                        "annualized_excess_return": primary.get("annualized_excess_return"),
                        "run_id": (seen.get("result") or {}).get("run_id"),
                        "created_at": row["created_at"],
                    }
                )
        if improved is not None:
            summaries = [s for s in summaries if s["improved"] is improved]
        return summaries

    @staticmethod
    def _require_exp(conn: sqlite3.Connection, exp_id: str) -> sqlite3.Row:
        row = conn.execute("SELECT * FROM experiments WHERE exp_id=?", (exp_id,)).fetchone()
        if row is None:
            raise ExperimentError(
                code="EXPERIMENT_NOT_FOUND",
                message=f"找不到实验记录：{exp_id}。",
                blocking=True,
                suggested_action="用 `experiment list` 查看已注册实验编号。",
            )
        return row

__all__ = ["ExperimentStore", "generate_exp_id"]
