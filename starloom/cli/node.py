"""CLI node commands — list/stop/patch."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import TYPE_CHECKING

import click

if TYPE_CHECKING:
    from starloom.graph_pkg import TraceGraph

from starloom.cli._resolve import load_session, resolve_session_id
from starloom.cli.output import (
    NodeList,
    NodePatched,
    NodeRow,
    NodeStopped,
    render_json,
)
from starloom.event_data import CheckpointPendingData
from starloom.messages import ActionResultMsg
from starloom.types import EventType, NodePatch, SessionStatus


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_graph(session_id: str) -> TraceGraph:
    """Load the trace graph for a session."""
    from starloom.session.persistence import load_graph

    sess = load_session(session_id)
    return load_graph(sess)


# ---------------------------------------------------------------------------
# Group
# ---------------------------------------------------------------------------


@click.group()
def node() -> None:
    """Inspect and modify nodes inside a session's trace graph.

    Node commands operate on the saved graph for one session. When a command
    accepts --session, session resolution follows the normal order: explicit
    session id, $STARLOOM_SESSION, then the most recently used session.
    """


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------


@node.command(name="list")
@click.option(
    "-s",
    "--session",
    "session_id",
    default=None,
    help="Session to inspect. If omitted, Starloom uses $STARLOOM_SESSION or the last session.",
)
def list_nodes(session_id: str | None) -> None:
    """List nodes recorded in a session's trace graph.

    Output includes the node id, sequence number, lifecycle status, node kind,
    prompt preview, and known cost.
    """
    resolved_id = resolve_session_id(session_id)
    graph = _load_graph(resolved_id)
    checkpoint_map = _load_pending_checkpoint_ids(resolved_id)
    rows = _build_node_rows(graph, checkpoint_map)
    render_json(NodeList(session_id=resolved_id, nodes=tuple(rows)))


def _build_node_rows(
    graph: TraceGraph, checkpoint_map: dict[str, str] | None = None
) -> list[NodeRow]:
    """Convert graph nodes to output rows."""
    rows: list[NodeRow] = []
    checkpoint_map = checkpoint_map or {}
    for n in graph.nodes:
        rows.append(
            NodeRow(
                id=n.id,
                seq=n.seq,
                status=n.status.value,
                kind=n.kind,
                prompt_preview=n.prompt_preview,
                cost_usd=n.cost_usd,
                checkpoint_id=checkpoint_map.get(n.id),
            )
        )
    return rows


# ---------------------------------------------------------------------------
# stop
# ---------------------------------------------------------------------------


@node.command()
@click.argument("node_id")
@click.option(
    "-s",
    "--session",
    "session_id",
    default=None,
    help="Session containing the node. If omitted, Starloom uses $STARLOOM_SESSION or the last session.",
)
def stop(node_id: str, session_id: str | None) -> None:
    """Ask the session server to stop one running node.

    This is a node-scoped stop request. Other nodes in the same workflow may
    continue running. The session must still be running and expose an active
    session.sock control channel.
    """
    resolved_id = resolve_session_id(session_id)
    sess = load_session(resolved_id)
    if sess.status == SessionStatus.COMPLETED:
        raise click.ClickException("Cannot request node stop on completed session.")
    if sess.status != SessionStatus.RUNNING:
        raise click.ClickException("Session is not running. Stop request rejected.")
    graph = _load_graph(resolved_id)
    trace_node = graph.get_node(node_id)
    if trace_node is None:
        raise click.ClickException(f"Node not found: {node_id}")
    sock = sess.dir / "session.sock"
    if not sock.exists():
        raise click.ClickException(f"Failed to request node stop: {node_id}")
    result = _send_stop_node(sock, node_id)
    if not result.ok:
        raise click.ClickException(
            result.error or f"Failed to request node stop: {node_id}"
        )
    render_json(NodeStopped(node_id=node_id, session_id=resolved_id))


def _load_pending_checkpoint_ids(session_id: str) -> dict[str, str]:
    """Map checkpoint node_id -> currently pending checkpoint_id from saved events."""
    from starloom.session.persistence import load_events

    sess = load_session(session_id)
    return _checkpoint_map_from_events(load_events(sess))


def _checkpoint_map_from_events(events: Iterable[object]) -> dict[str, str]:
    """Build node->checkpoint mapping from checkpoint events without clearing history."""
    mapping: dict[str, str] = {}
    for event in events:
        event_type = getattr(event, "type", None)
        data = getattr(event, "data", None)
        if event_type == EventType.CHECKPOINT_PENDING and isinstance(
            data, CheckpointPendingData
        ):
            mapping[data.node_id] = data.checkpoint_id
    return mapping


def _send_stop_node(sock_path: Path, node_id: str) -> ActionResultMsg:
    """Send StopNodeAction via socket to a running session server."""
    import asyncio

    from starloom.checkpoint import StopNodeAction
    from starloom.client import SessionClient

    async def _do() -> ActionResultMsg:
        client = SessionClient(sock_path)
        await client.connect_control()
        try:
            return await client.send_action(StopNodeAction(node_id=node_id))
        finally:
            await client.disconnect()

    try:
        return asyncio.run(_do())
    except (ConnectionError, OSError, ValueError) as exc:
        return ActionResultMsg(ok=False, error=str(exc))


# ---------------------------------------------------------------------------
# patch
# ---------------------------------------------------------------------------


@node.command()
@click.argument("node_id")
@click.option(
    "-s",
    "--session",
    "session_id",
    default=None,
    help="Session containing the node. If omitted, Starloom uses $STARLOOM_SESSION or the last session.",
)
@click.option(
    "--prompt", default=None, help="Replace the node prompt text stored in the graph."
)
@click.option(
    "--flags", default=None, help="Replace the node's raw backend flags string."
)
def patch(
    node_id: str,
    session_id: str | None,
    prompt: str | None,
    flags: str | None,
) -> None:
    """Update the saved specification for one node in the graph.

    This command persists prompt and/or flags changes in the session graph.
    Those updates affect later inspection and any future execution path that
    uses the patched node spec, such as a resumed run.

    \b
    Example:
      starloom node patch NODE_ID --flags "--model sonnet" --prompt "new prompt"
    """
    resolved_id = resolve_session_id(session_id)
    sess = load_session(resolved_id)
    if sess.status == SessionStatus.COMPLETED:
        raise click.ClickException(
            "Cannot patch node on completed session (terminal state)."
        )
    node_patch = _build_patch(prompt, flags)
    changed = _apply_patch(resolved_id, node_id, node_patch)
    render_json(
        NodePatched(
            node_id=node_id,
            session_id=resolved_id,
            fields_changed=tuple(changed),
        )
    )


def _build_patch(
    prompt: str | None,
    flags: str | None,
) -> NodePatch:
    """Construct a NodePatch from CLI options."""
    return NodePatch(prompt=prompt, flags=flags)


def _apply_patch(session_id: str, node_id: str, node_patch: NodePatch) -> list[str]:
    """Apply patch to graph node and persist. Returns changed field names."""
    from starloom.session.persistence import save_graph

    sess = load_session(session_id)
    graph = _load_graph(session_id)
    trace_node = graph.get_node(node_id)
    if trace_node is None:
        raise click.ClickException(f"Node not found: {node_id}")
    graph.patch_node(node_id, node_patch)
    save_graph(sess, graph)
    return _changed_fields(node_patch)


def _changed_fields(p: NodePatch) -> list[str]:
    """Return names of non-None fields in the patch."""
    from dataclasses import fields

    return [f.name for f in fields(p) if getattr(p, f.name) is not None]
