"""Validate advertised tool arguments without a runtime dependency."""
from __future__ import annotations

from typing import Any

from .core import ToolError


def validate(value: Any, schema: dict[str, Any], path: str = "arguments") -> None:
    kinds = {"object": dict, "array": list, "string": str, "integer": int,
             "number": (int, float), "boolean": bool, "null": type(None)}
    kind = schema.get("type")
    if isinstance(kind, list):
        for alternative in kind:
            try:
                validate(value, dict(schema, type=alternative), path)
                return
            except ToolError:
                pass
        raise ToolError(f"{path} must be one of {kind}", code="invalid_arguments")
    if kind and (not isinstance(value, kinds[kind]) or
                 (kind in ("integer", "number") and isinstance(value, bool))):
        raise ToolError(f"{path} must be {kind}", code="invalid_arguments")
    if "enum" in schema and value not in schema["enum"]:
        raise ToolError(f"{path} must be one of {schema['enum']}", code="invalid_arguments")
    if isinstance(value, dict):
        props = schema.get("properties", {})
        for key in schema.get("required", []):
            if key not in value:
                raise ToolError(f"{path}.{key} is required", code="invalid_arguments")
        for key, item in value.items():
            extra = schema.get("additionalProperties", True)
            if key not in props and extra is False:
                raise ToolError(f"unknown {path} property: {key}", code="invalid_arguments")
            child = props.get(key, extra if isinstance(extra, dict) else {})
            validate(item, child, f"{path}.{key}")
    if isinstance(value, list):
        for i, item in enumerate(value):
            validate(item, schema.get("items", {}), f"{path}[{i}]")
    for bound, check, measured in (
        ("minimum", lambda a, b: a < b, value),
        ("maximum", lambda a, b: a > b, value),
        ("minItems", lambda a, b: a < b, len(value) if isinstance(value, list) else 0),
        ("maxItems", lambda a, b: a > b, len(value) if isinstance(value, list) else 0),
        ("maxLength", lambda a, b: a > b, len(value) if isinstance(value, str) else 0),
    ):
        if bound in schema and check(measured, schema[bound]):
            raise ToolError(f"{path} violates {bound} {schema[bound]}", code="invalid_arguments")


def arguments(name: str, value: Any) -> None:
    from .specs import tool_specs

    spec = next((s for s in tool_specs() if s["name"] == name), None)
    if spec is not None:
        validate(value, spec["inputSchema"])
