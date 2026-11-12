from __future__ import annotations

from exceptions import QuantTrainingError

from .matrix import CapabilityMatrix, TaskPermission
from .registry import AIModel, ModelKind

class PermissionDenied(QuantTrainingError):
    pass

def _resolve_task(matrix: CapabilityMatrix, task: str | TaskPermission) -> TaskPermission:
    return task if isinstance(task, TaskPermission) else matrix.task(task)

def check_permission(
    matrix: CapabilityMatrix,
    model: AIModel,
    task: str | TaskPermission,
) -> None:
    
    t = _resolve_task(matrix, task)

    if not model.enabled:
        raise PermissionDenied(
            code="AI_MODEL_DISABLED",
            message=f"模型 {model.name} 已禁用。",
            context={"model": model.name, "task": t.task},
            blocking=True,
        )

    if model.kind.value not in t.permitted_executors:
        raise PermissionDenied(
            code="AI_EXECUTOR_NOT_PERMITTED",
            message=(
                f"任务 {t.task} 不允许执行者类型 {model.kind.value}"
                f"（允许：{t.permitted_executors}）"
            ),
            context={"model": model.name, "task": t.task},
            blocking=True,
        )

    if model.kind == ModelKind.API_MODEL:
        missing = [tag for tag in t.required_tags if tag not in model.capability_tags]
        if missing:
            raise PermissionDenied(
                code="AI_REQUIRED_TAGS_MISSING",
                message=f"模型 {model.name} 缺少任务 {t.task} 的必需标签：{missing}",
                context={"model": model.name, "task": t.task, "missing": missing},
                blocking=True,
                suggested_action="为模型补充能力标签，或选择满足条件的其它模型。",
            )

def score_model_for_task(
    matrix: CapabilityMatrix,
    model: AIModel,
    task: str | TaskPermission,
) -> int:
    
    t = _resolve_task(matrix, task)
    try:
        check_permission(matrix, model, t)
    except PermissionDenied:
        return -1
    if model.kind != ModelKind.API_MODEL:
        return 0
    return sum(1 for tag in t.optional_tags if tag in model.capability_tags)
