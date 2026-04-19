"""Typed event data — replaces dict[str, Any] on Event.data.

Each event type has a corresponding frozen dataclass. The EventData union
ensures exhaustive matching at module boundaries.
"""

from __future__ import annotations

from dataclasses import Field, dataclass, fields as dc_fields
from typing import cast, get_args, get_origin

from starloom.types import (
    AgentSpecData,
    CheckpointKind,
    DecisionKind,
    NodeStatus,
)


# ---------------------------------------------------------------------------
# Workflow events
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class WorkflowStartData:
    workflow_file: str
    params: dict[str, str]


@dataclass(frozen=True, slots=True)
class WorkflowOutputData:
    output: str | None


@dataclass(frozen=True, slots=True)
class WorkflowEndData:
    duration: float
    total_cost_usd: float | None
    node_count: int
    error: str | None


# ---------------------------------------------------------------------------
# Node events
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class NodeAddedData:
    prompt_preview: str
    kind: str
    parallel_group: str | None = None
    backend_name: str | None = None


@dataclass(frozen=True, slots=True)
class NodeStartedData:
    pass


@dataclass(frozen=True, slots=True)
class NodeFinishedData:
    result: str
    cost_usd: float | None
    input_tokens: int | None
    output_tokens: int | None
    backend_name: str | None = None


@dataclass(frozen=True, slots=True)
class NodeErrorData:
    error: str


@dataclass(frozen=True, slots=True)
class NodeSkippedData:
    reason: str


@dataclass(frozen=True, slots=True)
class NodeCachedData:
    result: str


@dataclass(frozen=True, slots=True)
class NodeStoppedData:
    reason: str = ""


# ---------------------------------------------------------------------------
# Agent observability
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ToolCallStartData:
    tool: str
    input_preview: str


@dataclass(frozen=True, slots=True)
class ToolCallEndData:
    tool: str
    output_preview: str


@dataclass(frozen=True, slots=True)
class AgentTextData:
    text: str


# ---------------------------------------------------------------------------
# Checkpoint events
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CheckpointPendingData:
    checkpoint_id: str
    kind: CheckpointKind
    node_id: str
    description: str
    tool: str | None = None
    tool_input_preview: str | None = None
    spec: AgentSpecData | None = None
    context: str = ""


@dataclass(frozen=True, slots=True)
class CheckpointResolvedData:
    checkpoint_id: str
    decision: DecisionKind
    decided_by: str | None = None


# ---------------------------------------------------------------------------
# Cost events
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CostUpdateData:
    node_cost_usd: float | None
    input_tokens: int | None
    output_tokens: int | None
    session_total_usd: float | None


# ---------------------------------------------------------------------------
# Union
# ---------------------------------------------------------------------------

EventData = (
    WorkflowStartData
    | WorkflowOutputData
    | WorkflowEndData
    | NodeAddedData
    | NodeStartedData
    | NodeFinishedData
    | NodeErrorData
    | NodeSkippedData
    | NodeCachedData
    | NodeStoppedData
    | ToolCallStartData
    | ToolCallEndData
    | AgentTextData
    | CheckpointPendingData
    | CheckpointResolvedData
    | CostUpdateData
)


# ---------------------------------------------------------------------------
# Deserialization: dict → typed EventData
# ---------------------------------------------------------------------------

_EVENT_TYPE_TO_DATA: dict[str, type[EventData]] = {
    "workflow.start": WorkflowStartData,
    "workflow.output": WorkflowOutputData,
    "workflow.end": WorkflowEndData,
    "node.added": NodeAddedData,
    "node.started": NodeStartedData,
    "node.finished": NodeFinishedData,
    "node.error": NodeErrorData,
    "node.skipped": NodeSkippedData,
    "node.cached": NodeCachedData,
    "node.stopped": NodeStoppedData,
    "tool.call.start": ToolCallStartData,
    "tool.call.end": ToolCallEndData,
    "agent.text": AgentTextData,
    "checkpoint.pending": CheckpointPendingData,
    "checkpoint.resolved": CheckpointResolvedData,
    "cost.update": CostUpdateData,
}


def event_data_from_dict(
    event_type: str,
    raw: dict[str, object],
) -> EventData | None:
    """Reconstruct typed EventData from a serialized dict.

    Returns None if event_type is unknown or raw is empty.
    """
    cls = _EVENT_TYPE_TO_DATA.get(event_type)
    if cls is None or not raw:
        return None
    return _construct_data(cls, raw)


def _construct_data(cls: type, raw: dict[str, object]) -> EventData:
    """Instantiate a dataclass from a raw dict, coercing enum fields."""
    kwargs: dict[str, object] = {}
    for f in dc_fields(cls):
        if f.name not in raw:
            continue
        kwargs[f.name] = _coerce_field(f, raw[f.name])
    return cast(EventData, cls(**kwargs))


def _coerce_field(f: "Field[object]", val: object) -> object:
    """Coerce a raw value to the field's declared type (enums, nested)."""
    field_type = _concrete_field_type(f.type)
    if field_type is None:
        return val
    if _is_enum(field_type) and isinstance(val, str):
        return field_type(val)
    if _is_dataclass(field_type) and isinstance(val, dict):
        return _construct_data(field_type, val)
    return val


def _concrete_field_type(field_type: object) -> type | None:
    if isinstance(field_type, str):
        return _resolve_forward_ref(field_type)
    origin = get_origin(field_type)
    if origin is None:
        return field_type if isinstance(field_type, type) else None
    for arg in get_args(field_type):
        if arg is type(None):
            continue
        resolved = _concrete_field_type(arg)
        if resolved is not None:
            return resolved
    return None


def _resolve_forward_ref(name: str) -> type | None:
    """Resolve string type annotations for known types."""
    from starloom.types import CheckpointKind, DecisionKind

    if "|" in name:
        for part in (segment.strip() for segment in name.split("|")):
            if part == "None":
                continue
            resolved = _resolve_forward_ref(part)
            if resolved is not None:
                return resolved
        return None

    _known = {
        "CheckpointKind": CheckpointKind,
        "DecisionKind": DecisionKind,
        "NodeStatus": NodeStatus,
        "AgentSpecData": AgentSpecData,
    }
    return _known.get(name)


def _is_enum(t: type) -> bool:
    from enum import Enum

    return isinstance(t, type) and issubclass(t, Enum)


def _is_dataclass(t: type) -> bool:
    return hasattr(t, "__dataclass_fields__")
