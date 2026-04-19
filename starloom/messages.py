"""Typed socket protocol messages — replaces bare dicts at module boundaries.

Server → Client:
  SnapshotMsg   — replay of all past events on subscribe
  EventMsg      — live event broadcast
  ActionResultMsg — response to a client action
  ClosedMsg     — session ended

Client → Server:
  SubscribeMsg  — request event stream
  ActionMsg     — send a GraphAction
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from starloom.checkpoint import (
    AnswerAction,
    ApproveAction,
    GraphAction,
    PatchNodeAction,
    RejectAction,
    StopNodeAction,
    StopSessionAction,
)
from starloom.events import Event
from starloom.serialization import node_patch_from_dict, node_patch_to_dict
from starloom.types import NodePatch


# ---------------------------------------------------------------------------
# Server → Client messages
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SnapshotMsg:
    """Replay of all past events sent on subscribe."""

    events: tuple[Event, ...]


@dataclass(frozen=True, slots=True)
class EventMsg:
    """Live event broadcast."""

    event: Event


@dataclass(frozen=True, slots=True)
class ActionResultMsg:
    """Response to a client action."""

    ok: bool
    error: str | None = None


@dataclass(frozen=True, slots=True)
class ClosedMsg:
    """Session ended."""

    reason: str


ServerMsg = SnapshotMsg | EventMsg | ActionResultMsg | ClosedMsg


# ---------------------------------------------------------------------------
# Serialization: ServerMsg → JSON dict (server side)
# ---------------------------------------------------------------------------


def serialize_msg(msg: ServerMsg) -> str:
    """Serialize a ServerMsg to a JSON line."""
    return json.dumps(_msg_to_dict(msg), default=str) + "\n"


def _msg_to_dict(msg: ServerMsg) -> dict[str, object]:
    if isinstance(msg, SnapshotMsg):
        return {"msg": "snapshot", "events": [e.to_dict() for e in msg.events]}
    if isinstance(msg, EventMsg):
        return {"msg": "event", "event": msg.event.to_dict()}
    if isinstance(msg, ActionResultMsg):
        return {"msg": "action_result", "ok": msg.ok, "error": msg.error}
    if isinstance(msg, ClosedMsg):
        return {"msg": "closed", "reason": msg.reason}
    raise ValueError(f"Unknown message type: {type(msg)}")


# ---------------------------------------------------------------------------
# Deserialization: JSON dict → ServerMsg (client side)
# ---------------------------------------------------------------------------


def parse_server_msg(line: bytes) -> ServerMsg | None:
    """Parse a JSON line into a typed ServerMsg. Returns None on EOF."""
    if not line:
        return None
    d: dict[str, Any] = json.loads(line)
    return _dict_to_msg(d)


def _dict_to_msg(d: dict[str, Any]) -> ServerMsg:
    kind = d["msg"]
    if kind == "snapshot":
        events = tuple(Event.from_dict(e) for e in d["events"])
        return SnapshotMsg(events=events)
    if kind == "event":
        return EventMsg(event=Event.from_dict(d["event"]))
    if kind == "action_result":
        return ActionResultMsg(ok=bool(d["ok"]), error=_opt_str(d.get("error")))
    if kind == "closed":
        return ClosedMsg(reason=str(d["reason"]))
    raise ValueError(f"Unknown server message: {kind}")


def _opt_str(val: object) -> str | None:
    """Convert an optional object to str | None."""
    return str(val) if val is not None else None


# ---------------------------------------------------------------------------
# GraphAction serialization (client → server)
# ---------------------------------------------------------------------------


def serialize_action(action: GraphAction) -> str:
    """Serialize a GraphAction to a JSON line for the wire."""
    payload = {"msg": "action", "action": _action_to_dict(action)}
    return json.dumps(payload, default=str) + "\n"


def _action_to_dict(action: GraphAction) -> dict[str, object]:
    if isinstance(action, ApproveAction):
        return {"type": "approve", "checkpoint_id": action.checkpoint_id}
    if isinstance(action, RejectAction):
        return _reject_dict(action)
    if isinstance(action, AnswerAction):
        return _answer_dict(action)
    if isinstance(action, PatchNodeAction):
        return _patch_action_dict(action)
    if isinstance(action, StopNodeAction):
        return {"type": "stop_node", "node_id": action.node_id}
    if isinstance(action, StopSessionAction):
        return {"type": "stop_session"}
    raise ValueError(f"Unknown action type: {type(action)}")


def _reject_dict(action: RejectAction) -> dict[str, object]:
    return {
        "type": "reject",
        "checkpoint_id": action.checkpoint_id,
        "reason": action.reason,
    }


def _answer_dict(action: AnswerAction) -> dict[str, object]:
    return {
        "type": "answer",
        "checkpoint_id": action.checkpoint_id,
        "answer": action.answer,
    }


def _patch_action_dict(action: PatchNodeAction) -> dict[str, object]:
    return {
        "type": "patch",
        "node_id": action.node_id,
        "patch": node_patch_to_dict(action.patch),
    }


# ---------------------------------------------------------------------------
# GraphAction deserialization (server side)
# ---------------------------------------------------------------------------


def parse_action(raw: dict[str, object]) -> GraphAction:
    """Parse a raw action dict into a typed GraphAction."""
    action_type = raw["type"]
    if action_type == "approve":
        return ApproveAction(checkpoint_id=str(raw["checkpoint_id"]))
    if action_type == "reject":
        return _parse_reject(raw)
    if action_type == "answer":
        return _parse_answer(raw)
    if action_type == "patch":
        return PatchNodeAction(
            node_id=str(raw["node_id"]),
            patch=_parse_node_patch(raw.get("patch", {})),
        )
    if action_type == "stop_node":
        return StopNodeAction(node_id=str(raw["node_id"]))
    if action_type == "stop_session":
        return StopSessionAction()
    raise ValueError(f"Unknown action type: {action_type}")


def _parse_reject(raw: dict[str, object]) -> RejectAction:
    return RejectAction(
        checkpoint_id=str(raw["checkpoint_id"]),
        reason=str(raw.get("reason", "")),
    )


def _parse_answer(raw: dict[str, object]) -> AnswerAction:
    return AnswerAction(
        checkpoint_id=str(raw["checkpoint_id"]),
        answer=str(raw["answer"]),
    )


def _parse_node_patch(raw: dict[str, object] | object) -> NodePatch:
    return node_patch_from_dict(raw)
