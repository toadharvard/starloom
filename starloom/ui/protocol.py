"""UI renderer protocols, plus snapshot data types."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from starloom.checkpoint import Decision
from starloom.event_data import CheckpointPendingData
from starloom.events import Event
from starloom.types import CheckpointKind, NodeStatus


# ---------------------------------------------------------------------------
# Snapshot data types
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class NodeSnapshot:
    """Immutable point-in-time view of a single node."""

    node_id: str
    seq: int
    kind: str
    status: NodeStatus
    prompt_preview: str
    elapsed: float | None
    cost_usd: float | None
    input_tokens: int | None
    output_tokens: int | None
    parallel_group: str | None
    error: str | None


@dataclass(frozen=True, slots=True)
class CheckpointSnapshot:
    """Immutable view of a pending checkpoint."""

    checkpoint_id: str
    kind: CheckpointKind
    node_id: str
    description: str


@dataclass(frozen=True, slots=True)
class SessionSnapshot:
    """Immutable point-in-time view of a whole session."""

    session_id: str
    workflow_file: str
    elapsed: float
    total_cost_usd: float | None
    nodes: tuple[NodeSnapshot, ...]
    pending_checkpoints: tuple[CheckpointSnapshot, ...]


@runtime_checkable
class EventRenderer(Protocol):
    """Receives streamed events for display."""

    def on_event(self, event: Event) -> None: ...


@runtime_checkable
class SnapshotRenderer(Protocol):
    """Receives point-in-time session snapshots for display."""

    def on_snapshot(self, snapshot: SessionSnapshot) -> None: ...


@runtime_checkable
class ReplayRenderer(Protocol):
    """Can delimit snapshot-replay mode distinctly from live updates."""

    def begin_replay(self) -> None: ...
    def end_replay(self) -> None: ...


@runtime_checkable
class CloseRenderer(Protocol):
    """Receives session-close notifications for display."""

    def on_closed(self, reason: str) -> None: ...


@runtime_checkable
class UIRenderer(EventRenderer, Protocol):
    """Base UI renderer contract required by streaming clients."""


@runtime_checkable
class InteractiveRenderer(Protocol):
    """Can prompt the operator for checkpoint decisions."""

    async def prompt_checkpoint(
        self,
        data: CheckpointPendingData,
    ) -> Decision: ...
