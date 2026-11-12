from __future__ import annotations

import ipaddress
from typing import Any
from urllib.parse import urlparse

from exceptions import ModelRouteError

from ..deps import ensure_module

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
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    if parsed.scheme not in {"http", "https"}:
        return False
    host = parsed.hostname or ""
    if not host or host == "localhost" or host.endswith(".local"):
        return False
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return True
    return not (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved)

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

    try:
        import httpx
    except ImportError as exc:
        raise ModelRouteError(
            code="AI_TOOL_DEPENDENCY_MISSING",
            message="未安装 httpx，无法 fetch URL。",
            blocking=True,
            suggested_action="pip install httpx",
        ) from exc

    trafilatura = ensure_module("trafilatura")

    from net import create_http_client

    headers = {
        "User-Agent": _UA,
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }
    try:
        with create_http_client(timeout=_DEFAULT_TIMEOUT, follow_redirects=True) as client:
            resp = client.get(url, headers=headers)
    except httpx.HTTPError as exc:
        raise ModelRouteError(
            code="AI_TOOL_FETCH_TRANSPORT",
            message=f"web_fetch 传输失败：{exc}",
            context={"url": url},
            blocking=True,
        ) from exc
    if resp.status_code >= 400:
        raise ModelRouteError(
            code="AI_TOOL_FETCH_STATUS",
            message=f"web_fetch HTTP {resp.status_code}",
            context={"url": url, "status": resp.status_code},
            blocking=True,
        )

    html_text = resp.text
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
            context={"url": url, "status": resp.status_code, "bytes": len(html_text)},
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
        "backend": "httpx+trafilatura",
    }

__all__ = ["SPEC", "run"]
