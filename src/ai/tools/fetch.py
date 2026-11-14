from __future__ import annotations

import http.client
from typing import Any

from exceptions import ModelRouteError

from ..deps import ensure_module
from .public_http import fetch_public, is_public_url

SPEC: dict[str, Any] = {
    "name": "web_fetch",
    "description": (
        "拉取一个公开 URL 并把正文转成 markdown 返回。"
        "结果会被截断到 max_chars；截断时 truncated=true，"
        "模型应据此判断是否需要再次精读或换链接。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "目标 URL，必须是 http/https 公网地址。",
            },
            "max_chars": {
                "type": "integer",
                "description": "返回正文最大字符数；默认 6000，最大 20000。",
                "minimum": 200,
                "maximum": 20000,
                "default": 6000,
            },
        },
        "required": ["url"],
    },
}

_DEFAULT_TIMEOUT = 15.0
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

def _is_public_http_url(url: str) -> bool:
    return is_public_url(url)

def _truncate(text: str, max_chars: int) -> tuple[str, bool]:
    if len(text) <= max_chars:
        return text, False
    return text[:max_chars], True

def run(arguments: dict[str, Any]) -> dict[str, Any]:
    url = (arguments.get("url") or "").strip()
    if not url:
        raise ModelRouteError(
            code="AI_TOOL_BAD_ARGUMENT",
            message="web_fetch 需要非空 url。",
            blocking=True,
        )
    if not _is_public_http_url(url):
        raise ModelRouteError(
            code="AI_TOOL_URL_FORBIDDEN",
            message=f"拒绝访问非公开 URL：{url}",
            context={"url": url},
            blocking=True,
            suggested_action="只允许 http/https 公网地址；私有网段 / localhost 一律拒绝。",
        )
    max_chars = int(arguments.get("max_chars") or 6000)
    max_chars = max(200, min(max_chars, 20000))

    trafilatura = ensure_module("trafilatura")

    headers = {
        "User-Agent": _UA,
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }
    try:
        status, html_text, url = fetch_public(url, headers=headers, timeout=_DEFAULT_TIMEOUT)
    except (OSError, http.client.HTTPException) as exc:
        raise ModelRouteError(
            code="AI_TOOL_FETCH_TRANSPORT",
            message=f"web_fetch 传输失败：{exc}",
            context={"url": url},
            blocking=True,
        ) from exc
    if status >= 400:
        raise ModelRouteError(
            code="AI_TOOL_FETCH_STATUS",
            message=f"web_fetch HTTP {status}",
            context={"url": url, "status": status},
            blocking=True,
        )

    extracted = trafilatura.extract(
        html_text,
        url=url,
        include_comments=False,
        include_tables=True,
        output_format="markdown",
        favor_recall=True,
    )
    if not extracted:
        raise ModelRouteError(
            code="AI_TOOL_FETCH_EMPTY",
            message=f"trafilatura 未能从 {url} 提取出可读正文。",
            context={"url": url, "status": status, "bytes": len(html_text)},
            blocking=True,
            suggested_action="换一个链接重试；不允许靠记忆推断页面内容。",
        )

    metadata = (
        trafilatura.extract_metadata(html_text)
        if hasattr(trafilatura, "extract_metadata")
        else None
    )
    title = ""
    if metadata is not None:
        title = (getattr(metadata, "title", None) or "").strip()

    truncated_text, truncated = _truncate(extracted, max_chars)
    return {
        "url": url,
        "title": title,
        "content_markdown": truncated_text,
        "truncated": truncated,
        "backend": "http.client+trafilatura",
    }

__all__ = ["SPEC", "run"]
