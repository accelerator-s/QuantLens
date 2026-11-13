from __future__ import annotations

import yaml
from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, ValidationError

from ai.registry import AIModel
from ai.router import AIRouter
from exceptions import ContractViolation, ModelRouteError
from strategy.discuss import _SCHEMA_GUIDE, _SYSTEM_PROMPT, build_static_context
from strategy.models import StrategyProfileDoc, StrategyStatus
from strategy.store import StrategyProfileStore, _format_validation_error

from ..ai_chat import chat_stream

router = APIRouter(tags=["strategy-chat"])

_TASK = "structured_config"

_WEBUI_GUIDE = """\

## 网页端引导要求（重要）
- 用户可能不熟悉量化术语。始终用自然中文交流，术语第一次出现时用一句话解释
  （例如「缩尾：把少数极端值压回正常范围，避免个别异常数据主导结果」）。
- 按下面的顺序逐步引导，每轮只聚焦一步，确认一步再进入下一步：
  1. 投资目标与风格：投资哪个市场、什么标的、大约持有多久、多久调一次仓、拿多少只票；
  2. 选股范围：是否剔除 ST、停牌、上市时间过短的股票；
  3. 因子选择：从「已添加因子」清单里挑选，说明每个因子衡量什么、如何加权；
  4. 数据预处理：直接推荐一套常用组合（如缩尾 + 标准化）并解释作用，让用户确认即可；
  5. 交易与成本假设：费率来源、滑点，给出常用默认值；
  6. 风控上限：单票、单行业的仓位上限。
- 对用户没有明确偏好的技术细节，主动给出常用默认值并简述理由，请用户确认，
  不要抛出开放式技术问题让用户自己填空。
- 不要向用户展示 YAML、JSON 或英文字段名；一律用中文描述配置内容。
- 各项都确认后，用一段中文小结复述整份配置，提示用户点「生成草案」。
"""

def _pick_model(ai_router: AIRouter, name: str) -> AIModel:
    
    registry = ai_router.registry
    if name:
        return registry.get(name)
    discuss_default = registry.get_discuss_model()
    if discuss_default:
        return registry.get(discuss_default)
    candidates = ai_router.candidates_for(_TASK)
    if not candidates:
        raise ModelRouteError(
            code="AI_NO_CANDIDATE",
            message="没有可生成配置的模型，暂时无法讨论策略。",
            blocking=True,
            suggested_action="请在“模型连接”添加支持结构化输出的模型。",
        )
    return candidates[0].model

class ChatMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: str
    content: str

class ChatPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: str = ""
    profile_name: str = ""
    messages: list[ChatMessage] = []

def _current_profile_yaml(name: str) -> str:
    doc = StrategyProfileStore().get(name)
    return yaml.safe_dump(
        doc.model_dump(mode="json", exclude={"created_at", "updated_at"}),
        allow_unicode=True,
        sort_keys=False,
    )

def _edit_context(name: str) -> str:
    if not name:
        return ""
    return (
        f"\n\n本次讨论是在既有策略配置 {name} 的基础上修改，当前内容如下（YAML）：\n"
        f"{_current_profile_yaml(name)}\n"
        "用户未提出修改的字段保持原样；profile_name 保持不变，除非用户明确要求改名。"
    )

@router.post("/strategies/chat")
def strategy_chat(payload: ChatPayload):
    ai_router = AIRouter()
    model = _pick_model(ai_router, payload.model.strip())
    system = (
        _SYSTEM_PROMPT
        + build_static_context()
        + _WEBUI_GUIDE
        + _edit_context(payload.profile_name.strip())
    )
    messages = [{"role": "system", "content": system}]
    for msg in payload.messages:
        if msg.role not in ("user", "assistant"):
            continue
        messages.append({"role": msg.role, "content": msg.content})
    return chat_stream(ai_router, model, messages)

def _build_summary(messages: list[ChatMessage]) -> str:
    lines: list[str] = []
    for msg in messages:
        if msg.role not in ("user", "assistant"):
            continue
        prefix = "用户" if msg.role == "user" else "AI"
        lines.append(f"{prefix}：{msg.content}")
    return "\n".join(lines)

@router.post("/strategies/chat/finalize")
def strategy_chat_finalize(payload: ChatPayload) -> dict:
    ai_router = AIRouter()
    edit_name = payload.profile_name.strip()
    edit_note = ""
    if edit_name:
        edit_note = (
            f"\n本次是修改既有策略配置 {edit_name}，当前内容如下（YAML）：\n"
            f"{_current_profile_yaml(edit_name)}\n"
            "以此为基础输出完整配置：讨论中未提到的字段照抄原值，"
            "profile_name 保持不变（除非讨论中明确要求改名）。\n"
        )
    base_prompt = (
        "根据以下用户与 AI 的多轮讨论内容，生成一份策略配置。\n"
        f"{edit_note}\n"
        f"讨论记录：\n{_build_summary(payload.messages)}\n\n"
        "请输出 JSON 格式的 structured_config：\n"
        "- config_kind: 固定为 'strategy_profile'\n"
        "- proposed_value: dict，即完整的策略配置文档，结构如下：\n"
        f"{_SCHEMA_GUIDE}\n"
        "- open_questions: list[str]，讨论中未敲定、需要用户线下确认的问题\n\n"
        '要求：所有小数（权重、比例、bps）用字符串表示（如 "0.1"）；'
        "讨论中未明确敲定的可选节可省略（留空），禁止编造用户未确认的值；"
        "status 一律为 'draft'。"
    )

    prompt = base_prompt
    last_error = ""
    for _attempt in range(3):
        result = ai_router.run_task(
            task=_TASK,
            prompt=prompt,
            system="根据讨论记录生成策略配置。输出必须是符合 structured_config "
            "schema 的纯 JSON 对象。",
            model_name=payload.model.strip() or None,
        )
        try:
            doc = StrategyProfileDoc.model_validate(result.get("proposed_value") or {})
        except ValidationError as exc:
            last_error = _format_validation_error(exc)
            prompt = (
                f"{base_prompt}\n\n"
                f"注意：你上一次输出的 proposed_value 校验失败：{last_error}\n"
                "请严格按 schema 修正后重新输出。"
            )
            continue
        doc.status = StrategyStatus.DRAFT  
        yaml_text = yaml.safe_dump(
            doc.model_dump(mode="json", exclude={"created_at", "updated_at"}),
            allow_unicode=True,
            sort_keys=False,
        )
        open_questions = [str(q) for q in result.get("open_questions") or []]
        return {
            "profile_name": doc.profile_name,
            "yaml_text": yaml_text,
            "open_questions": open_questions,
        }

    raise ContractViolation(
        code="WEBUI_STRATEGY_DRAFT_INVALID",
        message=f"策略配置草案校验失败（重试 3 次后）：{last_error}",
        suggested_action="补充讨论细节后重新生成草案。",
    )
