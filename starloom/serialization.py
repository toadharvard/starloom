"""Concrete JSON mapping helpers shared across event, graph, and wire layers."""

from __future__ import annotations

from dataclasses import fields
from enum import Enum
from typing import Any

from starloom.types import AgentSpecData, NodePatch


def json_ready(value: Any) -> Any:
    """Convert concrete project values into JSON-ready primitives."""
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, tuple):
        return [json_ready(v) for v in value]
    if isinstance(value, list):
        return [json_ready(v) for v in value]
    if isinstance(value, dict):
        return {str(k): json_ready(v) for k, v in value.items()}
    if hasattr(value, "__dataclass_fields__"):
        return dataclass_to_dict(value)
    return value


def dataclass_to_dict(obj: Any) -> dict[str, Any]:
    """Convert a dataclass instance to a plain JSON-ready dict."""
    return {f.name: json_ready(getattr(obj, f.name)) for f in fields(obj)}


def agent_spec_to_dict(spec: AgentSpecData) -> dict[str, object]:
    return {
        "prompt": spec.prompt,
        "flags": spec.flags,
    }


def agent_spec_from_dict(raw: dict[str, object]) -> AgentSpecData:
    return AgentSpecData(
        prompt=str(raw["prompt"]),
        flags=_opt_str(raw.get("flags")) or "",
    )


def node_patch_to_dict(patch: NodePatch) -> dict[str, object]:
    return {
        "prompt": patch.prompt,
        "flags": patch.flags,
    }


def node_patch_from_dict(raw: dict[str, object] | object) -> NodePatch:
    if not isinstance(raw, dict):
        return NodePatch()
    return NodePatch(
        prompt=_opt_str(raw.get("prompt")),
        flags=_opt_str(raw.get("flags")),
    )


def _opt_str(value: object) -> str | None:
    return str(value) if value is not None else None
