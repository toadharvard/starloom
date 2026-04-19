"""AgentMiddleware protocol — before/after call hooks with typed actions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from starloom.types import AgentSpecData


# ---------------------------------------------------------------------------
# Before-call actions
# ---------------------------------------------------------------------------


class BeforeAction:
    """Base for pre-execution decisions."""


@dataclass(frozen=True, slots=True)
class Run(BeforeAction):
    """Proceed with the call unchanged."""


@dataclass(frozen=True, slots=True)
class EditSpec(BeforeAction):
    """Replace the agent spec before executing."""

    new_spec: AgentSpecData


@dataclass(frozen=True, slots=True)
class Skip(BeforeAction):
    """Skip execution, return cached/default result."""

    default_result: str = ""


@dataclass(frozen=True, slots=True)
class Cancel(BeforeAction):
    """Cancel the entire workflow."""

    reason: str = ""


# ---------------------------------------------------------------------------
# After-call actions
# ---------------------------------------------------------------------------


class AfterAction:
    """Base for post-execution decisions."""


@dataclass(frozen=True, slots=True)
class Accept(AfterAction):
    """Accept the result as-is."""


@dataclass(frozen=True, slots=True)
class EditResult(AfterAction):
    """Replace the result."""

    new_result: str = ""


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


class AgentMiddleware(Protocol):
    """Hook interface for before/after agent calls."""

    def before_call(self, spec: AgentSpecData) -> BeforeAction: ...

    def after_call(self, spec: AgentSpecData, result: str) -> AfterAction: ...


# ---------------------------------------------------------------------------
# Chain
# ---------------------------------------------------------------------------


class MiddlewareChain:
    """Applies a sequence of middleware in order.

    before_call: runs each middleware in order, short-circuiting on
    Skip or Cancel. EditSpec updates the spec for subsequent middleware.

    after_call: runs each middleware in order. EditResult updates the
    result for subsequent middleware.
    """

    def __init__(self, layers: list[AgentMiddleware] | None = None) -> None:
        self._layers: list[AgentMiddleware] = list(layers) if layers else []

    def add(self, mw: AgentMiddleware) -> None:
        self._layers.append(mw)

    @property
    def layers(self) -> list[AgentMiddleware]:
        return list(self._layers)

    def before_call(self, spec: AgentSpecData) -> tuple[BeforeAction, AgentSpecData]:
        """Run before_call on each layer. Returns (action, final_spec).

        On Skip/Cancel the chain stops immediately.
        On EditSpec the spec is updated for subsequent layers.
        On Run the next layer sees the same (or edited) spec.
        """
        current_spec = spec
        for layer in self._layers:
            action = layer.before_call(current_spec)
            if isinstance(action, (Skip, Cancel)):
                return action, current_spec
            if isinstance(action, EditSpec):
                current_spec = action.new_spec
        return Run(), current_spec

    def after_call(self, spec: AgentSpecData, result: str) -> tuple[AfterAction, str]:
        """Run after_call on each layer. Returns (action, final_result).

        On EditResult the result is updated for subsequent layers.
        """
        current_result = result
        for layer in self._layers:
            action = layer.after_call(spec, current_result)
            if isinstance(action, EditResult):
                current_result = action.new_result
        return Accept(), current_result
