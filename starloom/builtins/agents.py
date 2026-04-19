"""Agent execution builtins — immediate calls plus parallel spec helpers."""

from __future__ import annotations

import asyncio
import concurrent.futures
import uuid
from collections.abc import Callable, Coroutine
from typing import Any

from starloom.backend.protocol import AgentBackend, AgentResult, StopRequestedError
from starloom.builtins._emit import emit_node_started
from starloom.builtins.context import BranchContext, RuntimeContext
from starloom.graph_pkg.node import NODE_KIND_AGENT
from starloom.event_data import (
    NodeAddedData,
    NodeCachedData,
    NodeErrorData,
    NodeFinishedData,
)
from starloom.middleware.protocol import AgentMiddleware, Cancel, EditSpec, Skip
from starloom.stop import NodeStopped
from starloom.types import AgentSpecData, EventType


def _make_spec(
    prompt: str,
    ctx: RuntimeContext,
    flags: str = "",
) -> AgentSpecData:
    """Build an AgentSpecData from call_agent arguments."""
    return AgentSpecData(prompt=prompt, flags=flags.strip())


def _resolve_backend_target(
    ctx: RuntimeContext,
    name: str | None,
) -> tuple[str, AgentBackend]:
    """Return the effective backend name and resolved backend instance."""
    backend_name = name or ctx.config.backend
    return backend_name, ctx.backend_resolver.resolve(backend_name)


def _apply_middleware(
    spec: AgentSpecData,
    middleware: list[AgentMiddleware],
) -> tuple[AgentSpecData, str | None]:
    """Apply before_call middleware. Returns (spec, cached_result|None)."""
    for middleware_item in middleware:
        action = middleware_item.before_call(spec)
        if isinstance(action, Skip):
            return spec, action.default_result
        if isinstance(action, EditSpec):
            spec = action.new_spec
        elif isinstance(action, Cancel):
            raise RuntimeError(f"Cancelled: {action.reason}")
    return spec, None


async def _emit_node_added(
    ctx: RuntimeContext,
    node_id: str,
    spec: AgentSpecData,
    seq: int,
    parallel_group: str | None,
    backend_name: str | None,
) -> None:
    """Emit NODE_ADDED event."""
    await ctx.bus.emit(
        ctx.bus.make_event(
            EventType.NODE_ADDED,
            node_id=node_id,
            seq=seq,
            data=NodeAddedData(
                prompt_preview=spec.prompt_preview,
                kind=NODE_KIND_AGENT,
                parallel_group=parallel_group,
                backend_name=backend_name,
            ),
        )
    )


async def _emit_add_and_start(
    ctx: RuntimeContext,
    node_id: str,
    spec: AgentSpecData,
    branch: BranchContext,
    parallel_group: str | None = None,
    backend_name: str | None = None,
) -> int:
    """Add node to graph, emit NODE_ADDED + NODE_STARTED. Return seq."""
    node = ctx.graph.add_node(
        node_id=node_id,
        spec=spec,
        parent_id=branch.current_parent,
        parallel_group=parallel_group,
        kind=NODE_KIND_AGENT,
        backend_name=backend_name,
    )
    ctx.graph.start_node(node_id)
    await _emit_node_added(
        ctx,
        node_id,
        spec,
        node.seq,
        parallel_group,
        backend_name,
    )
    await emit_node_started(ctx, node_id, node.seq)
    return node.seq


async def _handle_cached(
    ctx: RuntimeContext,
    node_id: str,
    spec: AgentSpecData,
    cached_result: str,
    branch: BranchContext,
    backend_name: str,
) -> str:
    """Record a cached node with explicit backend ownership and emit events."""
    node = ctx.graph.add_node(
        node_id=node_id,
        spec=spec,
        parent_id=branch.current_parent,
        kind=NODE_KIND_AGENT,
        backend_name=backend_name,
    )
    ctx.graph.cache_node(node_id, cached_result)
    await _emit_node_added(
        ctx,
        node_id,
        spec,
        node.seq,
        None,
        backend_name,
    )
    await ctx.bus.emit(
        ctx.bus.make_event(
            EventType.NODE_CACHED,
            node_id=node_id,
            seq=node.seq,
            data=NodeCachedData(result=cached_result),
        )
    )
    return cached_result


async def _run_agent(
    ctx: RuntimeContext,
    spec: AgentSpecData,
    node_id: str,
    seq: int,
    backend_name: str | None = None,
) -> str:
    """Execute agent via backend, record telemetry, emit finish/error."""
    effective_backend_name, backend = _resolve_backend_target(ctx, backend_name)
    try:
        result = await backend.run(spec, node_id, ctx.bus)
        await _record_success(
            ctx,
            result,
            node_id,
            seq,
            spec,
            effective_backend_name,
        )
        return result.output
    except StopRequestedError as exc:
        ctx.graph.stop_node(node_id)
        await ctx.bus.emit(
            ctx.bus.make_event(
                EventType.NODE_STOPPED,
                node_id=node_id,
                seq=seq,
                data=exc.data,
            )
        )
        raise NodeStopped(node_id=node_id) from exc
    except Exception as exc:
        ctx.graph.fail_node(node_id, str(exc))
        await ctx.bus.emit(
            ctx.bus.make_event(
                EventType.NODE_ERROR,
                node_id=node_id,
                seq=seq,
                data=NodeErrorData(error=str(exc)),
            )
        )
        raise


async def _emit_node_finished(
    ctx: RuntimeContext,
    result: AgentResult,
    node_id: str,
    seq: int,
    backend_name: str | None,
) -> None:
    """Emit NODE_FINISHED event."""
    await ctx.bus.emit(
        ctx.bus.make_event(
            EventType.NODE_FINISHED,
            node_id=node_id,
            seq=seq,
            data=NodeFinishedData(
                result=result.output,
                cost_usd=result.cost_usd,
                input_tokens=result.input_tokens,
                output_tokens=result.output_tokens,
                backend_name=backend_name,
            ),
        )
    )


async def _record_success(
    ctx: RuntimeContext,
    result: AgentResult,
    node_id: str,
    seq: int,
    spec: AgentSpecData,
    backend_name: str | None,
) -> None:
    """Record successful agent result in graph and events."""
    ctx.graph.finish_node(
        node_id,
        result.output,
        result.cost_usd,
        result.input_tokens,
        result.output_tokens,
    )
    await _emit_node_finished(ctx, result, node_id, seq, backend_name)


def _bridge_async(
    ctx: RuntimeContext,
    coro: Coroutine[Any, Any, str],
) -> str:
    """Block Starlark worker thread on an async coroutine."""
    future: concurrent.futures.Future[str] = asyncio.run_coroutine_threadsafe(
        coro,
        ctx.loop,
    )
    return future.result()


def _invoke_call_agent(
    ctx: RuntimeContext,
    middleware: list[AgentMiddleware],
    branch: BranchContext,
    prompt: str,
    flags: str,
    backend: str | None,
) -> str:
    """Core call_agent implementation (sync, immediate execution)."""
    from starloom.backend.protocol import WorkflowStopped

    if ctx.is_cancelled:
        raise WorkflowStopped("workflow stopped")
    spec, cached = _apply_middleware(
        _make_spec(prompt, ctx, flags),
        middleware,
    )
    return _bridge_async(
        ctx,
        _call_agent_async(
            ctx,
            spec,
            uuid.uuid4().hex[:8],
            cached,
            branch,
            backend,
        ),
    )


def make_call_agent(
    ctx: RuntimeContext,
    middleware: list[AgentMiddleware],
    branch: BranchContext,
) -> Callable[..., str]:
    """Return call_agent closure."""

    def call_agent(
        prompt: str,
        backend: str | None = None,
        flags: str = "",
    ) -> str:
        return _invoke_call_agent(
            ctx,
            middleware,
            branch,
            prompt,
            flags,
            backend,
        )

    return call_agent


async def _call_agent_async(
    ctx: RuntimeContext,
    spec: AgentSpecData,
    node_id: str,
    cached: str | None,
    branch: BranchContext,
    backend_name: str | None,
) -> str:
    """Async implementation for immediate agent execution."""
    effective_backend_name, _backend = _resolve_backend_target(ctx, backend_name)
    if cached is not None:
        return await _handle_cached(
            ctx,
            node_id,
            spec,
            cached,
            branch,
            effective_backend_name,
        )
    seq = await _emit_add_and_start(
        ctx,
        node_id,
        spec,
        branch,
        backend_name=effective_backend_name,
    )
    return await _run_agent(ctx, spec, node_id, seq, effective_backend_name)
