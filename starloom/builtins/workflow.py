"""Workflow builtins — output(), fail(), checkpoints, and internal asset loading."""

from __future__ import annotations

import asyncio
from pathlib import Path
import uuid
from collections.abc import Callable


from starloom.backend.protocol import WorkflowFailed
from starloom.builtins._emit import emit_node_started
from starloom.builtins.context import BranchContext, RuntimeContext
from starloom.checkpoint import (
    AnswerAction,
    Checkpoint,
    RejectAction,
    make_checkpoint_id,
)
from starloom.event_data import NodeAddedData, NodeFinishedData, WorkflowOutputData
from starloom.graph_pkg.node import NODE_KIND_CHECKPOINT
from starloom.types import CheckpointKind, EventType


def make_output(ctx: RuntimeContext) -> Callable[[str | None], None]:
    """Return output() closure."""

    def output(result: str | None) -> None:
        """Emit a workflow output block. Print-like: each call is independent."""
        future = asyncio.run_coroutine_threadsafe(
            _emit_workflow_output(ctx, result),
            ctx.loop,
        )
        future.result()

    return output


async def _emit_workflow_output(ctx: RuntimeContext, result: str | None) -> None:
    await ctx.bus.emit(
        ctx.bus.make_event(
            EventType.WORKFLOW_OUTPUT,
            data=WorkflowOutputData(output=result),
        )
    )


def make_fail() -> Callable[[str], None]:
    """Return fail() closure."""

    def fail(message: str) -> None:
        """Abort workflow execution with an explicit failure."""
        raise WorkflowFailed(message)

    return fail


def make_checkpoint(
    ctx: RuntimeContext,
    branch: BranchContext,
) -> Callable[..., str]:
    """Return checkpoint() closure for workflow-authored operator prompts.

    Creates a visible checkpoint node so explicit workflow pauses appear in the
    trace graph.
    """

    def checkpoint(question: str) -> str:
        """Pause workflow, wait for an operator response, and return it."""
        if ctx.config.dry_run:
            return ""
        future = asyncio.run_coroutine_threadsafe(
            _checkpoint_async(ctx, branch, question),
            ctx.loop,
        )
        return future.result()

    return checkpoint


def make_load_internal_skill(ctx: RuntimeContext) -> Callable[[str], str]:
    """Return load_internal_skill() closure.

    Paths are resolved relative to the workflow file's parent directory so a
    packaged build workflow can load its colocated internal skill assets.
    """

    def load_internal_skill(path_from_root: str) -> str:
        return _load_internal_skill_text(ctx, path_from_root)

    return load_internal_skill


async def _checkpoint_async(
    ctx: RuntimeContext,
    branch: BranchContext,
    question: str,
) -> str:
    """Create an explicit workflow checkpoint node and record its result."""
    node_id = uuid.uuid4().hex[:8]
    node = ctx.graph.add_checkpoint_node(
        node_id=node_id,
        question=question,
        parent_id=branch.current_parent,
    )
    ctx.graph.start_node(node_id)
    await _emit_checkpoint_start(ctx, node_id, node.seq, question)
    decision = await _wait_checkpoint(ctx, node_id, question)
    answer = decision.answer if isinstance(decision, AnswerAction) else ""
    _finish_checkpoint_node(ctx, node_id, decision, answer)
    await _emit_node_finished(ctx, node_id, node.seq, answer)
    return answer


async def _emit_checkpoint_start(
    ctx: RuntimeContext,
    node_id: str,
    seq: int,
    question: str,
) -> None:
    """Emit node-added and node-started events for a checkpoint node."""
    await _emit_node_added(ctx, node_id, seq, question)
    await emit_node_started(ctx, node_id, seq)


async def _wait_checkpoint(
    ctx: RuntimeContext,
    node_id: str,
    question: str,
) -> AnswerAction | RejectAction:
    """Create an explicit workflow checkpoint and wait for a response or rejection."""
    cp = Checkpoint(
        id=make_checkpoint_id(),
        kind=CheckpointKind.CHECKPOINT,
        node_id=node_id,
        description=question,
    )
    decision = await ctx.gate.wait(cp)
    if isinstance(decision, AnswerAction | RejectAction):
        return decision
    raise RuntimeError(
        "checkpoint() accepts operator responses or rejections, not approvals"
    )


def _finish_checkpoint_node(
    ctx: RuntimeContext,
    node_id: str,
    decision: AnswerAction | RejectAction,
    answer: str,
) -> None:
    """Mark the checkpoint node as completed or error based on decision."""
    if isinstance(decision, RejectAction):
        ctx.graph.fail_node(node_id, "rejected by operator")
    else:
        ctx.graph.finish_node(node_id, answer, None, None, None)


async def _emit_node_added(
    ctx: RuntimeContext,
    node_id: str,
    seq: int,
    question: str,
) -> None:
    await ctx.bus.emit(
        ctx.bus.make_event(
            EventType.NODE_ADDED,
            node_id=node_id,
            seq=seq,
            data=NodeAddedData(
                prompt_preview=question,
                kind=NODE_KIND_CHECKPOINT,
                backend_name=None,
            ),
        )
    )


async def _emit_node_finished(
    ctx: RuntimeContext,
    node_id: str,
    seq: int,
    answer: str,
) -> None:
    await ctx.bus.emit(
        ctx.bus.make_event(
            EventType.NODE_FINISHED,
            node_id=node_id,
            seq=seq,
            data=NodeFinishedData(
                result=answer,
                cost_usd=None,
                input_tokens=None,
                output_tokens=None,
                backend_name=None,
            ),
        )
    )


def _load_internal_skill_text(ctx: RuntimeContext, path_from_root: str) -> str:
    if not path_from_root:
        raise ValueError("load_internal_skill() requires a non-empty path")
    if path_from_root.startswith("/"):
        raise ValueError(
            "load_internal_skill() expects a root-relative path, not an absolute path"
        )
    if ".." in Path(path_from_root).parts:
        raise ValueError("load_internal_skill() does not allow '..' path traversal")

    workflow_path = Path(ctx.config.workflow_file).resolve()
    root_dir = workflow_path.parent
    full_path = (root_dir / path_from_root).resolve()

    try:
        full_path.relative_to(root_dir)
    except ValueError as exc:
        raise ValueError(
            "load_internal_skill() resolved outside the workflow root"
        ) from exc

    if not full_path.exists():
        raise ValueError(f"Internal skill not found: {path_from_root}")
    if not full_path.is_file():
        raise ValueError(f"Internal skill path is not a file: {path_from_root}")

    return full_path.read_text()
