"""CLI session command tests using CliRunner."""

from __future__ import annotations

import importlib
import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from click.testing import CliRunner

from starloom.cli import main
from starloom.messages import ServerMsg
from starloom.session.persistence import save_meta
from starloom.session.state import Session, SessionStatus, iso_now


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def sessions_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    import starloom.session.manager as mgr_mod
    import starloom.session.state as state_mod

    sd = tmp_path / "sessions"
    sd.mkdir()
    monkeypatch.setattr(state_mod, "SESSIONS_DIR", sd)
    monkeypatch.setattr(state_mod, "LAST_SESSION_FILE", sd / ".last")
    monkeypatch.setattr(mgr_mod, "SESSIONS_DIR", sd)
    monkeypatch.setattr(mgr_mod, "LAST_SESSION_FILE", sd / ".last")
    return sd


class TestResolveHelpers:
    def test_load_session_uses_manager(self, sessions_dir: Path) -> None:
        from starloom.cli._resolve import load_session

        sess = _make_session(sessions_dir, "via-manager")
        loaded = load_session(sess.id)
        assert loaded.id == sess.id


def _make_session(
    sessions_dir: Path,
    session_id: str = "abc123",
    status: SessionStatus = SessionStatus.RUNNING,
) -> Session:
    sess = Session(
        id=session_id, workflow_file="hello.star", status=status, created_at=iso_now()
    )
    save_meta(sess)
    return sess


def _set_last(sessions_dir: Path, session_id: str) -> None:
    (sessions_dir / ".last").write_text(session_id)


class TestSessionCreate:
    def test_missing_file(self, runner: CliRunner, sessions_dir: Path) -> None:
        result = runner.invoke(main, ["session", "create", "nonexistent.star"])
        assert result.exit_code != 0

    def test_creates_session(
        self, runner: CliRunner, sessions_dir: Path, tmp_path: Path
    ) -> None:
        wf = tmp_path / "wf.star"
        wf.write_text('output("hello")')
        result = runner.invoke(main, ["session", "create", str(wf)])
        assert result.exit_code == 0
        out = json.loads(result.output)
        assert out["workflow_file"] == str(wf)
        assert out["status"] == "running"
        assert len(out["session_id"]) > 0

    def test_persists_params(
        self, runner: CliRunner, sessions_dir: Path, tmp_path: Path
    ) -> None:
        wf = tmp_path / "wf.star"
        wf.write_text("")
        result = runner.invoke(
            main, ["session", "create", str(wf), "-p", "x=1", "-p", "y=2"]
        )
        assert result.exit_code == 0
        sid = json.loads(result.output)["session_id"]
        meta = json.loads((sessions_dir / sid / "meta.json").read_text())
        assert meta["params"] == {"x": "1", "y": "2"}

    def test_invalid_param_format(
        self, runner: CliRunner, sessions_dir: Path, tmp_path: Path
    ) -> None:
        wf = tmp_path / "wf.star"
        wf.write_text("")
        result = runner.invoke(main, ["session", "create", str(wf), "-p", "badparam"])
        assert result.exit_code != 0


class TestSessionResume:
    def test_not_found(self, runner: CliRunner, sessions_dir: Path) -> None:
        result = runner.invoke(main, ["session", "resume", "no-such-session"])
        assert result.exit_code != 0

    def test_already_completed(self, runner: CliRunner, sessions_dir: Path) -> None:
        sess = _make_session(sessions_dir, status=SessionStatus.COMPLETED)
        result = runner.invoke(main, ["session", "resume", sess.id])
        assert result.exit_code != 0
        assert "completed" in result.output.lower()

    def test_resumes_stopped(self, runner: CliRunner, sessions_dir: Path) -> None:
        sess = _make_session(sessions_dir, status=SessionStatus.STOPPED)
        result = runner.invoke(main, ["session", "resume", sess.id])
        assert result.exit_code == 0
        out = json.loads(result.output)
        assert out["status"] == "running"


class TestSessionStop:
    def test_not_running_completed_noop(
        self, runner: CliRunner, sessions_dir: Path
    ) -> None:
        sess = _make_session(sessions_dir, status=SessionStatus.COMPLETED)
        result = runner.invoke(main, ["session", "stop", sess.id])
        assert result.exit_code == 1

    def test_stops_running(
        self, runner: CliRunner, sessions_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from starloom.session.service import StopSessionResult

        sess = _make_session(sessions_dir)
        session_mod = importlib.import_module("starloom.cli.session")
        monkeypatch.setattr(
            session_mod,
            "request_stop",
            lambda _sid: StopSessionResult(
                session_id=sess.id, accepted=True, status="stop_requested", message=None
            ),
        )
        result = runner.invoke(main, ["session", "stop", sess.id])
        assert result.exit_code == 0


class TestSessionList:
    def test_empty(self, runner: CliRunner, sessions_dir: Path) -> None:
        result = runner.invoke(main, ["session", "list"])
        assert result.exit_code == 0
        assert json.loads(result.output)["sessions"] == []


class TestSessionDelete:
    def test_deletes_with_confirm(self, runner: CliRunner, sessions_dir: Path) -> None:
        sess = _make_session(sessions_dir)
        result = runner.invoke(main, ["session", "delete", sess.id, "--confirm"])
        assert result.exit_code == 0
        assert not (sessions_dir / sess.id).exists()


class TestSessionAttach:
    def test_no_socket_replays_saved(
        self, runner: CliRunner, sessions_dir: Path
    ) -> None:
        sess = _make_session(sessions_dir)
        result = runner.invoke(main, ["session", "attach", sess.id])
        assert result.exit_code == 0
        assert "No events recorded for this session." in result.output

    @pytest.mark.asyncio
    async def test_stream_events_detaches_on_checkpoint_pending_for_non_rich(
        self,
    ) -> None:
        from starloom.cli.session import _stream_events
        from starloom.event_data import CheckpointPendingData
        from starloom.events import EventBus
        from starloom.messages import EventMsg
        from starloom.types import CheckpointKind, EventType
        from starloom.ui.events_renderer import EventsRenderer

        bus = EventBus(session_id="test-session")
        checkpoint_event = bus.make_event(
            EventType.CHECKPOINT_PENDING,
            node_id="node-1",
            data=CheckpointPendingData(
                checkpoint_id="cp-1",
                kind=CheckpointKind.CHECKPOINT,
                node_id="node-1",
                description="Need answer",
            ),
        )

        client = AsyncMock()
        client.read_message = AsyncMock(
            side_effect=[EventMsg(event=checkpoint_event), None]
        )

        class _FakeSessionClient:
            def __init__(self, sock_path: Path) -> None:
                self.sock_path = sock_path

            async def connect(self) -> None:
                await client.connect()

            async def read_message(self) -> ServerMsg | None:
                message = await client.read_message()
                assert message is None or isinstance(message, EventMsg)
                return message

            async def disconnect(self) -> None:
                await client.disconnect()

        monkeypatch = pytest.MonkeyPatch()
        monkeypatch.setattr("starloom.client.SessionClient", _FakeSessionClient)
        try:
            await _stream_events(Path("/tmp/test.sock"), EventsRenderer(), "events")
        finally:
            monkeypatch.undo()

        assert client.connect.await_count == 1
        assert client.read_message.await_count == 1
        assert client.disconnect.await_count == 1
