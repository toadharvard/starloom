"""Session listing helpers — scan, filter, build rows."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from starloom.cli.output import SessionRow
from starloom.types import SessionStatus

if TYPE_CHECKING:
    from starloom.session.state import Session


def collect_session_rows(status_filter: str | None) -> list[SessionRow]:
    """Scan session directories and build row list."""
    from starloom.session.state import SESSIONS_DIR

    if not SESSIONS_DIR.exists():
        return []
    rows: list[SessionRow] = []
    for d in sorted(SESSIONS_DIR.iterdir()):
        row = _try_load_row(d, status_filter)
        if row is not None:
            rows.append(row)
    return rows


def _try_load_row(
    session_dir: Path,
    status_filter: str | None,
) -> SessionRow | None:
    """Load one session row, returning None if filtered or invalid."""
    if not session_dir.is_dir() or not (session_dir / "meta.json").exists():
        return None
    meta = _try_read_meta(session_dir)
    if meta is None:
        return None
    sess = _try_parse_session(meta)
    if sess is None:
        return None
    if not _matches_status_filter(sess, status_filter):
        return None
    return _build_row(sess)


def _build_row(sess: Session) -> SessionRow:
    """Convert a parsed session to an output row."""
    return SessionRow(
        id=sess.id,
        status=sess.status.value,
        workflow_file=sess.workflow_file,
        created_at=sess.created_at,
        node_count=sess.node_count,
        cost_usd=sess.total_cost_usd,
    )


def _matches_status_filter(
    sess: Session,
    status_filter: str | None,
) -> bool:
    """Check if a session matches the status filter.

    'crashed' is a pseudo-status matching error sessions from killed processes.
    """
    if not status_filter:
        return True
    if status_filter == "crashed":
        return sess.status == SessionStatus.ERROR and sess.error == "crash_detected"
    return sess.status.value == status_filter


def _try_read_meta(session_dir: Path) -> dict[str, object] | None:
    """Read meta.json, returning None on I/O errors."""
    from starloom.session.persistence import load_meta

    try:
        return load_meta(session_dir)
    except (FileNotFoundError, OSError):
        return None


def _try_parse_session(meta: dict[str, object]) -> Session | None:
    """Parse session from meta dict, returning None on malformed data."""
    from starloom.session.state import session_from_meta

    try:
        return session_from_meta(meta)
    except (KeyError, ValueError):
        return None
