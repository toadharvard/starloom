"""Tests for SessionServer — actual Unix socket communication."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Generator
from pathlib import Path

import pytest

from starloom.checkpoint import (
    ApproveAction,
    CheckpointGate,
    Checkpoint,
    RejectAction,
    AnswerAction,
)
from starloom.events import EventBus
from starloom.graph_pkg import TraceGraph
from starloom.server import SessionServer, StopActionResult
from starloom.types import (
    AgentSpecData,
    CheckpointKind,
    EventType,
)


@pytest.fixture
def sock_path(tmp_path: Path) -> Generator[Path, None, None]:
    """Use /tmp for short path — macOS 104-byte Unix socket limit."""
    import tempfile

    short_dir = Path(tempfile.mkdtemp(prefix="sl_"))
    sock = short_dir / "s.sock"
    yield sock
    import shutil

    shutil.rmtree(short_dir, ignore_errors=True)


@pytest.fixture
def bus() -> EventBus:
    return EventBus(session_id="test-session")


@pytest.fixture
def gate(bus: EventBus) -> CheckpointGate:
    return CheckpointGate(bus)


@pytest.fixture
def graph() -> TraceGraph:
    g = TraceGraph()
    spec = AgentSpecData(prompt="Test Agent", flags="--model haiku")
    g.add_node("n1", spec)
    return g


async def _connect(
    sock_path: Path,
) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    """Connect to Unix socket and return reader/writer."""
    reader, writer = await asyncio.open_unix_connection(str(sock_path))
    return reader, writer


async def _send_json(
    writer: asyncio.StreamWriter,
    data: dict[str, object],
) -> None:
    writer.write((json.dumps(data) + "\n").encode())
    await writer.drain()


async def _read_msg(reader: asyncio.StreamReader) -> dict[str, object]:
    line = await asyncio.wait_for(reader.readline(), timeout=2.0)
    raw = json.loads(line)
    assert isinstance(raw, dict)
    return raw


async def _read_action_result(reader: asyncio.StreamReader) -> dict[str, object]:
    """Read messages until an action_result, skipping broadcast events."""
    while True:
        msg = await _read_msg(reader)
        if msg.get("msg") == "action_result":
            return msg


# ── Basic lifecycle ──────────────────────────────────────────────


class TestServerLifecycle:
    @pytest.mark.asyncio
    async def test_start_and_stop(
        self,
        bus: EventBus,
        gate: CheckpointGate,
        sock_path: Path,
    ) -> None:
        srv = SessionServer(bus, gate, sock_path)
        await srv.start()
        assert sock_path.exists()
        await srv.stop()
        assert not sock_path.exists()

    @pytest.mark.asyncio
    async def test_client_connect_and_subscribe(
        self,
        bus: EventBus,
        gate: CheckpointGate,
        sock_path: Path,
    ) -> None:
        srv = SessionServer(bus, gate, sock_path)
        await srv.start()
        try:
            reader, writer = await _connect(sock_path)
            await _send_json(writer, {"msg": "subscribe"})
            msg = await _read_msg(reader)
            assert msg["msg"] == "snapshot"
            assert msg["events"] == []
            writer.close()
            await writer.wait_closed()
        finally:
            await srv.stop()


# ── Event broadcasting ───────────────────────────────────────────


class TestEventBroadcast:
    @pytest.mark.asyncio
    async def test_broadcast_events_to_client(
        self,
        bus: EventBus,
        gate: CheckpointGate,
        sock_path: Path,
    ) -> None:
        """Events emitted on the bus arrive at subscribed clients."""
        srv = SessionServer(bus, gate, sock_path)
        await srv.start()
        try:
            reader, writer = await _connect(sock_path)
            await _send_json(writer, {"msg": "subscribe"})
            # Read snapshot
            await _read_msg(reader)

            # Emit an event
            event = bus.make_event(EventType.WORKFLOW_START)
            await bus.emit(event)

            # Read the broadcast
            msg = await _read_msg(reader)
            assert msg["msg"] == "event"
            event_payload = msg["event"]
            assert isinstance(event_payload, dict)
            assert event_payload["type"] == "workflow.start"
            writer.close()
            await writer.wait_closed()
        finally:
            await srv.stop()

    @pytest.mark.asyncio
    async def test_snapshot_includes_past_events(
        self,
        bus: EventBus,
        gate: CheckpointGate,
        sock_path: Path,
    ) -> None:
        """Snapshot includes events emitted before client connected."""
        srv = SessionServer(bus, gate, sock_path)
        await srv.start()
        try:
            # Emit before client connects
            await bus.emit(bus.make_event(EventType.WORKFLOW_START))

            reader, writer = await _connect(sock_path)
            await _send_json(writer, {"msg": "subscribe"})
            msg = await _read_msg(reader)
            assert msg["msg"] == "snapshot"
            events = msg["events"]
            assert isinstance(events, list)
            assert len(events) == 1
            writer.close()
            await writer.wait_closed()
        finally:
            await srv.stop()

    @pytest.mark.asyncio
    async def test_multi_client_broadcast(
        self,
        bus: EventBus,
        gate: CheckpointGate,
        sock_path: Path,
    ) -> None:
        """Events are broadcast to all connected clients."""
        srv = SessionServer(bus, gate, sock_path)
        await srv.start()
        try:
            r1, w1 = await _connect(sock_path)
            r2, w2 = await _connect(sock_path)
            await _send_json(w1, {"msg": "subscribe"})
            await _send_json(w2, {"msg": "subscribe"})
            await _read_msg(r1)  # snapshot
            await _read_msg(r2)  # snapshot

            await bus.emit(bus.make_event(EventType.WORKFLOW_END))

            msg1 = await _read_msg(r1)
            msg2 = await _read_msg(r2)
            assert msg1["msg"] == "event"
            assert msg2["msg"] == "event"

            w1.close()
            w2.close()
            await w1.wait_closed()
            await w2.wait_closed()
        finally:
            await srv.stop()


# ── Checkpoint actions ───────────────────────────────────────────


class TestCheckpointActions:
    @pytest.mark.asyncio
    async def test_approve_checkpoint(
        self,
        bus: EventBus,
        gate: CheckpointGate,
        sock_path: Path,
    ) -> None:
        srv = SessionServer(bus, gate, sock_path)
        await srv.start()
        try:
            cp = Checkpoint(
                id="cp-1",
                kind=CheckpointKind.TOOL_CALL,
                node_id="n1",
                description="Bash call",
            )
            wait_task = asyncio.create_task(gate.wait(cp, timeout=5.0))
            # Wait for checkpoint to register
            await asyncio.sleep(0.05)

            reader, writer = await _connect(sock_path)
            await _send_json(writer, {"msg": "subscribe"})
            await _read_msg(reader)  # snapshot

            ApproveAction(checkpoint_id="cp-1")
            await _send_json(
                writer,
                {
                    "msg": "action",
                    "action": {"type": "approve", "checkpoint_id": "cp-1"},
                },
            )
            result = await _read_msg(reader)
            assert result["msg"] == "action_result"
            assert result["ok"] is True

            decision = await asyncio.wait_for(wait_task, timeout=2.0)
            assert isinstance(decision, ApproveAction)

            writer.close()
            await writer.wait_closed()
        finally:
            await srv.stop()

    @pytest.mark.asyncio
    async def test_reject_checkpoint(
        self,
        bus: EventBus,
        gate: CheckpointGate,
        sock_path: Path,
    ) -> None:
        srv = SessionServer(bus, gate, sock_path)
        await srv.start()
        try:
            cp = Checkpoint(
                id="cp-2",
                kind=CheckpointKind.TOOL_CALL,
                node_id="n1",
                description="Dangerous tool",
            )
            wait_task = asyncio.create_task(gate.wait(cp, timeout=5.0))
            await asyncio.sleep(0.05)

            reader, writer = await _connect(sock_path)
            await _send_json(writer, {"msg": "subscribe"})
            await _read_msg(reader)

            await _send_json(
                writer,
                {
                    "msg": "action",
                    "action": {
                        "type": "reject",
                        "checkpoint_id": "cp-2",
                        "reason": "too dangerous",
                    },
                },
            )
            result = await _read_msg(reader)
            assert result["ok"] is True

            decision = await asyncio.wait_for(wait_task, timeout=2.0)
            assert isinstance(decision, RejectAction)
            assert decision.reason == "too dangerous"

            writer.close()
            await writer.wait_closed()
        finally:
            await srv.stop()

    @pytest.mark.asyncio
    async def test_answer_checkpoint(
        self,
        bus: EventBus,
        gate: CheckpointGate,
        sock_path: Path,
    ) -> None:
        srv = SessionServer(bus, gate, sock_path)
        await srv.start()
        try:
            cp = Checkpoint(
                id="cp-3",
                kind=CheckpointKind.CHECKPOINT,
                node_id="n1",
                description="Deploy?",
            )
            wait_task = asyncio.create_task(gate.wait(cp, timeout=5.0))
            await asyncio.sleep(0.05)

            reader, writer = await _connect(sock_path)
            await _send_json(writer, {"msg": "subscribe"})
            await _read_msg(reader)

            await _send_json(
                writer,
                {
                    "msg": "action",
                    "action": {
                        "type": "answer",
                        "checkpoint_id": "cp-3",
                        "answer": "yes, deploy",
                    },
                },
            )
            result = await _read_msg(reader)
            assert result["ok"] is True

            decision = await asyncio.wait_for(wait_task, timeout=2.0)
            assert isinstance(decision, AnswerAction)
            assert decision.answer == "yes, deploy"

            writer.close()
            await writer.wait_closed()
        finally:
            await srv.stop()

    @pytest.mark.asyncio
    async def test_first_decision_wins(
        self,
        bus: EventBus,
        gate: CheckpointGate,
        sock_path: Path,
    ) -> None:
        """Two clients decide on the same checkpoint — first wins."""
        srv = SessionServer(bus, gate, sock_path)
        await srv.start()
        try:
            cp = Checkpoint(
                id="cp-race",
                kind=CheckpointKind.TOOL_CALL,
                node_id="n1",
                description="race",
            )
            wait_task = asyncio.create_task(gate.wait(cp, timeout=5.0))
            await asyncio.sleep(0.05)

            r1, w1 = await _connect(sock_path)
            r2, w2 = await _connect(sock_path)
            await _send_json(w1, {"msg": "subscribe"})
            await _send_json(w2, {"msg": "subscribe"})
            await _read_msg(r1)
            await _read_msg(r2)

            # Client 1 approves first
            await _send_json(
                w1,
                {
                    "msg": "action",
                    "action": {"type": "approve", "checkpoint_id": "cp-race"},
                },
            )
            res1 = await _read_action_result(r1)
            assert res1["ok"] is True

            # Client 2 tries to reject — already resolved
            await _send_json(
                w2,
                {
                    "msg": "action",
                    "action": {"type": "reject", "checkpoint_id": "cp-race"},
                },
            )
            res2 = await _read_action_result(r2)
            assert res2["ok"] is False

            decision = await asyncio.wait_for(wait_task, timeout=2.0)
            assert isinstance(decision, ApproveAction)

            w1.close()
            w2.close()
            await w1.wait_closed()
            await w2.wait_closed()
        finally:
            await srv.stop()


# ── Patch actions ────────────────────────────────────────────────


class TestActionRouting:
    @pytest.mark.asyncio
    async def test_stop_session_routes_stop_request(
        self,
        bus: EventBus,
        gate: CheckpointGate,
        sock_path: Path,
        graph: TraceGraph,
    ) -> None:
        accepted: list[bool] = []

        async def _request_stop_session() -> StopActionResult:
            accepted.append(True)
            return StopActionResult(True)

        srv = SessionServer(
            bus,
            gate,
            sock_path,
            graph=graph,
            request_stop_session=_request_stop_session,
        )
        await srv.start()
        try:
            reader, writer = await _connect(sock_path)
            await _send_json(writer, {"msg": "subscribe"})
            await _read_msg(reader)
            await _send_json(
                writer,
                {
                    "msg": "action",
                    "action": {"type": "stop_session"},
                },
            )
            result = await _read_msg(reader)
            assert result["ok"] is True
            assert accepted == [True]
        finally:
            writer.close()
            await writer.wait_closed()
            await srv.stop()

    @pytest.mark.asyncio
    async def test_stop_node_routes_only_to_owning_backend(
        self,
        bus: EventBus,
        gate: CheckpointGate,
        sock_path: Path,
    ) -> None:
        seen: list[str] = []

        async def _request_stop_node(node_id: str) -> StopActionResult:
            seen.append(node_id)
            return StopActionResult(True)

        srv = SessionServer(
            bus,
            gate,
            sock_path,
            request_stop_node=_request_stop_node,
        )
        await srv.start()
        try:
            reader, writer = await _connect(sock_path)
            await _send_json(writer, {"msg": "subscribe"})
            await _read_msg(reader)
            await _send_json(
                writer,
                {
                    "msg": "action",
                    "action": {"type": "stop_node", "node_id": "n1"},
                },
            )
            result = await _read_msg(reader)
            assert result["ok"] is True
            assert seen == ["n1"]
        finally:
            writer.close()
            await writer.wait_closed()
            await srv.stop()

    @pytest.mark.asyncio
    async def test_stop_session_routes_each_running_node_to_its_owner(
        self,
        bus: EventBus,
        gate: CheckpointGate,
        sock_path: Path,
    ) -> None:
        accepted: list[str] = []

        async def _request_stop_session() -> StopActionResult:
            accepted.append("stop_session")
            return StopActionResult(True)

        srv = SessionServer(
            bus,
            gate,
            sock_path,
            request_stop_session=_request_stop_session,
        )
        await srv.start()
        try:
            reader, writer = await _connect(sock_path)
            await _send_json(writer, {"msg": "subscribe"})
            await _read_msg(reader)
            await _send_json(
                writer,
                {
                    "msg": "action",
                    "action": {"type": "stop_session"},
                },
            )
            result = await _read_msg(reader)
            assert result["ok"] is True
            assert accepted == ["stop_session"]
        finally:
            writer.close()
            await writer.wait_closed()
            await srv.stop()

    @pytest.mark.asyncio
    async def test_stop_node_rejects_missing_backend_ownership(
        self,
        bus: EventBus,
        gate: CheckpointGate,
        sock_path: Path,
    ) -> None:
        async def _request_stop_node(node_id: str) -> StopActionResult:
            return StopActionResult(
                False, f"Cannot stop node without backend ownership: {node_id}"
            )

        srv = SessionServer(
            bus,
            gate,
            sock_path,
            request_stop_node=_request_stop_node,
        )
        await srv.start()
        try:
            reader, writer = await _connect(sock_path)
            await _send_json(writer, {"msg": "subscribe"})
            await _read_msg(reader)
            await _send_json(
                writer,
                {
                    "msg": "action",
                    "action": {"type": "stop_node", "node_id": "n1"},
                },
            )
            result = await _read_msg(reader)
            assert result == {
                "msg": "action_result",
                "ok": False,
                "error": "Cannot stop node without backend ownership: n1",
            }
        finally:
            writer.close()
            await writer.wait_closed()
            await srv.stop()

    @pytest.mark.asyncio
    async def test_stop_session_rejects_running_nodes_without_backend_ownership(
        self,
        bus: EventBus,
        gate: CheckpointGate,
        sock_path: Path,
    ) -> None:
        async def _request_stop_session() -> StopActionResult:
            return StopActionResult(
                False,
                "Cannot stop session: running nodes without backend ownership: n1",
            )

        srv = SessionServer(
            bus,
            gate,
            sock_path,
            request_stop_session=_request_stop_session,
        )
        await srv.start()
        try:
            reader, writer = await _connect(sock_path)
            await _send_json(writer, {"msg": "subscribe"})
            await _read_msg(reader)
            await _send_json(
                writer,
                {
                    "msg": "action",
                    "action": {"type": "stop_session"},
                },
            )
            result = await _read_msg(reader)
            assert result == {
                "msg": "action_result",
                "ok": False,
                "error": "Cannot stop session: running nodes without backend ownership: n1",
            }
        finally:
            writer.close()
            await writer.wait_closed()
            await srv.stop()


class TestPatchActions:
    @pytest.mark.asyncio
    async def test_patch_node_via_socket(
        self,
        bus: EventBus,
        gate: CheckpointGate,
        sock_path: Path,
        graph: TraceGraph,
    ) -> None:
        srv = SessionServer(bus, gate, sock_path, graph=graph)
        await srv.start()
        try:
            reader, writer = await _connect(sock_path)
            await _send_json(writer, {"msg": "subscribe"})
            await _read_msg(reader)

            await _send_json(
                writer,
                {
                    "msg": "action",
                    "action": {
                        "type": "patch",
                        "node_id": "n1",
                        "patch": {"flags": "--model sonnet"},
                    },
                },
            )
            result = await _read_msg(reader)
            assert result["ok"] is True

            node = graph.get_node("n1")
            assert node is not None
            assert node.effective_spec.flags == "--model sonnet"

            writer.close()
            await writer.wait_closed()
        finally:
            await srv.stop()

    @pytest.mark.asyncio
    async def test_patch_without_graph_fails(
        self,
        bus: EventBus,
        gate: CheckpointGate,
        sock_path: Path,
    ) -> None:
        srv = SessionServer(bus, gate, sock_path, graph=None)
        await srv.start()
        try:
            reader, writer = await _connect(sock_path)
            await _send_json(writer, {"msg": "subscribe"})
            await _read_msg(reader)

            await _send_json(
                writer,
                {
                    "msg": "action",
                    "action": {
                        "type": "patch",
                        "node_id": "n1",
                        "patch": {"flags": "--model sonnet"},
                    },
                },
            )
            result = await _read_msg(reader)
            assert result["ok"] is False

            writer.close()
            await writer.wait_closed()
        finally:
            await srv.stop()


# ── Error handling ───────────────────────────────────────────────


class TestErrorHandling:
    @pytest.mark.asyncio
    async def test_invalid_action_type(
        self,
        bus: EventBus,
        gate: CheckpointGate,
        sock_path: Path,
    ) -> None:
        srv = SessionServer(bus, gate, sock_path)
        await srv.start()
        try:
            reader, writer = await _connect(sock_path)
            await _send_json(writer, {"msg": "subscribe"})
            await _read_msg(reader)

            await _send_json(
                writer,
                {
                    "msg": "action",
                    "action": {"type": "bogus"},
                },
            )
            result = await _read_msg(reader)
            assert result["ok"] is False
            assert result["error"] is not None

            writer.close()
            await writer.wait_closed()
        finally:
            await srv.stop()

    @pytest.mark.asyncio
    async def test_missing_action_field(
        self,
        bus: EventBus,
        gate: CheckpointGate,
        sock_path: Path,
    ) -> None:
        srv = SessionServer(bus, gate, sock_path)
        await srv.start()
        try:
            reader, writer = await _connect(sock_path)
            await _send_json(writer, {"msg": "subscribe"})
            await _read_msg(reader)

            await _send_json(writer, {"msg": "action"})
            result = await _read_msg(reader)
            assert result["ok"] is False

            writer.close()
            await writer.wait_closed()
        finally:
            await srv.stop()


# ── Close notification ───────────────────────────────────────────


class TestClose:
    @pytest.mark.asyncio
    async def test_stop_sends_closed_message(
        self,
        bus: EventBus,
        gate: CheckpointGate,
        sock_path: Path,
    ) -> None:
        srv = SessionServer(bus, gate, sock_path)
        await srv.start()
        reader, writer = await _connect(sock_path)
        await _send_json(writer, {"msg": "subscribe"})
        await _read_msg(reader)  # snapshot

        await srv.stop()
        msg = await _read_msg(reader)
        assert msg["msg"] == "closed"
        assert msg["reason"] == "completed"
        writer.close()
        await writer.wait_closed()
