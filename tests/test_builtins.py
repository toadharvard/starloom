"""Tests for builtins — checkpoint, output, agent, _run_parallel, call_agent.

Tests use real Starlark execution via starlark-go where possible.
Unit tests for individual closures where Starlark isn't needed.
"""

from __future__ import annotations

import asyncio
from typing import NoReturn

import pytest

from starloom.backend.protocol import AgentBackend, AgentResult, StopRequestedError
from starloom.builtins import BranchContext, RuntimeContext
from starloom.checkpoint import AnswerAction, CheckpointGate
from starloom.event_data import CheckpointPendingData, WorkflowOutputData
from starloom.events import Event, EventBus
from starloom.graph_pkg import TraceGraph
from starloom.middleware.protocol import Accept, Skip
from starloom.runtime import StaticBackendResolver, execute
from starloom.types import AgentSpecData, EventType, NodeStatus, WorkflowConfig

from tests.conftest import collect_outputs


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class FakeBackend:
    """Backend that returns configurable output."""

    def __init__(self, output: str = "result") -> None:
        self._output = output
        self.calls: list[str] = []

    async def run(
        self, spec: AgentSpecData, _node_id: str, _bus: EventBus
    ) -> AgentResult:
        self.calls.append(spec.prompt)
        return AgentResult(
            output=self._output,
            cost_usd=None,
            input_tokens=None,
            output_tokens=None,
        )

    async def stop(self, _node_id: str) -> None:
        return None


class FailBackend:
    """Backend that always raises."""

    async def run(
        self, _spec: AgentSpecData, _node_id: str, _bus: EventBus
    ) -> NoReturn:
        raise RuntimeError("backend error")

    async def stop(self, _node_id: str) -> None:
        return None


def _config(**overrides: object) -> WorkflowConfig:
    workflow_file = str(overrides.pop("workflow_file", "test.star"))
    params = overrides.pop("params", {})
    assert isinstance(params, dict)
    typed_params = {str(k): str(v) for k, v in params.items()}
    return WorkflowConfig(
        workflow_file=workflow_file,
        params=typed_params,
        backend=str(overrides.pop("backend", "claude")),
        dry_run=bool(overrides.pop("dry_run", False)),
    )


# ---------------------------------------------------------------------------
# output()
# ---------------------------------------------------------------------------


class TestOutputBuiltin:
    @pytest.mark.asyncio
    async def test_output_emits_event(self, bus: EventBus) -> None:
        result = await execute(
            'output("hello")', _config(), bus, FakeBackend(), CheckpointGate(bus)
        )
        assert result.error is None
        assert collect_outputs(bus) == ["hello"]

    @pytest.mark.asyncio
    async def test_each_call_emits_its_own_event(self, bus: EventBus) -> None:
        source = 'output("a")\noutput("b")'
        result = await execute(
            source, _config(), bus, FakeBackend(), CheckpointGate(bus)
        )
        assert result.error is None
        assert collect_outputs(bus) == ["a", "b"]

    @pytest.mark.asyncio
    async def test_output_none_emits_null_payload(self, bus: EventBus) -> None:
        result = await execute(
            "output(None)", _config(), bus, FakeBackend(), CheckpointGate(bus)
        )
        assert result.error is None
        assert collect_outputs(bus) == [None]

    @pytest.mark.asyncio
    async def test_output_event_payload_is_typed(self, bus: EventBus) -> None:
        seen: list[WorkflowOutputData] = []

        async def track(event: Event) -> None:
            if event.type == EventType.WORKFLOW_OUTPUT:
                assert isinstance(event.data, WorkflowOutputData)
                seen.append(event.data)

        bus.subscribe(track)
        result = await execute(
            'output("hello")', _config(), bus, FakeBackend(), CheckpointGate(bus)
        )
        assert result.error is None
        assert seen == [WorkflowOutputData(output="hello")]


# ---------------------------------------------------------------------------
# checkpoint()
# ---------------------------------------------------------------------------


class TestCheckpointBuiltin:
    @pytest.mark.asyncio
    async def test_checkpoint_returns_answer(self, bus: EventBus) -> None:
        gate = CheckpointGate(bus)

        async def answer_soon() -> None:
            await asyncio.sleep(0.01)
            checkpoint_id = gate.pending_ids[0]
            gate.decide(
                checkpoint_id, AnswerAction(checkpoint_id=checkpoint_id, answer="42")
            )

        asyncio.create_task(answer_soon())
        result = await execute(
            'output(checkpoint("Need input?"))', _config(), bus, FakeBackend(), gate
        )
        assert result.error is None
        assert collect_outputs(bus) == ["42"]

    @pytest.mark.asyncio
    async def test_checkpoint_emits_pending_event(self, bus: EventBus) -> None:
        gate = CheckpointGate(bus)
        seen: list[CheckpointPendingData] = []

        async def track(event: Event) -> None:
            if event.type == EventType.CHECKPOINT_PENDING:
                assert isinstance(event.data, CheckpointPendingData)
                seen.append(event.data)

        async def answer_soon() -> None:
            await asyncio.sleep(0.01)
            checkpoint_id = gate.pending_ids[0]
            gate.decide(
                checkpoint_id, AnswerAction(checkpoint_id=checkpoint_id, answer="ok")
            )

        bus.subscribe(track)
        asyncio.create_task(answer_soon())
        result = await execute(
            'output(checkpoint("Question"))', _config(), bus, FakeBackend(), gate
        )
        assert result.error is None
        assert len(seen) == 1
        assert seen[0].description == "Question"


# ---------------------------------------------------------------------------
# call_agent()
# ---------------------------------------------------------------------------


class TestCallAgentBasic:
    @pytest.mark.asyncio
    async def test_call_agent_returns_backend_output(self, bus: EventBus) -> None:
        source = 'output(call_agent("say hi"))'
        result = await execute(
            source, _config(), bus, FakeBackend(output="hello"), CheckpointGate(bus)
        )
        assert result.error is None
        assert collect_outputs(bus) == ["hello"]

    @pytest.mark.asyncio
    async def test_call_agent_records_graph_node(self, bus: EventBus) -> None:
        source = 'call_agent("say hi")'
        result = await execute(
            source, _config(), bus, FakeBackend(output="hello"), CheckpointGate(bus)
        )
        assert result.error is None
        assert len(result.graph.nodes) == 1
        node = result.graph.nodes[0]
        assert node.spec.prompt == "say hi"
        assert node.result == "hello"
        assert node.status == NodeStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_call_agent_uses_named_backend(self, bus: EventBus) -> None:
        source = 'call_agent("prompt", backend="alt")'
        primary = FakeBackend(output="main")
        alternate = FakeBackend(output="alt")
        result = await execute(
            source,
            _config(),
            bus,
            primary,
            CheckpointGate(bus),
            backend_resolver=StaticBackendResolver(
                {
                    "claude": primary,
                    "alt": alternate,
                }
            ),
        )
        assert result.error is None
        assert primary.calls == []
        assert alternate.calls == ["prompt"]
        assert result.graph.nodes[0].backend_name == "alt"


class CacheMiddleware:
    def before_call(self, _spec: AgentSpecData) -> Skip:
        return Skip(default_result="cached-result")

    def after_call(self, _spec: AgentSpecData, _result: str) -> Accept:
        return Accept()


class TestCallAgentCaching:
    @pytest.mark.asyncio
    async def test_cached_call_agent_records_backend_name(self, bus: EventBus) -> None:
        source = 'call_agent("cached", backend="alt")'
        backend = FakeBackend(output="main")
        gate = CheckpointGate(bus)
        result = await execute(
            source,
            _config(),
            bus,
            backend,
            gate,
            middleware=[CacheMiddleware()],
            backend_resolver=StaticBackendResolver(
                {
                    "claude": backend,
                    "alt": FakeBackend(output="alt"),
                }
            ),
        )
        assert result.error is None
        assert len(result.graph.nodes) == 1
        node = result.graph.nodes[0]
        assert node.status == NodeStatus.CACHED
        assert node.backend_name == "alt"


class TestCallAgentErrors:
    @pytest.mark.asyncio
    async def test_backend_error_in_graph(self, bus: EventBus) -> None:
        """Backend error is recorded in graph node."""
        source = 'call_agent("will fail")'
        backend = FailBackend()
        gate = CheckpointGate(bus)
        result = await execute(source, _config(), bus, backend, gate)
        assert result.error is not None
        nodes = result.graph.nodes
        assert len(nodes) == 1
        assert nodes[0].status == NodeStatus.ERROR

    @pytest.mark.asyncio
    async def test_stop_requested_marks_only_node_stopped(self, bus: EventBus) -> None:
        """Node-local stop stays node-local and does not emit finish/error."""

        class StopBackend:
            async def run(
                self, _spec: AgentSpecData, _node_id: str, _bus: EventBus
            ) -> NoReturn:
                raise StopRequestedError("stop requested")

            async def stop(self, _node_id: str) -> None:
                return None

        types: list[EventType] = []

        async def track(event: Event) -> None:
            types.append(event.type)

        bus.subscribe(track)
        result = await execute(
            'call_agent("stop me")', _config(), bus, StopBackend(), CheckpointGate(bus)
        )
        assert result.error is None
        assert [event for event in types if event == EventType.NODE_STOPPED] == [
            EventType.NODE_STOPPED
        ]
        assert EventType.NODE_FINISHED not in types
        assert EventType.WORKFLOW_END in types
        assert result.graph.nodes[0].status == NodeStatus.STOPPED


# ---------------------------------------------------------------------------
# RuntimeContext helpers
# ---------------------------------------------------------------------------


class TestRuntimeContextHelpers:
    def test_branch_context_defaults(self) -> None:
        ctx = BranchContext()
        assert ctx.current_parent is None

    def test_branch_context_fork_keeps_parent(self) -> None:
        parent = BranchContext(parent_id="root")
        child = parent.fork()
        assert child.current_parent == "root"

    def test_runtime_context_holds_dependencies(self, bus: EventBus) -> None:
        backend: AgentBackend = FakeBackend()
        gate = CheckpointGate(bus)
        graph = TraceGraph()
        ctx = RuntimeContext(
            config=_config(),
            bus=bus,
            graph=graph,
            backend_resolver=StaticBackendResolver({"claude": backend}),
            gate=gate,
            loop=asyncio.new_event_loop(),
        )
        try:
            assert ctx.bus is bus
            assert ctx.gate is gate
            assert ctx.graph is graph
            assert ctx.is_cancelled is False
        finally:
            ctx.loop.close()
