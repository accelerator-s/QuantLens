from __future__ import annotations

import json
import time
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx

from exceptions import ModelRouteError
from net import create_http_client
from .registry import AIModel


def endpoint_base(endpoint: str) -> str:
    parts = urlsplit(endpoint.strip())
    if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username or parts.password or parts.query or parts.fragment:
        raise ModelRouteError(code="AI_ENDPOINT_INVALID", message="请填写有效的 HTTP 或 HTTPS 服务地址。", blocking=True)
    path = parts.path.rstrip("/")
    for suffix in ("/chat/completions", "/messages", "/models"):
        if path.endswith(suffix):
            path = path[:-len(suffix)]
            break
    if not path:
        path = "/v1"
    return urlunsplit((parts.scheme, parts.netloc, path, "", ""))


def _headers(api_type: str, api_key: str) -> dict[str, str]:
    if api_type == "anthropic":
        # Keep the widely supported Messages API version for compatible gateways.
        return {"x-api-key": api_key, "anthropic-version": "2023-06-01", "Content-Type": "application/json"}
    return {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}


def _request(client, method: str, url: str, *, api_key: str, **kwargs) -> dict:
    try:
        response = client.request(method, url, **kwargs)
    except httpx.HTTPError as exc:
        raise ModelRouteError(code="AI_HTTP_TRANSPORT_ERROR", message="连接失败，请检查服务地址和网络。", blocking=True) from exc
    if response.status_code >= 400:
        # A gateway can echo request headers; never expose its raw response or key.
        status = response.status_code
        detail = {401: "接口密钥无效", 403: "没有访问权限", 404: "接口或模型不存在", 429: "请求过多或额度不足"}.get(status, "服务端请求失败")
        raise ModelRouteError(code="AI_HTTP_STATUS_ERROR", message=f"{detail}（HTTP {status}）。", context={"status": status}, blocking=True)
    try:
        data = response.json()
        if not isinstance(data, dict):
            raise ValueError("expected object")
        return data
    except (ValueError, TypeError) as exc:
        raise ModelRouteError(code="AI_HTTP_RESPONSE_INVALID", message="服务端未返回有效的 JSON 对象。", blocking=True) from exc


def discover_models(endpoint: str, api_type: str, api_key: str, *, timeout: float = 30) -> list[dict[str, str]]:
    url = endpoint_base(endpoint) + "/models"
    models: dict[str, dict[str, str]] = {}
    cursor = ""
    with create_http_client(timeout=timeout, target_url=url) as client:
        for _ in range(100):
            params = {"after_id": cursor} if cursor else {}
            data = _request(client, "GET", url, api_key=api_key, headers=_headers(api_type, api_key), params=params)
            items = data.get("data")
            if not isinstance(items, list):
                raise ModelRouteError(code="AI_HTTP_RESPONSE_INVALID", message="模型列表格式异常，缺少 data 列表。", blocking=True)
            for item in items:
                if isinstance(item, dict) and isinstance(item.get("id"), str) and item["id"].strip():
                    model_id = item["id"].strip()
                    models[model_id] = {"id": model_id, "name": str(item.get("display_name") or model_id)}
            if not data.get("has_more"):
                return sorted(models.values(), key=lambda item: item["id"])
            next_cursor = data.get("last_id")
            if not isinstance(next_cursor, str) or not next_cursor or next_cursor == cursor:
                break
            cursor = next_cursor
    raise ModelRouteError(code="AI_MODEL_LIST_INCOMPLETE", message="模型列表分页异常，请重试。", blocking=True)


def _anthropic_messages(messages: list[dict]) -> tuple[str, list[dict]]:
    system = []
    converted = []
    for message in messages:
        role = message["role"]
        if role == "system":
            system.append(message.get("content") or "")
            continue
        if role == "tool":
            block = {"type": "tool_result", "tool_use_id": message["tool_call_id"], "content": message.get("content") or ""}
            if converted and converted[-1]["role"] == "user" and isinstance(converted[-1]["content"], list):
                converted[-1]["content"].append(block)
            else:
                converted.append({"role": "user", "content": [block]})
            continue
        content = message.get("_anthropic_content")
        if content is None:
            content = []
            if message.get("content"):
                content.append({"type": "text", "text": message["content"]})
            for call in message.get("tool_calls") or []:
                fn = call["function"]
                args = fn.get("arguments") or "{}"
                content.append({"type": "tool_use", "id": call["id"], "name": fn["name"], "input": json.loads(args) if isinstance(args, str) else args})
        converted.append({"role": role, "content": content})
    return "\n\n".join(system), converted


def completion(model: AIModel, api_key: str, messages: list[dict], *, timeout: float = 180, json_output: bool = False, tools: list[dict] | None = None, tool_choice: str = "auto", max_tokens: int | None = None) -> dict[str, Any]:
    anthropic = model.api_type == "anthropic"
    url = endpoint_base(model.provider_endpoint) + ("/messages" if anthropic else "/chat/completions")
    body: dict[str, Any] = {"model": model.api_model_id}
    if anthropic:
        system, converted = _anthropic_messages(messages)
        if json_output:
            system += "\n请只返回合法 JSON 对象。"
        if system:
            body["system"] = system
        body.update(messages=converted, max_tokens=max_tokens or 8192)
        if tools:
            body["tools"] = [{"name": spec["function"]["name"], "description": spec["function"].get("description", ""), "input_schema": spec["function"]["parameters"]} for spec in tools]
            body["tool_choice"] = {"type": tool_choice}
    else:
        body["messages"] = [{k: v for k, v in msg.items() if not k.startswith("_")} for msg in messages]
        if json_output:
            body["response_format"] = {"type": "json_object"}
        if tools:
            body.update(tools=tools, tool_choice=tool_choice)
        if max_tokens:
            body["max_completion_tokens"] = max_tokens
    with create_http_client(timeout=timeout, target_url=url) as client:
        data = _request(client, "POST", url, api_key=api_key, headers=_headers(model.api_type, api_key), json=body)
    try:
        if anthropic:
            blocks = data["content"]
            if not isinstance(blocks, list):
                raise ValueError("invalid content")
            msg: dict[str, Any] = {"role": "assistant", "content": "".join(block["text"] for block in blocks if block["type"] == "text"), "_anthropic_content": blocks}
            calls = [{"id": block["id"], "type": "function", "function": {"name": block["name"], "arguments": json.dumps(block["input"], ensure_ascii=False)}} for block in blocks if block["type"] == "tool_use"]
            if calls:
                msg["tool_calls"] = calls
        else:
            msg = data["choices"][0]["message"]
        if not isinstance(msg, dict) or (not isinstance(msg.get("content"), str) and not msg.get("tool_calls")):
            raise ValueError("missing message")
        return msg
    except (KeyError, IndexError, ValueError, TypeError) as exc:
        raise ModelRouteError(code="AI_HTTP_RESPONSE_INVALID", message="模型响应格式异常，未找到回复内容。", blocking=True) from exc


def probe_model(model: AIModel, api_key: str) -> dict:
    start = time.perf_counter()
    reply = completion(model, api_key, [{"role": "user", "content": "Reply with OK."}], timeout=30, max_tokens=512).get("content", "").strip()
    if not reply:
        raise ModelRouteError(code="AI_OUTPUT_EMPTY", message="模型未返回内容，无法确认连通性。", blocking=True)
    return {"ok": True, "model": model.api_model_id, "reply": reply, "latency_ms": round((time.perf_counter() - start) * 1000)}


def stream_completion(model: AIModel, api_key: str, messages: list[dict], *, timeout: float = 180):
    """Yield real provider deltas; never buffer a completed reply to simulate typing."""
    anthropic = model.api_type == "anthropic"
    url = endpoint_base(model.provider_endpoint) + ("/messages" if anthropic else "/chat/completions")
    body = {"model": model.api_model_id, "stream": True}
    if anthropic:
        system, converted = _anthropic_messages(messages)
        body.update(messages=converted, max_tokens=8192)
        if system:
            body["system"] = system
    else:
        body["messages"] = [{k: v for k, v in msg.items() if not k.startswith("_")} for msg in messages]
    try:
        with create_http_client(timeout=timeout, target_url=url) as client:
            with client.stream("POST", url, headers=_headers(model.api_type, api_key), json=body) as response:
                if response.status_code >= 400:
                    status = response.status_code
                    detail = {401: "接口密钥无效", 403: "没有访问权限", 404: "接口或模型不存在", 429: "请求过多或额度不足"}.get(status, "服务端请求失败")
                    raise ModelRouteError(code="AI_HTTP_STATUS_ERROR", message=f"{detail}（HTTP {status}）。", blocking=True)
                data_lines = []
                for line in response.iter_lines():
                    if line.startswith("data:"):
                        data_lines.append(line[5:].lstrip(" "))
                        continue
                    if line or not data_lines:
                        continue
                    raw = "\n".join(data_lines)
                    data_lines.clear()
                    if raw == "[DONE]":
                        return
                    event = json.loads(raw)
                    if event.get("error") or event.get("type") == "error":
                        raise ModelRouteError(code="AI_STREAM_ERROR", message="模型服务中断了回复，请重试。", blocking=True)
                    if anthropic:
                        if event.get("type") == "message_stop":
                            return
                        if event.get("type") == "content_block_start":
                            delta = event.get("content_block", {})
                        elif event.get("type") == "content_block_delta":
                            delta = event.get("delta", {})
                        else:
                            continue
                        for field, channel in (("thinking", "reasoning"), ("text", "content")):
                            if isinstance(delta.get(field), str) and delta[field]:
                                yield {"channel": channel, "text": delta[field]}
                    else:
                        choices = event.get("choices") or []
                        if not choices:
                            continue
                        delta = choices[0].get("delta") or {}
                        for field, channel in (("reasoning_content", "reasoning"), ("content", "content")):
                            if isinstance(delta.get(field), str) and delta[field]:
                                yield {"channel": channel, "text": delta[field]}
                raise ModelRouteError(code="AI_STREAM_INTERRUPTED", message="回复连接意外断开，请重试。", blocking=True)
    except httpx.HTTPError as exc:
        raise ModelRouteError(code="AI_HTTP_TRANSPORT_ERROR", message="连接失败或回复中断，请检查网络后重试。", blocking=True) from exc
    except (ValueError, TypeError, KeyError, IndexError) as exc:
        raise ModelRouteError(code="AI_HTTP_RESPONSE_INVALID", message="模型流式响应格式异常，请重试。", blocking=True) from exc
