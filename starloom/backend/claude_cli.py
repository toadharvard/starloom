"""ClaudeCLIBackend -- shells out to ``claude --output-format stream-json``.

Tool execution happens inside the subprocess. Our TOOL_CALL_START/END events
are observational, not gates. When a hook_port is provided, Claude's tool-use
hook calls our HookServer so Starloom can create tool-call checkpoints.
"""

from __future__ import annotations

import asyncio
import json
import shlex
from collections.abc import Awaitable, Callable

from starloom.backend._msg_handlers import (
    handle_assistant,
    handle_block_delta,
    handle_block_start,
    handle_block_stop,
    handle_result,
    handle_tool_result,
    handle_user,
)
from starloom.backend._stream_parser import (
    BlockState,
    StreamAccumulator,
    read_stderr,
    stdout_lines,
    try_parse_json,
)
from starloom.backend.protocol import AgentResult, StopRequestedError
from starloom.events import EventBus
from starloom.types import AgentSpecData

_HOOK_TIMEOUT_SECS = 3600

# Handler signature for stream message dispatch.
_Handler = Callable[
    [dict[str, object], StreamAccumulator, BlockState, str, EventBus],
    Awaitable[None],
]

_MSG_DISPATCH: dict[str | None, _Handler] = {
    "assistant": handle_assistant,
    "user": handle_user,
    "content_block_start": handle_block_start,
    "content_block_delta": handle_block_delta,
    "content_block_stop": handle_block_stop,
    "tool_result": handle_tool_result,
    "result": handle_result,
}


def _hook_settings_json(hook_port: int) -> str:
    """Build Claude settings JSON for the tool-use HTTP hook."""
    settings = {
        "hooks": {
            "PreToolUse": [
                {
                    "hooks": [
                        {
                            "type": "http",
                            "url": f"http://127.0.0.1:{hook_port}/hook",
                            "timeout": _HOOK_TIMEOUT_SECS,
                        }
                    ],
                }
            ],
        },
    }
    return json.dumps(settings)


class ClaudeCLIBackend:
    """Backend that shells out to ``claude`` CLI with streaming JSON.

    When *hook_port* is provided, Claude is configured to call the HookServer
    before tool use so Starloom can create tool-call checkpoints.
    """

    def __init__(self, hook_port: int | None = None) -> None:
        self._hook_port = hook_port
        self._processes: dict[str, asyncio.subprocess.Process] = {}

    def configure_hook_port(self, port: int) -> None:
        """Configure the hook port after HookServer starts."""
        self._hook_port = port

    async def run(
        self,
        spec: AgentSpecData,
        node_id: str,
        bus: EventBus,
    ) -> AgentResult:
        """Spawn claude subprocess, parse stream, return result."""
        cmd = _build_cmd(spec, self._hook_port)
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            limit=10 * 1024 * 1024,
        )
        self._processes[node_id] = proc
        try:
            result = await _parse_stream(proc, spec, node_id, bus)
        finally:
            self._processes.pop(node_id, None)
        return result

    async def stop(self, node_id: str) -> None:
        """Send SIGTERM to the subprocess for this node."""
        proc = self._processes.get(node_id)
        if proc and proc.returncode is None:
            proc.terminate()


# ---------------------------------------------------------------------------
# Command building
# ---------------------------------------------------------------------------


def _build_cmd(
    spec: AgentSpecData,
    hook_port: int | None = None,
) -> list[str]:
    """Construct claude CLI command with all flags."""
    cmd = [
        "claude",
        "-p",
        spec.prompt,
        "--model",
        "haiku",
        "--verbose",
        "--output-format",
        "stream-json",
    ]
    if hook_port is not None and not _skips_permissions(spec):
        cmd.extend(["--settings", _hook_settings_json(hook_port)])
        cmd.extend(["--permission-mode", "dontAsk"])
    else:
        cmd.extend(["--permission-mode", "bypassPermissions"])
    _append_backend_flags(cmd, spec)
    return cmd


def _append_backend_flags(
    cmd: list[str],
    spec: AgentSpecData,
) -> None:
    """Append backend-specific raw flags without interpreting semantics."""
    if spec.flags:
        cmd.extend(shlex.split(spec.flags))


def _skips_permissions(spec: AgentSpecData) -> bool:
    """True when raw Claude flags explicitly disable permission prompts."""
    if not spec.flags:
        return False
    tokens = shlex.split(spec.flags)
    if "--dangerously-skip-permissions" in tokens:
        return True
    for idx, token in enumerate(tokens):
        if token == "--permission-mode" and idx + 1 < len(tokens):
            return tokens[idx + 1] == "bypassPermissions"
        if token.startswith("--permission-mode="):
            return token.split("=", 1)[1] == "bypassPermissions"
    return False


# ---------------------------------------------------------------------------
# Stream parsing
# ---------------------------------------------------------------------------


async def _parse_stream(
    proc: asyncio.subprocess.Process,
    spec: AgentSpecData,
    node_id: str,
    bus: EventBus,
) -> AgentResult:
    """Read streaming JSON lines and dispatch by message type."""
    stream = StreamAccumulator()
    block = BlockState()
    async for line in stdout_lines(proc):
        msg = try_parse_json(line)
        if msg is None:
            continue
        msg_type = msg.get("type")
        handler = _MSG_DISPATCH.get(str(msg_type) if msg_type is not None else None)
        if handler is not None:
            await handler(msg, stream, block, node_id, bus)
    await proc.wait()
    if proc.returncode == -15:
        raise StopRequestedError("claude process terminated by stop request")
    return await _build_result(proc, stream, spec)


async def _build_result(
    proc: asyncio.subprocess.Process,
    stream: StreamAccumulator,
    spec: AgentSpecData,
) -> AgentResult:
    """Build AgentResult from accumulated state and process exit."""
    error = await read_stderr(proc)
    output = stream.final_output
    failed = bool(proc.returncode and proc.returncode != 0)
    output, error = _resolve_output_error(output, error, failed, proc.returncode)
    return AgentResult(
        output=output,
        cost_usd=stream.cost_usd,
        input_tokens=stream.usage.input_tokens,
        output_tokens=stream.usage.output_tokens,
        error=error if failed else None,
        backend_session_id=stream.backend_session_id,
    )


def _resolve_output_error(
    output: str | None,
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
