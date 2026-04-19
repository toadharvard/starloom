"""Checkpoint — unified pause/decide model for all interaction types.

Two sources, one protocol:
- TOOL_CALL — backend-driven tool-call interception
- CHECKPOINT — explicit checkpoint() in Starlark

CheckpointGate is pure logic (no I/O). It manages pending futures and
emits events when checkpoints are created or resolved.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass

from starloom.checkpoint_events import (
    make_checkpoint_pending_data,
    make_checkpoint_resolved_data,
)
from starloom.events import EventBus
from starloom.types import (
    AgentSpecData,
    CheckpointKind,
    DecisionKind,
    EventType,
    NodePatch,
)


# ---------------------------------------------------------------------------
# Decision types
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ApproveAction:
    checkpoint_id: str


@dataclass(frozen=True, slots=True)
class RejectAction:
    checkpoint_id: str
    reason: str = ""


@dataclass(frozen=True, slots=True)
class AnswerAction:
    checkpoint_id: str
    answer: str


Decision = ApproveAction | RejectAction | AnswerAction


# ---------------------------------------------------------------------------
# Session control actions (superset of checkpoint decisions)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PatchNodeAction:
    node_id: str
    patch: NodePatch


@dataclass(frozen=True, slots=True)
class StopNodeAction:
    node_id: str


@dataclass(frozen=True, slots=True)
class StopSessionAction:
    pass


GraphAction = (
    ApproveAction
    | RejectAction
    | AnswerAction
    | PatchNodeAction
    | StopNodeAction
    | StopSessionAction
)


# ---------------------------------------------------------------------------
# Checkpoint descriptor
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Checkpoint:
    """Describes a pending checkpoint."""

    id: str
    kind: CheckpointKind
    node_id: str
    description: str
    tool: str | None = None
    tool_input_preview: str | None = None
    spec: AgentSpecData | None = None


def make_checkpoint_id() -> str:
    return uuid.uuid4().hex[:12]


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


class InvalidDecision(Exception):
    """Raised when decision type doesn't match checkpoint kind."""


def validate_decision(checkpoint: Checkpoint, decision: Decision) -> None:
    """Ensure the decision type is valid for the checkpoint kind."""
    if checkpoint.kind == CheckpointKind.TOOL_CALL:
        if isinstance(decision, AnswerAction):
            raise InvalidDecision(
                f"checkpoint {checkpoint.id} is a tool-call checkpoint; "
                f"'answer' is only valid for workflow-authored checkpoint() pauses. "
                f"Use 'starloom checkpoint approve' or 'starloom checkpoint reject' instead."
            )
    elif checkpoint.kind == CheckpointKind.CHECKPOINT:
        if isinstance(decision, ApproveAction):
            raise InvalidDecision(
                f"checkpoint {checkpoint.id} is a workflow-authored checkpoint() pause; "
                f"'approve' is only valid for backend tool-call checkpoints. "
                f"Use 'starloom checkpoint answer {checkpoint.id} \"<text>\"' to resume the workflow, "
                f"or 'starloom checkpoint reject' to abort the checkpoint node."
            )


# ---------------------------------------------------------------------------
# Gate timeout
# ---------------------------------------------------------------------------

# 3500s = 100s safety margin below Claude CLI's 3600s hook timeout.
DEFAULT_GATE_TIMEOUT: float = 3500.0


# ---------------------------------------------------------------------------
# CheckpointGate — pure logic, no I/O
# ---------------------------------------------------------------------------


class CheckpointGate:
    """Manages pending checkpoints and their resolution.

    Pure logic: emits events via bus, resolves futures. No network I/O.
    """

    def __init__(self, bus: EventBus) -> None:
        self._bus = bus
        self._pending: dict[str, _PendingEntry] = {}

    @property
    def pending_ids(self) -> list[str]:
        return list(self._pending)

    def get_checkpoint(self, checkpoint_id: str) -> Checkpoint | None:
        entry = self._pending.get(checkpoint_id)
        return entry.checkpoint if entry else None

    async def wait(
        self,
        checkpoint: Checkpoint,
        timeout: float = DEFAULT_GATE_TIMEOUT,
    ) -> Decision:
        """Create a pending checkpoint and wait for a decision.

        On timeout, returns RejectAction (deny by default).
        """
        loop = asyncio.get_running_loop()
        future: asyncio.Future[Decision] = loop.create_future()
        self._pending[checkpoint.id] = _PendingEntry(checkpoint, future)
        await self._emit_pending(checkpoint)
        try:
            return await asyncio.wait_for(future, timeout=timeout)
        except asyncio.TimeoutError:
            self._pending.pop(checkpoint.id, None)
            return RejectAction(checkpoint_id=checkpoint.id)

    def decide(self, checkpoint_id: str, decision: Decision) -> bool:
        """Resolve a pending checkpoint. Returns True if resolved.

        Validates decision type against checkpoint kind before resolving.
        """
        entry = self._pending.get(checkpoint_id)
        if entry is None:
            return False
        validate_decision(entry.checkpoint, decision)
        if entry.future.done():
            return False
        self._pending.pop(checkpoint_id)
        entry.future.set_result(decision)
        # Fire-and-forget: decide() is sync (called from SessionServer's
        # action handler mid-await on _client_loop). Awaiting the emit
        # would deadlock because the client_loop is reading from the same
        # connection. CHECKPOINT_RESOLVED is observability-only, so
        # fire-and-forget is safe.
        asyncio.ensure_future(self._emit_resolved(entry.checkpoint, decision))
        return True

    async def _emit_pending(self, cp: Checkpoint) -> None:
        data = make_checkpoint_pending_data(cp)
        event = self._bus.make_event(
            EventType.CHECKPOINT_PENDING,
            node_id=cp.node_id,
            data=data,
        )
        await self._bus.emit(event)

    async def _emit_resolved(
        self,
        cp: Checkpoint,
        decision: Decision,
    ) -> None:
        kind = _decision_to_kind(decision)
        data = make_checkpoint_resolved_data(
            checkpoint_id=cp.id,
            decision=kind,
        )
        event = self._bus.make_event(
            EventType.CHECKPOINT_RESOLVED,
            node_id=cp.node_id,
            data=data,
        )
        await self._bus.emit(event)


@dataclass(slots=True)
class _PendingEntry:
    """Tracks a checkpoint awaiting a human decision.

    Pairs the checkpoint descriptor with its resolution future so that
    ``CheckpointGate.decide()`` can look up and complete the wait.
    """

    checkpoint: Checkpoint
    future: asyncio.Future[Decision]


def _decision_to_kind(decision: Decision) -> DecisionKind:
    if isinstance(decision, ApproveAction):
        return DecisionKind.APPROVED
    if isinstance(decision, RejectAction):
        return DecisionKind.REJECTED
    return DecisionKind.ANSWERED
