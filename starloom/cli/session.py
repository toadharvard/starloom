"""CLI session commands — create/resume/attach/stop/list/delete."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import click

if TYPE_CHECKING:
    from starloom.session.state import Session
    from starloom.ui.protocol import EventRenderer, SessionSnapshot

from starloom.cli._resolve import (
    load_session,
    parse_params,
    resolve_session_id,
)
from starloom.session.service import (
    create_session,
    launch_resume,
    request_stop,
    validate_resumable,
)
from starloom.cli.output import (
    SessionCreated,
    SessionList,
    SessionResumed,
    SessionStopped,
    render_json,
)
from starloom.types import WorkflowConfig


@click.group()
def session() -> None:
    """Manage persisted workflow executions.

    A session is one saved run of a workflow. Session commands let you create a
    new run, re-attach to live or saved output, resume interrupted work, stop a
    running session, list known sessions, and delete session data from disk.
    """


# ---------------------------------------------------------------------------
# create
# ---------------------------------------------------------------------------


@session.command()
@click.argument("workflow_file")
@click.option(
    "-p",
    "--param",
    "params",
    multiple=True,
    help="Workflow parameter override in KEY=VALUE form. Repeat to pass multiple values.",
)
@click.option(
    "--backend",
    type=click.Choice(["claude", "pi"]),
    default="claude",
    show_default=True,
    help="Default backend for agent nodes unless the workflow overrides it explicitly.",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Create the session but estimate execution/cost instead of running live agents.",
)
def create(
    workflow_file: str,
    params: tuple[str, ...],
    backend: str,
    dry_run: bool,
) -> None:
    """Create a new session and start the workflow in the background.

    The workflow file is copied into the session directory together with the
    resolved config so the run can be inspected or resumed later.

    \b
    Examples:
      starloom session create workflow.star
      starloom session create workflow.star -p topic=AI -p count=3
      starloom session create workflow.star --backend pi
      starloom session create workflow.star --dry-run
    """
    path = Path(workflow_file)
    if not path.exists():
        raise click.ClickException(f"Workflow file not found: {workflow_file}")
    parsed_params = parse_params(params)
    config = _build_config(
        workflow_file,
        parsed_params,
        backend,
        dry_run,
    )
    created = create_session(config, path)
    render_json(
        SessionCreated(
            session_id=created.session_id,
            workflow_file=created.workflow_file,
            status=created.status,
        )
    )


def _build_config(
    workflow_file: str,
    params: dict[str, str],
    backend: str,
    dry_run: bool,
) -> WorkflowConfig:
    """Construct a typed WorkflowConfig from CLI args."""
    return WorkflowConfig(
        workflow_file=workflow_file,
        params=params,
        backend=backend,
        dry_run=dry_run,
    )


# ---------------------------------------------------------------------------
# resume
# ---------------------------------------------------------------------------


@session.command()
@click.argument("session_id", required=False, default=None)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Accepted for CLI compatibility, but current resume still launches a live worker.",
)
def resume(
    session_id: str | None,
    dry_run: bool,
) -> None:
    """Resume an existing session instead of creating a new one.

    Use this for resumable sessions such as stopped or errored runs. The saved
    workflow file and parameters are reused from the existing session.

    Session resolution order is: explicit SESSION_ID, $STARLOOM_SESSION, then
    the most recently used session.

    A completed session cannot be resumed, and a still-running session cannot
    be resumed again. Patched nodes use their updated spec when resume reaches
    them. The current --dry-run flag is accepted, but resume still launches a
    real worker.
    """
    resolved_id = resolve_session_id(session_id)
    try:
        validate_resumable(resolved_id)
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc
    launch_resume(resolved_id)
    render_json(SessionResumed(session_id=resolved_id, status="running"))


# ---------------------------------------------------------------------------
# attach
# ---------------------------------------------------------------------------


@session.command()
@click.argument("session_id", required=False, default=None)
@click.option(
    "-o",
    "--output",
    "out_fmt",
    type=click.Choice(["rich", "json", "events"]),
    default="rich",
    show_default=True,
    help="Render mode: rich=live workflow header plus append-only events, json=structured stream, events=raw event-oriented output.",
)
def attach(session_id: str | None, out_fmt: str) -> None:
    """Attach to a session and render its execution state.

    If the session is still running and has an active session server, attach
    replays backlog events first and then continues with live updates. If there
    is no live socket, Starloom falls back to replaying saved events from disk.

    Use checkpoint commands separately to approve, reject, or answer pending
    checkpoints.

    \b
    Examples:
      starloom session attach SESSION_ID
      starloom session attach SESSION_ID -o json
      starloom session attach SESSION_ID -o events
    """
    import asyncio

    resolved_id = resolve_session_id(session_id)
    sess = load_session(resolved_id)
    renderer = _make_renderer(out_fmt, resolved_id)
    sock = sess.dir / "session.sock"
    if sock.exists():
        asyncio.run(_stream_events(sock, renderer, out_fmt))
    else:
        _replay_saved_events(sess, renderer)


def _make_renderer(out_fmt: str, session_id: str) -> EventRenderer:
    """Create an event renderer for the given output format."""
    if out_fmt == "rich":
        from starloom.ui.rich_terminal import RichTerminal

        return RichTerminal(session_id)
    if out_fmt == "json":
        from starloom.ui.json_renderer import JsonRenderer

        return JsonRenderer()
    from starloom.ui.events_renderer import EventsRenderer

    return EventsRenderer()


async def _stream_events(
    sock_path: Path,
    renderer: EventRenderer,
    out_fmt: str,
) -> None:
    """Connect via SessionClient, feed events to renderer."""
    from starloom.client import SessionClient
    from starloom.messages import ClosedMsg
    from starloom.ui.protocol import CloseRenderer

    client = SessionClient(sock_path)
    await client.connect()
    close_reason = "attach ended"
    try:
        while True:
            msg = await client.read_message()
            if not msg:
                break
            if _dispatch_msg(msg, renderer, out_fmt):
                break
            if isinstance(msg, ClosedMsg):
                close_reason = msg.reason
                break
    finally:
        if isinstance(renderer, CloseRenderer):
            renderer.on_closed(close_reason)
        await client.disconnect()


def _replay_saved_events(sess: Session, renderer: EventRenderer) -> None:
    """Reconstruct state from saved events and show the final snapshot."""
    from starloom.session.persistence import load_events
    from starloom.ui.protocol import CloseRenderer, ReplayRenderer, SnapshotRenderer

    events = load_events(sess)
    if not events:
        click.echo("No events recorded for this session.")
        return

    if isinstance(renderer, ReplayRenderer):
        renderer.begin_replay()
        for event in events:
            renderer.on_event(event)
        renderer.end_replay()
    elif isinstance(renderer, SnapshotRenderer):
        from starloom.ui.snapshot import SnapshotBuilder

        builder = SnapshotBuilder(sess.id)
        for event in events:
            builder.handle_event(event)
        renderer.on_snapshot(builder.snapshot())
    else:
        for event in events:
            renderer.on_event(event)
    if isinstance(renderer, CloseRenderer):
        renderer.on_closed("replay complete")


def _dispatch_msg(msg: object, renderer: EventRenderer, out_fmt: str = "rich") -> bool:
    """Route a ServerMsg to the appropriate renderer method.

    Returns True when attach should end after this message.
    """
    from starloom.messages import EventMsg, SnapshotMsg
    from starloom.ui.protocol import ReplayRenderer, SnapshotRenderer

    if isinstance(msg, EventMsg):
        renderer.on_event(msg.event)
        return _should_end_attach_on_event(msg.event)
    if isinstance(msg, SnapshotMsg):
        if isinstance(renderer, ReplayRenderer):
            renderer.begin_replay()
            for event in msg.events:
                renderer.on_event(event)
            renderer.end_replay()
        elif isinstance(renderer, SnapshotRenderer):
            renderer.on_snapshot(_build_snapshot_from_events(msg.events))
        else:
            for event in msg.events:
                renderer.on_event(event)
        return _should_end_attach_on_snapshot(msg, renderer)
    return False


def _should_end_attach_on_snapshot(msg: object, renderer: EventRenderer) -> bool:
    """Return True when a replayed snapshot reflects a terminal attach state."""
    from starloom.messages import SnapshotMsg
    from starloom.ui.protocol import SnapshotRenderer

    if not isinstance(msg, SnapshotMsg):
        return False
    if isinstance(renderer, SnapshotRenderer):
        snapshot = _build_snapshot_from_events(msg.events)
        return _should_end_attach_on_snapshot_state(snapshot)
    return any(_should_end_attach_on_event(event) for event in msg.events)


def _build_snapshot_from_events(events: tuple[object, ...]) -> SessionSnapshot:
    """Reconstruct final session state from a sequence of replay events."""
    from starloom.events import Event
    from starloom.ui.snapshot import SnapshotBuilder

    session_id = ""
    builder = SnapshotBuilder(session_id)
    for raw_event in events:
        if not isinstance(raw_event, Event):
            continue
        if not session_id:
            session_id = raw_event.session_id
            builder = SnapshotBuilder(session_id)
        builder.handle_event(raw_event)
    return builder.snapshot()


def _should_end_attach_on_snapshot_state(snapshot: SessionSnapshot) -> bool:
    """Return True when the reconstructed snapshot is waiting or terminal."""
    from starloom.types import NodeStatus

    if snapshot.pending_checkpoints:
        return True
    return any(
        node.status in {NodeStatus.COMPLETED, NodeStatus.ERROR, NodeStatus.STOPPED}
        for node in snapshot.nodes
    ) and not any(node.status == NodeStatus.RUNNING for node in snapshot.nodes)


def _should_end_attach_on_event(event: object) -> bool:
    """Return True when attach should end after rendering a live event."""
    from starloom.events import Event
    from starloom.event_data import WorkflowEndData
    from starloom.types import EventType

    if not isinstance(event, Event):
        return False
    if event.type == EventType.CHECKPOINT_PENDING:
        return True
    if event.type == EventType.WORKFLOW_END and isinstance(event.data, WorkflowEndData):
        return True
    return False


# ---------------------------------------------------------------------------
# stop
# ---------------------------------------------------------------------------


@session.command()
@click.argument("session_id", required=False, default=None)
def stop(session_id: str | None) -> None:
    """Ask a running session to stop.

    This sends a stop request to the session server. It is not a hard kill of the
    persisted session state; the worker/orchestrator is responsible for moving the
    session into its eventual terminal state.
    """
    resolved_id = resolve_session_id(session_id)
    result = request_stop(resolved_id)
    if not result.accepted:
        raise click.ClickException(result.message or "stop rejected")
    render_json(SessionStopped(session_id=resolved_id, status=result.status))


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------


@session.command(name="list")
@click.option(
    "--status",
    type=click.Choice(["running", "completed", "error", "stopped", "crashed"]),
    default=None,
    help="Only show sessions in one status. 'crashed' is a convenience filter for error sessions marked as crash_detected.",
)
def list_sessions(status: str | None) -> None:
    """List saved sessions on disk.

    Each row includes the session id, status, original workflow file path,
    creation time, node count, and total cost when known.
    """
    from starloom.cli.session_list import collect_session_rows

    rows = collect_session_rows(status)
    render_json(SessionList(sessions=tuple(rows)))


# ---------------------------------------------------------------------------
# delete
# ---------------------------------------------------------------------------


@session.command()
@click.argument("session_id", required=False, default=None)
@click.option(
    "--all",
    "delete_all",
    is_flag=True,
    help="Delete every saved session instead of one SESSION_ID.",
)
@click.option(
    "--older-than",
    default=None,
    help="Only delete sessions older than a duration like 30m, 12h, or 7d.",
)
@click.option(
    "--confirm", is_flag=True, help="Skip the interactive confirmation prompt."
)
def delete(
    session_id: str | None,
    delete_all: bool,
    older_than: str | None,
    confirm: bool,
) -> None:
    """Delete persisted session data from disk.

    This removes session directories under the local Starloom session store.
    Deleted sessions can no longer be attached to, resumed, or inspected unless
    you have another copy.

    \b
    Examples:
      starloom session delete SESSION_ID
      starloom session delete --all --confirm
      starloom session delete --all --older-than 7d
    """
    from starloom.cli.session_delete import delete_all_sessions, delete_one

    if delete_all:
        delete_all_sessions(older_than, confirm)
    elif session_id:
        delete_one(session_id, confirm)
    else:
        raise click.UsageError("Provide SESSION_ID or --all.")
