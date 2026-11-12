from __future__ import annotations

from fastapi import APIRouter

from providers.registry import ProviderRegistry, ProviderStatus

from ..serialize import json_safe
from ..tasks import manager

router = APIRouter(tags=["data"])

@router.get("/providers")
def list_providers() -> dict:
    registry = ProviderRegistry()
    return {
        "items": [json_safe(p) for p in registry.list()],
        "statuses": [s.value for s in ProviderStatus],
    }

@router.get("/data/tables")
def list_tables(provider: str = "") -> dict:
    from providers import get_provider

    registry = ProviderRegistry()
    if not provider:
        actives = registry.list(status=ProviderStatus.ACTIVE)
        if not actives:
            return {"provider": "", "items": [], "note": "无 active 数据源"}
        provider = actives[0].name
    registry.get(provider)
    dp = get_provider(provider)
    if not hasattr(dp, "list_tables"):
        return {"provider": provider, "items": [], "note": "该数据源未实现表清单接口"}
    return json_safe({"provider": provider, "items": dp.list_tables()})

class _LogWriter:

    def __init__(self, append) -> None:
        self._append = append
        self._buf = ""

    def write(self, text: str) -> None:
        self._buf += text
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            if line.strip():
                self._append(line.rstrip())

    def flush(self) -> None:
        if self._buf.strip():
            self._append(self._buf.rstrip())
        self._buf = ""

@router.get("/tasks/{task_id}")
def get_task(task_id: str) -> dict:
    return manager.get(task_id)

@router.get("/data/sync/active")
def get_active_data_sync() -> dict:
    
    return {"task": manager.latest(resource="data", running_only=True)}
