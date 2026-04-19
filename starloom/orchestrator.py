"""Orchestrator — wires session + runtime + servers.

Single entry point that assembles all layers: session persistence,
runtime execution, hook server, session server, checkpoint gate,
and middleware. The explicit backend argument is the instantiated
workflow-default backend; per-node execution still resolves through the
backend resolver.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator, cast

from starloom.backend.protocol import AgentBackend, BackendResolver, HookAwareBackend
from starloom.checkpoint import CheckpointGate
from starloom.events import Event, EventBus
from starloom.graph_pkg import TraceGraph
from starloom.hooks import HookServer
from starloom.middleware.protocol import AgentMiddleware
from starloom.runtime import (
    ExecutionResult,
    StaticBackendResolver,
    _make_context,
    execute,
)
from starloom.builtins.context import RuntimeContext
from starloom.server import SessionServer, StopActionResult
from starloom.session import Session, SessionManager
from starloom.session.persistence import SessionLock
from starloom.types import EventType, NodeStatus, WorkflowConfig

_GRAPH_EVENTS = frozenset(
    {
        EventType.NODE_ADDED,
        EventType.NODE_FINISHED,
        EventType.NODE_ERROR,
        EventType.NODE_CACHED,
        EventType.NODE_SKIPPED,
        EventType.NODE_STOPPED,
    }
)


@asynccontextmanager
async def _wired(
    session: Session,
    config: WorkflowConfig,
    backend: AgentBackend | None = None,
) -> AsyncIterator[_WiringContext]:
    bus, gate, graph = _init_wiring(session, config)
    backend_resolver = _make_backend_resolver(config, backend)
    hook_srv = _maybe_start_hook_server(gate, backend_resolver)
    ctx = _WiringContext(
        bus=bus,
        gate=gate,
        graph=graph,
        backend_resolver=backend_resolver,
    )
    bus.subscribe(lambda event: _clear_terminal_stop_tracking(ctx, event))
    srv = _make_session_server(bus, gate, session, graph, ctx)
    ctx.server = srv
    lock = SessionLock(session)
    lock.acquire()
    try:
        await _start_servers(
            hook_srv, srv, backend=backend, backend_resolver=backend_resolver
        )
        yield ctx
    finally:
        await _stop_servers(hook_srv, srv)
        lock.release()


def _make_session_server(
    bus: EventBus,
    gate: CheckpointGate,
    session: Session,
    graph: TraceGraph,
    ctx: _WiringContext,
) -> SessionServer:
    return SessionServer(
        bus,
        gate,
        session.dir / "session.sock",
        graph=graph,
        request_stop_node=ctx.request_stop_node,
        request_stop_session=ctx.request_stop_session,
    )


def _init_wiring(
    session: Session,
    config: WorkflowConfig,
) -> tuple[EventBus, CheckpointGate, TraceGraph]:
    bus = EventBus(session_id=session.id)
    gate = CheckpointGate(bus)
    graph = TraceGraph()
    _subscribe_persistence(bus, session, graph)
    if config.events:
        _subscribe_events_stream(bus)
    return bus, gate, graph


class _WiringContext:
    __slots__ = (
        "bus",
        "gate",
        "server",
        "graph",
        "backend_resolver",
        "session_stop_requested",
        "_stop_action_lock",
        "_stop_session_snapshot",
        "_node_stop_inflight",
        "execution_active",
        "runtime_ctx",
        "session_stop_event",
    )

    def __init__(
        self,
        bus: EventBus,
        gate: CheckpointGate,
        server: SessionServer | None = None,
        graph: TraceGraph | None = None,
        backend_resolver: BackendResolver | None = None,
    ) -> None:
        self.bus = bus
        self.gate = gate
        self.server = server
        self.graph = graph
        self.backend_resolver = backend_resolver
        self.session_stop_requested = False
        self._stop_action_lock = asyncio.Lock()
        self._stop_session_snapshot: tuple[str, ...] = ()
        self._node_stop_inflight: set[str] = set()
        self.execution_active = False
        self.runtime_ctx: RuntimeContext | None = None
        self.session_stop_event = asyncio.Event()

    async def request_stop_node(self, node_id: str) -> StopActionResult:
        async with self._stop_action_lock:
            if self.graph is None or self.backend_resolver is None:
                raise RuntimeError("Stop node handling is not configured")
            if not self.execution_active:
                return StopActionResult(False, "Session is not active")
            node = self.graph.get_node(node_id)
            if node is None:
                return StopActionResult(False, f"Node not found: {node_id}")
            if node.status != NodeStatus.RUNNING:
                return StopActionResult(False, f"Node is not running: {node_id}")
            if node.backend_name is None:
                return StopActionResult(
                    False, f"Cannot stop node without backend ownership: {node_id}"
                )
            try:
                backend = self.backend_resolver.resolve(node.backend_name)
            except ValueError as exc:
                return StopActionResult(False, str(exc))
            if node_id in self._node_stop_inflight:
                return StopActionResult(True, "stop already requested")
            self._node_stop_inflight.add(node_id)
        try:
            await backend.stop(node_id)
            return StopActionResult(True)
        except Exception as exc:
            async with self._stop_action_lock:
                self._node_stop_inflight.discard(node_id)
            return StopActionResult(False, str(exc))

    async def request_stop_session(self) -> StopActionResult:
        async with self._stop_action_lock:
            if self.graph is None or self.backend_resolver is None:
                raise RuntimeError("Stop session handling is not configured")
            if not self.execution_active:
                return StopActionResult(False, "Session is not active")
            if self.session_stop_requested:
                return StopActionResult(True, "stop already requested")
            running = [
                node for node in self.graph.nodes if node.status == NodeStatus.RUNNING
            ]
            validated: list[tuple[str, AgentBackend]] = []
            for node in running:
                if node.backend_name is None:
                    return StopActionResult(
                        False,
                        "Cannot stop session: running nodes without backend ownership: "
                        + node.id,
                    )
                try:
                    validated.append(
                        (node.id, self.backend_resolver.resolve(node.backend_name))
                    )
                except ValueError as exc:
                    return StopActionResult(False, str(exc))
            self.session_stop_requested = True
            self._stop_session_snapshot = tuple(node_id for node_id, _ in validated)
            self.session_stop_event.set()
            if self.runtime_ctx is not None:
                self.runtime_ctx.cancel_all()
            to_dispatch: list[tuple[str, AgentBackend]] = []
            for node_id, backend in validated:
                if node_id in self._node_stop_inflight:
                    continue
                self._node_stop_inflight.add(node_id)
                to_dispatch.append((node_id, backend))
        dispatch_errors: list[str] = []
        for node_id, backend in to_dispatch:
            try:
                await backend.stop(node_id)
            except Exception as exc:
                dispatch_errors.append(f"{node_id}: {exc}")
        if dispatch_errors:
            return StopActionResult(True, "; ".join(dispatch_errors))
        return StopActionResult(True)


async def run_workflow(
    config: WorkflowConfig,
    backend: AgentBackend,
    middleware: list[AgentMiddleware] | None = None,
    source: str | None = None,
    session: Session | None = None,
) -> ExecutionResult:
    if source is None:
        source = Path(config.workflow_file).read_text()
    if session is None:
        session = _create_session(config, source)

    async with _wired(session, config, backend=backend) as ctx:
        result = await _run_and_finalize(
            source, config, ctx, backend, session, middleware
        )
    return result


async def resume_workflow(
    session: Session,
    config: WorkflowConfig,
    backend: AgentBackend,
) -> ExecutionResult:
    from starloom.middleware.replay import ReplayMiddleware
    from starloom.session.persistence import load_workflow_source

    source = load_workflow_source(session)
    if not source:
        raise ValueError(f"No source saved for session {session.id}")
    SessionManager.mark_running(session)
    replay = ReplayMiddleware.from_session(session)

    async with _wired(session, config, backend=backend) as ctx:
        result = await _run_and_finalize(
            source,
            config,
            ctx,
            backend,
            session,
            cast(list[AgentMiddleware], [replay]),
        )
    return result


async def _run_and_finalize(
    source: str,
    config: WorkflowConfig,
    ctx: _WiringContext,
    backend: AgentBackend,
    session: Session,
    middleware: list[AgentMiddleware] | None,
) -> ExecutionResult:
    runtime_ctx = _make_runtime_context(config, ctx, backend)
    ctx.runtime_ctx = runtime_ctx
    ctx.session_stop_requested = False
    ctx._stop_session_snapshot = ()
    ctx._node_stop_inflight.clear()
    ctx.execution_active = True
    try:
        result = await _execute_with_stop(
            source=source,
            config=config,
            ctx=ctx,
            backend=backend,
            runtime_ctx=runtime_ctx,
            middleware=middleware,
        )
    finally:
        ctx.execution_active = False
    _finalize_session(session, result, stopped=ctx.session_stop_requested)
    return result


def _create_session(config: WorkflowConfig, source: str) -> Session:
    mgr = SessionManager()
    session = mgr.create(
        workflow_file=config.workflow_file,
        params=config.params,
        source=source,
        config=config,
    )
    mgr.set_last(session.id)
    return session


def _subscribe_persistence(
    bus: EventBus,
    session: Session,
    graph: TraceGraph,
) -> None:
    from starloom.session.persistence import append_event, save_graph, save_meta

    async def handler(event: Event) -> None:
        append_event(session, event)
        if event.type in _GRAPH_EVENTS:
            save_graph(session, graph)
        if event.type in _GRAPH_EVENTS or event.type == EventType.WORKFLOW_END:
            session.node_count = len(graph.nodes)
            session.total_cost_usd = graph.total_cost_usd
            save_meta(session)

    bus.subscribe(handler)


def _subscribe_events_stream(bus: EventBus) -> None:
    import sys

    async def handler(event: Event) -> None:
        sys.stderr.write(event.to_jsonl() + "\n")
        sys.stderr.flush()

    bus.subscribe(handler)


def _make_backend_resolver(
    config: WorkflowConfig,
    backend: AgentBackend | None,
) -> BackendResolver:
    if config.dry_run:
        return StaticBackendResolver(
            {config.backend: backend} if backend is not None else {}
        )
    if backend is None:
        return StaticBackendResolver({})
    from starloom.backend.claude_cli import ClaudeCLIBackend
    from starloom.backend.pi import PiBackend

    backends: dict[str, AgentBackend] = {config.backend: backend}
    if "claude" not in backends:
        backends["claude"] = ClaudeCLIBackend()
    if "pi" not in backends:
        backends["pi"] = PiBackend()
    return StaticBackendResolver(backends)


def _maybe_start_hook_server(
    gate: CheckpointGate,
    backend_resolver: BackendResolver | None,
) -> HookServer | None:
    if backend_resolver is None:
        return None
    for backend in backend_resolver.all().values():
        if isinstance(backend, HookAwareBackend):
            return HookServer(gate)
    return None


async def _start_servers(
    hook_server: HookServer | None,
    srv: SessionServer,
    backend: AgentBackend | None = None,
    backend_resolver: BackendResolver | None = None,
) -> None:
    if hook_server is not None:
        await hook_server.start()
        configured: set[int] = set()
        if backend_resolver is not None:
            for candidate in backend_resolver.all().values():
                if (
                    isinstance(candidate, HookAwareBackend)
                    and id(candidate) not in configured
                ):
                    candidate.configure_hook_port(hook_server.port)
                    configured.add(id(candidate))
        elif backend is not None and isinstance(backend, HookAwareBackend):
            backend.configure_hook_port(hook_server.port)
    await srv.start()


async def _stop_servers(
    hook_server: HookServer | None,
    srv: SessionServer,
) -> None:
    if hook_server is not None:
        await hook_server.stop()
    await srv.stop()


async def _execute_with_stop(
    source: str,
    config: WorkflowConfig,
    ctx: _WiringContext,
    backend: AgentBackend,
    runtime_ctx: RuntimeContext,
    middleware: list[AgentMiddleware] | None,
) -> ExecutionResult:
    async def _before_execute(_current_runtime_ctx: RuntimeContext) -> None:
        return None

    async def _after_execute(
        current_runtime_ctx: RuntimeContext,
        error: str | None,
    ) -> str | None:
        if ctx.session_stop_requested:
            await _reconcile_stopped_nodes(ctx)
            return None
        return error

    return await execute(
        source,
        config,
        ctx.bus,
        backend,
        ctx.gate,
        middleware=middleware,
        graph=runtime_ctx.graph,
        backend_resolver=ctx.backend_resolver,
        runtime_ctx=runtime_ctx,
        before_execute=_before_execute,
        after_execute=_after_execute,
    )


def _make_runtime_context(
    config: WorkflowConfig,
    ctx: _WiringContext,
    backend: AgentBackend,
) -> RuntimeContext:
    return _make_context(
        config=config,
        bus=ctx.bus,
        backend=backend,
        gate=ctx.gate,
        graph=ctx.graph,
        backend_resolver=ctx.backend_resolver,
    )


async def _reconcile_stopped_nodes(ctx: _WiringContext) -> None:
    if ctx.graph is None or not ctx.session_stop_requested:
        return
    snapshot = set(ctx._stop_session_snapshot)
    if not snapshot:
        return
    for node in ctx.graph.nodes:
        if node.id not in snapshot or node.status != NodeStatus.RUNNING:
            continue
        ctx.graph.stop_node(node.id)


def _finalize_session(
    session: Session,
    result: ExecutionResult,
    *,
    stopped: bool = False,
) -> None:
    from starloom.session.persistence import save_graph, save_meta

    session.node_count = len(result.graph.nodes)
    session.total_cost_usd = result.total_cost_usd
    if stopped:
        SessionManager.mark_stopped(session)
        session.total_cost_usd = result.total_cost_usd
        session.error = None
        save_meta(session)
    elif result.error:
        SessionManager.mark_error(session, result.error)
    else:
        SessionManager.mark_completed(session, total_cost_usd=result.total_cost_usd)
    save_graph(session, result.graph)


async def _clear_terminal_stop_tracking(ctx: _WiringContext, event: Event) -> None:
    if event.node_id is None:
        return
    if event.type in {
        EventType.NODE_STOPPED,
        EventType.NODE_FINISHED,
        EventType.NODE_ERROR,
        EventType.NODE_CACHED,
        EventType.NODE_SKIPPED,
    }:
        ctx._node_stop_inflight.discard(event.node_id)
