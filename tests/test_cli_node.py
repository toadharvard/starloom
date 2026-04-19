"""CLI node and checkpoint command tests using CliRunner."""

from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from starloom.cli import main
from starloom.event_data import CheckpointPendingData, CheckpointResolvedData
from starloom.events import Event
from starloom.graph_pkg import TraceGraph
from starloom.session.persistence import append_event, save_graph, save_meta
from starloom.session.state import Session, SessionStatus, iso_now
from starloom.types import AgentSpecData, CheckpointKind, DecisionKind, EventType


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


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


def _make_session(sessions_dir: Path, session_id: str = "abc123") -> Session:  # noqa: ARG001
    sess = Session(
        id=session_id,
        workflow_file="hello.star",
        status=SessionStatus.RUNNING,
        created_at=iso_now(),
    )
    save_meta(sess)
    return sess


def _add_node(sess: Session, node_id: str = "node-1") -> TraceGraph:
    spec = AgentSpecData(
        prompt="Do something useful",
        flags="--model haiku",
    )
    graph = TraceGraph()
    graph.add_node(node_id, spec)
    save_graph(sess, graph)
    return graph


# ---------------------------------------------------------------------------
# node list
# ---------------------------------------------------------------------------


class TestNodeList:
    def test_no_last_session(self, runner: CliRunner, sessions_dir: Path) -> None:
        result = runner.invoke(main, ["node", "list"])
        assert result.exit_code != 0

    def test_session_not_found(self, runner: CliRunner, sessions_dir: Path) -> None:
        result = runner.invoke(main, ["node", "list", "-s", "no-such"])
        assert result.exit_code != 0

    def test_empty_graph(self, runner: CliRunner, sessions_dir: Path) -> None:
        sess = _make_session(sessions_dir)
        result = runner.invoke(main, ["node", "list", "-s", sess.id])
        assert result.exit_code == 0
        assert json.loads(result.output)["nodes"] == []

    def test_lists_nodes(self, runner: CliRunner, sessions_dir: Path) -> None:
        sess = _make_session(sessions_dir)
        _add_node(sess, "alpha")
        result = runner.invoke(main, ["node", "list", "-s", sess.id])
        assert result.exit_code == 0
        out = json.loads(result.output)
        assert len(out["nodes"]) == 1
        assert out["nodes"][0]["id"] == "alpha"

    def test_uses_last_session(self, runner: CliRunner, sessions_dir: Path) -> None:
        sess = _make_session(sessions_dir, "lsess1")
        _add_node(sess, "n1")
        (sessions_dir / ".last").write_text(sess.id)
        result = runner.invoke(main, ["node", "list"])
        assert result.exit_code == 0

    def test_lists_pending_checkpoint_id_for_checkpoint_node(
        self, runner: CliRunner, sessions_dir: Path
    ) -> None:
        sess = _make_session(sessions_dir)
        graph = TraceGraph()
        graph.add_checkpoint_node("cp-node", "approve this?")
        save_graph(sess, graph)
        append_event(
            sess,
            Event(
                type=EventType.CHECKPOINT_PENDING,
                timestamp=1.0,
                session_id=sess.id,
                node_id="cp-node",
                data=CheckpointPendingData(
                    checkpoint_id="cp-123",
                    kind=CheckpointKind.CHECKPOINT,
                    node_id="cp-node",
                    description="approve this?",
                ),
            ),
        )
        result = runner.invoke(main, ["node", "list", "-s", sess.id])
        assert result.exit_code == 0
        out = json.loads(result.output)
        assert out["nodes"][0]["id"] == "cp-node"
        assert out["nodes"][0]["checkpoint_id"] == "cp-123"

    def test_keeps_checkpoint_id_after_resolution(
        self, runner: CliRunner, sessions_dir: Path
    ) -> None:
        sess = _make_session(sessions_dir)
        graph = TraceGraph()
        graph.add_checkpoint_node("cp-node", "approve this?")
        save_graph(sess, graph)
        append_event(
            sess,
            Event(
                type=EventType.CHECKPOINT_PENDING,
                timestamp=1.0,
                session_id=sess.id,
                node_id="cp-node",
                data=CheckpointPendingData(
                    checkpoint_id="cp-123",
                    kind=CheckpointKind.CHECKPOINT,
                    node_id="cp-node",
                    description="approve this?",
                ),
            ),
        )
        append_event(
            sess,
            Event(
                type=EventType.CHECKPOINT_RESOLVED,
                timestamp=2.0,
                session_id=sess.id,
                node_id="cp-node",
                data=CheckpointResolvedData(
                    checkpoint_id="cp-123",
                    decision=DecisionKind.ANSWERED,
                ),
            ),
        )
        result = runner.invoke(main, ["node", "list", "-s", sess.id])
        assert result.exit_code == 0
        out = json.loads(result.output)
        assert out["nodes"][0]["checkpoint_id"] == "cp-123"


# ---------------------------------------------------------------------------
# node stop
# ---------------------------------------------------------------------------


class TestNodeStop:
    def test_node_not_found(self, runner: CliRunner, sessions_dir: Path) -> None:
        sess = _make_session(sessions_dir)
        result = runner.invoke(main, ["node", "stop", "missing", "-s", sess.id])
        assert result.exit_code != 0

    def test_requests_node_stop(
        self, runner: CliRunner, sessions_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sess = _make_session(sessions_dir)
        _add_node(sess, "node-1")
        node_mod = importlib.import_module("starloom.cli.node")

        from starloom.messages import ActionResultMsg

        def _ok(*_args: object, **_kwargs: object) -> ActionResultMsg:
            return ActionResultMsg(ok=True)

        monkeypatch.setattr(node_mod, "_send_stop_node", _ok)
        (sess.dir / "session.sock").write_text("")
        result = runner.invoke(main, ["node", "stop", "node-1", "-s", sess.id])
        assert result.exit_code == 0
        out = json.loads(result.output)
        assert out["node_id"] == "node-1"
        assert out["session_id"] == sess.id

    def test_stop_reports_failure_when_server_rejects(
        self,
        runner: CliRunner,
        sessions_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        sess = _make_session(sessions_dir)
        _add_node(sess, "node-1")
        node_mod = importlib.import_module("starloom.cli.node")

        from starloom.messages import ActionResultMsg

        def _reject(*_args: object, **_kwargs: object) -> ActionResultMsg:
            return ActionResultMsg(ok=False, error="node is not running")

        monkeypatch.setattr(node_mod, "_send_stop_node", _reject)
        (sess.dir / "session.sock").write_text("")
        result = runner.invoke(main, ["node", "stop", "node-1", "-s", sess.id])
        assert result.exit_code == 1
        assert result.output == "Error: node is not running\n"


# ---------------------------------------------------------------------------
# node patch
# ---------------------------------------------------------------------------


class TestNodePatch:
    def test_node_not_found(self, runner: CliRunner, sessions_dir: Path) -> None:
        sess = _make_session(sessions_dir)
        result = runner.invoke(
            main,
            ["node", "patch", "missing", "-s", sess.id, "--flags", "--model sonnet"],
        )
        assert result.exit_code != 0

    def test_patches_flags(self, runner: CliRunner, sessions_dir: Path) -> None:
        sess = _make_session(sessions_dir)
        _add_node(sess, "n1")
        result = runner.invoke(
            main, ["node", "patch", "n1", "-s", sess.id, "--flags", "--model sonnet"]
        )
        assert result.exit_code == 0
        out = json.loads(result.output)
        assert "flags" in out["fields_changed"]

    def test_patches_prompt(self, runner: CliRunner, sessions_dir: Path) -> None:
        sess = _make_session(sessions_dir)
        _add_node(sess, "n1")
        result = runner.invoke(
            main, ["node", "patch", "n1", "-s", sess.id, "--prompt", "new prompt"]
        )
        assert result.exit_code == 0
        assert "prompt" in json.loads(result.output)["fields_changed"]


# ---------------------------------------------------------------------------
# checkpoint commands (require server socket — test error paths)
# ---------------------------------------------------------------------------


class TestCheckpointApprove:
    def test_no_server(self, runner: CliRunner, sessions_dir: Path) -> None:
        sess = _make_session(sessions_dir)
        result = runner.invoke(main, ["checkpoint", "approve", "cp-1", "-s", sess.id])
        assert result.exit_code != 0

    def test_session_not_found(self, runner: CliRunner, sessions_dir: Path) -> None:
        result = runner.invoke(main, ["checkpoint", "approve", "cp-1", "-s", "no-such"])
        assert result.exit_code != 0


class TestCheckpointReject:
    def test_no_server(self, runner: CliRunner, sessions_dir: Path) -> None:
        sess = _make_session(sessions_dir)
        result = runner.invoke(
            main,
            ["checkpoint", "reject", "cp-1", "-s", sess.id, "--reason", "not safe"],
        )
        assert result.exit_code != 0

    def test_session_not_found(self, runner: CliRunner, sessions_dir: Path) -> None:
        result = runner.invoke(main, ["checkpoint", "reject", "cp-1", "-s", "no-such"])
        assert result.exit_code != 0


class TestCheckpointAnswer:
    def test_no_server(self, runner: CliRunner, sessions_dir: Path) -> None:
        sess = _make_session(sessions_dir)
        result = runner.invoke(
            main, ["checkpoint", "answer", "cp-1", "the answer", "-s", sess.id]
        )
        assert result.exit_code != 0

    def test_session_not_found(self, runner: CliRunner, sessions_dir: Path) -> None:
        result = runner.invoke(
            main, ["checkpoint", "answer", "cp-1", "reply", "-s", "no-such"]
        )
        assert result.exit_code != 0
