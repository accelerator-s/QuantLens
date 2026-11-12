from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

from exceptions import QuantTrainingError
from paths import workspace

_REGISTRY_FILE = "factors.yaml"

def _utcnow() -> datetime:
    return datetime.now(tz=UTC)

def registry_path() -> Path:
    return workspace().config / _REGISTRY_FILE

class FactorStatus(StrEnum):
    DRAFT = "draft"

    ACTIVE = "active"

    DISABLED = "disabled"
    RETIRED = "retired"

class FactorMetadata(BaseModel):

    model_config = ConfigDict(extra="forbid")

    name: str
    display_name: str = ""

    assumption: str = ""
    applicable_markets: list[str] = Field(default_factory=list)
    applicable_instruments: list[str] = Field(default_factory=list)
    timescale: str = "daily"
    dependencies: list[str] = Field(default_factory=list)
    output_frequency: str = "daily"
    direction: str = "undecided"
    parameters: dict[str, Any] = Field(default_factory=dict)
    standardization: list[str] = Field(default_factory=list)
    missing_handling: str = "drop"
    ai_generated: bool = False

    production_permission: bool = False

    risk_notes: str = ""
    evaluation_metrics: list[str] = Field(default_factory=list)
    status: FactorStatus = FactorStatus.DRAFT
    version: str = "v1"
    code_hash: str = ""
    plugin_path: str = ""

    last_evaluated_at: datetime | None = None
    decay_history: list[dict[str, Any]] = Field(default_factory=list)
    related_issue_id: str = ""
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        v = v.strip()
        if not v.replace("_", "").isalnum():
            raise ValueError("因子名只允许 [A-Za-z0-9_]。")
        return v

class _RegistryDoc(BaseModel):
    version: int = 1
    factors: list[FactorMetadata] = Field(default_factory=list)

class FactorRegistry:

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
        status: FactorStatus | None = None,
        market: str | None = None,
        instrument_class: str | None = None,
    ) -> list[FactorMetadata]:
        items = self.load().factors
        if status is not None:
            items = [f for f in items if f.status == status]
        if market is not None:
            items = [f for f in items if not f.applicable_markets or market in f.applicable_markets]
        if instrument_class is not None:
            items = [
                f
                for f in items
                if not f.applicable_instruments or instrument_class in f.applicable_instruments
            ]
        return sorted(items, key=lambda f: (f.display_name or f.name, f.name))

    def get(self, name: str) -> FactorMetadata:
        for f in self.load().factors:
            if f.name == name:
                return f
        raise QuantTrainingError(
            code="FACTOR_NOT_FOUND",
            message=f"未注册因子：{name}",
            blocking=True,
        )

    def upsert(self, factor: FactorMetadata, *, override: bool = False) -> None:
        doc = self.load()
        idx = next((i for i, f in enumerate(doc.factors) if f.name == factor.name), None)
        if idx is not None and not override:
            raise QuantTrainingError(
                code="FACTOR_EXISTS",
                message=f"因子 {factor.name} 已存在；如需覆盖请加 --override。",
                blocking=True,
            )
        factor.updated_at = _utcnow()
        if idx is not None:
            factor.created_at = doc.factors[idx].created_at
            doc.factors[idx] = factor
        else:
            doc.factors.append(factor)
        self._save(doc)

    def transition(
        self,
        name: str,
        *,
        target: FactorStatus,
        confirm: bool = False,
    ) -> FactorMetadata:
        if target in (FactorStatus.ACTIVE, FactorStatus.RETIRED) and not confirm:
            raise QuantTrainingError(
                code="FACTOR_TRANSITION_CONFIRM_REQUIRED",
                message=f"将因子置为 {target.value} 需要 --confirm。",
                blocking=True,
            )
        doc = self.load()
        for f in doc.factors:
            if f.name == name:
                f.status = target
                if target == FactorStatus.ACTIVE:
                    f.production_permission = True
                if target in (FactorStatus.DISABLED, FactorStatus.RETIRED):
                    f.production_permission = False
                f.updated_at = _utcnow()
                self._save(doc)
                return f
        raise QuantTrainingError(
            code="FACTOR_NOT_FOUND",
            message=f"未注册因子：{name}",
            blocking=True,
        )

    def remove(self, name: str, *, confirm: bool = False) -> FactorMetadata:
        if not confirm:
            raise QuantTrainingError(
                code="FACTOR_REMOVE_CONFIRM_REQUIRED",
                message=f"删除因子 {name} 需要 --confirm。",
                blocking=True,
            )
        doc = self.load()
        target = next((f for f in doc.factors if f.name == name), None)
        if target is None:
            raise QuantTrainingError(
                code="FACTOR_NOT_FOUND",
                message=f"未注册因子：{name}",
                blocking=True,
            )
        doc.factors = [f for f in doc.factors if f.name != name]
        self._save(doc)
        return target

    def _save(self, doc: _RegistryDoc) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        doc.factors = sorted(doc.factors, key=lambda f: f.name)
        self.path.write_text(
            yaml.safe_dump(doc.model_dump(mode="json"), allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )

def hash_code(source: str) -> str:
    return hashlib.sha256(source.encode("utf-8")).hexdigest()[:16]

__all__ = [
    "FactorMetadata",
    "FactorRegistry",
    "FactorStatus",
    "hash_code",
    "registry_path",
]
