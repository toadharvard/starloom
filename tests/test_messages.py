"""Tests for the typed socket protocol messages."""

from __future__ import annotations

from starloom.checkpoint import (
    AnswerAction,
    ApproveAction,
    PatchNodeAction,
    RejectAction,
    StopNodeAction,
    StopSessionAction,
)
from starloom.events import Event, EventBus
from starloom.messages import (
    ActionResultMsg,
    ClosedMsg,
    EventMsg,
    SnapshotMsg,
    parse_action,
    parse_server_msg,
    serialize_action,
    serialize_msg,
)
from starloom.types import EventType, NodePatch


def _make_event() -> Event:
    bus = EventBus(session_id="test")
    return bus.make_event(EventType.WORKFLOW_START)


class TestServerMsgRoundTrip:
    def test_snapshot_msg(self) -> None:
        event = _make_event()
        msg = SnapshotMsg(events=(event,))
        raw = serialize_msg(msg)
        parsed = parse_server_msg(raw.encode())
        assert isinstance(parsed, SnapshotMsg)
        assert len(parsed.events) == 1
        assert parsed.events[0].type == EventType.WORKFLOW_START

    def test_event_msg(self) -> None:
        event = _make_event()
        msg = EventMsg(event=event)
        raw = serialize_msg(msg)
        parsed = parse_server_msg(raw.encode())
        assert isinstance(parsed, EventMsg)
        assert parsed.event.type == EventType.WORKFLOW_START

    def test_action_result_ok(self) -> None:
        msg = ActionResultMsg(ok=True)
        raw = serialize_msg(msg)
        parsed = parse_server_msg(raw.encode())
        assert isinstance(parsed, ActionResultMsg)
        assert parsed.ok is True
        assert parsed.error is None

    def test_action_result_error(self) -> None:
        msg = ActionResultMsg(ok=False, error="bad request")
        raw = serialize_msg(msg)
        parsed = parse_server_msg(raw.encode())
        assert isinstance(parsed, ActionResultMsg)
        assert parsed.ok is False
        assert parsed.error == "bad request"

    def test_closed_msg(self) -> None:
        msg = ClosedMsg(reason="completed")
        raw = serialize_msg(msg)
        parsed = parse_server_msg(raw.encode())
        assert isinstance(parsed, ClosedMsg)
        assert parsed.reason == "completed"

    def test_eof_returns_none(self) -> None:
        assert parse_server_msg(b"") is None


class TestActionRoundTrip:
    def test_approve(self) -> None:
        action = ApproveAction(checkpoint_id="cp-1")
        raw = serialize_action(action)
        import json

        d = json.loads(raw)
        parsed = parse_action(d["action"])
        assert isinstance(parsed, ApproveAction)
        assert parsed.checkpoint_id == "cp-1"

    def test_reject(self) -> None:
        action = RejectAction(checkpoint_id="cp-2", reason="no way")
        raw = serialize_action(action)
        import json

        d = json.loads(raw)
        parsed = parse_action(d["action"])
        assert isinstance(parsed, RejectAction)
        assert parsed.reason == "no way"

    def test_answer(self) -> None:
        action = AnswerAction(checkpoint_id="cp-3", answer="yes")
        raw = serialize_action(action)
        import json

        d = json.loads(raw)
        parsed = parse_action(d["action"])
        assert isinstance(parsed, AnswerAction)
        assert parsed.answer == "yes"

    def test_patch_node(self) -> None:
        patch = NodePatch(prompt="rewrite", flags="--model opus --max-turns 600")
        action = PatchNodeAction(node_id="n1", patch=patch)
        raw = serialize_action(action)
        import json

        d = json.loads(raw)
        parsed = parse_action(d["action"])
        assert isinstance(parsed, PatchNodeAction)
        assert parsed.node_id == "n1"
        assert parsed.patch.prompt == "rewrite"
        assert parsed.patch.flags == "--model opus --max-turns 600"

    def test_stop_node(self) -> None:
        action = StopNodeAction(node_id="n1")
        raw = serialize_action(action)
        import json

        d = json.loads(raw)
        parsed = parse_action(d["action"])
        assert isinstance(parsed, StopNodeAction)
        assert parsed.node_id == "n1"

    def test_stop_session(self) -> None:
        action = StopSessionAction()
        raw = serialize_action(action)
        import json

        d = json.loads(raw)
        parsed = parse_action(d["action"])
        assert isinstance(parsed, StopSessionAction)
