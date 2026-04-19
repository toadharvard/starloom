"""Parallel execution builtins — spec builder plus parallel runner."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable
from typing import cast

from starloom.backend.protocol import WorkflowStopped
from starloom.builtins.context import BranchContext, RuntimeContext
from starloom.stop import NodeStopped


def make_agent() -> Callable[..., dict[str, object]]:
    """Return agent() closure — builds a work-item spec for parallel_map."""

    def agent(
        prompt: str,
        backend: str | None = None,
        flags: str = "",
    ) -> dict[str, object]:
        """Return a work-item spec for parallel_map. Does not execute."""
        d: dict[str, object] = {"prompt": prompt}
        pairs: list[tuple[str, object]] = [
            ("backend", backend),
            ("flags", flags),
        ]
        d.update({k: v for k, v in pairs if v is not None})
        return d

    return agent


def make_run_parallel(
    ctx: RuntimeContext,
    branch: BranchContext,
) -> Callable[[list[dict[str, object]]], list[str]]:
    """Return _run_parallel closure."""

    def _run_parallel(specs: list[dict[str, object]]) -> list[str]:
        """Run spec dicts concurrently with forked BranchContexts."""
        if ctx.is_cancelled:
            raise WorkflowStopped("workflow stopped")
        future = asyncio.run_coroutine_threadsafe(
            _run_parallel_async(ctx, branch, specs),
            ctx.loop,
        )
        return future.result()

    return _run_parallel


async def _run_one(
    ctx: RuntimeContext,
    branch: BranchContext,
    spec_dict: dict[str, object],
    parallel_group_id: str,
    results: list[str | NodeStopped | None],
    errors: list[str | None],
    idx: int,
) -> None:
    """Run a single parallel branch, storing result or error."""
    try:
        results[idx] = await _run_one_branch(
            ctx,
            branch,
            spec_dict,
            parallel_group_id,
        )
    except NodeStopped as exc:
        results[idx] = exc
    except Exception as exc:
        errors[idx] = str(exc)


async def _run_one_branch(
    ctx: RuntimeContext,
    branch: BranchContext,
    spec_dict: dict[str, object],
    parallel_group_id: str,
) -> str:
    """Execute one parallel work item in a forked branch."""
    from starloom.builtins.agents import (
        _emit_add_and_start,
        _make_spec,
        _resolve_backend_target,
        _run_agent,
    )

    forked = branch.fork()
    spec = _make_spec(
        str(spec_dict.get("prompt", "")), ctx, cast(str, spec_dict.get("flags", ""))
    )
    effective_backend_name, _backend = _resolve_backend_target(
        ctx,
        cast(str | None, spec_dict.get("backend")),
    )
    node_id = uuid.uuid4().hex[:8]
    seq = await _emit_add_and_start(
        ctx,
        node_id,
        spec,
        forked,
        parallel_group_id,
        backend_name=effective_backend_name,
    )
    return await _run_agent(
        ctx,
        spec,
        node_id,
        seq,
        effective_backend_name,
    )


async def _run_parallel_async(
    ctx: RuntimeContext,
    branch: BranchContext,
    specs: list[dict[str, object]],
) -> list[str]:
    """Run all specs concurrently. Sibling continues on error."""
    if ctx.is_cancelled:
        raise WorkflowStopped("workflow stopped")
    parallel_group_id = uuid.uuid4().hex[:8]
    results: list[str | NodeStopped | None] = [None] * len(specs)
    errors: list[str | None] = [None] * len(specs)
    await asyncio.gather(
        *[
            _run_one(ctx, branch, s, parallel_group_id, results, errors, i)
            for i, s in enumerate(specs)
        ]
    )
    if ctx.is_cancelled:
        raise WorkflowStopped("workflow stopped")
    _check_parallel_errors(results, errors)
    return _finalize_parallel_results(results)


def _finalize_parallel_results(
    results: list[str | NodeStopped | None],
) -> list[str]:
    finalized: list[str] = []
    for result in results:
        if result is None:
            raise RuntimeError("parallel branch result missing")
        if isinstance(result, NodeStopped):
            raise result
        finalized.append(result)
    return finalized


def _check_parallel_errors(
    results: list[str | NodeStopped | None],
    errors: list[str | None],
) -> None:
    """Raise if any parallel branch failed."""
    failed = [(i, e) for i, e in enumerate(errors) if e is not None]
    if not failed:
        return
    msg = f"{len(failed)}/{len(results)} parallel branches failed:\n"
    for idx, err in failed:
        msg += f"  [{idx}]: {err}\n"
    raise RuntimeError(msg)
