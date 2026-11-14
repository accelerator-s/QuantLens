from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from exceptions import QuantTrainingError
from paths import workspace

_REGISTRY_FILE = "factors.yaml"

def research_base_name(name: str) -> str | None:
    match = re.fullmatch(r"(.+)_r_[0-9a-f]{12}", name)
    return match.group(1) if match else None

def _utcnow() -> datetime:
    return datetime.now(tz=UTC)

def registry_path() -> Path:
    return workspace().config / _REGISTRY_FILE

class FactorStatus(StrEnum):
    DRAFT = "draft"

    ACTIVE = "active"

    DISABLED = "disabled"

def _parse_status(value: FactorStatus | str) -> FactorStatus:
    try:
        return FactorStatus(value)
    except (ValueError, TypeError) as exc:
        raise QuantTrainingError(
            code="FACTOR_INVALID_STATUS",
            message=f"非法因子状态：{value!r}。",
            suggested_action="可选值：" + "、".join(status.value for status in FactorStatus) + "。",
            blocking=True,
        ) from exc

class FactorMetadata(BaseModel):

    model_config = ConfigDict(extra="forbid", validate_assignment=True, revalidate_instances="always")

    name: str
    display_name: str = ""

    assumption: str = ""
    applicable_markets: list[str] = Field(default_factory=list)
    applicable_instruments: list[str] = Field(default_factory=list)
    timescale: str = "daily"
    dependencies: list[str] = Field(default_factory=list)
    eligibility: dict[str,float] = Field(default_factory=dict)
    min_history: int = Field(default=120, ge=0)
    window_days: int = Field(default=60, ge=1)
    scope: Literal["stock", "cross_section"] = "stock"
    engine: Literal["pandas", "spark"] = "pandas"
    output_frequency: str = "daily"
    direction: Literal["positive", "negative"] = "positive"
    parameters: dict[str, Any] = Field(default_factory=dict)
    standardization: list[str] = Field(default_factory=list)
    missing_handling: str = "drop"
    ai_generated: bool = False

    risk_notes: str = ""
    evaluation_metrics: list[str] = Field(default_factory=list)
    status: FactorStatus = FactorStatus.DRAFT
    version: str = "v1"
    code_hash: str = ""
    plugin_path: str = ""

    last_evaluated_at: datetime | None = None
    decay_history: list[dict[str, Any]] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)

    @model_validator(mode="before")
    @classmethod
    def _discard_legacy_permission(cls, value):
        if isinstance(value, dict):
            value = {key: item for key, item in value.items() if key != "production_permission"}
        return value

    @property
    def production_permission(self) -> bool:
        # Compatibility for internal readers; no independent permission state.
        return self.status == FactorStatus.ACTIVE

    @model_validator(mode='after')
    def _eligibility_fields(self):
        import math
        if set(self.eligibility)-set(self.dependencies) or any(not math.isfinite(value) for value in self.eligibility.values()):
            raise ValueError('因子适用范围必须引用已声明依赖，并给出有限的最低值')
        return self

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
        status: FactorStatus | str | None = None,
        market: str | None = None,
        instrument_class: str | None = None,
        include_research: bool = False,
    ) -> list[FactorMetadata]:
        if status is not None:
            status = _parse_status(status)
        items = self.load().factors
        # Candidate copies are execution inputs, not additional catalog factors.
        if not include_research:
            items = [f for f in items if research_base_name(f.name) is None]
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
        factor = FactorMetadata.model_validate(factor)
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
        target: FactorStatus | str,
        confirm: bool = False,
    ) -> FactorMetadata:
        target = _parse_status(target)
        doc = self.load()
        for f in doc.factors:
            if f.name == name:
                f.status = target
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
