from __future__ import annotations

import sys
import os
from importlib import metadata
from uuid import uuid4

from fastapi import APIRouter

from paths import workspace
from session import WorkspaceSession, load_session, save_session
from version import __version__

from ..serialize import json_safe

router = APIRouter(tags=["status"])

_SERVER_INSTANCE = uuid4().hex

_REQUIRED_PACKAGES = ("rich", "pyyaml", "pydantic", "fastapi", "uvicorn", "pyspark", "duckdb", "pandas", "pyarrow", "numpy", "httpx")

@router.get("/status")
def get_status() -> dict:
    
    ws = workspace()
    dirs = []
    missing = 0
    for d in ws.all_dirs():
        exists = d.exists()
        if not exists:
            missing += 1
        dirs.append({"path": str(d.relative_to(ws.root)), "exists": exists})

    packages = {}
    for pkg in _REQUIRED_PACKAGES:
        try:
            packages[pkg] = metadata.version(pkg)
        except metadata.PackageNotFoundError:
            packages[pkg] = None

    matrix_file = ws.config / "ai_capability_matrix.yaml"
    session = load_session()
    from ..services import startup_status
    return {
        "version": __version__,
        "server": {"pid": os.getpid(), "instance_id": _SERVER_INSTANCE},
        "recovery":os.environ.get("QUANTLENS_RECOVERY")=="1",
        "service_startup": startup_status(),
        "python": sys.version.split()[0],
        "workspace": {
            "root": str(ws.root),
            "dirs": dirs,
            "missing": missing,
            "matrix_file_exists": matrix_file.exists(),
        },
        "packages": packages,
        "session": json_safe(session),
    }

@router.get("/session")
def get_session() -> dict:
    return json_safe(load_session())

@router.put("/session")
def put_session(payload: WorkspaceSession) -> dict:
    save_session(payload)
    return json_safe(load_session())
