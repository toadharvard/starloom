"""SnapshotBuilder -- events to SessionSnapshot (pure state machine)."""

from __future__ import annotations

import time
from dataclasses import dataclass

from starloom.event_data import (
    CheckpointPendingData,
    CheckpointResolvedData,
    CostUpdateData,
    NodeAddedData,
    NodeErrorData,
    NodeFinishedData,
    WorkflowStartData,
)
from starloom.events import Event
from starloom.types import EventType, NodeStatus
from starloom.ui.protocol import (
    CheckpointSnapshot,
    NodeSnapshot,
    SessionSnapshot,
)


# ---------------------------------------------------------------------------
# Internal mutable node state
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class _NodeState:
    """Mutable accumulator for a single node."""

    node_id: str
    seq: int
    kind: str
    status: NodeStatus = NodeStatus.PENDING
    prompt_preview: str = ""
    start_time: float | None = None
    elapsed: float | None = None
    cost_usd: float | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    parallel_group: str | None = None
    error: str | None = None


# ---------------------------------------------------------------------------
# Event-type dispatch table
# ---------------------------------------------------------------------------

_DISPATCH: dict[EventType, str] = {
    EventType.WORKFLOW_START: "_on_workflow_start",
    EventType.NODE_ADDED: "_on_node_added",
    EventType.NODE_STARTED: "_on_node_started",
    EventType.NODE_FINISHED: "_on_node_finished",
    EventType.NODE_ERROR: "_on_node_error",
    EventType.NODE_SKIPPED: "_on_node_status",
    EventType.NODE_CACHED: "_on_node_status",
    EventType.NODE_STOPPED: "_on_node_status",
    EventType.CHECKPOINT_PENDING: "_on_checkpoint_pending",
    EventType.CHECKPOINT_RESOLVED: "_on_checkpoint_resolved",
    EventType.COST_UPDATE: "_on_cost_update",
}

_STATUS_MAP: dict[EventType, NodeStatus] = {
    EventType.NODE_SKIPPED: NodeStatus.SKIPPED,
    EventType.NODE_CACHED: NodeStatus.CACHED,
    EventType.NODE_STOPPED: NodeStatus.STOPPED,
}


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------


class SnapshotBuilder:
    """Build SessionSnapshot from an event stream.

    Pure state machine: call ``handle_event`` for each event, then
    ``snapshot()`` at any time to get an immutable view.
    """

    def __init__(self, session_id: str) -> None:
        self._session_id = session_id
        self._workflow_file = ""
        self._start_time: float | None = None
        self._last_event_time: float | None = None
        self._total_cost: float | None = None
        self._nodes: dict[str, _NodeState] = {}
        self._checkpoints: dict[str, CheckpointSnapshot] = {}
        self._seq_counter = 0

    # -- public API --------------------------------------------------------

    @property
    def start_time(self) -> float | None:
        """Wall-clock timestamp of WORKFLOW_START, or None if not seen yet."""
        return self._start_time

    def handle_event(self, event: Event) -> None:
        """Dispatch *event* to the appropriate handler."""
        self._last_event_time = event.timestamp
        method_name = _DISPATCH.get(event.type)
        if method_name is not None:
            getattr(self, method_name)(event)

    def snapshot(self) -> SessionSnapshot:
        """Return an immutable snapshot of the current state."""
        elapsed = self._elapsed()
        nodes = tuple(self._node_snapshot(n) for n in self._nodes.values())
        checkpoints = tuple(self._checkpoints.values())
        return SessionSnapshot(
            session_id=self._session_id,
            workflow_file=self._workflow_file,
            elapsed=elapsed,
            total_cost_usd=self._total_cost,
            nodes=nodes,
            pending_checkpoints=checkpoints,
        )

    # -- private handlers --------------------------------------------------

    def _on_workflow_start(self, event: Event) -> None:
        assert isinstance(event.data, WorkflowStartData)
        self._workflow_file = event.data.workflow_file
        self._start_time = event.timestamp

    def _on_node_added(self, event: Event) -> None:
        assert isinstance(event.data, NodeAddedData)
        node_id = event.node_id or ""
        self._seq_counter += 1
        self._nodes[node_id] = _NodeState(
            node_id=node_id,
            seq=self._seq_counter,
            kind=event.data.kind,
            prompt_preview=event.data.prompt_preview,
            parallel_group=event.data.parallel_group,
        )

    def _on_node_started(self, event: Event) -> None:
        node = self._get_node(event)
        if node is None:
            return
        node.status = NodeStatus.RUNNING
        node.start_time = event.timestamp

    def _on_node_finished(self, event: Event) -> None:
        node = self._get_node(event)
        if node is None:
            return
        assert isinstance(event.data, NodeFinishedData)
        node.status = NodeStatus.COMPLETED
        node.cost_usd = event.data.cost_usd
        node.input_tokens = event.data.input_tokens
        node.output_tokens = event.data.output_tokens
        if node.start_time is not None:
            node.elapsed = event.timestamp - node.start_time

    def _on_node_error(self, event: Event) -> None:
        node = self._get_node(event)
        if node is None:
            return
        assert isinstance(event.data, NodeErrorData)
        node.status = NodeStatus.ERROR
        node.error = event.data.error
        if node.start_time is not None:
            node.elapsed = event.timestamp - node.start_time

    def _on_node_status(self, event: Event) -> None:
        node = self._get_node(event)
        if node is None:
            return
        node.status = _STATUS_MAP[event.type]

    def _on_checkpoint_pending(self, event: Event) -> None:
        assert isinstance(event.data, CheckpointPendingData)
        self._checkpoints[event.data.checkpoint_id] = CheckpointSnapshot(
            checkpoint_id=event.data.checkpoint_id,
            kind=event.data.kind,
            node_id=event.data.node_id,
            description=event.data.description,
        )

    def _on_checkpoint_resolved(self, event: Event) -> None:
        assert isinstance(event.data, CheckpointResolvedData)
        self._checkpoints.pop(event.data.checkpoint_id, None)

    def _on_cost_update(self, event: Event) -> None:
        assert isinstance(event.data, CostUpdateData)
        self._total_cost = event.data.session_total_usd

    # -- helpers -----------------------------------------------------------

    def _get_node(self, event: Event) -> _NodeState | None:
        return self._nodes.get(event.node_id or "")

    def _elapsed(self) -> float:
        if self._start_time is None:
            return 0.0
        end = self._last_event_time or time.time()
        return end - self._start_time

    @staticmethod
    def _node_snapshot(state: _NodeState) -> NodeSnapshot:
        return NodeSnapshot(
            node_id=state.node_id,
            seq=state.seq,
            kind=state.kind,
            status=state.status,
            prompt_preview=state.prompt_preview,
            elapsed=state.elapsed,
            cost_usd=state.cost_usd,
            input_tokens=state.input_tokens,
            output_tokens=state.output_tokens,
            parallel_group=state.parallel_group,
            error=state.error,
        )
