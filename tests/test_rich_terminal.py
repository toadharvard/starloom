from __future__ import annotations

from starloom.event_data import (
    CheckpointPendingData,
    NodeAddedData,
    NodeStartedData,
    WorkflowEndData,
    WorkflowOutputData,
    WorkflowStartData,
)
from starloom.events import EventBus
from rich.panel import Panel

from starloom.types import CheckpointKind, EventType
from starloom.ui.rich_terminal import RichTerminal


class _FakeLive:
    def __init__(self) -> None:
        self.started = False
        self.stopped = False
        self.updates: list[object] = []

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.stopped = True

    def update(self, renderable: object) -> None:
        self.updates.append(renderable)


class _FakeConsole:
    def __init__(self) -> None:
        self.printed: list[object] = []

    def print(self, obj: object = "") -> None:
        self.printed.append(obj)


def _renderer() -> tuple[RichTerminal, _FakeLive, _FakeConsole]:
    renderer = RichTerminal("sess")
    live = _FakeLive()
    console = _FakeConsole()
    renderer._live = live
    renderer._console = console
    renderer._live_started = False
    return renderer, live, console


def test_live_starts_and_updates_on_events() -> None:
    bus = EventBus(session_id="sess")
    renderer, live, _console = _renderer()
    renderer.on_event(
        bus.make_event(
            EventType.WORKFLOW_START,
            data=WorkflowStartData(workflow_file="demo.star", params={}),
        )
    )
    renderer.on_event(
        bus.make_event(
            EventType.NODE_ADDED,
            node_id="n1",
            seq=1,
            data=NodeAddedData(prompt_preview="do work", kind="agent"),
        )
    )
    renderer.on_event(
        bus.make_event(
            EventType.NODE_STARTED,
            node_id="n1",
            seq=1,
            data=NodeStartedData(),
        )
    )
    assert live.started is True
    assert len(live.updates) >= 3


def test_append_outputs_in_order() -> None:
    bus = EventBus(session_id="sess")
    renderer, _live, console = _renderer()
    renderer.on_event(
        bus.make_event(
            EventType.WORKFLOW_START,
            data=WorkflowStartData(workflow_file="demo.star", params={}),
        )
    )
    renderer.on_event(
        bus.make_event(
            EventType.WORKFLOW_OUTPUT,
            data=WorkflowOutputData(output="first"),
        )
    )
    renderer.on_event(
        bus.make_event(
            EventType.WORKFLOW_OUTPUT,
            data=WorkflowOutputData(output="second"),
        )
    )
    assert len(console.printed) == 2
    first = console.printed[0]
    second = console.printed[1]
    assert isinstance(first, Panel)
    assert isinstance(second, Panel)
    assert first.title == "Output"
    assert second.title == "Output"
    assert first.renderable == "first"
    assert second.renderable == "second"


def test_append_checkpoint_block() -> None:
    bus = EventBus(session_id="sess")
    renderer, _live, console = _renderer()
    renderer.on_event(
        bus.make_event(
            EventType.WORKFLOW_START,
            data=WorkflowStartData(workflow_file="demo.star", params={}),
        )
    )
    renderer.on_event(
        bus.make_event(
            EventType.CHECKPOINT_PENDING,
            node_id="cp1",
            data=CheckpointPendingData(
                checkpoint_id="cp1",
                kind=CheckpointKind.CHECKPOINT,
                node_id="cp1",
                description="Proceed?",
            ),
        )
    )
    assert len(console.printed) == 1
    panel = console.printed[0]
    assert isinstance(panel, Panel)
    assert panel.title == "Checkpoint"
    assert panel.renderable == "Proceed?"


def test_append_failure_block() -> None:
    bus = EventBus(session_id="sess")
    renderer, _live, console = _renderer()
    renderer.on_event(
        bus.make_event(
            EventType.WORKFLOW_START,
            data=WorkflowStartData(workflow_file="demo.star", params={}),
        )
    )
    renderer.on_event(
        bus.make_event(
            EventType.WORKFLOW_END,
            data=WorkflowEndData(
                duration=1.0,
                total_cost_usd=None,
                node_count=1,
                error="boom",
            ),
        )
    )
    assert len(console.printed) == 1
    panel = console.printed[0]
    assert isinstance(panel, Panel)
    assert panel.title == "Failure"
    assert panel.renderable == "boom"


def test_close_stops_live() -> None:
    renderer, live, _console = _renderer()
    renderer._ensure_live_started()
    renderer.on_closed("done")
    assert live.stopped is True
