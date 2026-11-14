from __future__ import annotations

import html as _html
import http.client
import re
from defusedxml import ElementTree as ET
from defusedxml.common import DefusedXmlException
from typing import Any
from urllib.parse import urlencode

from exceptions import ModelRouteError
from .public_http import fetch_public, is_public_url

SPEC: dict[str, Any] = {
    "name": "web_search",
    "description": (
        "对公开互联网发起搜索（Bing 后端，国内外均可用），"
        "返回候选标题/URL/摘要列表。"
        "适用于查证最新事件、官方公告、术语定义等。"
        "结果是只读的，仅供模型继续 fetch 或直接阅读摘要。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "搜索关键词；尽量具体并显式带上时间 / 地区。",
            },
            "top_k": {
                "type": "integer",
                "description": "返回前 K 条；默认 5，最大 10。",
                "minimum": 1,
                "maximum": 10,
                "default": 5,
            },
        },
        "required": ["query"],
    },
}

_DEFAULT_TIMEOUT = 15.0
_BING_URL = "https://www.bing.com/search"
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

def _is_private_url(url: str) -> bool:
    return not is_public_url(url)

def _strip_tags(s: str) -> str:
    s = re.sub(r"<[^>]+>", "", s)
    return _html.unescape(s).strip()

def run(arguments: dict[str, Any]) -> dict[str, Any]:
    query = (arguments.get("query") or "").strip()
    if not query:
        raise ModelRouteError(
            code="AI_TOOL_BAD_ARGUMENT",
            message="web_search 需要非空 query。",
            context={"arguments": arguments},
            blocking=True,
        )
    top_k = int(arguments.get("top_k") or 5)
    top_k = max(1, min(top_k, 10))

    params = {"q": query, "format": "rss"}
    headers = {"User-Agent": _UA}
    try:
        status, body, _ = fetch_public(_BING_URL + "?" + urlencode(params), headers=headers, timeout=_DEFAULT_TIMEOUT)
    except (OSError, http.client.HTTPException) as exc:
        raise ModelRouteError(
            code="AI_TOOL_SEARCH_FAILED",
            message=f"web_search 请求 Bing RSS 失败：{exc}",
            context={"query": query},
            blocking=True,
            suggested_action="检查网络；本框架不会自动切换其它搜索引擎，也不会靠记忆回答。",
        ) from exc
    if status >= 400:
        raise ModelRouteError(
            code="AI_TOOL_SEARCH_STATUS",
            message=f"Bing RSS 返回 HTTP {status}",
            context={"query": query, "status": status},
            blocking=True,
        )

    try:
        root = ET.fromstring(body)
    except (ET.ParseError, DefusedXmlException) as exc:
        raise ModelRouteError(
            code="AI_TOOL_SEARCH_PARSE",
            message=f"Bing RSS 解析失败：{exc}",
            context={"query": query, "head": body[:300]},
            blocking=True,
        ) from exc

    results: list[dict[str, str]] = []
    for item in root.iter("item"):
        title = _strip_tags(item.findtext("title") or "")
        link = (item.findtext("link") or "").strip()
        description = _strip_tags(item.findtext("description") or "")
        if not link.startswith(("http://", "https://")) or _is_private_url(link):
            continue
        results.append({"title": title, "url": link, "snippet": description})
        if len(results) >= top_k:
            break

    if not results:
        raise ModelRouteError(
            code="AI_TOOL_SEARCH_EMPTY",
            message=f"Bing RSS 对 query={query!r} 未返回任何结果。",
            context={"query": query},
            blocking=True,
            suggested_action="换关键词重试；不允许靠记忆推断结果。",
        )
    return {"query": query, "results": results}

__all__ = ["SPEC", "run"]
