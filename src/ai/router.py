from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import UTC
from typing import Any

from exceptions import ContractViolation, ModelRouteError
from net import create_http_client

from .matrix import CapabilityMatrix, load_matrix
from .permissions import check_permission, score_model_for_task
from .registry import AIModel, ModelKind, ModelRegistry
from .schemas import validate_ai_output

_API_KEY_ENV = "QUANT_API_KEY"
_NAME_SAFE_RE = re.compile(r"[^A-Z0-9]+")
_THINK_BLOCK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*\})\s*```", re.DOTALL)

def _strip_model_wrapping(raw: str) -> str:
    
    if not raw:
        return raw
    text = _THINK_BLOCK_RE.sub("", raw).strip()
    fence = _FENCE_RE.search(text)
    if fence:
        return fence.group(1).strip()
    
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        return text[start : end + 1]
    return text

def env_var_name(model_name: str) -> str:
    
    safe = _NAME_SAFE_RE.sub("_", model_name.upper()).strip("_")
    return f"{_API_KEY_ENV}_{safe}"

def _api_key_for(model: AIModel) -> str:
    
    from .secrets import load_into_env

    load_into_env()
    var = env_var_name(model.name)
    value = os.environ.get(var)
    if value:
        return value
    raise ModelRouteError(
        code="AI_API_KEY_MISSING",
        message=f"未找到 {model.name} 的 API key（{var} 未设置）。",
        context={"env_var": var},
        blocking=True,
        suggested_action=(
            f"运行 `python main.py configure ai update-model {model.name}`，"
            "并在交互引导中写入 API key；"
            "禁止把 key 写入注册表 / issue / 记忆。"
        ),
    )

@dataclass
class RouteDecision:

    model: AIModel
    score: int

class AIRouter:

    def __init__(
        self,
        *,
        matrix: CapabilityMatrix | None = None,
        registry: ModelRegistry | None = None,
        request_timeout: float = 180.0,
    ) -> None:
        self.matrix = matrix or load_matrix()
        self.registry = registry or ModelRegistry()
        self.request_timeout = request_timeout

    def candidates_for(self, task: str) -> list[RouteDecision]:
        
        out: list[RouteDecision] = []
        t = self.matrix.task(task)
        for m in self.registry.list(kind=ModelKind.API_MODEL):
            score = score_model_for_task(self.matrix, m, t)
            if score >= 0:
                out.append(RouteDecision(model=m, score=score))
        out.sort(key=lambda d: d.score, reverse=True)
        return out

    def pick(self, task: str, *, model_name: str | None = None) -> AIModel:
        
        if model_name:
            model = self.registry.get(model_name)
            check_permission(self.matrix, model, task)
            return model
        cand = self.candidates_for(task)
        if not cand:
            raise ModelRouteError(
                code="AI_NO_CANDIDATE",
                message=f"任务 {task} 没有可用的 api_model。",
                context={"task": task},
                blocking=True,
                suggested_action="先通过 `configure ai add-model` 注册模型，或调整能力标签。",
            )
        return cand[0].model

    def run_task(
        self,
        *,
        task: str,
        prompt: str,
        model_name: str | None = None,
        system: str = "",
        related_issue_id: str = "",
        enable_tools: bool = False,
        max_tool_turns: int = 4,
    ) -> dict[str, Any]:
        
        model = self.pick(task, model_name=model_name)
        api_key = _api_key_for(model)

        if enable_tools:
            raw = self._chat_completion_with_tools(
                model,
                api_key,
                task=task,
                system=system,
                user=prompt,
                max_tool_turns=max_tool_turns,
                related_issue_id=related_issue_id,
            )
        else:
            raw = self._chat_completion(model, api_key, system=system, user=prompt)

        payload = self._parse_json_payload(raw)
        payload = self._unwrap_task_envelope(task, payload)
        
        payload.setdefault("task", task)
        payload.setdefault("model_name", model.name)
        if related_issue_id:
            payload.setdefault("related_issue_id", related_issue_id)
        
        try:
            validated = validate_ai_output(task, payload)
        except ContractViolation as exc:
            
            exc.context = {**exc.context, "model_name": model.name}
            raise
        return validated.model_dump(mode="json")

    @staticmethod
    def _unwrap_task_envelope(task: str, payload: dict[str, Any]) -> dict[str, Any]:
        
        nested = payload.get(task)
        if len(payload) == 1 and isinstance(nested, dict):
            return dict(nested)
        return payload

    def _chat_completion(
        self,
        model: AIModel,
        api_key: str,
        *,
        system: str,
        user: str,
    ) -> str:
        
        try:
            import httpx  
        except ImportError as exc:  
            raise ModelRouteError(
                code="AI_HTTP_DEPENDENCY_MISSING",
                message="未安装 httpx，无法调用 OpenAI 兼容接口。",
                blocking=True,
                suggested_action="在虚拟环境中安装 httpx：pip install httpx",
            ) from exc

        url = model.provider_endpoint.rstrip("/") + "/chat/completions"
        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": user})
        body = {
            "model": model.api_model_id,
            "messages": messages,
            "response_format": {"type": "json_object"},
        }
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        try:
            with create_http_client(timeout=self.request_timeout) as client:
                resp = client.post(url, headers=headers, json=body)
        except httpx.HTTPError as exc:
            raise ModelRouteError(
                code="AI_HTTP_TRANSPORT_ERROR",
                message=f"调用 {model.name} 失败：{exc}",
                context={"model": model.name, "url": url},
                blocking=True,
                suggested_action="检查网络与 endpoint；本框架不会自动切换其它模型。",
            ) from exc

        if resp.status_code >= 400:
            raise ModelRouteError(
                code="AI_HTTP_STATUS_ERROR",
                message=f"{model.name} 返回 HTTP {resp.status_code}: {resp.text[:500]}",
                context={"model": model.name, "status": resp.status_code},
                blocking=True,
            )

        try:
            data = resp.json()
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, ValueError, TypeError) as exc:
            raise ModelRouteError(
                code="AI_HTTP_RESPONSE_INVALID",
                message=f"{model.name} 响应格式异常：{exc}",
                context={"model": model.name, "raw": resp.text[:500]},
                blocking=True,
            ) from exc

    @staticmethod
    def _parse_json_payload(raw: str) -> dict[str, Any]:
        
        cleaned = _strip_model_wrapping(raw)
        try:
            payload = json.loads(cleaned)
        except json.JSONDecodeError as exc:
            raise ModelRouteError(
                code="AI_OUTPUT_NOT_JSON",
                message=f"模型未返回合法 JSON：{exc}",
                context={"raw": raw[:500]},
                blocking=True,
                suggested_action="确认请求中传入了 response_format=json_object，或 prompt 里明确要求 JSON 输出。",
            ) from exc
        if not isinstance(payload, dict):
            raise ModelRouteError(
                code="AI_OUTPUT_NOT_OBJECT",
                message="模型返回的 JSON 不是对象。",
                context={"raw": raw[:500]},
                blocking=True,
            )
        return payload

    def _chat_completion_with_tools(
        self,
        model: AIModel,
        api_key: str,
        *,
        task: str,
        system: str,
        user: str,
        max_tool_turns: int,
        related_issue_id: str,
    ) -> str:
        
        try:
            import httpx
        except ImportError as exc:  
            raise ModelRouteError(
                code="AI_HTTP_DEPENDENCY_MISSING",
                message="未安装 httpx，无法调用 OpenAI 兼容接口。",
                blocking=True,
                suggested_action="pip install httpx",
            ) from exc

        from .tools import all_tool_specs, get_tool

        tool_specs = all_tool_specs()
        url = model.provider_endpoint.rstrip("/") + "/chat/completions"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        
        sys_prompt = ((system + "\n\n") if system else "") + (
            "你可以调用 web_search / web_fetch 检索公开互联网信息。"
            "工具结果可能被截断（truncated=true 时只返回前 N 字符）。"
            "信息不足时显式告知，**禁止凭记忆编造**。"
            "最终输出必须是合法 JSON 对象，对应当前任务 schema。"
        )
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": user},
        ]

        for turn in range(max_tool_turns + 1):
            body = {
                "model": model.api_model_id,
                "messages": messages,
                "tools": tool_specs,
                "tool_choice": "auto",
            }
            
            if turn == max_tool_turns:
                body["tool_choice"] = "none"
                body["response_format"] = {"type": "json_object"}
            try:
                with create_http_client(timeout=self.request_timeout) as client:
                    resp = client.post(url, headers=headers, json=body)
            except httpx.HTTPError as exc:
                raise ModelRouteError(
                    code="AI_HTTP_TRANSPORT_ERROR",
                    message=f"调用 {model.name} 失败：{exc}",
                    context={"model": model.name, "url": url},
                    blocking=True,
                ) from exc
            if resp.status_code >= 400:
                raise ModelRouteError(
                    code="AI_HTTP_STATUS_ERROR",
                    message=f"{model.name} 返回 HTTP {resp.status_code}: {resp.text[:500]}",
                    context={"model": model.name, "status": resp.status_code},
                    blocking=True,
                )
            try:
                data = resp.json()
                msg = data["choices"][0]["message"]
            except (KeyError, IndexError, ValueError, TypeError) as exc:
                raise ModelRouteError(
                    code="AI_HTTP_RESPONSE_INVALID",
                    message=f"{model.name} 响应格式异常：{exc}",
                    context={"model": model.name, "raw": resp.text[:500]},
                    blocking=True,
                ) from exc

            tool_calls = msg.get("tool_calls") or []
            if not tool_calls:
                content = msg.get("content") or ""
                if not content:
                    raise ModelRouteError(
                        code="AI_OUTPUT_EMPTY",
                        message=f"{model.name} 未返回最终内容。",
                        blocking=True,
                    )
                return content

            messages.append(
                {
                    "role": "assistant",
                    "content": msg.get("content") or "",
                    "tool_calls": tool_calls,
                }
            )
            for call in tool_calls:
                fn = (call.get("function") or {}).get("name", "")
                tool = get_tool(fn)
                args_raw = (call.get("function") or {}).get("arguments") or "{}"
                try:
                    arguments = (
                        json.loads(args_raw) if isinstance(args_raw, str) else dict(args_raw)
                    )
                except json.JSONDecodeError as exc:
                    raise ModelRouteError(
                        code="AI_TOOL_ARGUMENTS_INVALID",
                        message=f"工具 {fn} 参数非法 JSON：{exc}",
                        context={"raw": args_raw[:200]},
                        blocking=True,
                    ) from exc
                if tool is None:
                    raise ModelRouteError(
                        code="AI_TOOL_UNKNOWN",
                        message=f"模型请求未知工具：{fn}",
                        context={"tool": fn},
                        blocking=True,
                    )
                try:
                    result = tool.run(arguments)
                except ModelRouteError:
                    raise
                except Exception as exc:  
                    raise ModelRouteError(
                        code="AI_TOOL_RUNTIME",
                        message=f"工具 {fn} 执行失败：{exc}",
                        context={"tool": fn, "arguments": arguments},
                        blocking=True,
                    ) from exc

                _audit_tool_call(
                    task=task,
                    related_issue_id=related_issue_id,
                    model_name=model.name,
                    tool=fn,
                    arguments=arguments,
                    result=result,
                )
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.get("id") or "",
                        "name": fn,
                        "content": json.dumps(result, ensure_ascii=False),
                    }
                )
        
        raise ModelRouteError(
            code="AI_TOOL_LOOP_EXHAUSTED",
            message=f"模型在 {max_tool_turns} 轮工具调用后仍未输出最终结果。",
            context={"task": task, "model": model.name},
            blocking=True,
        )

def _audit_tool_call(
    *,
    task: str,
    related_issue_id: str,
    model_name: str,
    tool: str,
    arguments: dict[str, Any],
    result: dict[str, Any],
) -> None:
    
    from datetime import datetime

    from paths import workspace

    runs_dir = workspace().runs / task
    runs_dir.mkdir(parents=True, exist_ok=True)
    record = {
        "ts": datetime.now(UTC).isoformat(),
        "task": task,
        "issue_id": related_issue_id,
        "model": model_name,
        "tool": tool,
        "arguments": arguments,
        "result_summary": _summarize_result(result),
    }
    line = json.dumps(record, ensure_ascii=False)
    with (runs_dir / "tools.jsonl").open("a", encoding="utf-8") as fp:
        fp.write(line + "\n")

def _summarize_result(result: dict[str, Any]) -> dict[str, Any]:
    
    summary: dict[str, Any] = {}
    for key in ("url", "title", "truncated", "backend", "query"):
        if key in result:
            summary[key] = result[key]
    if "content_markdown" in result:
        summary["content_chars"] = len(result["content_markdown"])
    if "results" in result and isinstance(result["results"], list):
        summary["result_count"] = len(result["results"])
    return summary

__all__ = ["AIRouter", "RouteDecision", "env_var_name"]
