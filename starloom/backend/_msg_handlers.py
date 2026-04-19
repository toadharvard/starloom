"""Message handlers for Claude CLI streaming JSON protocol.

Each handler processes one message type from the claude subprocess
stream. All are module-level async functions — no class dependency.
"""

from __future__ import annotations

import time

from starloom.backend._stream_parser import (
    BlockState,
    StreamAccumulator,
    get_content_blocks,
    parse_cost_usd,
    parse_usage,
)
from starloom.backend.event_translation import (
    emit_agent_text,
    emit_tool_call_end,
    emit_tool_call_start,
    tool_input_preview,
)
from starloom.events import EventBus


async def handle_assistant(
    msg: dict[str, object],
    stream: StreamAccumulator,
    block: BlockState,
    node_id: str,
    bus: EventBus,
) -> None:
    """Handle assistant turn messages with inline content blocks."""
    for blk in get_content_blocks(msg):
        blk_type = blk.get("type", "")
        if blk_type == "tool_use":
            await _emit_inline_tool(blk, stream, node_id, bus)
        elif blk_type == "text":
            await _emit_text(str(blk.get("text", "")), stream, node_id, bus)


async def _emit_inline_tool(
    blk: dict[str, object],
    stream: StreamAccumulator,
    node_id: str,
    bus: EventBus,
) -> None:
    stream.call_seq += 1
    name = str(blk.get("name", "unknown"))
    tool_id = blk.get("id")
    if isinstance(tool_id, str):
        stream.pending_tools[tool_id] = name
    await emit_tool_call_start(
        bus,
        node_id=node_id,
        tool=name,
        input_preview=tool_input_preview(blk.get("input", {})),
    )


async def _emit_text(
    text: str,
    stream: StreamAccumulator,
    node_id: str,
    bus: EventBus,
) -> None:
    if not text.strip():
        return
    stream.reasoning.append(text)
    await emit_agent_text(bus, node_id=node_id, text=text)


async def handle_user(
    msg: dict[str, object],
    stream: StreamAccumulator,
    block: BlockState,
    node_id: str,
    bus: EventBus,
) -> None:
    """Handle user turn messages (tool results from CLI)."""
    for blk in get_content_blocks(msg):
        if blk.get("type") == "tool_result":
            await _emit_tool_end(blk, stream, node_id, bus)


async def _emit_tool_end(
    blk: dict[str, object],
    stream: StreamAccumulator,
    node_id: str,
    bus: EventBus,
) -> None:
    tool_use_id = blk.get("tool_use_id")
    output = str(blk.get("content", ""))
    name = (
        stream.pending_tools.pop(tool_use_id, "unknown")
        if isinstance(tool_use_id, str)
        else "unknown"
    )
    await emit_tool_call_end(
        bus,
        node_id=node_id,
        tool=name,
        output_preview=output,
    )


async def handle_block_start(
    msg: dict[str, object],
    stream: StreamAccumulator,
    block: BlockState,
    node_id: str,
    bus: EventBus,
) -> None:
    """Initialize block state on content_block_start."""
    cb = msg.get("content_block", {})
    if not isinstance(cb, dict):
        return
    cb_type = cb.get("type")
    block.block_type = str(cb_type) if cb_type is not None else None
    if block.block_type == "tool_use":
        _init_tool_block(block, cb)
    elif block.block_type == "text":
        block.text_buffer = ""


def _init_tool_block(
    block: BlockState,
    cb: dict[str, object],
) -> None:
    """Set up block state for a tool_use content block."""
    block.tool_name = str(cb.get("name", "unknown"))
    cb_id = cb.get("id")
    block.tool_id = str(cb_id) if cb_id is not None else None
    block.input_json = ""
    block.tool_start_time = time.time()


async def handle_block_delta(
    msg: dict[str, object],
    stream: StreamAccumulator,
    block: BlockState,
    node_id: str,
    bus: EventBus,
) -> None:
    """Accumulate delta content for the current block."""
    delta = msg.get("delta", {})
    if not isinstance(delta, dict):
        return
    delta_type = delta.get("type")
    if delta_type == "text_delta":
        await _handle_text_delta(delta, block, node_id, bus)
    elif delta_type == "input_json_delta":
        block.input_json += str(delta.get("partial_json", ""))


async def _handle_text_delta(
    delta: dict[str, object],
    block: BlockState,
    node_id: str,
    bus: EventBus,
) -> None:
    text = str(delta.get("text", ""))
    block.text_buffer += text
    if text.strip():
        await emit_agent_text(bus, node_id=node_id, text=text)


async def handle_block_stop(
    msg: dict[str, object],
    stream: StreamAccumulator,
    block: BlockState,
    node_id: str,
    bus: EventBus,
) -> None:
    """Finalize a content block on content_block_stop."""
    if block.block_type == "tool_use" and block.tool_name:
        await _finalize_tool_block(stream, block, node_id, bus)
    elif block.block_type == "text" and block.text_buffer.strip():
        stream.reasoning.append(block.text_buffer)
    block.reset()


async def _finalize_tool_block(
    stream: StreamAccumulator,
    block: BlockState,
    node_id: str,
    bus: EventBus,
) -> None:
    """Emit TOOL_CALL_START from a completed streaming tool block."""
    stream.call_seq += 1
    preview = block.input_json
    tool_name = block.tool_name or "unknown"
    if isinstance(block.tool_id, str):
        stream.pending_tools[block.tool_id] = tool_name
    await emit_tool_call_start(
        bus,
        node_id=node_id,
        tool=tool_name,
        input_preview=preview,
    )


async def handle_tool_result(
    msg: dict[str, object],
    stream: StreamAccumulator,
    block: BlockState,
    node_id: str,
    bus: EventBus,
) -> None:
    """Emit TOOL_CALL_END from a top-level tool_result message."""
    tool_use_id = msg.get("tool_use_id")
    output = str(msg.get("content", ""))
    name = (
        stream.pending_tools.pop(tool_use_id, "unknown")
        if isinstance(tool_use_id, str)
        else "unknown"
    )
    await emit_tool_call_end(
        bus,
        node_id=node_id,
        tool=name,
        output_preview=output,
    )


async def handle_result(
    msg: dict[str, object],
    stream: StreamAccumulator,
    block: BlockState,
    node_id: str,
    bus: EventBus,
) -> None:
    """Extract final output, session id, usage, and inline pseudo-tool traces."""
    result_text = str(msg.get("result", ""))
    stream.final_output = result_text
    sid = msg.get("session_id")
    stream.backend_session_id = str(sid) if sid is not None else None
    msg_usage = msg.get("usage", {})
    if isinstance(msg_usage, dict):
        stream.usage = parse_usage(msg_usage)
    stream.cost_usd = parse_cost_usd(msg)
    await _emit_pseudo_tool_calls_from_result(result_text, stream, node_id, bus)


async def _emit_pseudo_tool_calls_from_result(
    result_text: str,
    stream: StreamAccumulator,
    node_id: str,
    bus: EventBus,
) -> None:
    """Fallback parser for print-mode function_calls blocks embedded in final text."""
    import json
    import re

    blocks = re.findall(
        r"<function_calls>\s*(.*?)\s*</function_calls>", result_text, re.DOTALL
    )
    for raw_block in blocks:
        block_text = raw_block.strip()
        if not block_text:
            continue
        try:
            payload = json.loads(block_text)
        except (json.JSONDecodeError, ValueError):
            continue
        if not isinstance(payload, list):
            continue
        for item in payload:
            if not isinstance(item, dict):
                continue
            tool_name = item.get("tool") or item.get("tool_name")
            arguments = item.get("arguments", {})
            if not isinstance(tool_name, str):
                continue
            await emit_tool_call_start(
                bus,
                node_id=node_id,
                tool=tool_name,
                input_preview=tool_input_preview(arguments),
            )
            await emit_tool_call_end(
                bus,
                node_id=node_id,
                tool=tool_name,
                output_preview="[embedded in Claude result text]",
            )
