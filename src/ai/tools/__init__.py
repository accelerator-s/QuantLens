from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .fetch import SPEC as FETCH_SPEC
from .fetch import run as fetch_run
from .search import SPEC as SEARCH_SPEC
from .search import run as search_run

@dataclass(frozen=True)
class Tool:
    name: str
    spec: dict[str, Any]
    run: Callable[[dict[str, Any]], dict[str, Any]]

TOOLS: dict[str, Tool] = {
    "web_search": Tool("web_search", SEARCH_SPEC, search_run),
    "web_fetch": Tool("web_fetch", FETCH_SPEC, fetch_run),
}

def get_tool(name: str) -> Tool | None:
    return TOOLS.get(name)

def all_tool_specs() -> list[dict[str, Any]]:
    
    return [{"type": "function", "function": t.spec} for t in TOOLS.values()]

__all__ = ["Tool", "TOOLS", "all_tool_specs", "get_tool"]
