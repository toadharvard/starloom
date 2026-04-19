"""Cost and token helpers used for telemetry and dry-run estimation."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from starloom.graph_pkg.node import AgentNodePayload


@dataclass(frozen=True, slots=True)
class TokenUsage:
    """Immutable token usage record."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    def __add__(self, other: TokenUsage) -> TokenUsage:
        return TokenUsage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            cache_read_tokens=self.cache_read_tokens + other.cache_read_tokens,
            cache_write_tokens=self.cache_write_tokens + other.cache_write_tokens,
        )


MODEL_PRICING: dict[str, tuple[float, float]] = {
    "opus": (15.0, 75.0),
    "sonnet": (3.0, 15.0),
    "haiku": (0.25, 1.25),
}

_MILLION = 1_000_000


def resolve_pricing(model: str) -> tuple[float, float]:
    """Return (input_price, output_price) per million tokens."""
    lower = model.lower()
    for name, pricing in MODEL_PRICING.items():
        if name in lower:
            return pricing
    return MODEL_PRICING["sonnet"]


def estimate_cost(usage: TokenUsage, model: str) -> float:
    """Calculate USD cost from token usage and model name."""
    inp, out = resolve_pricing(model)
    return usage.input_tokens * inp / _MILLION + usage.output_tokens * out / _MILLION


@dataclass(frozen=True, slots=True)
class CostEstimate:
    """Cost estimate with percentile ranges from historical data."""

    usage: TokenUsage
    cost_usd: float
    cost_usd_min: float | None = None
    cost_usd_max: float | None = None


def _canonical_model(model: str) -> str:
    """Map a model name to its canonical key (haiku/sonnet/opus)."""
    lower = model.lower()
    for name in ("opus", "sonnet", "haiku"):
        if name in lower:
            return name
    return lower


def get_historical_costs_per_node() -> dict[str, dict[str, float]]:
    """Collect historical cost-per-node from completed sessions."""
    try:
        from starloom.session.state import SESSIONS_DIR

        if not SESSIONS_DIR.exists():
            return {}
        return _scan_sessions(SESSIONS_DIR)
    except ImportError:
        return {}


def _scan_sessions(sessions_dir: Path) -> dict[str, dict[str, float]]:
    costs: dict[str, list[float]] = {}
    for d in sessions_dir.iterdir():
        _collect_session_costs(d, costs)
    return _compute_percentiles(costs)


def _collect_session_costs(
    session_dir: Path,
    costs: dict[str, list[float]],
) -> None:
    if not session_dir.is_dir() or not (session_dir / "graph.json").exists():
        return
    try:
        import json
        from starloom.graph_pkg import TraceGraph

        data = json.loads((session_dir / "graph.json").read_text())
        graph = TraceGraph.from_dict(data)
        for node in graph.nodes:
            if node.cost_usd and node.cost_usd > 0:
                if isinstance(node.payload, AgentNodePayload):
                    key = _canonical_model(node.payload.spec.flags)
                    costs.setdefault(key, []).append(node.cost_usd)
    except (OSError, ValueError):
        pass


def _compute_percentiles(
    costs: dict[str, list[float]],
) -> dict[str, dict[str, float]]:
    result: dict[str, dict[str, float]] = {}
    for model, cost_list in costs.items():
        if not cost_list:
            continue
        s = sorted(cost_list)
        n = len(s)
        result[model] = {
            "p10": s[max(0, int(n * 0.1))],
            "p50": s[max(0, int(n * 0.5))],
            "p90": s[min(n - 1, int(n * 0.9))],
            "count": float(n),
        }
    return result
