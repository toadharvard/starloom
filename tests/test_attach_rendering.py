from __future__ import annotations

from pathlib import Path

from starloom.cli.session import (
    _build_snapshot_from_events,
    _dispatch_msg,
    _replay_saved_events,
    _should_end_attach_on_event,
    _should_end_attach_on_snapshot,
)
from starloom.event_data import (
    CheckpointPendingData,
    CheckpointResolvedData,
    WorkflowEndData,
    WorkflowOutputData,
    WorkflowStartData,
)
from starloom.events import Event, EventBus
from starloom.messages import SnapshotMsg
from starloom.session.persistence import append_event, save_meta
from starloom.session.state import Session, SessionStatus, iso_now
from starloom.ui.protocol import NodeSnapshot, SessionSnapshot
from starloom.ui.snapshot import SnapshotBuilder
from starloom.types import CheckpointKind, EventType, NodeStatus


class _Renderer:
    def __init__(self) -> None:
        self.builder = SnapshotBuilder("test-session")
        self.replay = False
        self.logged: list[str] = []
        self.snapshots: list[SessionSnapshot] = []
        self.closed: list[str] = []

    def begin_replay(self) -> None:
        self.replay = True

    def end_replay(self) -> None:
        self.replay = False
        self.on_snapshot(self.builder.snapshot())

    def on_event(self, event: Event) -> None:
        self.builder.handle_event(event)
        if not self.replay:
            self.logged.append(event.type.value)

    def on_snapshot(self, snapshot: SessionSnapshot) -> None:
        self.snapshots.append(snapshot)

    def on_closed(self, reason: str) -> None:
        self.closed.append(reason)


def _session(tmp_path: Path) -> Session:
    sess = Session(
        id="test-session",
        workflow_file="w.star",
        status=SessionStatus.COMPLETED,
        created_at=iso_now(),
    )
    sess.dir.mkdir(parents=True, exist_ok=True)
    save_meta(sess)
    return sess


def test_dispatch_snapshot_replays_snapshot_silently() -> None:
    bus = EventBus(session_id="test-session")
    events = (
        bus.make_event(
            EventType.WORKFLOW_START,
            data=WorkflowStartData(workflow_file="w.star", params={}),
        ),
        bus.make_event(
            EventType.WORKFLOW_END,
            data=WorkflowEndData(
                duration=1.0,
                total_cost_usd=None,
                node_count=0,
                error=None,
            ),
        ),
    )
    renderer = _Renderer()

    _dispatch_msg(SnapshotMsg(events=events), renderer)

    assert renderer.logged == []
    assert len(renderer.snapshots) == 1
    assert renderer.snapshots[0].workflow_file == "w.star"


def test_rich_terminal_replay_does_not_print_historical_checkpoint_blocks() -> None:
    from starloom.ui.rich_terminal import RichTerminal

    bus = EventBus(session_id="test-session")
    pending = bus.make_event(
        EventType.CHECKPOINT_PENDING,
        node_id="n1",
        data=CheckpointPendingData(
            checkpoint_id="cp1",
            kind=CheckpointKind.CHECKPOINT,
            node_id="n1",
            description="need approval",
        ),
    )
    resolved = bus.make_event(
        EventType.CHECKPOINT_RESOLVED,
        node_id="n1",
        data=CheckpointResolvedData(
            checkpoint_id="cp1",
            decision="answered",
            decided_by="operator",
        ),
    )

    terminal = RichTerminal("test-session")
    printed: list[object] = []
    terminal._console = type(
        "C", (), {"print": lambda _self, obj="": printed.append(obj)}
    )()
    terminal._live = type(
        "L",
        (),
        {
            "start": lambda _self: None,
            "stop": lambda _self: None,
            "update": lambda _self, _renderable: None,
        },
    )()

    _dispatch_msg(SnapshotMsg(events=(pending, resolved)), terminal, "rich")
    terminal.on_closed("done")

    assert printed == []


def test_rich_terminal_starts_and_stops_ticker() -> None:
    from starloom.ui.rich_terminal import RichTerminal

    terminal = RichTerminal("test-session")
    terminal._live = type(
        "L",
        (),
        {
            "start": lambda _self: None,
            "stop": lambda _self: None,
            "update": lambda _self, _renderable: None,
        },
    )()

    terminal.begin_replay()
    assert terminal._ticker is not None
    terminal.on_closed("done")
    assert terminal._ticker is None


def test_rich_terminal_header_shows_prompt_preview_for_running_node() -> None:
    from starloom.ui.rich_terminal import RichTerminal

    terminal = RichTerminal("test-session")
    snapshot = SessionSnapshot(
        session_id="test-session",
        workflow_file="wf.star",
        elapsed=12.3,
        total_cost_usd=None,
        nodes=(
            NodeSnapshot(
                node_id="node-1",
                seq=1,
                kind="agent",
                status=NodeStatus.RUNNING,
                prompt_preview="Implement benchmark harness",
                elapsed=None,
                cost_usd=None,
                input_tokens=None,
                output_tokens=None,
                parallel_group=None,
                error=None,
            ),
        ),
        pending_checkpoints=(),
    )

    panel = terminal._render_header(snapshot)
    lines = [str(item) for item in panel.renderable.renderables]
    text = "\n".join(lines)
    assert "node-1" in text
    assert "Implement benchmark harness" in text


def test_replay_saved_events_builds_snapshot_without_logging(tmp_path: Path) -> None:
    sess = _session(tmp_path)
    bus = EventBus(session_id="test-session")
    append_event(
        sess,
        bus.make_event(
            EventType.WORKFLOW_START,
            data=WorkflowStartData(workflow_file="w.star", params={}),
        ),
    )
    append_event(
        sess,
        bus.make_event(
            EventType.WORKFLOW_END,
            data=WorkflowEndData(
                duration=1.0,
                total_cost_usd=None,
                node_count=0,
                error=None,
            ),
        ),
    )
    renderer = _Renderer()

    _replay_saved_events(sess, renderer)

    assert renderer.logged == []
    assert len(renderer.snapshots) == 1
    assert renderer.closed == ["replay complete"]


class _DetachRenderer:
    def on_event(self, _event: Event) -> None:
        return None


def test_should_end_attach_depends_on_output_mode() -> None:
    bus = EventBus(session_id="test-session")
    event = bus.make_event(
        EventType.CHECKPOINT_PENDING,
        node_id="n1",
        data=CheckpointPendingData(
            checkpoint_id="cp1",
            kind=CheckpointKind.CHECKPOINT,
            node_id="n1",
            description="need approval",
        ),
    )

    assert _should_end_attach_on_event(event) is True


def test_should_end_attach_ignores_non_event_objects() -> None:
    assert _should_end_attach_on_event(object()) is False


def test_dispatch_snapshot_detaches_when_replay_contains_pending() -> None:
    bus = EventBus(session_id="test-session")
    event = bus.make_event(
        EventType.CHECKPOINT_PENDING,
        node_id="n1",
        data=CheckpointPendingData(
            checkpoint_id="cp1",
            kind=CheckpointKind.CHECKPOINT,
            node_id="n1",
            description="need approval",
        ),
    )
    renderer = _DetachRenderer()

    assert _dispatch_msg(SnapshotMsg(events=(event,)), renderer, "events") is True


def test_dispatch_snapshot_does_not_detach_on_resolved_historical_checkpoint() -> None:
    bus = EventBus(session_id="test-session")
    pending = bus.make_event(
        EventType.CHECKPOINT_PENDING,
        node_id="n1",
        data=CheckpointPendingData(
            checkpoint_id="cp1",
            kind=CheckpointKind.CHECKPOINT,
            node_id="n1",
            description="need approval",
        ),
    )
    resolved = bus.make_event(
        EventType.CHECKPOINT_RESOLVED,
        node_id="n1",
        data=CheckpointResolvedData(
            checkpoint_id="cp1",
            decision="answered",
            decided_by="operator",
        ),
    )
    renderer = _Renderer()

    assert (
        _dispatch_msg(SnapshotMsg(events=(pending, resolved)), renderer, "rich")
        is False
    )
    assert renderer.snapshots[-1].pending_checkpoints == ()


def test_build_snapshot_from_events_removes_resolved_checkpoint() -> None:
    bus = EventBus(session_id="test-session")
    events = (
        bus.make_event(
            EventType.WORKFLOW_START,
            data=WorkflowStartData(workflow_file="w.star", params={}),
        ),
        bus.make_event(
            EventType.CHECKPOINT_PENDING,
            node_id="n1",
            data=CheckpointPendingData(
                checkpoint_id="cp1",
                kind=CheckpointKind.CHECKPOINT,
                node_id="n1",
                description="need approval",
            ),
        ),
        bus.make_event(
            EventType.CHECKPOINT_RESOLVED,
            node_id="n1",
            data=CheckpointResolvedData(
                checkpoint_id="cp1",
                decision="answered",
                decided_by="operator",
            ),
        ),
    )

    snapshot = _build_snapshot_from_events(events)

    assert snapshot.pending_checkpoints == ()


def test_should_end_attach_on_snapshot_uses_final_snapshot_state() -> None:
    bus = EventBus(session_id="test-session")
    pending = bus.make_event(
        EventType.CHECKPOINT_PENDING,
        node_id="n1",
        data=CheckpointPendingData(
            checkpoint_id="cp1",
            kind=CheckpointKind.CHECKPOINT,
            node_id="n1",
            description="need approval",
        ),
    )
    resolved = bus.make_event(
        EventType.CHECKPOINT_RESOLVED,
        node_id="n1",
        data=CheckpointResolvedData(
            checkpoint_id="cp1",
            decision="answered",
            decided_by="operator",
        ),
    )

    assert (
        _should_end_attach_on_snapshot(SnapshotMsg(events=(pending,)), _Renderer())
        is True
    )
    assert (
        _should_end_attach_on_snapshot(
            SnapshotMsg(events=(pending, resolved)), _Renderer()
        )
        is False
    )


def test_rich_detaches_on_workflow_end() -> None:
    bus = EventBus(session_id="test-session")
    event = bus.make_event(
        EventType.WORKFLOW_END,
        data=WorkflowEndData(
            duration=1.0,
            total_cost_usd=None,
            node_count=0,
            error=None,
        ),
    )
    assert _should_end_attach_on_event(event) is True


def test_rich_does_not_detach_on_workflow_output() -> None:
    bus = EventBus(session_id="test-session")
    event = bus.make_event(
        EventType.WORKFLOW_OUTPUT,
        data=WorkflowOutputData(output="hello"),
    )

    assert _should_end_attach_on_event(event) is False
