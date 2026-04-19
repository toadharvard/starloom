"""RichTerminal -- live header plus append-only workflow events."""

from __future__ import annotations

import time
from dataclasses import dataclass
from threading import Event as ThreadEvent, Lock, Thread
from typing import Protocol

from starloom.checkpoint import Decision
from starloom.event_data import (
    CheckpointPendingData,
    WorkflowEndData,
    WorkflowOutputData,
)
from starloom.events import Event
from starloom.types import CheckpointKind, EventType, NodeStatus
from starloom.ui.protocol import (
    CheckpointSnapshot,
    CloseRenderer,
    NodeSnapshot,
    ReplayRenderer,
    SessionSnapshot,
    SnapshotRenderer,
)
from starloom.ui.snapshot import SnapshotBuilder

from rich.console import Console, ConsoleRenderable, Group
from rich.live import Live
from rich.panel import Panel
from rich.text import Text


_SPINNER_FRAMES = ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")


@dataclass(slots=True)
class _TerminalState:
    status: str = "Starting"
    terminal: bool = False


class _LiveLike(Protocol):
    def start(self) -> None: ...
    def stop(self) -> None: ...
    def update(self, renderable: ConsoleRenderable) -> None: ...


class _ConsoleLike(Protocol):
    def print(self, obj: object = "") -> None: ...


class RichTerminal(ReplayRenderer, SnapshotRenderer, CloseRenderer):
    def __init__(self, session_id: str = "") -> None:
        self._console: _ConsoleLike = Console(stderr=True)
        self._builder = SnapshotBuilder(session_id)
        self._state = _TerminalState()
        self._spinner_index = 0
        console = Console(stderr=True)
        self._console = console
        self._live: _LiveLike = Live(
            self._render_header(),
            console=console,
            refresh_per_second=8,
        )
        self._live_started = False
        self._replay_mode = False
        self._printed_blocks: list[str] = []
        self._render_lock = Lock()
        self._ticker_stop = ThreadEvent()
        self._ticker: Thread | None = None

    def begin_replay(self) -> None:
        self._replay_mode = True
        self._ensure_live_started()

    def end_replay(self) -> None:
        self._replay_mode = False
        self._refresh_header()

    def on_event(self, event: Event) -> None:
        self._ensure_live_started()
        self._builder.handle_event(event)
        self._advance_status(event)
        self._spinner_index = (self._spinner_index + 1) % len(_SPINNER_FRAMES)
        self._refresh_header()
        if self._replay_mode:
            return
        if event.type == EventType.WORKFLOW_OUTPUT and isinstance(
            event.data, WorkflowOutputData
        ):
            self._print_block(self._render_output_block(event.data))
        elif event.type == EventType.CHECKPOINT_PENDING and isinstance(
            event.data, CheckpointPendingData
        ):
            self._print_block(self._render_checkpoint_block(event.data))
        elif (
            event.type == EventType.WORKFLOW_END
            and isinstance(event.data, WorkflowEndData)
            and event.data.error
        ):
            self._print_block(self._render_failure_block(event.data.error))

    def on_snapshot(self, snapshot: SessionSnapshot) -> None:
        self._ensure_live_started()
        self._builder = SnapshotBuilder(snapshot.session_id)
        self._state.status = self._snapshot_status(snapshot)
        self._state.terminal = not bool(snapshot.pending_checkpoints) and not any(
            node.status == NodeStatus.RUNNING for node in snapshot.nodes
        )
        self._refresh_header(snapshot)

    def on_closed(self, _reason: str) -> None:
        self._stop_live()

    async def prompt_checkpoint(self, data: CheckpointPendingData) -> Decision:
        raise RuntimeError(
            f"interactive checkpoints are not supported in attach rich mode: {data.checkpoint_id}"
        )

    def _ensure_live_started(self) -> None:
        if self._live_started:
            return
        self._live.start()
        self._live_started = True
        self._start_ticker()

    def _stop_live(self) -> None:
        if not self._live_started:
            return
        self._stop_ticker()
        self._live.stop()
        self._live_started = False

    def _refresh_header(self, snapshot: SessionSnapshot | None = None) -> None:
        with self._render_lock:
            self._live.update(self._render_header(snapshot))

    def _advance_status(self, event: Event) -> None:
        snapshot = self._builder.snapshot()
        running = self._running_node(snapshot)
        pending = self._pending_checkpoint(snapshot)
        if event.type == EventType.WORKFLOW_END and isinstance(
            event.data, WorkflowEndData
        ):
            self._state.terminal = True
            self._state.status = "Failed" if event.data.error else "Completed"
            return
        if pending is not None:
            self._state.status = (
                "Waiting for tool approval"
                if pending.kind == CheckpointKind.TOOL_CALL
                else "Waiting for checkpoint"
            )
            return
        if running is not None:
            self._state.status = "Running"
            return
        self._state.status = "Running"

    def _render_header(self, snapshot: SessionSnapshot | None = None) -> Panel:
        snap = snapshot or self._live_snapshot()
        workflow = snap.workflow_file or snap.session_id or "workflow"
        spinner = (
            "" if self._state.terminal else f" {_SPINNER_FRAMES[self._spinner_index]}"
        )
        line1 = Text.assemble(
            (workflow, "bold"), (f" · {self._state.status}{spinner}", "cyan")
        )
        current = self._running_node(snap)
        pending = self._pending_checkpoint(snap)
        if current is not None:
            label = current.prompt_preview.strip() or current.kind
            line2 = f"Current: {current.node_id} · {label}"
        elif pending is not None:
            line2 = f"Current: {pending.node_id} · {pending.description}"
        else:
            last = self._last_finished_node(snap)
            if last is not None:
                label = last.prompt_preview.strip() or last.kind
                line2 = f"Last finished: {last.node_id} · {label}"
            else:
                line2 = "Current: -"
        completed = self._count_nodes(snap, NodeStatus.COMPLETED)
        running_count = self._count_nodes(snap, NodeStatus.RUNNING)
        errors = self._count_nodes(snap, NodeStatus.ERROR)
        waiting = len(snap.pending_checkpoints)
        line3 = (
            f"Completed: {completed} · Running: {running_count} · Errors: {errors} "
            f"· Pending checkpoints: {waiting} · Elapsed: {snap.elapsed:.1f}s"
        )
        return Panel(Group(line1, line2, line3), border_style="blue")

    def _render_output_block(self, data: WorkflowOutputData) -> Panel:
        output = data.output
        if output is None or output == "":
            return Panel("<empty>", title="Output", border_style="green")
        return Panel(output, title="Output", border_style="green")

    def _render_checkpoint_block(self, data: CheckpointPendingData) -> Panel:
        if data.kind == CheckpointKind.TOOL_CALL:
            lines = ["Tool approval required"]
            if data.tool:
                lines.append(f"Tool: {data.tool}")
            if data.tool_input_preview:
                lines.extend(["Input:", data.tool_input_preview])
            return Panel("\n".join(lines), title="Checkpoint", border_style="yellow")
        return Panel(data.description, title="Checkpoint", border_style="yellow")

    def _render_failure_block(self, error: str) -> Panel:
        return Panel(error, title="Failure", border_style="red")

    def _print_block(self, panel: Panel) -> None:
        marker = repr(panel.renderable)
        if self._replay_mode and marker in self._printed_blocks:
            return
        self._printed_blocks.append(marker)
        self._console.print(panel)

    @staticmethod
    def _count_nodes(snapshot: SessionSnapshot, status: NodeStatus) -> int:
        return sum(1 for node in snapshot.nodes if node.status == status)

    @staticmethod
    def _running_node(snapshot: SessionSnapshot) -> NodeSnapshot | None:
        running = [node for node in snapshot.nodes if node.status == NodeStatus.RUNNING]
        return max(running, key=lambda node: node.seq, default=None)

    @staticmethod
    def _last_finished_node(snapshot: SessionSnapshot) -> NodeSnapshot | None:
        done = [node for node in snapshot.nodes if node.status == NodeStatus.COMPLETED]
        return max(done, key=lambda node: node.seq, default=None)

    @staticmethod
    def _pending_checkpoint(snapshot: SessionSnapshot) -> CheckpointSnapshot | None:
        return snapshot.pending_checkpoints[0] if snapshot.pending_checkpoints else None

    def _snapshot_status(self, snapshot: SessionSnapshot) -> str:
        pending = self._pending_checkpoint(snapshot)
        if pending is not None:
            return (
                "Waiting for tool approval"
                if pending.kind == CheckpointKind.TOOL_CALL
                else "Waiting for checkpoint"
            )
        if self._running_node(snapshot) is not None:
            return "Running"
        if any(node.status == NodeStatus.ERROR for node in snapshot.nodes):
            return "Failed"
        if snapshot.nodes:
            return "Completed"
        return "Starting"

    def _start_ticker(self) -> None:
        if self._ticker is not None and self._ticker.is_alive():
            return
        self._ticker_stop.clear()
        self._ticker = Thread(target=self._ticker_loop, daemon=True)
        self._ticker.start()

    def _stop_ticker(self) -> None:
        self._ticker_stop.set()
        ticker = self._ticker
        if ticker is not None and ticker.is_alive():
            ticker.join(timeout=0.2)
        self._ticker = None

    def _ticker_loop(self) -> None:
        while not self._ticker_stop.wait(0.125):
            if self._replay_mode or self._state.terminal:
                continue
            self._spinner_index = (self._spinner_index + 1) % len(_SPINNER_FRAMES)
            self._refresh_header()

    def _live_snapshot(self) -> SessionSnapshot:
        snapshot = self._builder.snapshot()
        start = self._builder.start_time
        if self._state.terminal or start is None:
            return snapshot
        return SessionSnapshot(
            session_id=snapshot.session_id,
            workflow_file=snapshot.workflow_file,
            elapsed=time.time() - start,
            total_cost_usd=snapshot.total_cost_usd,
            nodes=snapshot.nodes,
            pending_checkpoints=snapshot.pending_checkpoints,
        )
