from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict

from paths import workspace

_SESSION_FILE = "workspace_session.yaml"

class WorkspaceSession(BaseModel):

    model_config = ConfigDict(extra="forbid")

    active_market: Literal["CN-A"] = "CN-A"
    active_instrument_class: Literal["stock"] = "stock"
    last_login: str = ""
    commission_rate: str = ""
    last_data_update: str = ""

def _session_path() -> Path:
    return workspace().config / _SESSION_FILE

def load_session() -> WorkspaceSession:
    
    path = _session_path()
    if not path.exists():
        return WorkspaceSession()
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return WorkspaceSession.model_validate(raw)

def save_session(session: WorkspaceSession) -> None:
    
    path = _session_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(
            session.model_dump(mode="json"),
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )

def has_active_market(session: WorkspaceSession) -> bool:
    
    return bool(session.active_market and session.active_instrument_class)
