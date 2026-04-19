from __future__ import annotations

import pytest

from starloom.backend.protocol import AgentResult
from starloom.checkpoint import CheckpointGate
from starloom.event_data import NodeAddedData, NodeFinishedData
from starloom.events import Event, EventBus
from starloom.middleware.protocol import Accept, Skip
from starloom.runtime import StaticBackendResolver, execute
from starloom.types import AgentSpecData, EventType, NodeStatus, WorkflowConfig


class FakeBackend:
    def __init__(self, output: str = "result", name: str = "default") -> None:
        self._output = output
        self._name = name
        self.calls: list[str] = []

    async def run(
        self, spec: AgentSpecData, node_id: str, bus: EventBus
    ) -> AgentResult:
        self.calls.append(spec.prompt)
        return AgentResult(
            output=f"{self._name}:{self._output}",
            cost_usd=None,
            input_tokens=1,
            output_tokens=1,
        )

    async def stop(self, _node_id: str) -> None:
        return None


class CacheMiddleware:
    def before_call(self, _spec: AgentSpecData) -> Skip:
        return Skip(default_result="cached-result")

    def after_call(self, _spec: AgentSpecData, _result: str) -> Accept:
        return Accept()


def _config() -> WorkflowConfig:
    return WorkflowConfig(workflow_file="test.star", params={}, backend="claude")


@pytest.mark.asyncio
async def test_cached_agent_node_records_backend_name() -> None:
    bus = EventBus(session_id="cached-node")
    gate = CheckpointGate(bus)
    default_backend = FakeBackend(output="main", name="claude")
    alt_backend = FakeBackend(output="alt", name="pi")

    result = await execute(
        'call_agent("cached", backend="pi")',
        _config(),
        bus,
        default_backend,
        gate,
        middleware=[CacheMiddleware()],
        backend_resolver=StaticBackendResolver(
            {
                "claude": default_backend,
                "pi": alt_backend,
            }
        ),
    )

    assert result.error is None
    assert len(result.graph.nodes) == 1
    node = result.graph.nodes[0]
    assert node.status == NodeStatus.CACHED
    assert node.backend_name == "pi"
    assert default_backend.calls == []
    assert alt_backend.calls == []


@pytest.mark.asyncio
async def test_mixed_backend_events_and_graph_persist_effective_backend() -> None:
    bus = EventBus(session_id="mixed-events")
    gate = CheckpointGate(bus)
    default_backend = FakeBackend(output="main", name="claude")
    alt_backend = FakeBackend(output="alt", name="pi")
    added_backend_names: list[str | None] = []
    finished_backend_names: list[str | None] = []

    async def track(event: Event) -> None:
        if event.type == EventType.NODE_ADDED:
            assert isinstance(event.data, NodeAddedData)
            added_backend_names.append(event.data.backend_name)
        if event.type == EventType.NODE_FINISHED:
            assert isinstance(event.data, NodeFinishedData)
            finished_backend_names.append(event.data.backend_name)

    bus.subscribe(track)

    result = await execute(
        'a = call_agent("one")\nb = call_agent("two", backend="pi")\noutput(a + "," + b)',
        _config(),
        bus,
        default_backend,
        gate,
        backend_resolver=StaticBackendResolver(
            {
                "claude": default_backend,
                "pi": alt_backend,
            }
        ),
    )

    assert result.error is None
    assert [node.backend_name for node in result.graph.nodes] == ["claude", "pi"]
    assert added_backend_names == ["claude", "pi"]
    assert finished_backend_names == ["claude", "pi"]
