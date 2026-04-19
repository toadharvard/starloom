"""SessionManager — create / load / resolve / list / delete sessions."""

from __future__ import annotations

import json
import os
import shutil
import time
import uuid
from datetime import timedelta
from pathlib import Path

from starloom.types import SessionStatus, WorkflowConfig

from starloom.session.state import (
    LAST_SESSION_FILE,
    SESSIONS_DIR,
    Session,
    iso_now,
    session_from_meta,
)
from starloom.session.persistence import (
    load_meta,
    save_config,
    save_meta,
    save_workflow_source,
)


class SessionManager:
    """Factory and registry for sessions.

    All filesystem access for session discovery goes through here.
    """

    # ── Create ────────────────────────────────────────────────────

    @staticmethod
    def create(
        workflow_file: str,
        params: dict[str, str] | None = None,
        source: str | None = None,
        config: WorkflowConfig | None = None,
    ) -> Session:
        """Create a new session on disk and return it."""
        session_id = _new_id()
        session = Session(
            id=session_id,
            workflow_file=workflow_file,
            status=SessionStatus.RUNNING,
            created_at=iso_now(),
            params=params or {},
        )
        save_meta(session)
        if source is not None:
            save_workflow_source(session, source)
        if config is not None:
            save_config(session, config)
        return session

    # ── Load ──────────────────────────────────────────────────────

    @staticmethod
    def load(session_id: str) -> Session:
        """Load a session by exact ID."""
        session_dir = SessionManager.session_dir(session_id)
        if not session_dir.exists():
            msg = f"Session not found: {session_id}"
            raise FileNotFoundError(msg)
        meta = load_meta(session_dir)
        return session_from_meta(meta)

    @staticmethod
    def session_dir(session_id: str) -> Path:
        """Return the storage directory for a session id."""
        return SESSIONS_DIR / session_id

    # ── Resolve (prefix / env / .last) ────────────────────────────

    @staticmethod
    def resolve(session_id: str | None = None) -> Session:
        """Load by ID prefix, env var, or last session.

        Priority: explicit arg > STARLOOM_SESSION env > .last file.
        Raises FileNotFoundError on ambiguous prefix or missing session.
        """
        effective_id = session_id or os.environ.get("STARLOOM_SESSION")
        if effective_id:
            return _resolve_by_id(effective_id)
        return _resolve_last()

    # ── List ──────────────────────────────────────────────────────

    @staticmethod
    def list_all(
        status_filter: SessionStatus | None = None,
    ) -> list[Session]:
        """List sessions, newest first. Optionally filter by status."""
        if not SESSIONS_DIR.exists():
            return []
        sessions: list[Session] = []
        for d in SESSIONS_DIR.iterdir():
            session = _try_load_dir(d)
            if session is None:
                continue
            if status_filter and session.status != status_filter:
                continue
            sessions.append(session)
        sessions.sort(key=lambda s: s.created_at, reverse=True)
        return sessions

    # ── Delete ────────────────────────────────────────────────────

    @staticmethod
    def delete(session_id: str) -> None:
        """Delete a session directory."""
        path = SESSIONS_DIR / session_id
        if path.exists():
            shutil.rmtree(path)

    @staticmethod
    def delete_older_than(duration: timedelta) -> int:
        """Delete sessions older than *duration*. Returns count deleted."""
        if not SESSIONS_DIR.exists():
            return 0
        cutoff = time.time() - duration.total_seconds()
        deleted = 0
        for d in list(SESSIONS_DIR.iterdir()):
            session = _try_load_dir(d)
            if session is None:
                continue
            if _created_timestamp(session) < cutoff:
                shutil.rmtree(d)
                deleted += 1
        return deleted

    # ── Last-session pointer ──────────────────────────────────────

    @staticmethod
    def set_last(session_id: str) -> None:
        """Write the .last pointer for quick resolution."""
        LAST_SESSION_FILE.parent.mkdir(parents=True, exist_ok=True)
        LAST_SESSION_FILE.write_text(session_id)

    @staticmethod
    def last_session_id() -> str:
        """Read the .last pointer file."""
        if LAST_SESSION_FILE.exists():
            return LAST_SESSION_FILE.read_text().strip()
        msg = "No sessions found. Run a workflow first."
        raise FileNotFoundError(msg)

    @staticmethod
    def mark_running(session: Session) -> None:
        """Persist a RUNNING state transition."""
        session.status = SessionStatus.RUNNING
        save_meta(session)

    @staticmethod
    def mark_completed(
        session: Session,
        total_cost_usd: float | None = None,
    ) -> None:
        """Persist a COMPLETED state transition."""
        session.status = SessionStatus.COMPLETED
        session.total_cost_usd = total_cost_usd
        session.finished_at = iso_now()
        save_meta(session)

    @staticmethod
    def mark_error(session: Session, error: str) -> None:
        """Persist an ERROR state transition."""
        session.status = SessionStatus.ERROR
        session.error = error
        session.finished_at = session.finished_at or iso_now()
        save_meta(session)

    @staticmethod
    def mark_stopped(session: Session) -> None:
        """Persist a STOPPED state transition."""
        session.status = SessionStatus.STOPPED
        session.finished_at = iso_now()
        save_meta(session)

    # ── Stop requests ─────────────────────────────────────────────

    @staticmethod
    def request_stop(session_id: str) -> None:
        """Write a .stop sentinel to request cancellation."""
        session_dir = SESSIONS_DIR / session_id
        session_dir.mkdir(parents=True, exist_ok=True)
        (session_dir / ".stop").touch()

    @staticmethod
    def request_node_stop(session_id: str, node_id: str) -> None:
        """Write a .stop.<node_id> sentinel."""
        session_dir = SESSIONS_DIR / session_id
        session_dir.mkdir(parents=True, exist_ok=True)
        (session_dir / f".stop.{node_id}").touch()


# ── Private helpers ───────────────────────────────────────────────────


def _new_id() -> str:
    """Generate a session ID: timestamp prefix + short UUID."""
    ts = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
    short = uuid.uuid4().hex[:8]
    return f"{ts}-{short}"


def _try_load_dir(d: Path) -> Session | None:
    """Try to load a session from a directory; return None on failure."""
    if d.name.startswith(".") or not d.is_dir():
        return None
    try:
        meta = load_meta(d)
        return session_from_meta(meta)
    except (FileNotFoundError, json.JSONDecodeError, TypeError, KeyError, ValueError):
        return None


def _resolve_by_id(effective_id: str) -> Session:
    """Resolve a session by exact or prefix match."""
    exact = SESSIONS_DIR / effective_id
    if exact.exists():
        return SessionManager.load(effective_id)
    matches = _prefix_matches(effective_id)
    if len(matches) == 1:
        return SessionManager.load(matches[0])
    if len(matches) > 1:
        listed = "\n  ".join(sorted(matches))
        msg = (
            f"Ambiguous session prefix '{effective_id}' "
            f"matches {len(matches)} sessions:\n  {listed}"
        )
        raise FileNotFoundError(msg)
    msg = f"Session not found: {effective_id}"
    raise FileNotFoundError(msg)


def _prefix_matches(prefix: str) -> list[str]:
    """Find session directory names matching a prefix."""
    if not SESSIONS_DIR.exists():
        return []
    return [
        d.name
        for d in SESSIONS_DIR.iterdir()
        if d.name.startswith(prefix) and not d.name.startswith(".")
    ]


def _resolve_last() -> Session:
    """Resolve via the .last pointer file."""
    if LAST_SESSION_FILE.exists():
        last_id = LAST_SESSION_FILE.read_text().strip()
        return SessionManager.load(last_id)
    msg = "No sessions found. Run a workflow first."
    raise FileNotFoundError(msg)


def _created_timestamp(session: Session) -> float:
    """Parse created_at ISO string to epoch seconds."""
    try:
        return time.mktime(time.strptime(session.created_at, "%Y-%m-%dT%H:%M:%SZ"))
    except (ValueError, TypeError):
        return 0.0
