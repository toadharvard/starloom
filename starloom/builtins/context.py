"""BranchContext and RuntimeContext — shared execution state."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from starloom.backend.protocol import BackendResolver
from starloom.checkpoint import CheckpointGate
from starloom.events import EventBus
from starloom.graph_pkg import TraceGraph
from starloom.types import WorkflowConfig


class BranchContext:
    """Per-branch parent tracking for parallel execution.

    Each concurrent branch (parallel_map lane) gets its
    own BranchContext with an independent parent stack.
    """

    def __init__(self, parent_id: str | None = None) -> None:
        self._stack: list[str] = [parent_id] if parent_id else []

    @property
    def current_parent(self) -> str | None:
        return self._stack[-1] if self._stack else None

    def fork(self, parent_id: str | None = None) -> BranchContext:
        """Create a child branch context."""
        return BranchContext(parent_id=parent_id or self.current_parent)


@dataclass
class RuntimeContext:
    """Shared execution state across all builtins.

    Fields accessed from worker threads are synchronized via the event
    loop (schedule mutations via run_coroutine_threadsafe).
    """

    config: WorkflowConfig
    bus: EventBus
    graph: TraceGraph
    backend_resolver: BackendResolver
    gate: CheckpointGate
    loop: asyncio.AbstractEventLoop
    _cancel_all: bool = False

    def cancel_all(self) -> None:
        """Mark accepted workflow/session-wide stop intent.

        This prevents future workflow progression. It is never a node-local stop.
        """
        self._cancel_all = True

    @property
    def is_cancelled(self) -> bool:
        """Whether workflow/session-wide stop intent has been accepted."""
        return self._cancel_all
