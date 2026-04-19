"""Tokenizer helpers used for dry-run estimation.

Dry-run is the only place where we estimate token usage locally.
Live backends should report factual telemetry from the agent/backend.
"""

from __future__ import annotations

from functools import lru_cache

import tiktoken

from starloom.cost import TokenUsage

_DEFAULT_ENCODING = "cl100k_base"


def estimate_usage(prompt: str, extra_text: str | None = None) -> TokenUsage:
    """Estimate dry-run token usage with tiktoken.

    We intentionally use a single tokenizer for deterministic local
    estimates. Output tokens remain a fixed dry-run allowance so estimated
    cost stays comparable across runs.
    """
    return TokenUsage(
        input_tokens=_count_text(prompt) + _count_text(extra_text),
        output_tokens=_default_output_tokens(),
    )


@lru_cache(maxsize=1)
def _encoder() -> tiktoken.Encoding:
    """Return the shared tokenizer encoder."""
    return tiktoken.get_encoding(_DEFAULT_ENCODING)


def _count_text(text: str | None) -> int:
    """Count tokens for one text fragment."""
    if not text:
        return 0
    return len(_encoder().encode(text))


def _default_output_tokens() -> int:
    """Default dry-run output token allowance."""
    return 500
