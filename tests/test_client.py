"""Tests for SessionClient — actual Unix socket communication via SessionServer."""

from __future__ import annotations

import asyncio
from collections.abc import Generator
from pathlib import Path

import pytest

from starloom.checkpoint import (
    AnswerAction,
    ApproveAction,
    Checkpoint,
    CheckpointGate,
    RejectAction,
)
from starloom.client import SessionClient
from starloom.events import EventBus
from starloom.graph_pkg import TraceGraph
from starloom.messages import EventMsg, SnapshotMsg
from starloom.server import SessionServer
from starloom.types import (
    AgentSpecData,
    CheckpointKind,
    EventType,
    NodePatch,
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
    return EventBus(session_id="client-test")


@pytest.fixture
def gate(bus: EventBus) -> CheckpointGate:
    return CheckpointGate(bus)


@pytest.fixture
def graph() -> TraceGraph:
    g = TraceGraph()
    spec = AgentSpecData(prompt="Test Agent", flags="--model haiku")
    g.add_node("n1", spec)
    return g


class TestClientLifecycle:
    @pytest.mark.asyncio
    async def test_connect_control_does_not_subscribe(
        self,
        bus: EventBus,
        gate: CheckpointGate,
        sock_path: Path,
    ) -> None:
        srv = SessionServer(bus, gate, sock_path)
        await srv.start()
        try:
            client = SessionClient(sock_path)
            await client.connect_control()
            assert await client.approve("missing") is False
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(client.read_message(), timeout=0.1)
            await client.disconnect()
        finally:
            await srv.stop()

    @pytest.mark.asyncio
    async def test_connect_and_disconnect(
        self,
        bus: EventBus,
        gate: CheckpointGate,
        sock_path: Path,
    ) -> None:
        srv = SessionServer(bus, gate, sock_path)
        await srv.start()
        try:
            client = SessionClient(sock_path)
            await client.connect()
            msg = await asyncio.wait_for(client.read_message(), timeout=2.0)
            assert isinstance(msg, SnapshotMsg)
            assert msg.events == ()
            await client.disconnect()
        finally:
            await srv.stop()

    @pytest.mark.asyncio
    async def test_not_connected_raises(self) -> None:
        client = SessionClient(Path("/nonexistent.sock"))
        with pytest.raises(RuntimeError, match="Not connected"):
            await client.read_message()


class TestClientEvents:
    @pytest.mark.asyncio
    async def test_receive_live_event(
        self,
        bus: EventBus,
        gate: CheckpointGate,
        sock_path: Path,
    ) -> None:
        srv = SessionServer(bus, gate, sock_path)
        await srv.start()
        try:
            client = SessionClient(sock_path)
            await client.connect()
            await client.read_message()

            await bus.emit(bus.make_event(EventType.WORKFLOW_START))
            msg = await asyncio.wait_for(client.read_message(), timeout=2.0)
            assert isinstance(msg, EventMsg)
            assert msg.event.type == EventType.WORKFLOW_START

            await client.disconnect()
        finally:
            await srv.stop()

    @pytest.mark.asyncio
    async def test_snapshot_includes_past_events(
        self,
        bus: EventBus,
        gate: CheckpointGate,
        sock_path: Path,
    ) -> None:
        srv = SessionServer(bus, gate, sock_path)
        await srv.start()
        try:
            await bus.emit(bus.make_event(EventType.WORKFLOW_START))
            await bus.emit(bus.make_event(EventType.WORKFLOW_END))

            client = SessionClient(sock_path)
            await client.connect()
            msg = await asyncio.wait_for(client.read_message(), timeout=2.0)
            assert isinstance(msg, SnapshotMsg)
            assert len(msg.events) == 2

            await client.disconnect()
        finally:
            await srv.stop()


class TestClientDecisions:
    @pytest.mark.asyncio
    async def test_approve(
        self,
        bus: EventBus,
        gate: CheckpointGate,
        sock_path: Path,
    ) -> None:
        srv = SessionServer(bus, gate, sock_path)
        await srv.start()
        try:
            cp = Checkpoint(
                id="cp-a",
                kind=CheckpointKind.TOOL_CALL,
                node_id="n1",
                description="test",
            )
            wait_task = asyncio.create_task(gate.wait(cp, timeout=5.0))
            await asyncio.sleep(0.05)

            client = SessionClient(sock_path)
            await client.connect()
            await client.read_message()

            ok = await client.approve("cp-a")
            assert ok is True

            decision = await asyncio.wait_for(wait_task, timeout=2.0)
            assert isinstance(decision, ApproveAction)

            await client.disconnect()
        finally:
            await srv.stop()

    @pytest.mark.asyncio
    async def test_reject(
        self,
        bus: EventBus,
        gate: CheckpointGate,
        sock_path: Path,
    ) -> None:
        srv = SessionServer(bus, gate, sock_path)
        await srv.start()
        try:
            cp = Checkpoint(
                id="cp-r",
                kind=CheckpointKind.TOOL_CALL,
                node_id="n1",
                description="test",
            )
            wait_task = asyncio.create_task(gate.wait(cp, timeout=5.0))
            await asyncio.sleep(0.05)

            client = SessionClient(sock_path)
            await client.connect()
            await client.read_message()

            ok = await client.reject("cp-r", reason="nope")
            assert ok is True

            decision = await asyncio.wait_for(wait_task, timeout=2.0)
            assert isinstance(decision, RejectAction)
            assert decision.reason == "nope"

            await client.disconnect()
        finally:
            await srv.stop()

    @pytest.mark.asyncio
    async def test_answer(
        self,
        bus: EventBus,
        gate: CheckpointGate,
        sock_path: Path,
    ) -> None:
        srv = SessionServer(bus, gate, sock_path)
        await srv.start()
        try:
            cp = Checkpoint(
                id="cp-ans",
                kind=CheckpointKind.CHECKPOINT,
                node_id="n1",
                description="question",
            )
            wait_task = asyncio.create_task(gate.wait(cp, timeout=5.0))
            await asyncio.sleep(0.05)

            client = SessionClient(sock_path)
            await client.connect()
            await client.read_message()

            ok = await client.answer("cp-ans", "42")
            assert ok is True

            decision = await asyncio.wait_for(wait_task, timeout=2.0)
            assert isinstance(decision, AnswerAction)
            assert decision.answer == "42"

            await client.disconnect()
        finally:
            await srv.stop()


class TestClientPatching:
    @pytest.mark.asyncio
    async def test_patch_node(
        self,
        bus: EventBus,
        gate: CheckpointGate,
        sock_path: Path,
        graph: TraceGraph,
    ) -> None:
        srv = SessionServer(bus, gate, sock_path, graph=graph)
        await srv.start()
        try:
            client = SessionClient(sock_path)
            await client.connect()
            await client.read_message()

            patch = NodePatch(flags="--model opus --max-turns 600")
            ok = await client.patch_node("n1", patch)
            assert ok is True

            node = graph.get_node("n1")
            assert node is not None
            assert node.effective_spec.flags == "--model opus --max-turns 600"

            await client.disconnect()
        finally:
            await srv.stop()

    @pytest.mark.asyncio
    async def test_patch_nonexistent_node_fails(
        self,
        bus: EventBus,
        gate: CheckpointGate,
        sock_path: Path,
        graph: TraceGraph,
    ) -> None:
        srv = SessionServer(bus, gate, sock_path, graph=graph)
        await srv.start()
        try:
            client = SessionClient(sock_path)
            await client.connect()
            await client.read_message()

            patch = NodePatch(flags="--model opus")
            ok = await client.patch_node("nonexistent", patch)
            assert ok is False

            await client.disconnect()
        finally:
            await srv.stop()


class TestMultiClient:
    @pytest.mark.asyncio
    async def test_two_clients_first_wins(
        self,
        bus: EventBus,
        gate: CheckpointGate,
        sock_path: Path,
    ) -> None:
        srv = SessionServer(bus, gate, sock_path)
        await srv.start()
        try:
            cp = Checkpoint(
                id="cp-mc",
                kind=CheckpointKind.TOOL_CALL,
                node_id="n1",
                description="race",
            )
            wait_task = asyncio.create_task(gate.wait(cp, timeout=5.0))
            await asyncio.sleep(0.05)

            c1 = SessionClient(sock_path)
            c2 = SessionClient(sock_path)
            await c1.connect()
            await c2.connect()
            await c1.read_message()
            await c2.read_message()

            ok1 = await c1.approve("cp-mc")
            assert ok1 is True

            ok2 = await c2.reject("cp-mc")
            assert ok2 is False

            decision = await asyncio.wait_for(wait_task, timeout=2.0)
            assert isinstance(decision, ApproveAction)

            await c1.disconnect()
            await c2.disconnect()
        finally:
            await srv.stop()

    @pytest.mark.asyncio
    async def test_both_clients_see_events(
        self,
        bus: EventBus,
        gate: CheckpointGate,
        sock_path: Path,
    ) -> None:
        srv = SessionServer(bus, gate, sock_path)
        await srv.start()
        try:
            c1 = SessionClient(sock_path)
            c2 = SessionClient(sock_path)
            await c1.connect()
            await c2.connect()
            await c1.read_message()
            await c2.read_message()

            await bus.emit(bus.make_event(EventType.WORKFLOW_START))

            m1 = await asyncio.wait_for(c1.read_message(), timeout=2.0)
            m2 = await asyncio.wait_for(c2.read_message(), timeout=2.0)
            assert isinstance(m1, EventMsg)
            assert isinstance(m2, EventMsg)

            await c1.disconnect()
            await c2.disconnect()
        finally:
            await srv.stop()
