"""Shared emit helpers used by both agents.py and workflow.py."""

from __future__ import annotations

from starloom.builtins.context import RuntimeContext
from starloom.event_data import NodeStartedData
from starloom.types import EventType


async def emit_node_started(
    ctx: RuntimeContext,
    node_id: str,
    seq: int,
) -> None:
    """Emit NODE_STARTED event."""
    await ctx.bus.emit(
        ctx.bus.make_event(
            EventType.NODE_STARTED,
            node_id=node_id,
            seq=seq,
            data=NodeStartedData(),
        )
    )
