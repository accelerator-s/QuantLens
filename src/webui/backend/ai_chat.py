from __future__ import annotations

import re

from ai.registry import AIModel
from ai.router import AIRouter, _api_key_for
from exceptions import ModelRouteError

def pick_chat_model(ai_router: AIRouter, name: str) -> AIModel:
    
    registry = ai_router.registry
    if name:
        return registry.get(name)
    discuss_default = registry.get_discuss_model()
    if discuss_default:
        return registry.get(discuss_default)
    candidates = ai_router.candidates_for("structured_config")
    if not candidates:
        raise ModelRouteError(
            code="AI_NO_CANDIDATE",
            message="没有可用于讨论的 api_model。",
            blocking=True,
            suggested_action="先注册模型，或在 AI 配置页设置 discuss 默认模型。",
        )
    return candidates[0].model

def chat_once(ai_router: AIRouter, model: AIModel, messages: list[dict]) -> str:
    return chat_reply(ai_router, model, messages)["reply"]


def chat_reply(ai_router: AIRouter, model: AIModel, messages: list[dict]) -> dict[str, str]:
    from ai.transport import completion

    message = completion(model, _api_key_for(model), messages, timeout=ai_router.request_timeout)
    reasoning = message.get("reasoning_content") or ""
    if not reasoning:
        reasoning = "\n\n".join(
            block["thinking"] for block in message.get("_anthropic_content", [])
            if block.get("type") == "thinking" and isinstance(block.get("thinking"), str)
        )
    def without_signatures(text: str) -> str:
        return re.sub(r"<signature\s*>[\s\S]*?(?:</signature\s*>|$)", "", text, flags=re.IGNORECASE)

    return {
        "reply": without_signatures(message.get("content") or ""),
        "reasoning": without_signatures(reasoning) if isinstance(reasoning, str) else "",
    }
