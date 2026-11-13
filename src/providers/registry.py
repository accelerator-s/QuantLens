from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field

from exceptions import QuantTrainingError
from paths import workspace

_REGISTRY_FILE = "providers.yaml"

def _utcnow() -> datetime:
    return datetime.now(tz=UTC)

def registry_path() -> Path:
    return workspace().config / _REGISTRY_FILE

class ProviderStatus(StrEnum):
    DRAFT = "draft"
    ACTIVE = "active"
    DISABLED = "disabled"

class ProviderMetadata(BaseModel):

    model_config = ConfigDict(extra="forbid")

    name: str
    auth_method: str = "campus_network_auto"
    target_tables: list[str] = Field(default_factory=list)
    update_frequency: str = ""
    source_category: str = ""
    market: str = ""
    instrument_class: str = ""
    status: ProviderStatus = ProviderStatus.DRAFT
    manifest_version: str = "v1"
    notes: str = ""
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)

class _RegistryDoc(BaseModel):
    version: int = 1
    providers: list[ProviderMetadata] = Field(default_factory=list)

class ProviderRegistry:

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or registry_path()

    def load(self) -> _RegistryDoc:
        if not self.path.exists():
            return _RegistryDoc()
        raw = yaml.safe_load(self.path.read_text(encoding="utf-8")) or {}
        return _RegistryDoc.model_validate(raw)

    def list(
        self,
        *,
        status: ProviderStatus | None = None,
        market: str | None = None,
        instrument_class: str | None = None,
    ) -> list[ProviderMetadata]:
        items = self.load().providers
        if status is not None:
            items = [p for p in items if p.status == status]
        if market is not None:
            items = [p for p in items if p.market == market]
        if instrument_class is not None:
            items = [p for p in items if p.instrument_class == instrument_class]
        return sorted(items, key=lambda p: p.name)

    def get(self, name: str) -> ProviderMetadata:
        for p in self.load().providers:
            if p.name == name:
                return p
        raise QuantTrainingError(
            code="PROVIDER_NOT_FOUND",
            message=f"未注册数据源：{name}",
            blocking=True,
        )

    def upsert(self, provider: ProviderMetadata, *, override: bool = False) -> None:
        doc = self.load()
        idx = next((i for i, p in enumerate(doc.providers) if p.name == provider.name), None)
        if idx is not None and not override:
            raise QuantTrainingError(
                code="PROVIDER_EXISTS",
                message=f"数据源 {provider.name} 已存在；如需覆盖请加 --override。",
                blocking=True,
            )
        provider.updated_at = _utcnow()
        if idx is not None:
            provider.created_at = doc.providers[idx].created_at
            doc.providers[idx] = provider
        else:
            doc.providers.append(provider)
        self._save(doc)

    def transition(
        self, name: str, *, target: ProviderStatus, confirm: bool = False
    ) -> ProviderMetadata:
        if target == ProviderStatus.ACTIVE and not confirm:
            raise QuantTrainingError(
                code="PROVIDER_TRANSITION_CONFIRM_REQUIRED",
                message=f"将数据源置为 {target.value} 需要 --confirm。",
                blocking=True,
            )
        doc = self.load()
        for p in doc.providers:
            if p.name == name:
                p.status = target
                p.updated_at = _utcnow()
                self._save(doc)
                return p
        raise QuantTrainingError(
            code="PROVIDER_NOT_FOUND",
            message=f"未注册数据源：{name}",
            blocking=True,
        )

    def remove(self, name: str, *, confirm: bool = False) -> ProviderMetadata:
        if not confirm:
            raise QuantTrainingError(
                code="PROVIDER_REMOVE_CONFIRM_REQUIRED",
                message=f"删除数据源 {name} 需要 --confirm。",
                blocking=True,
            )
        doc = self.load()
        target = next((p for p in doc.providers if p.name == name), None)
        if target is None:
            raise QuantTrainingError(
                code="PROVIDER_NOT_FOUND",
                message=f"未注册数据源：{name}",
                blocking=True,
            )
        doc.providers = [p for p in doc.providers if p.name != name]
        self._save(doc)
        return target

    def _save(self, doc: _RegistryDoc) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        doc.providers = sorted(doc.providers, key=lambda p: p.name)
        self.path.write_text(
            yaml.safe_dump(doc.model_dump(mode="json"), allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )

__all__ = [
    "ProviderMetadata",
    "ProviderRegistry",
    "ProviderStatus",
    "registry_path",
]
