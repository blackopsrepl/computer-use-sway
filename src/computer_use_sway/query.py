"""Element queries: role synonyms and name/text matching."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from . import core

ROLE_SYNONYMS = {
    "button": {"push button", "button", "toggle button"},
    "entry": {"entry", "text", "text box", "password text", "editbar", "terminal", "search box"},
    "checkbox": {"check box", "checkbox"},
    "radio": {"radio button"},
    "link": {"link"},
    "menuitem": {"menu item", "check menu item", "radio menu item"},
    "tab": {"page tab"},
    "combobox": {"combo box"},
    "slider": {"slider", "scroll bar", "spin button"},
    "list": {"list", "list box", "tree table", "table"},
    "item": {"list item", "table cell", "tree item"},
}
MATCHES = ("exact", "contains", "regex")


@dataclass
class Query:
    role: str | None = None
    name: str | None = None
    text: str | None = None
    match: str = "contains"
    window: dict[str, Any] | None = None
    nth: int = 0


def parse(arg: Any) -> Query:
    if not isinstance(arg, dict):
        raise core.ToolError("query must be an object")
    unknown = set(arg) - {"role", "name", "text", "match", "window", "nth"}
    if unknown:
        raise core.ToolError(f"unknown query keys: {', '.join(sorted(unknown))}")
    q = Query()
    for key in ("role", "name", "text"):
        value = arg.get(key)
        if value is not None and (not isinstance(value, str) or not value):
            raise core.ToolError(f"query.{key} must be a non-empty string")
        setattr(q, key, value)
    q.match = arg.get("match", "contains")
    if q.match not in MATCHES:
        raise core.ToolError("query.match must be exact, contains, or regex")
    if q.match == "regex":
        for value in (q.name, q.text):
            if value is not None:
                try:
                    re.compile(value)
                except re.error as exc:
                    raise core.ToolError(f"invalid regex: {exc}") from exc
    window = arg.get("window")
    if window is not None and not isinstance(window, dict):
        raise core.ToolError("query.window must be a window selector object")
    q.window = window
    nth = arg.get("nth", 0)
    if isinstance(nth, bool) or not isinstance(nth, int) or nth < 0:
        raise core.ToolError("query.nth must be a non-negative integer")
    q.nth = nth
    if q.role is None and q.name is None and q.text is None:
        raise core.ToolError("query needs at least one of role, name, text")
    return q


def role_matches(q_role: str | None, node_role: str) -> bool:
    if q_role is None:
        return True
    wanted = q_role.lower()
    actual = node_role.lower()
    if wanted == actual:
        return True
    return actual in ROLE_SYNONYMS.get(wanted, set())


def string_matches(pattern: str | None, value: str | None, match: str) -> bool:
    if pattern is None:
        return True
    if not value:
        return False
    if match == "exact":
        return value.strip().lower() == pattern.strip().lower()
    if match == "regex":
        return re.search(pattern, value) is not None
    return pattern.lower() in value.lower()


def matches(q: Query, role: str, name: str | None, text: str | None) -> bool:
    if not role_matches(q.role, role):
        return False
    if not string_matches(q.name, name, q.match):
        return False
    if q.text is not None:
        return string_matches(q.text, text, q.match) or string_matches(q.text, name, q.match)
    return True
