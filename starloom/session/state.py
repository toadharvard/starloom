"""Session dataclass — metadata and directory layout."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from collections.abc import Mapping
from typing import TYPE_CHECKING, cast

from starloom.types import SessionStatus

__all__ = [
    "SESSIONS_DIR",
    "LAST_SESSION_FILE",
    "Session",
    "SessionStatus",
    "session_from_meta",
    "iso_now",
]

if TYPE_CHECKING:
    from starloom.graph_pkg import TraceGraph


SESSIONS_DIR = Path.home() / ".starloom" / "sessions"
LAST_SESSION_FILE = SESSIONS_DIR / ".last"


@dataclass(slots=True)
class Session:
    """A persisted workflow session with metadata, event history, source copy, and trace graph."""

    id: str
    workflow_file: str
    status: SessionStatus
    created_at: str  # ISO 8601
    finished_at: str | None = None
    error: str | None = None
    node_count: int = 0
    total_cost_usd: float | None = None
    params: dict[str, str] = field(default_factory=dict)

    @property
    def dir(self) -> Path:
        """Session directory: ~/.starloom/sessions/<id>."""
        return SESSIONS_DIR / self.id

    def load_graph(self) -> TraceGraph:
        """Load the persisted trace graph for this session."""
        from starloom.session.persistence import load_graph

        return load_graph(self)

    def to_meta_dict(self) -> dict[str, object]:
        """Serialise to a plain dict for meta.json."""
        return {
            "id": self.id,
            "workflow_file": self.workflow_file,
            "status": self.status.value,
            "created_at": self.created_at,
            "finished_at": self.finished_at,
            "error": self.error,
            "node_count": self.node_count,
            "total_cost_usd": self.total_cost_usd,
            "params": self.params,
        }


def _meta_str(meta: Mapping[str, object], key: str, default: str = "") -> str:
    return str(meta.get(key, default))


def _meta_str_or_none(meta: Mapping[str, object], key: str) -> str | None:
    val = meta.get(key)
    return str(val) if val is not None else None


def session_from_meta(meta: Mapping[str, object]) -> Session:
    """Reconstruct a Session from a meta.json dict.

    Tolerates missing 'status' — defaults to RUNNING.
    """
    return Session(
        id=str(meta["id"]),
        workflow_file=_meta_str(meta, "workflow_file", "<unknown>"),
        status=_parse_status(meta.get("status")),
        created_at=_meta_str(meta, "created_at"),
        finished_at=_meta_str_or_none(meta, "finished_at"),
        error=_meta_str_or_none(meta, "error"),
        node_count=int(str(meta.get("node_count", 0))),
        total_cost_usd=(
            float(str(meta.get("total_cost_usd")))
            if meta.get("total_cost_usd") is not None
            else None
        ),
        params=cast(dict[str, str], meta.get("params", {})),
    )


def _parse_status(raw: str | object) -> SessionStatus:
    """Parse status from meta, defaulting to RUNNING if missing."""
    if raw is None:
        return SessionStatus.RUNNING
    try:
        return SessionStatus(raw)
    except ValueError:
        return SessionStatus.RUNNING


def iso_now() -> str:
    """Current UTC time in ISO 8601."""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
