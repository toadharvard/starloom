"""Internal stop-distinct carriers preserved across builtins/runtime."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class NodeStopped(RuntimeError):
    """Workflow control-flow unwind caused by an isolated node stop."""

    node_id: str | None = None

    def __post_init__(self) -> None:
        RuntimeError.__init__(self, "node stopped")
