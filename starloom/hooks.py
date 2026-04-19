"""HookServer — HTTP server for Claude CLI tool-use interception.

Claude CLI sends a POST with tool_name/tool_input before using a tool.
Starloom creates a tool-call checkpoint, waits for an operator decision,
and responds with allow/deny.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from aiohttp import web

from starloom.checkpoint import (
    ApproveAction,
    Checkpoint,
    CheckpointGate,
    Decision,
    RejectAction,
    make_checkpoint_id,
)
from starloom.types import CheckpointKind

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class HookRequest:
    """Typed representation of a Claude CLI tool-use interception POST body."""

    tool_name: str
    tool_input_preview: str


class HookServer:
    """HTTP server that receives Claude CLI tool-use interception requests.

    Claude CLI sends POST with {tool_name, tool_input, ...}.
    Response must include hookSpecificOutput.permissionDecision.
    """

    def __init__(self, gate: CheckpointGate) -> None:
        self._gate = gate
        self._node_id = "cli"
        self._app = web.Application()
        self._app.router.add_post("/hook", self._handle_hook)
        self._runner: web.AppRunner | None = None
        self._port: int = 0

    async def start(self, port: int = 0) -> int:
        """Start the HTTP server and return the actual bound port."""
        self._runner = web.AppRunner(self._app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, "127.0.0.1", port)
        await site.start()
        self._port = _extract_port(self._runner)
        logger.info("HookServer listening on port %d", self._port)
        return self._port

    async def stop(self) -> None:
        """Shut down the HTTP server."""
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None

    @property
    def port(self) -> int:
        return self._port

    def set_node_id(self, node_id: str) -> None:
        """Update the node_id used for checkpoint creation."""
        self._node_id = node_id

    async def _handle_hook(self, request: web.Request) -> web.Response:
        """Handle a Claude CLI tool-use interception POST request."""
        hook_req = await _parse_hook_request(request)
        return await self._checkpoint_and_wait(hook_req)

    async def _checkpoint_and_wait(
        self,
        hook_req: HookRequest,
    ) -> web.Response:
        """Create a checkpoint, wait for decision, return response."""
        checkpoint = _make_tool_checkpoint(hook_req, self._node_id)
        decision = await self._gate.wait(checkpoint)
        return _decision_to_response(decision)


async def _parse_hook_request(request: web.Request) -> HookRequest:
    """Parse a Claude CLI tool-use interception POST body into HookRequest."""
    body = await request.json()
    tool_name = str(body.get("tool_name", "unknown"))
    raw_input = body.get("tool_input", {})
    preview = json.dumps(raw_input, default=str)
    return HookRequest(tool_name=tool_name, tool_input_preview=preview)


def _make_tool_checkpoint(req: HookRequest, node_id: str) -> Checkpoint:
    """Build a tool-call checkpoint from a typed HookRequest."""
    return Checkpoint(
        id=make_checkpoint_id(),
        kind=CheckpointKind.TOOL_CALL,
        node_id=node_id,
        description=f"Tool call: {req.tool_name}",
        tool=req.tool_name,
        tool_input_preview=req.tool_input_preview,
    )


def _decision_response(decision: str, reason: str | None = None) -> web.Response:
    """Respond with the Claude CLI hook response envelope."""
    hook_specific_output: dict[str, object] = {
        "hookEventName": "PreToolUse",
        "permissionDecision": decision,
    }
    if reason is not None:
        hook_specific_output["permissionDecisionReason"] = reason
    payload: dict[str, object] = {
        "hookSpecificOutput": hook_specific_output,
    }
    return web.json_response(payload)


def _allow_response() -> web.Response:
    """Respond with an allow decision."""
    return _decision_response("allow")


def _deny_response(reason: str) -> web.Response:
    """Respond with a deny decision."""
    return _decision_response("deny", reason)


def _decision_to_response(decision: Decision) -> web.Response:
    """Convert a gate decision to a Claude CLI hook response."""
    if isinstance(decision, ApproveAction):
        return _allow_response()
    if isinstance(decision, RejectAction):
        return _deny_response(decision.reason or "rejected by operator")
    return _deny_response("unexpected decision type")


def _extract_port(runner: web.AppRunner) -> int:
    """Extract the bound port from a started AppRunner."""
    for site in runner.sites:
        server = getattr(site, "_server", None)  # noqa: SLF001
        if server is None:
            continue
        sockets = getattr(server, "sockets", None)
        if not sockets:
            continue
        sockname = sockets[0].getsockname()
        if isinstance(sockname, tuple) and len(sockname) >= 2:
            return int(sockname[1])
    return 0
