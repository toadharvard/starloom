"""Stream parsing helpers for ClaudeCLIBackend.

Internal module -- not part of the public API. Contains data classes for
parser state and pure-function helpers for JSON stream processing.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

from starloom.cost import TokenUsage


@dataclass
class BlockState:
    """Mutable state for the current streaming content block."""

    block_type: str | None = None
    tool_name: str | None = None
    tool_id: str | None = None
    input_json: str = ""
    text_buffer: str = ""
    tool_start_time: float | None = None

    def reset(self) -> None:
        """Clear all block state after content_block_stop."""
        self.block_type = None
        self.tool_name = None
        self.tool_id = None
        self.input_json = ""
        self.text_buffer = ""
        self.tool_start_time = None


@dataclass
class StreamAccumulator:
    """Accumulates parsed results across the entire streaming session."""

    final_output: str = ""
    usage: TokenUsage = field(default_factory=TokenUsage)
    cost_usd: float | None = None
    backend_session_id: str | None = None
    call_seq: int = 0
    pending_tools: dict[str, str] = field(
        default_factory=dict
    )  # tool_use_id → tool_name
    reasoning: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Pure-function helpers
# ---------------------------------------------------------------------------


async def stdout_lines(
    proc: asyncio.subprocess.Process,
) -> AsyncIterator[str]:
    """Yield decoded, stripped lines from subprocess stdout."""
    assert proc.stdout is not None
    while True:
        raw = await proc.stdout.readline()
        if not raw:
            break
        line = raw.decode("utf-8", errors="replace").strip()
        if line:
            yield line


def try_parse_json(line: str) -> dict[str, object] | None:
    """Parse a JSON line into a dict.

    Returns None when *line* is not valid JSON or parses to a non-dict
    value (e.g. a bare string or array). This is expected during normal
    streaming — non-JSON lines (progress indicators, blank lines) are
    silently skipped by the caller.
    """
    try:
        msg = json.loads(line)
        return msg if isinstance(msg, dict) else None
    except (json.JSONDecodeError, ValueError):
        return None


def get_content_blocks(msg: dict[str, object]) -> list[dict[str, object]]:
    """Extract content blocks from an assistant or user message."""
    message = msg.get("message", {})
    if not isinstance(message, dict):
        return []
    content = message.get("content", [])
    if not isinstance(content, list):
        return []
    return [block for block in content if isinstance(block, dict)]


def _int_val(raw: dict[str, object], key: str) -> int:
    """Safely extract an int value from a dict with object values."""
    v = raw.get(key)
    return int(v) if isinstance(v, (int, float, str)) else 0


def parse_usage(raw: dict[str, object]) -> TokenUsage:
    """Parse a usage dict into a TokenUsage dataclass."""
    return TokenUsage(
        input_tokens=_int_val(raw, "input_tokens"),
        output_tokens=_int_val(raw, "output_tokens"),
        cache_read_tokens=_int_val(raw, "cache_read_input_tokens"),
        cache_write_tokens=_int_val(raw, "cache_creation_input_tokens"),
    )


def input_preview(tool_input: dict[str, object] | object) -> str:
    """Serialize tool input dict to a JSON string for display."""
    return json.dumps(tool_input, default=str)


def parse_cost_usd(raw: dict[str, object]) -> float | None:
    """Parse backend-reported total_cost_usd from a result message."""
    value = raw.get("total_cost_usd")
    if value is None:
        return None
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return None


async def read_stderr(proc: asyncio.subprocess.Process) -> str | None:
    """Read stderr, return string or None if empty."""
    assert proc.stderr is not None
    raw = await proc.stderr.read()
    text = raw.decode("utf-8", errors="replace").strip()
    return text or None
