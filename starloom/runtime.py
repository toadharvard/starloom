"""Runtime -- pure execute(source, config, bus) -> ExecutionResult.

Threading model:
- Asyncio event loop runs on the main thread (agent I/O, event dispatch).
- Starlark execution runs in ThreadPoolExecutor workers.
- Workers bridge to async via asyncio.run_coroutine_threadsafe().
"""

from __future__ import annotations

import asyncio
import time
from concurrent.futures import ThreadPoolExecutor
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from starloom.backend.protocol import (
    AgentBackend,
    BackendResolver,
    StopRequestedError,
    WorkflowFailed,
    WorkflowStopped,
)
from starloom.stop import NodeStopped
from starloom.builtins import PRELUDE, BranchContext, RuntimeContext, make_builtins
from starloom.checkpoint import CheckpointGate
from starloom.event_data import WorkflowEndData, WorkflowStartData
from starloom.events import EventBus
from starloom.graph_pkg import TraceGraph
from starloom.params import parse_params, resolve_params
from starloom.middleware.protocol import AgentMiddleware
from starloom.types import EventType, WorkflowConfig

# Starlark builtins dict passed to s.set(**builtins).
# Values are callables, resolved params, or the PARAMS dict.
StarlarkBuiltins = dict[str, object]


@dataclass(frozen=True, slots=True)
class ExecutionResult:
    """Immutable result of a workflow execution."""

    session_id: str
    graph: TraceGraph
    duration: float
    total_cost_usd: float | None
    error: str | None = None


async def execute(
    source: str,
    config: WorkflowConfig,
    bus: EventBus,
    backend: AgentBackend,
    gate: CheckpointGate,
    middleware: list[AgentMiddleware] | None = None,
    graph: TraceGraph | None = None,
    backend_resolver: BackendResolver | None = None,
    runtime_ctx: RuntimeContext | None = None,
    *,
    before_execute: Callable[[RuntimeContext], Awaitable[None]] | None = None,
    after_execute: Callable[[RuntimeContext, str | None], Awaitable[str | None]]
    | None = None,
) -> ExecutionResult:
    """Run a workflow and emit workflow lifecycle events."""
    ctx = runtime_ctx or _make_context(
        config,
        bus,
        backend,
        gate,
        graph,
        backend_resolver=backend_resolver,
    )
    start = time.time()
    if before_execute is not None:
        await before_execute(ctx)
    await _emit_start(bus, config)
    error = await _execute_workflow(source, config, ctx, middleware)
    duration = time.time() - start
    if after_execute is not None:
        error = await after_execute(ctx, error)
    await _emit_end(bus, ctx.graph, error, duration)
    return _make_result(bus.session_id, ctx.graph, duration, error)


class StaticBackendResolver:
    """Resolve backends from an explicit name->instance mapping."""

    def __init__(self, backends: dict[str, AgentBackend]) -> None:
        self._backends = dict(backends)

    def resolve(self, name: str) -> AgentBackend:
        try:
            return self._backends[name]
        except KeyError as exc:
            raise ValueError(f"Unknown backend: {name}") from exc

    def all(self) -> dict[str, AgentBackend]:
        return dict(self._backends)


def _make_context(
    config: WorkflowConfig,
    bus: EventBus,
    backend: AgentBackend,
    gate: CheckpointGate,
    graph: TraceGraph | None = None,
    backend_resolver: BackendResolver | None = None,
) -> RuntimeContext:
    """Build RuntimeContext with fresh (or provided) graph.

    The explicit resolver is the authority for node execution. When absent,
    bootstrap a resolver that exposes the provided backend as the workflow's
    default backend instance.
    """
    resolver = backend_resolver or StaticBackendResolver({config.backend: backend})
    return RuntimeContext(
        config=config,
        bus=bus,
        graph=graph or TraceGraph(),
        backend_resolver=resolver,
        gate=gate,
        loop=asyncio.get_running_loop(),
    )


async def _execute_workflow(
    source: str,
    config: WorkflowConfig,
    ctx: RuntimeContext,
    middleware: list[AgentMiddleware] | None,
) -> str | None:
    """Run the workflow, returning an error message or None."""
    if ctx.is_cancelled:
        return None
    try:
        builtins = _build_builtins(source, config, ctx, middleware or [])
        await _run_in_executor(ctx.loop, builtins, source)
        return None
    except Exception as exc:
        if _is_stop_outcome(exc):
            return None
        failed_message = _extract_workflow_failed_message(exc)
        if failed_message is not None:
            return failed_message
        if _is_builtin_node_stop_error(exc) and _graph_has_stopped_node(ctx.graph):
            return None
        if _is_builtin_fail_error(exc):
            return _extract_builtin_fail_message(str(exc))
        # Starlark runtime can raise any exception type (EvalError,
        # SyntaxError, builtin errors, etc.) — catch broadly to report.
        return str(exc)


def _make_result(
    session_id: str,
    graph: TraceGraph,
    duration: float,
    error: str | None,
) -> ExecutionResult:
    """Build the final ExecutionResult."""
    return ExecutionResult(
        session_id=session_id,
        graph=graph,
        duration=duration,
        total_cost_usd=graph.total_cost_usd,
        error=error,
    )


async def _emit_start(bus: EventBus, config: WorkflowConfig) -> None:
    """Emit workflow.start event."""
    await bus.emit(
        bus.make_event(
            EventType.WORKFLOW_START,
            data=WorkflowStartData(
                workflow_file=config.workflow_file,
                params=config.params,
            ),
        )
    )


async def _emit_end(
    bus: EventBus,
    graph: TraceGraph,
    error: str | None,
    duration: float,
) -> None:
    """Emit workflow.end event (always called)."""
    await bus.emit(
        bus.make_event(
            EventType.WORKFLOW_END,
            data=WorkflowEndData(
                duration=duration,
                total_cost_usd=graph.total_cost_usd,
                node_count=len(graph.nodes),
                error=error,
            ),
        )
    )


def _build_builtins(
    source: str,
    config: WorkflowConfig,
    ctx: RuntimeContext,
    middleware: list[AgentMiddleware],
) -> StarlarkBuiltins:
    """Parse params, resolve, build Starlark builtins dict."""
    declared = parse_params(source)
    resolved = resolve_params(declared, config.params)
    branch = BranchContext()
    builtins = make_builtins(ctx, middleware, branch)
    builtins["PARAMS"] = resolved
    for key, val in resolved.items():
        builtins[key] = val
    return builtins


async def _run_in_executor(
    loop: asyncio.AbstractEventLoop,
    builtins: StarlarkBuiltins,
    source: str,
) -> None:
    """Run Starlark in a ThreadPoolExecutor."""
    executor = ThreadPoolExecutor(max_workers=1)
    try:
        await loop.run_in_executor(
            executor,
            _run_starlark,
            builtins,
            source,
        )
    finally:
        executor.shutdown(wait=False)


def _is_stop_outcome(exc: Exception) -> bool:
    current: BaseException | None = exc
    while current is not None:
        if isinstance(current, WorkflowStopped | NodeStopped | StopRequestedError):
            return True
        context = getattr(current, "__context__", None)
        if context is not None and context is not current:
            current = context
            continue
        current = current.__cause__
    return False


def _extract_workflow_failed_message(exc: Exception) -> str | None:
    current: BaseException | None = exc
    while current is not None:
        if isinstance(current, WorkflowFailed):
            return str(current)
        current = current.__cause__
    return None


def _is_builtin_fail_error(exc: Exception) -> bool:
    text = str(exc)
    return text.startswith("<builtin> in fail:")


def _is_builtin_node_stop_error(exc: Exception) -> bool:
    return str(exc) == "<builtin> in call_agent:0:0: node stopped"


def _extract_builtin_fail_message(text: str) -> str:
    _, _, message = text.partition(": ")
    return message or text


def _graph_has_stopped_node(graph: TraceGraph) -> bool:
    return any(node.status.name == "STOPPED" for node in graph.nodes)


def _run_starlark(builtins: StarlarkBuiltins, source: str) -> None:
    """Execute Starlark source with builtins. Runs on worker thread."""
    from starlark_go import Starlark

    s = Starlark()
    s.set(**builtins)
    s.exec(PRELUDE)
    s.exec(source)
