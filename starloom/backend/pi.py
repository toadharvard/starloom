"""PiBackend -- shells out to ``pi --mode json``.

Pi exposes a JSON event stream, not Claude-style stream-json output.
This backend adapts pi's event stream to Starloom's backend protocol and
observability events.
"""

from __future__ import annotations

import asyncio
import shlex
from dataclasses import dataclass

from starloom.backend.protocol import AgentResult, StopRequestedError
from starloom.cost import TokenUsage
from starloom.event_data import AgentTextData, ToolCallEndData, ToolCallStartData
from starloom.events import EventBus
from starloom.types import AgentSpecData, EventType


@dataclass(slots=True)
class _PiStreamState:
    """Mutable accumulator for pi JSON event stream parsing."""

    output: str = ""
    usage: TokenUsage = TokenUsage()
    cost_usd: float | None = None
    backend_session_id: str | None = None
    pending_tools: dict[str, str] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.pending_tools is None:
            self.pending_tools = {}


class PiBackend:
    """Backend that shells out to ``pi`` CLI using JSON event mode."""

    def __init__(self) -> None:
        self._processes: dict[str, asyncio.subprocess.Process] = {}

    async def run(
        self,
        spec: AgentSpecData,
        node_id: str,
        bus: EventBus,
    ) -> AgentResult:
        """Spawn pi subprocess, parse event stream, return result."""
        cmd = _build_cmd(spec)
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            limit=10 * 1024 * 1024,
        )
        self._processes[node_id] = proc
        try:
            result = await _parse_stream(proc, node_id, bus)
        finally:
            self._processes.pop(node_id, None)
        return result

    async def stop(self, node_id: str) -> None:
        """Send SIGTERM to the subprocess for this node."""
        proc = self._processes.get(node_id)
        if proc and proc.returncode is None:
            proc.terminate()


def _build_cmd(spec: AgentSpecData) -> list[str]:
    """Construct pi CLI command for one-shot JSON event streaming."""
    cmd = [
        "pi",
        "--mode",
        "json",
        "--print",
        "--no-session",
        "--model",
        "codex-lb/gpt-5.4",
    ]
    if spec.flags:
        cmd.extend(shlex.split(spec.flags))
    cmd.append(spec.prompt)
    return cmd


async def _parse_stream(
    proc: asyncio.subprocess.Process,
    node_id: str,
    bus: EventBus,
) -> AgentResult:
    """Read pi JSON events and translate them to backend result/events."""
    state = _PiStreamState()
    assert proc.stdout is not None
    while True:
        raw = await proc.stdout.readline()
        if not raw:
            break
        line = raw.decode("utf-8", errors="replace").strip()
        if not line:
            continue
        event = _try_parse_json(line)
        if event is None:
            continue
        await _handle_event(event, state, node_id, bus)
    await proc.wait()
    if proc.returncode == -15:
        raise StopRequestedError("pi process terminated by stop request")
    error = await _read_stderr(proc)
    failed = bool(proc.returncode and proc.returncode != 0)
    output, final_error = _resolve_output_error(
        state.output,
        error,
        failed,
        proc.returncode,
    )
    return AgentResult(
        output=output,
        cost_usd=state.cost_usd,
        input_tokens=state.usage.input_tokens,
        output_tokens=state.usage.output_tokens,
        error=final_error if failed else None,
        backend_session_id=state.backend_session_id,
    )


async def _handle_event(
    event: dict[str, object],
    state: _PiStreamState,
    node_id: str,
    bus: EventBus,
) -> None:
    """Handle one pi JSON event object."""
    event_type = str(event.get("type", ""))
    if event_type == "session":
        sid = event.get("id")
        state.backend_session_id = str(sid) if sid is not None else None
        return
    if event_type == "message_update":
        await _handle_message_update(event, state, node_id, bus)
        return
    if event_type == "tool_execution_start":
        await _handle_tool_start(event, state, node_id, bus)
        return
    if event_type == "tool_execution_end":
        await _handle_tool_end(event, state, node_id, bus)
        return
    if event_type == "message_end":
        _handle_message_end(event, state)


async def _handle_message_update(
    event: dict[str, object],
    state: _PiStreamState,
    node_id: str,
    bus: EventBus,
) -> None:
    assistant_event = event.get("assistantMessageEvent")
    if not isinstance(assistant_event, dict):
        return
    if assistant_event.get("type") != "text_delta":
        return
    delta = str(assistant_event.get("delta", ""))
    if not delta:
        return
    await bus.emit(
        bus.make_event(
            EventType.AGENT_TEXT,
            node_id=node_id,
            data=AgentTextData(text=delta),
        )
    )


async def _handle_tool_start(
    event: dict[str, object],
    state: _PiStreamState,
    node_id: str,
    bus: EventBus,
) -> None:
    tool_call_id = str(event.get("toolCallId", ""))
    tool_name = str(event.get("toolName", "unknown"))
    args = event.get("args", {})
    state.pending_tools[tool_call_id] = tool_name
    await bus.emit(
        bus.make_event(
            EventType.TOOL_CALL_START,
            node_id=node_id,
            data=ToolCallStartData(tool=tool_name, input_preview=str(args)),
        )
    )


async def _handle_tool_end(
    event: dict[str, object],
    state: _PiStreamState,
    node_id: str,
    bus: EventBus,
) -> None:
    tool_call_id = str(event.get("toolCallId", ""))
    result = event.get("result", {})
    tool_name = state.pending_tools.pop(
        tool_call_id, str(event.get("toolName", "unknown"))
    )
    await bus.emit(
        bus.make_event(
            EventType.TOOL_CALL_END,
            node_id=node_id,
            data=ToolCallEndData(tool=tool_name, output_preview=str(result)),
        )
    )


def _handle_message_end(
    event: dict[str, object],
    state: _PiStreamState,
) -> None:
    message = event.get("message")
    if not isinstance(message, dict):
        return
    if message.get("role") != "assistant":
        return
    state.output = _extract_text(message.get("content"))
    usage = message.get("usage")
    if isinstance(usage, dict):
        state.usage = TokenUsage(
            input_tokens=_int_value(usage.get("input")),
            output_tokens=_int_value(usage.get("output")),
            cache_read_tokens=_int_value(usage.get("cacheRead")),
            cache_write_tokens=_int_value(usage.get("cacheWrite")),
        )
        state.cost_usd = _extract_cost_usd(usage.get("cost"))


def _extract_text(content: object) -> str:
    """Extract concatenated text content from a pi assistant message."""
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for item in content:
        if isinstance(item, dict) and item.get("type") == "text":
            parts.append(str(item.get("text", "")))
    return "".join(parts)


def _int_value(value: object) -> int:
    """Convert JSON scalar to int safely."""
    if isinstance(value, (int, float, str)):
        return int(value)
    return 0


def _extract_cost_usd(value: object) -> float | None:
    """Extract backend-reported total cost if present and non-zero."""
    if not isinstance(value, dict):
        return None
    total = value.get("total")
    if not isinstance(total, (int, float, str)):
        return None
    cost = float(total)
    return cost if cost > 0 else None


def _try_parse_json(line: str) -> dict[str, object] | None:
    """Parse one JSON line into an object dict."""
    import json

    try:
        value = json.loads(line)
    except (json.JSONDecodeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


async def _read_stderr(proc: asyncio.subprocess.Process) -> str | None:
    """Read stderr and return stripped text if non-empty."""
    assert proc.stderr is not None
    raw = await proc.stderr.read()
    text = raw.decode("utf-8", errors="replace").strip()
    return text or None


def _resolve_output_error(
    output: str,
    error: str | None,
    failed: bool,
    rc: int | None,
) -> tuple[str, str | None]:
    """Determine final output and error strings."""
    if failed:
        error = error or f"Process exited with code {rc}"
        return output or f"[error] {error}", error
    if not output:
        error = error or "Agent produced no output"
        return f"[error] {error}", error
    return output, error
