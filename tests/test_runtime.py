"""Tests for runtime.py — execute() with real Starlark via starlark-go.

Every test runs actual Starlark code through the full runtime pipeline.
"""

from __future__ import annotations

from typing import NoReturn

import pytest

from starloom.backend.protocol import AgentResult, StopRequestedError
from starloom.checkpoint import CheckpointGate
from starloom.events import Event, EventBus
from starloom.runtime import StaticBackendResolver, execute
from starloom.types import AgentSpecData, EventType, NodeStatus, WorkflowConfig

from tests.conftest import collect_outputs


# ---------------------------------------------------------------------------
# Fake backend — returns prompt echo or configurable output
# ---------------------------------------------------------------------------


class FakeBackend:
    """Backend that echoes the prompt as output."""

    def __init__(self, output: str = "agent-output", name: str = "default") -> None:
        self._output = output
        self._name = name
        self.calls: list[str] = []

    async def run(
        self, spec: AgentSpecData, _node_id: str, _bus: EventBus
    ) -> AgentResult:
        self.calls.append(spec.prompt)
        return AgentResult(
            output=f"{self._name}:{self._output}",
            cost_usd=None,
            input_tokens=10,
            output_tokens=20,
        )

    async def stop(self, _node_id: str) -> None:
        return None


def _config(
    params: dict[str, str] | None = None,
    dry_run: bool = False,
    backend: str = "claude",
) -> WorkflowConfig:
    return WorkflowConfig(
        workflow_file="test.star",
        params=params or {},
        backend=backend,
        dry_run=dry_run,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestExecuteBasic:
    @pytest.mark.asyncio
    async def test_empty_workflow(self, bus: EventBus) -> None:
        """An empty workflow executes without error."""
        backend = FakeBackend()
        gate = CheckpointGate(bus)
        result = await execute("", _config(), bus, backend, gate)
        assert result.error is None
        assert collect_outputs(bus) == []

    @pytest.mark.asyncio
    async def test_output_builtin(self, bus: EventBus) -> None:
        """output() emits a workflow.output event per call."""
        source = 'output("hello world")'
        backend = FakeBackend()
        gate = CheckpointGate(bus)
        result = await execute(source, _config(), bus, backend, gate)
        assert result.error is None
        assert collect_outputs(bus) == ["hello world"]

    @pytest.mark.asyncio
    async def test_starlark_variables(self, bus: EventBus) -> None:
        """Starlark code can use variables and expressions."""
        source = """
x = "foo"
y = "bar"
output(x + "_" + y)
"""
        backend = FakeBackend()
        gate = CheckpointGate(bus)
        result = await execute(source, _config(), bus, backend, gate)
        assert result.error is None
        assert collect_outputs(bus) == ["foo_bar"]

    @pytest.mark.asyncio
    async def test_syntax_error(self, bus: EventBus) -> None:
        """Starlark syntax errors are captured in result.error."""
        source = "def broken(:\n  pass"
        backend = FakeBackend()
        gate = CheckpointGate(bus)
        result = await execute(source, _config(), bus, backend, gate)
        assert result.error is not None

    @pytest.mark.asyncio
    async def test_runtime_error(self, bus: EventBus) -> None:
        """Runtime errors in Starlark are captured."""
        source = "x = 1 / 0"
        backend = FakeBackend()
        gate = CheckpointGate(bus)
        result = await execute(source, _config(), bus, backend, gate)
        assert result.error is not None

    @pytest.mark.asyncio
    async def test_fail_builtin_marks_execution_error(self, bus: EventBus) -> None:
        """fail() turns semantic workflow failure into runtime error result."""
        source = 'fail("workflow did not converge")'
        backend = FakeBackend()
        gate = CheckpointGate(bus)
        result = await execute(source, _config(), bus, backend, gate)
        assert collect_outputs(bus) == []
        assert result.error == "workflow did not converge"


class ResolverBackend(FakeBackend):
    pass


class TestExecuteCallAgent:
    @pytest.mark.asyncio
    async def test_call_agent_returns_output(self, bus: EventBus) -> None:
        """call_agent() invokes backend and returns its output."""
        source = """
result = call_agent("Do something")
output(result)
"""
        backend = FakeBackend(output="done!")
        gate = CheckpointGate(bus)
        result = await execute(source, _config(), bus, backend, gate)
        assert result.error is None
        assert collect_outputs(bus) == ["default:done!"]
        assert len(backend.calls) == 1
        assert backend.calls[0] == "Do something"

    @pytest.mark.asyncio
    async def test_call_agent_with_flags(self, bus: EventBus) -> None:
        """call_agent() accepts raw backend flags."""
        source = 'result = call_agent("prompt", flags="--model opus")\noutput(result)'
        backend = FakeBackend(output="ok")
        gate = CheckpointGate(bus)
        result = await execute(source, _config(), bus, backend, gate)
        assert result.error is None
        assert collect_outputs(bus) == ["default:ok"]

    @pytest.mark.asyncio
    async def test_sequential_agents(self, bus: EventBus) -> None:
        """Multiple call_agent() calls execute sequentially."""
        source = """
a = call_agent("first")
b = call_agent("second")
output(a + " " + b)
"""
        backend = FakeBackend(output="x")
        gate = CheckpointGate(bus)
        result = await execute(source, _config(), bus, backend, gate)
        assert result.error is None
        assert collect_outputs(bus) == ["default:x default:x"]
        assert len(backend.calls) == 2

    @pytest.mark.asyncio
    async def test_call_agent_creates_graph_node(self, bus: EventBus) -> None:
        """call_agent() creates a node in the trace graph."""
        source = 'call_agent("test prompt")'
        backend = FakeBackend()
        gate = CheckpointGate(bus)
        result = await execute(source, _config(), bus, backend, gate)
        assert len(result.graph.nodes) == 1
        node = result.graph.nodes[0]
        assert node.spec.prompt == "test prompt"
        assert node.result == "default:agent-output"
        assert node.backend_name == "claude"

    @pytest.mark.asyncio
    async def test_default_backend_comes_from_config(self, bus: EventBus) -> None:
        """Default node execution resolves through config.backend."""
        source = 'result = call_agent("prompt")\noutput(result)'
        primary = FakeBackend(output="main", name="main")
        configured_default = ResolverBackend(output="cfg", name="configured")
        gate = CheckpointGate(bus)
        result = await execute(
            source,
            _config(backend="pi"),
            bus,
            primary,
            gate,
            backend_resolver=StaticBackendResolver(
                {
                    "claude": primary,
                    "pi": configured_default,
                }
            ),
        )
        assert result.error is None
        assert collect_outputs(bus) == ["configured:cfg"]
        assert primary.calls == []
        assert configured_default.calls == ["prompt"]

    @pytest.mark.asyncio
    async def test_call_agent_with_backend_override(self, bus: EventBus) -> None:
        """call_agent() resolves backend overrides through the resolver."""
        source = 'result = call_agent("prompt", backend="test")\noutput(result)'
        backend = FakeBackend(output="main", name="main")
        override = ResolverBackend(output="alt", name="override")
        gate = CheckpointGate(bus)
        result = await execute(
            source,
            _config(),
            bus,
            backend,
            gate,
            backend_resolver=StaticBackendResolver(
                {
                    "claude": backend,
                    "test": override,
                }
            ),
        )
        assert result.error is None
        assert collect_outputs(bus) == ["override:alt"]
        assert backend.calls == []
        assert override.calls == ["prompt"]

    @pytest.mark.asyncio
    async def test_call_agent_with_explicit_default_backend_override(
        self, bus: EventBus
    ) -> None:
        """Explicit override to the default backend should still resolve."""
        source = 'result = call_agent("prompt", backend="claude")\noutput(result)'
        backend = FakeBackend(output="main", name="main")
        gate = CheckpointGate(bus)
        result = await execute(
            source,
            _config(),
            bus,
            backend,
            gate,
            backend_resolver=StaticBackendResolver({"claude": backend}),
        )
        assert result.error is None
        assert collect_outputs(bus) == ["main:main"]
        assert backend.calls == ["prompt"]

    @pytest.mark.asyncio
    async def test_parallel_map_supports_mixed_backends(self, bus: EventBus) -> None:
        """parallel_map supports per-node backend overrides in one workflow."""
        source = """
items = [
    agent("default"),
    agent("override", backend="pi"),
    agent("explicit default", backend="claude"),
]
output(",".join(_run_parallel(items)))
"""
        default_backend = FakeBackend(output="main", name="default")
        alternate_backend = ResolverBackend(output="alt", name="pi")
        gate = CheckpointGate(bus)
        result = await execute(
            source,
            _config(),
            bus,
            default_backend,
            gate,
            backend_resolver=StaticBackendResolver(
                {
                    "claude": default_backend,
                    "pi": alternate_backend,
                }
            ),
        )
        assert result.error is None
        assert collect_outputs(bus) == ["default:main,pi:alt,default:main"]
        assert default_backend.calls == ["default", "explicit default"]
        assert alternate_backend.calls == ["override"]
        assert [node.backend_name for node in result.graph.nodes] == [
            "claude",
            "pi",
            "claude",
        ]


class TestExecuteParallel:
    @pytest.mark.asyncio
    async def test_mixed_backends_in_single_workflow(self, bus: EventBus) -> None:
        """Mixed backend overrides execute within one workflow run."""
        source = """
a = call_agent("first", backend="claude")
b = call_agent("second", backend="pi")
output(a + "," + b)
"""
        default_backend = FakeBackend(output="default", name="default")
        claude_backend = ResolverBackend(output="claude", name="claude")
        pi_backend = ResolverBackend(output="pi", name="pi")
        gate = CheckpointGate(bus)
        result = await execute(
            source,
            _config(),
            bus,
            default_backend,
            gate,
            backend_resolver=StaticBackendResolver(
                {
                    "claude": claude_backend,
                    "pi": pi_backend,
                }
            ),
        )
        assert result.error is None
        assert collect_outputs(bus) == ["claude:claude,pi:pi"]
        assert default_backend.calls == []
        assert claude_backend.calls == ["first"]
        assert pi_backend.calls == ["second"]

    @pytest.mark.asyncio
    async def test_parallel_map(self, bus: EventBus) -> None:
        """parallel_map() runs agents concurrently via PRELUDE."""
        source = """
items = ["a", "b", "c"]
results = parallel_map(lambda x: agent(x), items)
output(",".join(results))
"""
        backend = FakeBackend(output="done")
        gate = CheckpointGate(bus)
        result = await execute(source, _config(), bus, backend, gate)
        assert result.error is None
        assert collect_outputs(bus) == ["default:done,default:done,default:done"]
        assert len(backend.calls) == 3

    @pytest.mark.asyncio
    async def test_parallel_map_creates_nodes(self, bus: EventBus) -> None:
        """parallel_map creates one graph node per item."""
        source = """
parallel_map(lambda x: agent("prompt " + x), ["1", "2"])
"""
        backend = FakeBackend()
        gate = CheckpointGate(bus)
        result = await execute(source, _config(), bus, backend, gate)
        assert result.error is None
        assert len(result.graph.nodes) == 2

    @pytest.mark.asyncio
    async def test_agent_returns_dict(self, bus: EventBus) -> None:
        """agent() builds a spec dict for parallel_map; it does not execute."""
        source = """
spec = agent("test prompt", flags="--model opus")
output(spec["prompt"])
"""
        backend = FakeBackend()
        gate = CheckpointGate(bus)
        result = await execute(source, _config(), bus, backend, gate)
        assert result.error is None
        assert collect_outputs(bus) == ["test prompt"]


class TestExecuteStopSemantics:
    @pytest.mark.asyncio
    async def test_stop_requested_is_typed_without_string_matching(
        self, bus: EventBus
    ) -> None:
        class TypedStopBackend(FakeBackend):
            async def run(
                self, _spec: AgentSpecData, _node_id: str, _bus: EventBus
            ) -> NoReturn:
                raise StopRequestedError("different stop text")

        types: list[EventType] = []

        async def track(event: Event) -> None:
            types.append(event.type)

        bus.subscribe(track)
        result = await execute(
            'call_agent("x")', _config(), bus, TypedStopBackend(), CheckpointGate(bus)
        )
        assert result.error is None
        assert types == [
            EventType.WORKFLOW_START,
            EventType.NODE_ADDED,
            EventType.NODE_STARTED,
            EventType.NODE_STOPPED,
            EventType.WORKFLOW_END,
        ]
        assert result.graph.nodes[0].status == NodeStatus.STOPPED

    @pytest.mark.asyncio
    async def test_builtin_wrapped_node_stopped_is_not_normalized_by_text(
        self, bus: EventBus
    ) -> None:
        class WrappedStopBackend(FakeBackend):
            async def run(
                self, _spec: AgentSpecData, _node_id: str, _bus: EventBus
            ) -> NoReturn:
                raise RuntimeError("node stopped")

        result = await execute(
            'call_agent("x")', _config(), bus, WrappedStopBackend(), CheckpointGate(bus)
        )
        assert collect_outputs(bus) == []
        assert result.error is not None
        assert result.graph.nodes[0].status == NodeStatus.ERROR


class TestExecuteEvents:
    @pytest.mark.asyncio
    async def test_emits_workflow_start_and_end(self, bus: EventBus) -> None:
        """execute() emits WORKFLOW_START and WORKFLOW_END."""
        types: list[EventType] = []

        async def track(event: Event) -> None:
            types.append(event.type)

        bus.subscribe(track)
        backend = FakeBackend()
        gate = CheckpointGate(bus)
        await execute("", _config(), bus, backend, gate)
        assert types == [EventType.WORKFLOW_START, EventType.WORKFLOW_END]

    @pytest.mark.asyncio
    async def test_emits_node_lifecycle(self, bus: EventBus) -> None:
        """call_agent emits NODE_ADDED, NODE_STARTED, NODE_FINISHED."""
        types: list[EventType] = []

        async def track(event: Event) -> None:
            types.append(event.type)

        bus.subscribe(track)
        backend = FakeBackend()
        gate = CheckpointGate(bus)
        await execute('call_agent("x")', _config(), bus, backend, gate)
        assert types == [
            EventType.WORKFLOW_START,
            EventType.NODE_ADDED,
            EventType.NODE_STARTED,
            EventType.NODE_FINISHED,
            EventType.WORKFLOW_END,
        ]

    @pytest.mark.asyncio
    async def test_result_has_duration(self, bus: EventBus) -> None:
        """ExecutionResult includes duration."""
        backend = FakeBackend()
        gate = CheckpointGate(bus)
        result = await execute("", _config(), bus, backend, gate)
        assert result.duration >= 0

    @pytest.mark.asyncio
    async def test_result_cost_unknown_without_backend_cost(
        self, bus: EventBus
    ) -> None:
        """ExecutionResult leaves total cost unknown when backend omits it."""
        source = 'call_agent("x")'
        backend = FakeBackend()
        gate = CheckpointGate(bus)
        result = await execute(source, _config(), bus, backend, gate)
        assert result.total_cost_usd is None


class TestExecuteParams:
    @pytest.mark.asyncio
    async def test_params_injected(self, bus: EventBus) -> None:
        """Workflow params are available as top-level variables."""
        source = """
PARAMS = [param("name", type="string")]
output(name)
"""
        config = _config(params={"name": "alice"})
        backend = FakeBackend()
        gate = CheckpointGate(bus)
        result = await execute(source, config, bus, backend, gate)
        assert result.error is None
        assert collect_outputs(bus) == ["alice"]

    @pytest.mark.asyncio
    async def test_missing_required_param_errors(self, bus: EventBus) -> None:
        """Missing required param produces an error."""
        source = 'PARAMS = [param("required_val")]'
        config = _config(params={})
        backend = FakeBackend()
        gate = CheckpointGate(bus)
        result = await execute(source, config, bus, backend, gate)
        assert result.error is not None
        assert "required_val" in result.error


class TestPromptPreview:
    @pytest.mark.asyncio
    async def test_first_line_is_preview(self, bus: EventBus) -> None:
        """prompt_preview is the first line of the prompt."""
        source = 'call_agent("Agent Name\\nDo the task")'
        backend = FakeBackend()
        gate = CheckpointGate(bus)
        result = await execute(source, _config(), bus, backend, gate)
        node = result.graph.nodes[0]
        assert node.prompt_preview == "Agent Name"

    @pytest.mark.asyncio
    async def test_single_line_prompt(self, bus: EventBus) -> None:
        """Single-line prompt: preview == full prompt."""
        source = 'call_agent("Just one line")'
        backend = FakeBackend()
        gate = CheckpointGate(bus)
        result = await execute(source, _config(), bus, backend, gate)
        node = result.graph.nodes[0]
        assert node.prompt_preview == "Just one line"
