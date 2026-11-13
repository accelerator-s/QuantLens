from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, field_validator

from exceptions import QuantTrainingError
from paths import workspace

_REGISTRY_FILENAME = "ai_models.yaml"

def registry_path() -> Path:
    return workspace().config / _REGISTRY_FILENAME

class ModelKind(StrEnum):
    API_MODEL = "api_model"

class AIModel(BaseModel):
    name: str

    kind: ModelKind
    provider_endpoint: str = ""
    api_type: Literal["openai", "anthropic"] = "openai"

    api_model_id: str = ""

    capability_tags: list[str] = Field(default_factory=list)
    notes: str = ""
    enabled: bool = True
    created_at: datetime = Field(default_factory=lambda: datetime.now(tz=UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(tz=UTC))

    @field_validator("name")
    @classmethod
    def _name_not_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("name 不能为空")
        return v

    def model_post_init(self, _ctx: object) -> None:  
        if self.kind == ModelKind.API_MODEL:
            if not self.provider_endpoint or not self.api_model_id:
                raise ValueError(
                    f"api_model {self.name} 必须同时提供 provider_endpoint 与 api_model_id。"
                )
            if not self.capability_tags:
                raise ValueError(f"api_model {self.name} 至少声明一个能力标签。")

class _RegistryDoc(BaseModel):
    version: int = 1
    models: list[AIModel] = Field(default_factory=list)
    discuss_model: str = ""

class ModelRegistry:

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or registry_path()

    def load(self) -> _RegistryDoc:
        if not self.path.exists():
            return _RegistryDoc()
        raw = yaml.safe_load(self.path.read_text(encoding="utf-8")) or {}
        return _RegistryDoc.model_validate(raw)

    def list(self, *, kind: ModelKind | None = None) -> list[AIModel]:
        models = self.load().models
        if kind is not None:
            models = [m for m in models if m.kind == kind]
        return sorted(models, key=lambda m: (m.kind.value, m.name))

    def get(self, name: str) -> AIModel:
        for m in self.load().models:
            if m.name == name:
                return m
        raise QuantTrainingError(
            code="AI_MODEL_NOT_FOUND",
            message=f"未注册模型：{name}",
            context={"name": name},
            blocking=True,
        )

    def add(self, model: AIModel, *, override: bool = False) -> None:
        doc = self.load()
        existing_idx = next((i for i, m in enumerate(doc.models) if m.name == model.name), None)
        if existing_idx is not None and not override:
            raise QuantTrainingError(
                code="AI_MODEL_EXISTS",
                message=f"模型 {model.name} 已存在；如需覆盖请使用 --override。",
                context={"name": model.name},
                blocking=True,
                suggested_action="重新执行命令并加上 --override。",
            )
        if existing_idx is not None:
            
            old = doc.models[existing_idx]
            model = model.model_copy(
                update={
                    "created_at": old.created_at,
                    "updated_at": datetime.now(tz=UTC),
                }
            )
            doc.models[existing_idx] = model
        else:
            doc.models.append(model)
        self._save(doc)

    def remove(self, name: str, *, confirm: bool = False) -> AIModel:
        if not confirm:
            raise QuantTrainingError(
                code="AI_MODEL_REMOVE_CONFIRM_REQUIRED",
                message=f"删除模型 {name} 需要人工确认。",
                blocking=True,
                suggested_action="重新执行命令并加上 --confirm。",
            )
        doc = self.load()
        new_models = [m for m in doc.models if m.name != name]
        if len(new_models) == len(doc.models):
            raise QuantTrainingError(
                code="AI_MODEL_NOT_FOUND",
                message=f"未注册模型：{name}",
                blocking=True,
            )
        removed = next(m for m in doc.models if m.name == name)
        doc.models = new_models
        self._save(doc)
        return removed

    def get_discuss_model(self) -> str:
        return self.load().discuss_model

    def set_discuss_model(self, name: str) -> None:
        doc = self.load()
        doc.discuss_model = name
        self._save(doc)

    def set_enabled(self, name: str, enabled: bool) -> AIModel:
        doc = self.load()
        for m in doc.models:
            if m.name == name:
                m.enabled = enabled
                m.updated_at = datetime.now(tz=UTC)
                self._save(doc)
                return m
        raise QuantTrainingError(
            code="AI_MODEL_NOT_FOUND",
            message=f"未注册模型：{name}",
            blocking=True,
        )

    def _save(self, doc: _RegistryDoc) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        
        doc.models = sorted(doc.models, key=lambda m: (m.kind.value, m.name))
        data = doc.model_dump(mode="json")
        self.path.write_text(
            yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
