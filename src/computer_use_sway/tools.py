"""The MCP tool registry, assembled from the tool family modules."""

from __future__ import annotations

from typing import Any, Callable

from . import tools_input, tools_observe, tools_semantic

TOOLS: dict[str, Callable[[dict[str, Any]], Any]] = {}
TOOLS.update(tools_observe.TOOLS)
TOOLS.update(tools_input.TOOLS)
TOOLS.update(tools_semantic.TOOLS)


def register(family: dict[str, Callable[[dict[str, Any]], Any]]) -> None:
    TOOLS.update(family)
