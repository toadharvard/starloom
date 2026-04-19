"""Tests for cost.py — TokenUsage and pricing helpers."""

from __future__ import annotations

import pytest

from starloom.cost import TokenUsage, estimate_cost, resolve_pricing


class TestTokenUsage:
    def test_frozen(self) -> None:
        u = TokenUsage(input_tokens=100, output_tokens=50)
        with pytest.raises(AttributeError):
            u.input_tokens = 200  # type: ignore[misc]

    def test_total_tokens(self) -> None:
        u = TokenUsage(input_tokens=100, output_tokens=50)
        assert u.total_tokens == 150

    def test_add(self) -> None:
        a = TokenUsage(input_tokens=100, output_tokens=50, cache_read_tokens=10)
        b = TokenUsage(input_tokens=200, output_tokens=100, cache_write_tokens=5)
        c = a + b
        assert c.input_tokens == 300
        assert c.output_tokens == 150
        assert c.cache_read_tokens == 10
        assert c.cache_write_tokens == 5

    def test_defaults(self) -> None:
        u = TokenUsage()
        assert u.input_tokens == 0
        assert u.output_tokens == 0
        assert u.total_tokens == 0


class TestPricing:
    def test_haiku_pricing(self) -> None:
        inp, out = resolve_pricing("haiku")
        assert inp == 0.25
        assert out == 1.25

    def test_substring_match(self) -> None:
        inp, out = resolve_pricing("claude-haiku-4-5-20251001")
        assert inp == 0.25

    def test_unknown_defaults_to_sonnet(self) -> None:
        inp, out = resolve_pricing("unknown-model")
        assert inp == 3.0
        assert out == 15.0

    def test_estimate_cost(self) -> None:
        usage = TokenUsage(input_tokens=1_000_000, output_tokens=1_000_000)
        cost = estimate_cost(usage, "haiku")
        assert abs(cost - (0.25 + 1.25)) < 1e-9
