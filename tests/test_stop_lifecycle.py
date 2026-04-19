from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

import pytest

from starloom.backend.protocol import AgentResult
from starloom.checkpoint import CheckpointGate
from starloom.event_data import NodeStoppedData, WorkflowEndData
from starloom.events import Event, EventBus
from starloom.graph_pkg import TraceGraph
from starloom.orchestrator import _execute_with_stop, _WiringContext
from starloom.runtime import StaticBackendResolver, _make_context
from starloom.server import SessionServer, StopActionResult
from starloom.types import AgentSpecData, EventType, NodeStatus, WorkflowConfig


class BlockingBackend:
    def __init__(self) -> None:
        self.stop_calls: list[str] = []
        self._release = asyncio.Event()
        self.started = asyncio.Event()
        self.stop_requested = False

    async def run(
        self, _spec: AgentSpecData, _node_id: str, _bus: EventBus
    ) -> AgentResult:
        self.started.set()
        await self._release.wait()
        if self.stop_requested:
            from starloom.backend.protocol import StopRequestedError

            raise StopRequestedError("stop requested")
        return AgentResult(output="done")

    async def stop(self, node_id: str) -> None:
        self.stop_calls.append(node_id)
        self.stop_requested = True
        self._release.set()


async def _connect(
    sock_path: Path,
) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    return await asyncio.open_unix_connection(str(sock_path))


async def _send_json(writer: asyncio.StreamWriter, data: dict[str, object]) -> None:
    import json

    writer.write((json.dumps(data) + "\n").encode())
    await writer.drain()


async def _read_msg(reader: asyncio.StreamReader) -> dict[str, object]:
    import json

    line = await asyncio.wait_for(reader.readline(), timeout=2.0)
    raw = json.loads(line)
    assert isinstance(raw, dict)
    return raw


@pytest.mark.asyncio
async def test_stop_request_event_precedes_node_stopped_event() -> None:
    bus = EventBus(session_id="stop-lifecycle")
    gate = CheckpointGate(bus)
    graph = TraceGraph()
    backend = BlockingBackend()
    config = WorkflowConfig(workflow_file="test.star", params={}, backend="claude")
    ctx = _WiringContext(
        bus=bus,
        gate=gate,
        graph=graph,
        backend_resolver=StaticBackendResolver({"claude": backend}),
    )
    runtime_ctx = _make_context(
        config=config,
        bus=bus,
        backend=backend,
        gate=gate,
        graph=graph,
        backend_resolver=ctx.backend_resolver,
    )

    seen: list[EventType] = []
    stopped_data: list[NodeStoppedData] = []
    workflow_end: list[WorkflowEndData] = []

    async def track(event: Event) -> None:
        seen.append(event.type)
        if event.type == EventType.NODE_STOPPED:
            assert isinstance(event.data, NodeStoppedData)
            stopped_data.append(event.data)
        if event.type == EventType.WORKFLOW_END:
            assert isinstance(event.data, WorkflowEndData)
            workflow_end.append(event.data)

    bus.subscribe(track)

    with tempfile.TemporaryDirectory(prefix="sl_stop_") as td:
        sock_path = Path(td) / "session.sock"
        ctx.runtime_ctx = runtime_ctx
        ctx.execution_active = True

        async def _request_stop_session() -> StopActionResult:
            return await ctx.request_stop_session()

        server = SessionServer(
            bus,
            gate,
            sock_path,
            graph=graph,
            request_stop_session=_request_stop_session,
        )
        await server.start()
        try:
            task = asyncio.create_task(
                _execute_with_stop(
                    source='call_agent("block")',
                    config=config,
                    ctx=ctx,
                    backend=backend,
                    runtime_ctx=runtime_ctx,
                    middleware=None,
                )
            )

            while not graph.nodes:
                await asyncio.sleep(0.01)
            await backend.started.wait()

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

            while True:
                msg = await _read_msg(reader)
                if msg.get("msg") == "action_result":
                    assert msg["ok"] is True
                    break

            result = await asyncio.wait_for(task, timeout=5.0)
            assert result.error is None
            assert backend.stop_calls == [graph.nodes[0].id]
            assert seen.count(EventType.NODE_STOPPED) == 1
            assert EventType.NODE_FINISHED not in seen
            assert EventType.NODE_ERROR not in seen
            assert stopped_data[0].reason == "stop requested"
            assert workflow_end and workflow_end[-1].error is None
            assert graph.nodes[0].status == NodeStatus.STOPPED
            assert seen == [
                EventType.WORKFLOW_START,
                EventType.NODE_ADDED,
                EventType.NODE_STARTED,
                EventType.NODE_STOPPED,
                EventType.WORKFLOW_END,
            ]

            writer.close()
            await writer.wait_closed()
        finally:
            await server.stop()
