"""Shared backend stream-to-event translation helpers."""

from __future__ import annotations

import json

from starloom.event_data import AgentTextData, ToolCallEndData, ToolCallStartData
from starloom.events import EventBus
from starloom.types import EventType


async def emit_agent_text(bus: EventBus, *, node_id: str, text: str) -> None:
    if not text.strip():
        return
    await bus.emit(
        bus.make_event(
            EventType.AGENT_TEXT,
            node_id=node_id,
            data=AgentTextData(text=text),
        )
    )


async def emit_tool_call_start(
    bus: EventBus,
    *,
    node_id: str,
    tool: str,
    input_preview: str,
) -> None:
    await bus.emit(
        bus.make_event(
            EventType.TOOL_CALL_START,
            node_id=node_id,
            data=ToolCallStartData(tool=tool, input_preview=input_preview),
        )
    )


async def emit_tool_call_end(
    bus: EventBus,
    *,
    node_id: str,
    tool: str,
    output_preview: str,
) -> None:
    await bus.emit(
        bus.make_event(
            EventType.TOOL_CALL_END,
            node_id=node_id,
            data=ToolCallEndData(tool=tool, output_preview=output_preview),
        )
    )


def tool_input_preview(raw: object) -> str:
    if isinstance(raw, dict):
        return json.dumps(raw, default=str)
    return str(raw)
