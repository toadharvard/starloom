"""Typed output schemas for CLI responses.

Every CLI command returns a typed dataclass, never a raw dict.
Formatters render these to JSON or human-readable text.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from typing import Any, Literal

from starloom.serialization import dataclass_to_dict


# ---------------------------------------------------------------------------
# Output format type
# ---------------------------------------------------------------------------

OutputFormat = Literal["rich", "json", "events"]


# ---------------------------------------------------------------------------
# Output schemas
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SessionCreated:
    session_id: str
    workflow_file: str
    status: str


@dataclass(frozen=True, slots=True)
class SessionResumed:
    session_id: str
    status: str


@dataclass(frozen=True, slots=True)
class SessionStopped:
    session_id: str
    status: str


@dataclass(frozen=True, slots=True)
class SessionDeleted:
    session_id: str


@dataclass(frozen=True, slots=True)
class SessionRow:
    """One row in session list output."""

    id: str
    status: str
    workflow_file: str
    created_at: str
    node_count: int
    cost_usd: float | None


@dataclass(frozen=True, slots=True)
class SessionList:
    sessions: tuple[SessionRow, ...]


@dataclass(frozen=True, slots=True)
class NodeRow:
    """One row in node list output."""

    id: str
    seq: int
    status: str
    kind: str
    prompt_preview: str
    cost_usd: float | None
    checkpoint_id: str | None = None


@dataclass(frozen=True, slots=True)
class NodeList:
    session_id: str
    nodes: tuple[NodeRow, ...]


@dataclass(frozen=True, slots=True)
class NodePatched:
    node_id: str
    session_id: str
    fields_changed: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class NodeStopped:
    node_id: str
    session_id: str


@dataclass(frozen=True, slots=True)
class CheckpointDecided:
    checkpoint_id: str
    action: str


@dataclass(frozen=True, slots=True)
class ExplainResult:
    topic: str
    text: str


# ---------------------------------------------------------------------------
# Serialisation
# ---------------------------------------------------------------------------


def render_json(data: Any) -> None:
    """Print a typed dataclass as JSON to stdout."""
    sys.stdout.write(json.dumps(dataclass_to_dict(data), indent=2, default=str) + "\n")


def render_text(text: str) -> None:
    """Print plain text to stdout."""
    sys.stdout.write(text + "\n")


def render_error(message: str) -> None:
    """Print error message to stderr and exit."""
    sys.stderr.write(f"Error: {message}\n")
    sys.exit(1)
