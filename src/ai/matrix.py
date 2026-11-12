from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field, field_validator

from exceptions import QuantTrainingError
from paths import workspace

_MATRIX_FILENAME = "ai_capability_matrix.yaml"

def matrix_path() -> Path:
    
    return workspace().config / _MATRIX_FILENAME

class TaskPermission(BaseModel):
    task: str
    description: str = ""
    required_tags: list[str] = Field(default_factory=list)
    optional_tags: list[str] = Field(default_factory=list)
    permitted_executors: list[str] = Field(default_factory=list)

    @field_validator("task")
    @classmethod
    def _task_not_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("task 不能为空")
        return v

class CapabilityMatrix(BaseModel):
    version: int = 1
    tags: list[str] = Field(default_factory=list)
    tasks: list[TaskPermission] = Field(default_factory=list)

    def task(self, name: str) -> TaskPermission:
        for t in self.tasks:
            if t.task == name:
                return t
        raise QuantTrainingError(
            code="AI_TASK_UNKNOWN",
            message=f"矩阵中未声明任务：{name}",
            context={"task": name},
            blocking=True,
            suggested_action="先通过 framework issue 在矩阵中加入该任务。",
        )

    def task_names(self) -> list[str]:
        return [t.task for t in self.tasks]

def load_matrix(path: Path | None = None) -> CapabilityMatrix:
    
    target = path or matrix_path()
    if not target.exists():
        raise QuantTrainingError(
            code="AI_MATRIX_MISSING",
            message=f"未找到能力矩阵文件：{target}",
            context={"path": str(target)},
            blocking=True,
            suggested_action="确认 config/ai_capability_matrix.yaml 是否随仓库提交，或在控制台中运行 `doctor --init`。",
        )
    raw = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
    matrix = CapabilityMatrix.model_validate(raw)
    _validate_matrix(matrix)
    return matrix

def _validate_matrix(matrix: CapabilityMatrix) -> None:
    
    seen: set[str] = set()
    for t in matrix.tasks:
        if t.task in seen:
            raise QuantTrainingError(
                code="AI_MATRIX_DUPLICATE_TASK",
                message=f"任务名重复：{t.task}",
                blocking=True,
            )
        seen.add(t.task)
        for tag in [*t.required_tags, *t.optional_tags]:
            if tag not in matrix.tags:
                raise QuantTrainingError(
                    code="AI_MATRIX_UNKNOWN_TAG",
                    message=f"任务 {t.task} 引用了未声明的标签：{tag}",
                    blocking=True,
                    suggested_action="在 tags: 顶层列表中加入该标签，或修正任务声明。",
                )
        for ex in t.permitted_executors:
            if ex not in {"api_model", "external_ide_ai", "human", "mixed"}:
                raise QuantTrainingError(
                    code="AI_MATRIX_BAD_EXECUTOR",
                    message=f"任务 {t.task} 的执行者类型非法：{ex}",
                    blocking=True,
                )
