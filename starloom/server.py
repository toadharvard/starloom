"""SessionServer — Unix socket, multi-client broadcast.

Clients subscribe to receive a snapshot of past events plus a live
stream. They can also send GraphActions that are forwarded to the
CheckpointGate (for decisions) or applied to the trace graph (for patches).
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

from starloom.checkpoint import (
    ApproveAction,
    AnswerAction,
    CheckpointGate,
    GraphAction,
    InvalidDecision,
    PatchNodeAction,
    RejectAction,
    StopNodeAction,
    StopSessionAction,
)
from starloom.events import Event, EventBus
from starloom.graph_pkg import TraceGraph
from starloom.messages import (
    ActionResultMsg,
    ClosedMsg,
    EventMsg,
    ServerMsg,
    SnapshotMsg,
    parse_action,
    serialize_msg,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class StopActionResult:
    accepted: bool
    error: str | None = None


class _ClientRegistry:
    """Track connected client writers and broadcast typed messages."""

    def __init__(self) -> None:
        self._clients: list[asyncio.StreamWriter] = []

    def add(self, writer: asyncio.StreamWriter) -> None:
        self._clients.append(writer)

    def remove(self, writer: asyncio.StreamWriter) -> None:
        try:
            self._clients.remove(writer)
        except ValueError:
            pass
        writer.close()

    def close_all(self) -> None:
        for writer in self._clients:
            writer.close()
        self._clients.clear()

    async def broadcast(self, msg: ServerMsg) -> None:
        dead: list[asyncio.StreamWriter] = []
        for writer in self._clients:
            if not await _try_send_msg(writer, msg):
                dead.append(writer)
        for writer in dead:
            self.remove(writer)


class _SessionActionHandler:
    """Apply parsed client actions to the gate, graph, and stop callbacks."""

    def __init__(
        self,
        gate: CheckpointGate,
        graph: TraceGraph | None,
        request_stop_node: Callable[[str], Awaitable[StopActionResult]] | None = None,
        request_stop_session: Callable[[], Awaitable[StopActionResult]] | None = None,
    ) -> None:
        self._gate = gate
        self._graph = graph
        self._request_stop_node = request_stop_node
        self._request_stop_session = request_stop_session

    async def execute(self, action: GraphAction) -> StopActionResult | bool:
        if isinstance(action, PatchNodeAction):
            return self._handle_patch(action)
        if isinstance(action, (StopNodeAction, StopSessionAction)):
            return await self._handle_stop(action)
        return self._handle_decision(action)

    def _handle_decision(
        self,
        action: ApproveAction | RejectAction | AnswerAction,
    ) -> bool:
        return self._gate.decide(action.checkpoint_id, action)

    def _handle_patch(self, action: PatchNodeAction) -> bool:
        if self._graph is None:
            return False
        self._graph.patch_node(action.node_id, action.patch)
        return True

    async def _handle_stop(
        self,
        action: StopNodeAction | StopSessionAction,
    ) -> StopActionResult:
        if isinstance(action, StopNodeAction):
            if self._request_stop_node is None:
                raise RuntimeError("Stop node callback not configured")
            return await self._request_stop_node(action.node_id)
        if self._request_stop_session is None:
            raise RuntimeError("Stop session callback not configured")
        return await self._request_stop_session()


class SessionServer:
    """Unix socket server that broadcasts events to connected clients.

    Clients subscribe to receive a snapshot of past events plus a live
    stream. They can also send actions (approve/reject/answer/patch)
    that are forwarded to the CheckpointGate or trace graph.
    """

    def __init__(
        self,
        bus: EventBus,
        gate: CheckpointGate,
        sock_path: Path,
        graph: TraceGraph | None = None,
        request_stop_node: Callable[[str], Awaitable[StopActionResult]] | None = None,
        request_stop_session: Callable[[], Awaitable[StopActionResult]] | None = None,
    ) -> None:
        self._bus = bus
        self._sock_path = sock_path
        self._server: asyncio.Server | None = None
        self._clients = _ClientRegistry()
        self._actions = _SessionActionHandler(
            gate=gate,
            graph=graph,
            request_stop_node=request_stop_node,
            request_stop_session=request_stop_session,
        )

    async def start(self) -> None:
        """Start Unix socket server and subscribe to bus events."""
        self._sock_path.parent.mkdir(parents=True, exist_ok=True)
        self._sock_path.unlink(missing_ok=True)
        self._server = await asyncio.start_unix_server(
            self._handle_client,
            path=str(self._sock_path),
        )
        self._bus.subscribe(self._on_event)

    async def stop(self) -> None:
        """Close server, disconnect clients, cleanup socket file."""
        self._bus.unsubscribe(self._on_event)
        await self._broadcast_msg(ClosedMsg(reason="completed"))
        self._clients.close_all()
        await self._shutdown_server()
        self._sock_path.unlink(missing_ok=True)

    async def _handle_client(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        """Handle an incoming client connection."""
        self._clients.add(writer)
        try:
            await self._client_loop(reader, writer)
        except (ConnectionError, asyncio.IncompleteReadError):
            pass
        finally:
            self._clients.remove(writer)

    async def _client_loop(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        """Read messages from a client until disconnect."""
        while True:
            line = await reader.readline()
            if not line:
                break
            msg = json.loads(line)
            await self._dispatch_client_msg(msg, writer)

    async def _dispatch_client_msg(
        self,
        msg: dict[str, object],
        writer: asyncio.StreamWriter,
    ) -> None:
        """Route a parsed client message to the correct handler."""
        kind = msg.get("msg")
        if kind == "subscribe":
            await self._handle_subscribe(writer)
        elif kind == "action":
            await self._handle_action(msg, writer)

    async def _handle_subscribe(self, writer: asyncio.StreamWriter) -> None:
        """Send snapshot of all past events to a newly subscribed client."""
        events = tuple(self._bus.log)
        await _send_msg(writer, SnapshotMsg(events=events))

    async def _handle_action(
        self,
        msg: dict[str, object],
        writer: asyncio.StreamWriter,
    ) -> None:
        """Parse an action message and forward to gate/graph."""
        try:
            raw_action = msg["action"]
            if not isinstance(raw_action, dict):
                raise ValueError("invalid action")
            action = parse_action(raw_action)
            result = await self._actions.execute(action)
            if isinstance(result, StopActionResult):
                await _send_msg(
                    writer, ActionResultMsg(ok=result.accepted, error=result.error)
                )
            else:
                await _send_msg(writer, ActionResultMsg(ok=result))
        except (KeyError, ValueError, RuntimeError, InvalidDecision) as exc:
            await _send_msg(writer, ActionResultMsg(ok=False, error=str(exc)))

    async def _on_event(self, event: Event) -> None:
        """EventBus handler — broadcast every event to clients."""
        await self._broadcast_msg(EventMsg(event=event))

    async def _broadcast_msg(self, msg: ServerMsg) -> None:
        """Send a typed message to all connected clients."""
        await self._clients.broadcast(msg)

    async def _shutdown_server(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None


async def _send_msg(writer: asyncio.StreamWriter, msg: ServerMsg) -> None:
    """Write a typed ServerMsg as a JSON line to a stream writer."""
    writer.write(serialize_msg(msg).encode())
    await writer.drain()


async def _try_send_msg(
    writer: asyncio.StreamWriter,
    msg: ServerMsg,
) -> bool:
    """Send a message, returning False on failure."""
    try:
        await _send_msg(writer, msg)
        return True
    except (ConnectionError, OSError):
        return False
