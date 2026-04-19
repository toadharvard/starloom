"""DryRunBackend -- local dry-run estimation via tiktoken."""

from __future__ import annotations

from starloom.backend.protocol import AgentResult
from starloom.cost import (
    CostEstimate,
    _canonical_model,
    estimate_cost,
    get_historical_costs_per_node,
)
from starloom.events import EventBus
from starloom.tokenizer import estimate_usage
from starloom.types import AgentSpecData


def _model_from_flags(spec: AgentSpecData) -> str:
    marker = "--model "
    if marker in spec.flags:
        tail = spec.flags.split(marker, 1)[1].lstrip()
        if tail:
            return tail.split(None, 1)[0]
    return "haiku"


class DryRunBackend:
    """Returns placeholder results with tokenizer-based estimates."""

    def __init__(self) -> None:
        self._costs = get_historical_costs_per_node()

    async def run(
        self,
        spec: AgentSpecData,
        node_id: str,
        bus: EventBus,
    ) -> AgentResult:
        """Return a placeholder result with estimated usage and cost."""
        usage = estimate_usage(spec.prompt)
        cost_usd = estimate_cost(usage, _model_from_flags(spec))
        return AgentResult(
            output="[dry-run] not executed",
            cost_usd=cost_usd,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
        )

    async def stop(self, node_id: str) -> None:
        """No-op: nothing to stop in dry-run mode."""

    def estimate(self, spec: AgentSpecData) -> CostEstimate:
        """Estimate cost using historical data or tokenizer-based usage."""
        model_key = _canonical_model(_model_from_flags(spec))
        cost_data = self._costs.get(model_key)
        usage = estimate_usage(spec.prompt)
        if cost_data:
            return CostEstimate(
                usage=usage,
                cost_usd=cost_data["p50"],
                cost_usd_min=cost_data["p10"],
                cost_usd_max=cost_data["p90"],
            )
        return CostEstimate(
            usage=usage, cost_usd=estimate_cost(usage, _model_from_flags(spec))
        )
