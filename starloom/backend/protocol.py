"""AgentBackend protocol -- abstract interface for agent execution."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from starloom.event_data import NodeStoppedData
from starloom.events import EventBus
from starloom.types import AgentSpecData


@dataclass(frozen=True, slots=True)
class AgentResult:
    """Immutable result from an agent execution.

    Live backends should report only factual telemetry they actually
    receive from the agent/runtime. Unknown values stay ``None``.
    """

    output: str
    cost_usd: float | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    error: str | None = None
    backend_session_id: str | None = None


class StopRequestedError(RuntimeError):
    """Backend node execution was interrupted by an explicit node-scoped stop.

    Callers must preserve this as stop semantics, not collapse it into a generic error.
    """

    def __init__(self, reason: str = "stop requested") -> None:
        super().__init__(reason)
        self.data = NodeStoppedData(reason=reason)


class WorkflowStopped(RuntimeError):
    """Workflow execution stopped due to explicit session/workflow-wide stop intent.

    This is distinct from StopRequestedError, which is node-scoped.
    """


class WorkflowFailed(RuntimeError):
    """Workflow execution failed explicitly via the fail() builtin."""


class AgentBackend(Protocol):
    """Protocol for pluggable agent backends."""

    async def run(
        self,
        spec: AgentSpecData,
        node_id: str,
        bus: EventBus,
    ) -> AgentResult: ...

    async def stop(self, node_id: str) -> None: ...


@runtime_checkable
class HookAwareBackend(Protocol):
    """Optional capability for backends that can consume hook server ports."""

    def configure_hook_port(self, port: int) -> None: ...


class BackendResolver(Protocol):
    """Resolve backend instances by configured short name."""

    def resolve(self, name: str) -> AgentBackend: ...

    def all(self) -> dict[str, AgentBackend]: ...
