"""Enums, configs, protocols, and typed data structures.

Zero dict[str, Any] at module boundaries. Every value crossing a boundary
is a typed dataclass or enum.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Protocol, TypeVar, runtime_checkable

if TYPE_CHECKING:
    from starloom.event_data import EventData
    from starloom.events import Event


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class SessionStatus(Enum):
    """Session state machine states."""

    RUNNING = "running"
    COMPLETED = "completed"
    ERROR = "error"
    STOPPED = "stopped"


class NodeStatus(Enum):
    """Lifecycle states for a trace node."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    ERROR = "error"
    SKIPPED = "skipped"
    CACHED = "cached"
    STOPPED = "stopped"


class CheckpointKind(Enum):
    """Source of a checkpoint."""

    TOOL_CALL = "tool_call"
    CHECKPOINT = "checkpoint"


class DecisionKind(Enum):
    """How a checkpoint was resolved."""

    APPROVED = "approved"
    REJECTED = "rejected"
    ANSWERED = "answered"


class EventType(Enum):
    """All event types emitted by the system."""

    # Workflow lifecycle
    WORKFLOW_START = "workflow.start"
    WORKFLOW_OUTPUT = "workflow.output"
    WORKFLOW_END = "workflow.end"

    # Node lifecycle
    NODE_ADDED = "node.added"
    NODE_STARTED = "node.started"
    NODE_FINISHED = "node.finished"
    NODE_ERROR = "node.error"
    NODE_SKIPPED = "node.skipped"
    NODE_CACHED = "node.cached"
    NODE_STOPPED = "node.stopped"

    # Agent observability
    TOOL_CALL_START = "tool.call.start"
    TOOL_CALL_END = "tool.call.end"
    AGENT_TEXT = "agent.text"

    # Checkpoints
    CHECKPOINT_PENDING = "checkpoint.pending"
    CHECKPOINT_RESOLVED = "checkpoint.resolved"

    # Cost / usage telemetry
    COST_UPDATE = "cost.update"


class ParamType(Enum):
    """Supported workflow parameter types."""

    STRING = "string"
    INT = "int"
    FLOAT = "float"
    BOOL = "bool"
    LIST = "list"


# ---------------------------------------------------------------------------
# Protocols
# ---------------------------------------------------------------------------


@runtime_checkable
class EventEmitter(Protocol):
    """Minimal interface for emitting events — decouples foundation from EventBus."""

    @property
    def session_id(self) -> str: ...

    def make_event(
        self,
        event_type: EventType,
        node_id: str | None = None,
        seq: int | None = None,
        data: EventData | None = None,
    ) -> Event: ...

    async def emit(self, event: Event) -> None: ...


# ---------------------------------------------------------------------------
# Typed data structures
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AgentSpecData:
    """Immutable agent specification — what to run and how."""

    prompt: str
    flags: str = ""

    @property
    def prompt_preview(self) -> str:
        """First line of prompt (agent name for display)."""
        first_line = self.prompt.split("\n", 1)[0]
        return first_line


@dataclass(frozen=True, slots=True)
class NodePatch:
    """Mutation applied to a node before (re-)execution.

    Only non-None fields override the original spec.
    """

    prompt: str | None = None
    flags: str | None = None

    def apply(self, spec: AgentSpecData) -> AgentSpecData:
        """Return a new spec with patch fields overriding originals."""
        return AgentSpecData(
            prompt=self.prompt if self.prompt is not None else spec.prompt,
            flags=_pick(self.flags, spec.flags),
        )


_T = TypeVar("_T")


def _pick(override: _T | None, original: _T) -> _T:
    """Return override if not None, else original."""
    return override if override is not None else original


@dataclass(frozen=True, slots=True)
class WorkflowConfig:
    """Immutable configuration for a workflow execution."""

    workflow_file: str
    params: dict[str, str]
    backend: str = "claude"
    dry_run: bool = False
    events: bool = False
    challenge: bool = False
