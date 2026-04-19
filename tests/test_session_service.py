from __future__ import annotations

import json
from pathlib import Path

import pytest

import importlib

from starloom.session.persistence import save_meta
from starloom.session.service import request_stop, validate_resumable
from starloom.session.state import Session, SessionStatus, iso_now


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


def _make_session(
    session_id: str = "svc-001",
    status: SessionStatus = SessionStatus.RUNNING,
) -> Session:
    sess = Session(
        id=session_id,
        workflow_file="hello.star",
        status=status,
        created_at=iso_now(),
    )
    save_meta(sess)
    return sess


def test_request_stop_does_not_mutate_terminal_state(
    sessions_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sess = _make_session()
    service_mod = importlib.import_module("starloom.session.service")
    monkeypatch.setattr(
        service_mod,
        "_send_stop_session",
        lambda _sock: service_mod.StopDeliveryResult(True, None),
    )
    (sessions_dir / sess.id / "session.sock").write_text("")
    result = request_stop(sess.id)
    assert result.accepted is True
    assert result.status == "stop_requested"
    assert result.message == "stop request accepted"
    meta = json.loads((sessions_dir / sess.id / "meta.json").read_text())
    assert meta["status"] == "running"


def test_request_stop_for_non_running_is_noop(sessions_dir: Path) -> None:
    sess = _make_session(status=SessionStatus.COMPLETED)
    result = request_stop(sess.id)
    assert result.accepted is False
    assert result.status == "completed"


def test_validate_resumable_rejects_completed(sessions_dir: Path) -> None:
    sess = _make_session(status=SessionStatus.COMPLETED)
    with pytest.raises(ValueError, match="completed"):
        validate_resumable(sess.id)
