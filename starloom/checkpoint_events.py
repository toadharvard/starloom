"""Shared checkpoint event emission helpers."""

from __future__ import annotations

from typing import TYPE_CHECKING

from starloom.event_data import CheckpointPendingData, CheckpointResolvedData
from starloom.events import EventBus
from starloom.types import DecisionKind, EventType

if TYPE_CHECKING:
    from starloom.checkpoint import Checkpoint


def make_checkpoint_pending_data(
    checkpoint: "Checkpoint",
    *,
    context: str = "",
    checkpoint_id: str | None = None,
) -> CheckpointPendingData:
    return CheckpointPendingData(
        checkpoint_id=checkpoint.id if checkpoint_id is None else checkpoint_id,
        kind=checkpoint.kind,
        node_id=checkpoint.node_id,
        description=checkpoint.description,
        tool=checkpoint.tool,
        tool_input_preview=checkpoint.tool_input_preview,
        spec=checkpoint.spec,
        context=context,
    )


def make_checkpoint_resolved_data(
    *,
    checkpoint_id: str,
    decision: DecisionKind,
    decided_by: str | None = None,
) -> CheckpointResolvedData:
    return CheckpointResolvedData(
        checkpoint_id=checkpoint_id,
        decision=decision,
        decided_by=decided_by,
    )


async def emit_checkpoint_pending(
    bus: EventBus,
    *,
    event_type: EventType,
    checkpoint: "Checkpoint",
    seq: int | None = None,
    context: str = "",
    checkpoint_id: str | None = None,
) -> None:
    await bus.emit(
        bus.make_event(
            event_type,
            node_id=checkpoint.node_id,
            seq=seq,
            data=make_checkpoint_pending_data(
                checkpoint,
                context=context,
                checkpoint_id=checkpoint_id,
            ),
        )
    )


async def emit_checkpoint_resolved(
    bus: EventBus,
    *,
    event_type: EventType,
    node_id: str,
    decision: DecisionKind,
    seq: int | None = None,
    checkpoint_id: str = "",
    decided_by: str | None = None,
) -> None:
    await bus.emit(
        bus.make_event(
            event_type,
            node_id=node_id,
            seq=seq,
            data=make_checkpoint_resolved_data(
                checkpoint_id=checkpoint_id,
                decision=decision,
                decided_by=decided_by,
            ),
        )
    )
