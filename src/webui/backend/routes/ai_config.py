from __future__ import annotations

import yaml
from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict

from ai import secrets
from ai.discuss import _SYSTEM_PROMPT, _TRIAGE_TO_WIZARD
from ai.matrix import load_matrix, matrix_path
from ai.permissions import score_model_for_task
from ai.registry import AIModel, ModelKind, ModelRegistry
from ai.router import AIRouter, env_var_name
from exceptions import ContractViolation

from ..ai_chat import chat_once, pick_chat_model
from ..schemas import ConfirmPayload, confirm_preview, parse_enum
from ..serialize import json_safe

router = APIRouter(tags=["ai"])

@router.get("/ai/models")
def list_models() -> dict:
    registry = ModelRegistry()
    configured = set(secrets.list_secret_names())
    items = []
    for model in registry.list():
        data = json_safe(model)
        env_name = env_var_name(model.name)
        data["env_var"] = env_name
        data["configured"] = env_name in configured
        items.append(data)
    return {
        "items": items,
        "discuss_model": registry.get_discuss_model(),
    }

@router.get("/ai/matrix")
def get_matrix() -> dict:
    matrix = load_matrix()
    return json_safe(
        {
            "version": matrix.version,
            "tags": matrix.tags,
            "tasks": matrix.tasks,
        }
    )

@router.get("/ai/recommendations/factor-evolution")
def factor_evolution_model_recommendations() -> dict:
    
    matrix = load_matrix()
    registry = ModelRegistry()
    configured = set(secrets.list_secret_names())
    rows = []
    rejected = []
    for model in registry.list(kind=ModelKind.API_MODEL):
        factor_score = score_model_for_task(matrix, model, "factor_candidate")
        review_score = score_model_for_task(matrix, model, "structured_config")
        has_key = env_var_name(model.name) in configured
        if factor_score < 0 or review_score < 0 or not model.enabled:
            rejected.append(
                {
                    "name": model.name,
                    "reason": "未同时满足因子生成和结果复盘的必需能力。",
                }
            )
            continue
        score = factor_score + review_score
        matched = []
        for tag, label in {
            "reasoning_high": "深度推理",
            "structured_output": "结构化输出",
            "coding_high": "代码生成",
            "long_context": "长上下文",
            "review_strict": "严格复盘",
            "risk_aware": "风险识别",
        }.items():
            if tag in model.capability_tags:
                matched.append(label)
        rows.append(
            {
                "name": model.name,
                "score": score,
                "configured": has_key,
                "selectable": has_key,
                "capabilities": matched,
                "reason": (
                    f"同时通过候选生成与结果复盘权限检查；"
                    f"已具备{'、'.join(matched) or '基础能力'}。"
                    + ("" if has_key else "尚未配置 接口密钥，暂不可选。")
                ),
            }
        )
    rows.sort(key=lambda item: (-item["score"], item["name"]))
    selectable = [item for item in rows if item["selectable"]]
    recommended = selectable[0]["name"] if selectable else ""
    for item in rows:
        item["recommended"] = item["name"] == recommended
    return {
        "recommended": recommended,
        "items": rows,
        "rejected": rejected,
        "policy": "推荐只影响默认顺序；用户的明确选择优先，但不能绕过任务配置的必需权限。",
    }

@router.get("/ai/recommendations/strategy-evolution")
def strategy_evolution_model_recommendations() -> dict:
    
    matrix = load_matrix()
    configured = set(secrets.list_secret_names())
    rows = []
    for model in ModelRegistry().list(kind=ModelKind.API_MODEL):
        score = score_model_for_task(matrix, model, "structured_config")
        if score < 0 or not model.enabled:
            continue
        has_key = env_var_name(model.name) in configured
        rows.append(
            {
                "name": model.name,
                "score": score,
                "configured": has_key,
                "selectable": has_key,
                "reason": (
                    "已通过策略候选生成与结果复盘的结构化输出权限检查。"
                    + ("" if has_key else "尚未配置 接口密钥，暂不可选。")
                ),
            }
        )
    rows.sort(key=lambda item: (-item["score"], item["name"]))
    selectable = [item for item in rows if item["selectable"]]
    recommended = selectable[0]["name"] if selectable else ""
    for item in rows:
        item["recommended"] = item["name"] == recommended
    return {
        "recommended": recommended,
        "items": rows,
        "policy": "推荐只影响默认顺序；用户可指定任何通过任务配置权限检查的可用模型。",
    }

@router.get("/ai/discuss-model")
def get_discuss_model() -> dict:
    return {"discuss_model": ModelRegistry().get_discuss_model()}

class ModelPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    kind: str
    provider_endpoint: str = ""
    api_model_id: str = ""
    capability_tags: list[str] = []
    notes: str = ""
    enabled: bool = True
    override: bool = False
    api_key: str = ""

class EnabledPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool

class DiscussModelPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str

def _build_model(payload: ModelPayload, *, name: str | None = None) -> AIModel:
    matrix = load_matrix()
    unknown_tags = sorted(set(payload.capability_tags) - set(matrix.tags))
    if unknown_tags:
        raise ContractViolation(
            code="AI_MODEL_CAPABILITY_TAG_INVALID",
            message=f"模型包含任务配置未声明的标签：{'、'.join(unknown_tags)}",
            context={"unknown_tags": unknown_tags, "allowed_tags": matrix.tags},
            blocking=True,
            suggested_action="只能从 AI 任务配置提供的能力标签中选择。",
        )
    return AIModel(
        name=name or payload.name,
        kind=parse_enum(ModelKind, payload.kind, "kind"),
        provider_endpoint=payload.provider_endpoint,
        api_model_id=payload.api_model_id,
        capability_tags=payload.capability_tags,
        notes=payload.notes,
        enabled=payload.enabled,
    )

def _store_api_key(name: str, raw_key: str) -> bool:
    
    key = raw_key.strip()
    if not key:
        return False
    secrets.set_secret(env_var_name(name), key)
    return True

@router.post("/ai/models")
def add_model(payload: ModelPayload) -> dict:
    registry = ModelRegistry()
    registry.add(_build_model(payload), override=payload.override)
    key_saved = _store_api_key(payload.name, payload.api_key)
    return json_safe({"model": registry.get(payload.name), "key_saved": key_saved})

@router.put("/ai/models/{name}")
def update_model(name: str, payload: ModelPayload) -> dict:
    registry = ModelRegistry()
    registry.get(name)
    registry.add(_build_model(payload, name=name), override=True)
    key_saved = _store_api_key(name, payload.api_key)
    return json_safe({"model": registry.get(name), "key_saved": key_saved})

class RemoveModelPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirm: bool = False
    keep_key: bool = False

@router.post("/ai/models/{name}/remove")
def remove_model(name: str, payload: RemoveModelPayload) -> dict:
    registry = ModelRegistry()
    model = registry.get(name)
    has_key = env_var_name(name) in set(secrets.list_secret_names())
    if not payload.confirm:
        if payload.keep_key:
            key_note = "本地 接口密钥 将保留。"
        elif has_key:
            key_note = "本地 接口密钥 将一并删除（勾选保留可跳过）。"
        else:
            key_note = "本地未存有该模型的 接口密钥。"
        return confirm_preview(
            {
                "操作": "删除已添加模型",
                "模型": model.name,
                "类型": model.kind.value,
                "能力标签": "、".join(model.capability_tags) or "（无）",
                "API key": key_note,
            }
        )
    removed = registry.remove(name, confirm=True)
    key_removed = False
    if not payload.keep_key and removed.kind == ModelKind.API_MODEL:
        key_removed = secrets.remove_secret(env_var_name(name))
    return json_safe({"removed": removed.name, "key_removed": key_removed})

@router.post("/ai/models/{name}/key/delete")
def delete_model_key(name: str, payload: ConfirmPayload) -> dict:
    ModelRegistry().get(name)
    env_name = env_var_name(name)
    has_key = env_name in set(secrets.list_secret_names())
    if not payload.confirm:
        return confirm_preview(
            {
                "操作": "删除本地 接口密钥",
                "模型": name,
                "环境变量": env_name,
                "当前状态": "已配置" if has_key else "未配置（本次为空操作）",
            }
        )
    removed = secrets.remove_secret(env_name)
    return {"model": name, "key_removed": removed}

@router.post("/ai/models/{name}/test")
def test_model(name: str) -> dict:
    ai_router = AIRouter()
    model = ai_router.registry.get(name)
    if model.kind != ModelKind.API_MODEL:
        raise ContractViolation(
            code="WEBUI_MODEL_NOT_TESTABLE",
            message=f"模型 {name} 不是 api_model，无法做连通性测试。",
        )
    reply = chat_once(
        ai_router,
        model,
        [{"role": "user", "content": "请计算 17*23，直接给出数字。"}],
    )
    return {"ok": True, "model": name, "reply": reply.strip()}

@router.post("/ai/models/{name}/enabled")
def set_model_enabled(name: str, payload: EnabledPayload) -> dict:
    model = ModelRegistry().set_enabled(name, payload.enabled)
    return json_safe({"model": model})

@router.post("/ai/discuss-model")
def set_discuss_model(payload: DiscussModelPayload) -> dict:
    registry = ModelRegistry()
    registry.get(payload.name)
    registry.set_discuss_model(payload.name)
    return {"discuss_model": registry.get_discuss_model()}

class ChatMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: str
    content: str

class ChatPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: str = ""
    messages: list[ChatMessage] = []

@router.post("/ai/chat")
def ai_chat(payload: ChatPayload) -> dict:
    ai_router = AIRouter()
    model = pick_chat_model(ai_router, payload.model.strip())
    system = _SYSTEM_PROMPT + "上下文：用户正在 界面 中梳理一个待创建的 issue。\n"
    messages = [{"role": "system", "content": system}]
    for msg in payload.messages:
        if msg.role not in ("user", "assistant"):
            continue
        messages.append({"role": msg.role, "content": msg.content})
    reply = chat_once(ai_router, model, messages)
    return {"reply": reply, "model": model.name}

@router.post("/ai/chat/finalize")
def ai_chat_finalize(payload: ChatPayload) -> dict:
    ai_router = AIRouter()
    lines = []
    for msg in payload.messages:
        prefix = "用户" if msg.role == "user" else "AI"
        lines.append(f"{prefix}：{msg.content}")
    summary = "\n".join(lines)

    base_prompt = (
        "根据以下用户与 AI 的多轮讨论内容，生成一个 issue_triage 结构化建议。\n\n"
        f"讨论记录：\n{summary}\n\n"
        "请输出 JSON 格式的 issue_triage。所有字段均为纯文本字符串（str），"
        "禁止使用 dict 或 list 作为字段值。具体字段：\n"
        "- suggested_type: str, issue 类型（如 'market_rule', 'framework' 等）\n"
        "- suggested_priority: str, 优先级，只能是 'p0'/'p1'/'p2'/'p3' 之一\n"
        "- suggested_title: str, 简洁的 issue 标题\n"
        "- background: str, 背景说明\n"
        "- goal: str, 目标\n"
        "- scope: str, 影响范围（纯文本，多项用逗号分隔，不要用 list/dict）\n"
        "- acceptance: str, 验收标准（纯文本，多项用分号分隔，不要用 list/dict）\n"
        "- risks: str, 风险说明（纯文本，不要用 list/dict，可留空字符串）\n"
        "- confidence: float, 对该建议的置信度（0-1）\n"
        "- rationale: str, 理由\n"
    )

    max_retries = 3
    prompt = base_prompt
    result: dict | None = None
    for attempt in range(max_retries):
        try:
            result = ai_router.run_task(
                task="issue_triage",
                prompt=prompt,
                system="根据讨论记录为用户生成 issue 建议。输出必须是纯 JSON 对象，"
                "所有字段为字符串类型，不得使用数组或嵌套对象。",
                model_name=payload.model.strip() or None,
            )
            break
        except ContractViolation as exc:
            if attempt >= max_retries - 1:
                raise
            prompt = (
                f"{base_prompt}\n\n"
                f"注意：你上一次的输出校验失败：{exc.message}\n"
                "请严格按照上述字段类型要求重新输出，所有值必须是字符串。"
            )

    assert result is not None
    suggestion = {}
    for triage_key, wizard_key in _TRIAGE_TO_WIZARD.items():
        value = result.get(triage_key, "")
        if value:
            suggestion[wizard_key] = str(value)
    return json_safe({"suggestion": suggestion, "raw": result})

_EXECUTORS = ["api_model", "external_ide_ai", "human", "mixed"]

class SetTaskPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirm: bool = False
    required: list[str] | None = None
    optional: list[str] | None = None
    executors: list[str] | None = None

@router.post("/ai/matrix/tasks/{task_name}")
def set_matrix_task(task_name: str, payload: SetTaskPayload) -> dict:
    matrix = load_matrix()
    target = matrix.task(task_name)
    new_required = payload.required if payload.required is not None else list(target.required_tags)
    new_optional = payload.optional if payload.optional is not None else list(target.optional_tags)
    new_executors = (
        payload.executors if payload.executors is not None else list(target.permitted_executors)
    )

    for tag in [*new_required, *new_optional]:
        if tag not in matrix.tags:
            raise ContractViolation(
                code="WEBUI_MATRIX_UNKNOWN_TAG",
                message=f"未知标签：{tag}",
                suggested_action=f"可选标签：{'、'.join(matrix.tags)}",
            )
    for ex in new_executors:
        if ex not in _EXECUTORS:
            raise ContractViolation(
                code="WEBUI_MATRIX_UNKNOWN_EXECUTOR",
                message=f"未知执行者：{ex}",
                suggested_action=f"可选执行者：{'、'.join(_EXECUTORS)}",
            )
    if not new_executors:
        raise ContractViolation(
            code="WEBUI_MATRIX_EXECUTORS_EMPTY",
            message="执行方式不能为空。",
        )

    def _fmt(values: list[str]) -> str:
        labels={"api_model":"接口模型","external_ide_ai":"外部开发助手","human":"人工","mixed":"人工与模型",
                "reasoning_high":"深度推理","coding_high":"代码生成","cheap_batch":"低成本批处理","long_context":"长上下文",
                "review_strict":"严格复核","structured_output":"结构化输出","debugging":"调试诊断","risk_aware":"风险识别","web_browsing":"联网检索"}
        return "、".join(labels.get(v,v) for v in values) or "—"

    if not payload.confirm:
        return confirm_preview(
            {
                "操作": f"修改任务：{target.description}",
                "必需标签": f"{_fmt(target.required_tags)} → {_fmt(new_required)}",
                "可选标签": f"{_fmt(target.optional_tags)} → {_fmt(new_optional)}",
                "执行方式": f"{_fmt(target.permitted_executors)} → {_fmt(new_executors)}",
                "说明": "确认后保存任务设置。",
            }
        )

    target.required_tags = new_required
    target.optional_tags = new_optional
    target.permitted_executors = new_executors
    matrix_path().write_text(
        yaml.safe_dump(matrix.model_dump(mode="json"), allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    return json_safe({"task": target})

@router.get("/ai/route-preview")
def route_preview(task: str) -> dict:
    ai_router = AIRouter()
    t = ai_router.matrix.task(task)
    candidates = [
        {"model": d.model.name, "score": d.score, "capability_tags": list(d.model.capability_tags)}
        for d in ai_router.candidates_for(task)
    ]
    return json_safe(
        {
            "task": t.task,
            "description": t.description,
            "required_tags": t.required_tags,
            "optional_tags": t.optional_tags,
            "permitted_executors": t.permitted_executors,
            "candidates": candidates,
        }
    )
