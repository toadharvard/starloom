"""Persistence helpers — persist and load session metadata, trace graph, events, workflow source copy, and lock state."""

from __future__ import annotations

import fcntl
import json
from pathlib import Path
from typing import IO, TYPE_CHECKING, cast

from starloom.events import Event
from starloom.graph_pkg import TraceGraph
from starloom.types import SessionStatus, WorkflowConfig

if TYPE_CHECKING:
    from starloom.session.state import Session


# ── Meta ──────────────────────────────────────────────────────────────


def save_meta(session: Session) -> None:
    """Write meta.json to session directory."""
    session.dir.mkdir(parents=True, exist_ok=True)
    path = session.dir / "meta.json"
    path.write_text(json.dumps(session.to_meta_dict(), indent=2, default=str))


def load_meta(session_dir: Path) -> dict[str, object]:
    """Read and parse meta.json from a session directory."""
    path = session_dir / "meta.json"
    if not path.exists():
        msg = f"meta.json not found in {session_dir}"
        raise FileNotFoundError(msg)
    result: dict[str, object] = json.loads(path.read_text())
    return result


# ── Graph ─────────────────────────────────────────────────────────────


def save_graph(session: Session, graph: TraceGraph) -> None:
    """Persist the session trace graph to graph.json."""
    session.dir.mkdir(parents=True, exist_ok=True)
    (session.dir / "graph.json").write_text(graph.to_json())


def load_graph(session: Session) -> TraceGraph:
    """Load the persisted session trace graph from graph.json."""
    path = session.dir / "graph.json"
    if not path.exists():
        return TraceGraph()
    data = json.loads(path.read_text())
    return TraceGraph.from_dict(data)


# ── Events ────────────────────────────────────────────────────────────


def append_event(session: Session, event: Event) -> None:
    """Append a single event line to events.jsonl."""
    session.dir.mkdir(parents=True, exist_ok=True)
    with (session.dir / "events.jsonl").open("a") as f:
        f.write(event.to_jsonl() + "\n")


def load_events(session: Session) -> list[Event]:
    """Load events.jsonl and reconstruct Events with typed EventData."""
    path = session.dir / "events.jsonl"
    if not path.exists():
        return []
    events: list[Event] = []
    with path.open() as f:
        for line in f:
            line = line.strip()
            if line:
                events.append(Event.from_jsonl(line))
    return events


# ── Config ───────────────────────────────────────────────────────────


def save_config(session: Session, config: WorkflowConfig) -> None:
    """Write config.json to session directory."""
    session.dir.mkdir(parents=True, exist_ok=True)
    path = session.dir / "config.json"
    path.write_text(json.dumps(_config_to_dict(config)))


def load_config(session: Session) -> WorkflowConfig:
    """Load config.json from session directory."""
    path = session.dir / "config.json"
    return _config_from_dict(json.loads(path.read_text()))


def _config_to_dict(config: WorkflowConfig) -> dict[str, object]:
    """Serialize WorkflowConfig to a plain dict."""
    d: dict[str, object] = {
        "workflow_file": config.workflow_file,
        "params": config.params,
        "backend": config.backend,
        "dry_run": config.dry_run,
        "events": config.events,
        "challenge": config.challenge,
    }
    return d


def _config_from_dict(d: dict[str, object]) -> WorkflowConfig:
    """Deserialize a plain dict to WorkflowConfig."""
    return WorkflowConfig(
        workflow_file=str(d["workflow_file"]),
        params=cast(dict[str, str], d.get("params", {})),
        backend=str(d.get("backend", "claude")),
        dry_run=bool(d.get("dry_run", False)),
        events=bool(d.get("events", False)),
        challenge=bool(d.get("challenge", False)),
    )


# ── Workflow source ───────────────────────────────────────────────────


def save_workflow_source(session: Session, source: str) -> None:
    """Write the persisted workflow source copy to workflow.star."""
    session.dir.mkdir(parents=True, exist_ok=True)
    (session.dir / "workflow.star").write_text(source)


def load_workflow_source(session: Session) -> str:
    """Load the persisted workflow source copy from workflow.star."""
    path = session.dir / "workflow.star"
    if path.exists():
        return path.read_text()
    return ""


# ── Lock / crash detection ───────────────────────────────────────────


class SessionLock:
    """POSIX advisory lock on a session directory.

    Held until close() or process death (enabling crash detection).
    Use as a context manager for automatic cleanup.
    """

    def __init__(self, session: Session) -> None:
        self._session = session
        self._fd: IO[str] | None = None

    @property
    def is_held(self) -> bool:
        return self._fd is not None

    def acquire(self) -> None:
        self._session.dir.mkdir(parents=True, exist_ok=True)
        lock_path = self._session.dir / "lock"
        fd = open(lock_path, "w")  # noqa: SIM115
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (BlockingIOError, OSError) as exc:
            fd.close()
            msg = f"Session {self._session.id} is already locked (another process?)"
            raise RuntimeError(msg) from exc
        self._fd = fd

    def release(self) -> None:
        if self._fd is None:
            return
        try:
            fcntl.flock(self._fd, fcntl.LOCK_UN)
            self._fd.close()
        except OSError:
            pass
        self._fd = None
        (self._session.dir / "lock").unlink(missing_ok=True)

    def __enter__(self) -> SessionLock:
        self.acquire()
        return self

    def __exit__(self, *_: object) -> None:
        self.release()


def acquire_lock(session: Session) -> SessionLock:
    """Acquire an exclusive advisory lock and return the lock owner."""
    lock = SessionLock(session)
    lock.acquire()
    return lock


def release_lock(lock: SessionLock) -> None:
    """Release a previously acquired advisory lock."""
    lock.release()


def detect_crash(session: Session) -> bool:
    """Check whether a RUNNING session's owner process is dead.

    Returns True if crash detected (lock is stale). Also marks the
    session as ERROR with reason 'crash_detected'.
    """
    if session.status != SessionStatus.RUNNING:
        return False
    lock_path = session.dir / "lock"
    if not lock_path.exists():
        from starloom.session.manager import SessionManager

        SessionManager.mark_error(session, "crash_detected")
        return True
    return _try_steal_lock(session, lock_path)


def _try_steal_lock(session: Session, lock_path: Path) -> bool:
    """Attempt to acquire a stale lock. Returns True if crash detected."""
    try:
        fd = open(lock_path, "w")  # noqa: SIM115
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        # Got the lock => owner process is dead
        fcntl.flock(fd, fcntl.LOCK_UN)
        fd.close()
        from starloom.session.manager import SessionManager

        SessionManager.mark_error(session, "crash_detected")
        return True
    except (BlockingIOError, OSError):
        # Lock held => process alive
        return False
