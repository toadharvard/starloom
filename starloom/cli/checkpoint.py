"""CLI checkpoint commands — approve/reject/answer via SessionClient."""

from __future__ import annotations

import asyncio
from pathlib import Path

import click

from starloom.checkpoint import AnswerAction, ApproveAction, GraphAction, RejectAction
from starloom.cli._resolve import load_session, resolve_session_id
from starloom.cli.output import CheckpointDecided, render_json


@click.group()
def checkpoint_group() -> None:
    """Resolve pending checkpoints in a running session.

    Checkpoints pause workflow progress until an operator decides what to do.
    Some checkpoints expect approve/reject decisions, while workflow-authored
    checkpoint() calls expect a text answer.
    """


@checkpoint_group.command()
@click.argument("checkpoint_id")
@click.option(
    "-s",
    "--session",
    "session_id",
    default=None,
    help="Session containing the checkpoint. If omitted, Starloom uses $STARLOOM_SESSION or the last session.",
)
def approve(checkpoint_id: str, session_id: str | None) -> None:
    """Approve a pending checkpoint.

    Use this for checkpoints that represent a yes/allow decision, such as a
    backend-driven tool-call interception. The session must still be running and
    have an active control socket.
    """
    ok, error = _send(session_id, ApproveAction(checkpoint_id=checkpoint_id))
    _report(checkpoint_id, "approved", ok, error)


@checkpoint_group.command()
@click.argument("checkpoint_id")
@click.option(
    "--reason", default="", help="Optional explanation recorded with the rejection."
)
@click.option(
    "-s",
    "--session",
    "session_id",
    default=None,
    help="Session containing the checkpoint. If omitted, Starloom uses $STARLOOM_SESSION or the last session.",
)
def reject(checkpoint_id: str, reason: str, session_id: str | None) -> None:
    """Reject a pending checkpoint.

    For tool-call checkpoints this denies the action and surfaces the rejection
    back into the execution path. The session must still be running and have an
    active control socket.
    """
    ok, error = _send(
        session_id,
        RejectAction(checkpoint_id=checkpoint_id, reason=reason),
    )
    _report(checkpoint_id, "rejected", ok, error)


@checkpoint_group.command()
@click.argument("checkpoint_id")
@click.argument("text")
@click.option(
    "-s",
    "--session",
    "session_id",
    default=None,
    help="Session containing the checkpoint. If omitted, Starloom uses $STARLOOM_SESSION or the last session.",
)
def answer(checkpoint_id: str, text: str, session_id: str | None) -> None:
    """Answer a workflow-authored checkpoint with text.

    Use this for checkpoint() pauses that are waiting for operator input. The
    session must still be running and have an active control socket.
    """
    ok, error = _send(
        session_id,
        AnswerAction(checkpoint_id=checkpoint_id, answer=text),
    )
    _report(checkpoint_id, "answered", ok, error)


def _send(
    session_id: str | None,
    action: GraphAction,
) -> tuple[bool, str | None]:
    """Connect to session server and send a decision. Returns (ok, error)."""
    from starloom.types import SessionStatus

    sid = resolve_session_id(session_id)
    sess = load_session(sid)
    if sess.status != SessionStatus.RUNNING:
        raise click.ClickException(
            f"Session {sid} is not running. Checkpoints only work on running sessions."
        )
    sock = sess.dir / "session.sock"
    if not sock.exists():
        raise click.ClickException(
            f"Session {sid} has no active server (session.sock not found)."
        )
    return asyncio.run(_send_async(sock, action))


async def _send_async(
    sock_path: Path,
    action: GraphAction,
) -> tuple[bool, str | None]:
    """Send action via SessionClient and return (ok, error)."""
    from starloom.client import SessionClient

    client = SessionClient(sock_path)
    await client.connect()
    try:
        result = await client.send_action(action)
        return result.ok, result.error
    finally:
        await client.disconnect()


def _report(
    checkpoint_id: str,
    action: str,
    ok: bool,
    error: str | None,
) -> None:
    """Print result of the decision."""
    render_json(CheckpointDecided(checkpoint_id=checkpoint_id, action=action))
    if ok:
        return
    if error:
        raise click.ClickException(error)
    raise click.ClickException(
        f"checkpoint {checkpoint_id} was not resolved "
        f"(already resolved, unknown, or session not running)."
    )
