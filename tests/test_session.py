"""Tests for session state, persistence, and manager."""

from __future__ import annotations

from pathlib import Path

import pytest

from starloom.session.state import (
    Session,
    SessionStatus,
    iso_now,
    session_from_meta,
)
from starloom.session.persistence import (
    append_event,
    load_events,
    load_graph,
    load_meta,
    save_graph,
    save_meta,
    save_workflow_source,
    load_workflow_source,
    SessionLock,
    detect_crash,
)
from starloom.session.manager import SessionManager
from starloom.events import EventBus
from starloom.graph_pkg import TraceGraph
from starloom.types import AgentSpecData, EventType


@pytest.fixture
def sessions_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect SESSIONS_DIR and LAST_SESSION_FILE to tmp."""
    import starloom.session.state as state_mod
    import starloom.session.manager as mgr_mod

    sessions = tmp_path / "sessions"
    sessions.mkdir()
    last_file = sessions / ".last"

    monkeypatch.setattr(state_mod, "SESSIONS_DIR", sessions)
    monkeypatch.setattr(state_mod, "LAST_SESSION_FILE", last_file)
    monkeypatch.setattr(mgr_mod, "SESSIONS_DIR", sessions)
    monkeypatch.setattr(mgr_mod, "LAST_SESSION_FILE", last_file)
    return sessions


class TestSessionResolutionAPI:
    def test_session_dir(self, sessions_dir: Path) -> None:
        assert SessionManager.session_dir("abc") == sessions_dir / "abc"


def _make_session(session_id: str = "test-001") -> Session:
    return Session(
        id=session_id,
        workflow_file="hello.star",
        status=SessionStatus.RUNNING,
        created_at=iso_now(),
    )


# ── Session state transitions ─────────────────────────────────────


class TestSessionState:
    def test_initial_session(self, sessions_dir: Path) -> None:
        s = _make_session()
        assert s.status == SessionStatus.RUNNING
        assert s.dir == sessions_dir / "test-001"

    def test_mark_completed(self, sessions_dir: Path) -> None:
        s = _make_session()
        s.dir.mkdir(parents=True)
        SessionManager.mark_completed(s, total_cost_usd=1.5)
        assert s.status == SessionStatus.COMPLETED
        assert s.total_cost_usd == 1.5
        assert s.finished_at is not None

    def test_mark_error(self, sessions_dir: Path) -> None:
        s = _make_session()
        s.dir.mkdir(parents=True)
        SessionManager.mark_error(s, "something broke")
        assert s.status == SessionStatus.ERROR
        assert s.error == "something broke"

    def test_mark_stopped(self, sessions_dir: Path) -> None:
        s = _make_session()
        s.dir.mkdir(parents=True)
        SessionManager.mark_stopped(s)
        assert s.status == SessionStatus.STOPPED


# ── session_from_meta ────────────────────────────────────────────


class TestSessionFromMeta:
    def test_full_meta(self) -> None:
        meta: dict[str, object] = {
            "id": "abc",
            "workflow_file": "w.star",
            "status": "completed",
            "created_at": "2026-01-01T00:00:00Z",
            "finished_at": "2026-01-01T00:01:00Z",
            "output": "ok",
            "error": None,
            "node_count": 2,
            "total_cost_usd": 0.5,
            "params": {"x": "1"},
        }
        s = session_from_meta(meta)
        assert s.id == "abc"
        assert s.status == SessionStatus.COMPLETED

    def test_missing_status_defaults_to_running(self) -> None:
        """Bug #5: missing status should not crash."""
        meta: dict[str, object] = {
            "id": "xyz",
            "workflow_file": "w.star",
            "created_at": "",
        }
        s = session_from_meta(meta)
        assert s.status == SessionStatus.RUNNING

    def test_unknown_status_defaults_to_running(self) -> None:
        meta: dict[str, object] = {
            "id": "xyz",
            "workflow_file": "w.star",
            "status": "bogus",
        }
        s = session_from_meta(meta)
        assert s.status == SessionStatus.RUNNING

    def test_ignores_legacy_patch_fields(self) -> None:
        meta: dict[str, object] = {
            "id": "p1",
            "workflow_file": "w.star",
            "status": "stopped",
            "patches": [
                {"prompt": "new prompt", "flags": "--model sonnet"},
            ],
            "patch_node_ids": ["node-1"],
        }
        s = session_from_meta(meta)
        assert s.id == "p1"
        assert s.status == SessionStatus.STOPPED


# ── Persistence ──────────────────────────────────────────────────


class TestPersistence:
    def test_meta_round_trip(self, sessions_dir: Path) -> None:
        s = _make_session()
        save_meta(s)
        loaded = load_meta(s.dir)
        assert loaded["id"] == "test-001"
        assert loaded["status"] == "running"

    def test_trace_graph_round_trip(self, sessions_dir: Path) -> None:
        s = _make_session()
        g = TraceGraph()
        spec = AgentSpecData(prompt="Hello", flags="--model haiku")
        g.add_node("n1", spec, backend_name="pi")
        save_graph(s, g)
        loaded = load_graph(s)
        assert len(loaded.nodes) == 1
        assert loaded.nodes[0].id == "n1"
        assert loaded.nodes[0].backend_name == "pi"

    def test_empty_trace_graph_on_missing(self, sessions_dir: Path) -> None:
        s = _make_session()
        s.dir.mkdir(parents=True)
        g = load_graph(s)
        assert len(g.nodes) == 0

    def test_events_round_trip(self, sessions_dir: Path) -> None:
        s = _make_session()
        bus = EventBus(session_id="test-001")
        event = bus.make_event(EventType.WORKFLOW_START)
        append_event(s, event)
        events = load_events(s)
        assert len(events) == 1
        assert events[0].type == EventType.WORKFLOW_START

    def test_persisted_workflow_source_round_trip(self, sessions_dir: Path) -> None:
        s = _make_session()
        save_workflow_source(s, "call_agent('hello')")
        assert load_workflow_source(s) == "call_agent('hello')"

    def test_empty_workflow_source_on_missing(self, sessions_dir: Path) -> None:
        s = _make_session()
        s.dir.mkdir(parents=True)
        assert load_workflow_source(s) == ""


# ── Lock / crash detection ───────────────────────────────────────


class TestLock:
    def test_lock_acquire_release(self, sessions_dir: Path) -> None:
        s = _make_session()
        s.dir.mkdir(parents=True)
        lock = SessionLock(s)
        lock.acquire()
        assert lock.is_held is True
        lock.release()
        assert lock.is_held is False

    def test_double_lock_raises(self, sessions_dir: Path) -> None:
        s = _make_session()
        s.dir.mkdir(parents=True)
        lock1 = SessionLock(s)
        lock1.acquire()
        lock2 = SessionLock(s)
        with pytest.raises(RuntimeError, match="already locked"):
            lock2.acquire()
        lock1.release()

    def test_crash_detection(self, sessions_dir: Path) -> None:
        s = _make_session()
        s.dir.mkdir(parents=True)
        # No lock file → crash detected
        assert detect_crash(s) is True
        assert s.status == SessionStatus.ERROR

    def test_no_crash_if_not_running(self, sessions_dir: Path) -> None:
        s = _make_session()
        s.status = SessionStatus.COMPLETED
        assert detect_crash(s) is False


# ── SessionManager ───────────────────────────────────────────────


class TestSessionManager:
    def test_create(self, sessions_dir: Path) -> None:
        s = SessionManager.create("hello.star", source="print('hi')")
        assert s.workflow_file == "hello.star"
        assert s.status == SessionStatus.RUNNING
        assert (s.dir / "meta.json").exists()
        assert (s.dir / "workflow.star").exists()

    def test_load(self, sessions_dir: Path) -> None:
        s = SessionManager.create("hello.star")
        loaded = SessionManager.load(s.id)
        assert loaded.id == s.id

    def test_resolve_by_exact_id(self, sessions_dir: Path) -> None:
        s = SessionManager.create("hello.star")
        resolved = SessionManager.resolve(s.id)
        assert resolved.id == s.id

    def test_resolve_by_prefix(self, sessions_dir: Path) -> None:
        s = SessionManager.create("hello.star")
        prefix = s.id[:8]
        resolved = SessionManager.resolve(prefix)
        assert resolved.id == s.id

    def test_resolve_last(self, sessions_dir: Path) -> None:
        s = SessionManager.create("hello.star")
        SessionManager.set_last(s.id)
        resolved = SessionManager.resolve()
        assert resolved.id == s.id

    def test_resolve_no_sessions_raises(self, sessions_dir: Path) -> None:
        with pytest.raises(FileNotFoundError, match="No sessions found"):
            SessionManager.resolve()

    def test_list_all(self, sessions_dir: Path) -> None:
        SessionManager.create("a.star")
        SessionManager.create("b.star")
        all_sessions = SessionManager.list_all()
        assert len(all_sessions) == 2

    def test_list_with_status_filter(self, sessions_dir: Path) -> None:
        s1 = SessionManager.create("a.star")
        s2 = SessionManager.create("b.star")
        s1.dir.mkdir(parents=True, exist_ok=True)
        SessionManager.mark_completed(s1)
        running = SessionManager.list_all(status_filter=SessionStatus.RUNNING)
        assert len(running) == 1
        assert running[0].id == s2.id

    def test_delete(self, sessions_dir: Path) -> None:
        s = SessionManager.create("hello.star")
        assert s.dir.exists()
        SessionManager.delete(s.id)
        assert not s.dir.exists()

    def test_request_stop(self, sessions_dir: Path) -> None:
        s = SessionManager.create("hello.star")
        SessionManager.request_stop(s.id)
        assert (s.dir / ".stop").exists()

    def test_request_node_stop(self, sessions_dir: Path) -> None:
        s = SessionManager.create("hello.star")
        SessionManager.request_node_stop(s.id, "node-1")
        assert (s.dir / ".stop.node-1").exists()
