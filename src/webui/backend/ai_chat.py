from __future__ import annotations

import re
import json

from ai.registry import AIModel
from ai.router import AIRouter, _api_key_for
from exceptions import ModelRouteError


class _SignatureFilter:
    """Remove signature tags even when split across provider chunks."""
    def __init__(self):
        self.pending = ""
        self.hidden = False

    def feed(self, text: str, *, final: bool = False) -> str:
        self.pending += text
        output = []
        while self.pending:
            tag = re.search(r"</?signature\s*>", self.pending, re.IGNORECASE)
            if tag:
                if not self.hidden:
                    output.append(self.pending[:tag.start()])
                self.hidden = not tag[0].startswith("</")
                self.pending = self.pending[tag.end():]
                continue
            start = self.pending.rfind("<")
            fragment = self.pending[start:].lower() if start >= 0 else ""
            partial = fragment and (any(tag.startswith(fragment) for tag in ("<signature", "</signature")) or re.fullmatch(r"</?signature\s*", fragment))
            end = start if partial else len(self.pending)
            if not self.hidden:
                output.append(self.pending[:end])
            self.pending = "" if final else self.pending[end:]
            if end == 0:
                break
        return "".join(output)


def chat_stream(ai_router: AIRouter, model: AIModel, messages: list[dict]):
    from fastapi.responses import StreamingResponse
    from ai.transport import stream_completion
    from exceptions import QuantTrainingError
    from .errors import unexpected_error

    def encode(event):
        return json.dumps(event, ensure_ascii=False) + "\n"

    def events():
        filters = {channel: _SignatureFilter() for channel in ("content", "reasoning")}
        try:
            yield encode({"type": "start", "model": model.name})
            for delta in stream_completion(model, _api_key_for(model), messages, timeout=ai_router.request_timeout):
                text = filters[delta["channel"]].feed(delta["text"])
                if text:
                    yield encode({"type": "delta", "channel": delta["channel"], "text": text})
            for channel, scrubber in filters.items():
                text = scrubber.feed("", final=True)
                if text:
                    yield encode({"type": "delta", "channel": channel, "text": text})
            yield encode({"type": "done"})
        except Exception as exc:
            error = exc if isinstance(exc, QuantTrainingError) else unexpected_error(exc)
            yield encode({"type": "error", "message": error.message})

    return StreamingResponse(events(), media_type="application/x-ndjson", headers={
        "Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no",
    })

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
