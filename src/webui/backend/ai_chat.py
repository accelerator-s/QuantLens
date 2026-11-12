from __future__ import annotations

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
    candidates = ai_router.candidates_for("issue_triage")
    if not candidates:
        raise ModelRouteError(
            code="AI_NO_CANDIDATE",
            message="没有可用于讨论的 api_model。",
            blocking=True,
            suggested_action="先注册模型，或在 AI 配置页设置 discuss 默认模型。",
        )
    return candidates[0].model

def chat_once(ai_router: AIRouter, model: AIModel, messages: list[dict]) -> str:
    
    api_key = _api_key_for(model)
    url = model.provider_endpoint.rstrip("/") + "/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    body = {"model": model.api_model_id, "messages": messages}

    from net import create_http_client

    try:
        with create_http_client(timeout=ai_router.request_timeout) as client:
            resp = client.post(url, headers=headers, json=body)
    except Exception as exc:
        raise ModelRouteError(
            code="AI_HTTP_TRANSPORT_ERROR",
            message=f"调用 {model.name} 失败：{exc}",
            context={"model": model.name},
            blocking=True,
        ) from exc
    if resp.status_code >= 400:
        raise ModelRouteError(
            code="AI_HTTP_STATUS_ERROR",
            message=f"模型返回 HTTP {resp.status_code}: {resp.text[:300]}",
            context={"model": model.name, "status": resp.status_code},
            blocking=True,
        )
    try:
        return resp.json()["choices"][0]["message"]["content"]
    except (KeyError, IndexError, ValueError, TypeError) as exc:
        raise ModelRouteError(
            code="AI_RESPONSE_MALFORMED",
            message=f"{model.name} 响应格式异常，缺少 choices[0].message.content。",
            context={"model": model.name},
            blocking=True,
        ) from exc
