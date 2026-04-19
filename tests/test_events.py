"""Tests for events.py — EventBus, Event."""

from __future__ import annotations

import pytest

from starloom.event_data import CheckpointPendingData, WorkflowStartData
from starloom.types import AgentSpecData, CheckpointKind
from starloom.events import Event, EventBus
from starloom.types import EventType


class TestEvent:
    def test_create(self) -> None:
        data = WorkflowStartData(workflow_file="f.star", params={})
        e = Event(
            type=EventType.WORKFLOW_START,
            timestamp=1000.0,
            session_id="s1",
            data=data,
        )
        assert e.type == EventType.WORKFLOW_START
        assert e.session_id == "s1"
        assert e.node_id is None

    def test_frozen(self) -> None:
        e = Event(
            type=EventType.WORKFLOW_END,
            timestamp=1.0,
            session_id="s",
        )
        with pytest.raises(AttributeError):
            e.session_id = "other"  # type: ignore[misc]

    def test_to_jsonl_roundtrip(self) -> None:
        data = WorkflowStartData(workflow_file="test.star", params={"k": "v"})
        e = Event(
            type=EventType.WORKFLOW_START,
            timestamp=1234.5,
            session_id="sess1",
            data=data,
        )
        jsonl = e.to_jsonl()
        assert '"workflow.start"' in jsonl
        assert '"test.star"' in jsonl

    def test_to_dict(self) -> None:
        e = Event(
            type=EventType.NODE_STARTED,
            timestamp=1.0,
            session_id="s",
            node_id="n1",
            seq=3,
            data=None,
        )
        d = e.to_dict()
        assert d["type"] == "node.started"
        assert d["node_id"] == "n1"
        assert d["seq"] == 3

    def test_from_jsonl_roundtrip_with_nested_data(self) -> None:
        e = Event(
            type=EventType.CHECKPOINT_PENDING,
            timestamp=1.0,
            session_id="s",
            node_id="n1",
            data=CheckpointPendingData(
                checkpoint_id="cp-1",
                kind=CheckpointKind.CHECKPOINT,
                node_id="n1",
                description="need approval",
                spec=AgentSpecData(prompt="hello", flags="--model haiku"),
            ),
        )
        restored = Event.from_jsonl(e.to_jsonl())
        assert isinstance(restored.data, CheckpointPendingData)
        assert restored.data.spec is not None
        assert restored.data.spec.prompt == "hello"


class TestEventBus:
    @pytest.mark.asyncio
    async def test_emit_calls_handlers(self, bus: EventBus) -> None:
        received: list[Event] = []

        async def handler(event: Event) -> None:
            received.append(event)

        bus.subscribe(handler)
        event = bus.make_event(EventType.WORKFLOW_START)
        await bus.emit(event)
        assert len(received) == 1
        assert received[0].type == EventType.WORKFLOW_START

    @pytest.mark.asyncio
    async def test_typed_subscription(self, bus: EventBus) -> None:
        received: list[Event] = []

        async def handler(event: Event) -> None:
            received.append(event)

        bus.subscribe(handler, EventType.NODE_STARTED)
        await bus.emit(bus.make_event(EventType.WORKFLOW_START))
        await bus.emit(bus.make_event(EventType.NODE_STARTED, node_id="n1"))
        assert len(received) == 1
        assert received[0].type == EventType.NODE_STARTED

    @pytest.mark.asyncio
    async def test_unsubscribe(self, bus: EventBus) -> None:
        received: list[Event] = []

        async def handler(event: Event) -> None:
            received.append(event)

        bus.subscribe(handler)
        await bus.emit(bus.make_event(EventType.WORKFLOW_START))
        bus.unsubscribe(handler)
        await bus.emit(bus.make_event(EventType.WORKFLOW_END))
        assert len(received) == 1

    @pytest.mark.asyncio
    async def test_sequential_handler_order(self, bus: EventBus) -> None:
        order: list[int] = []

        async def h1(event: Event) -> None:
            order.append(1)

        async def h2(event: Event) -> None:
            order.append(2)

        bus.subscribe(h1)
        bus.subscribe(h2)
        await bus.emit(bus.make_event(EventType.WORKFLOW_START))
        assert order == [1, 2]

    @pytest.mark.asyncio
    async def test_log(self, bus: EventBus) -> None:
        await bus.emit(bus.make_event(EventType.WORKFLOW_START))
        await bus.emit(bus.make_event(EventType.WORKFLOW_END))
        assert len(bus.log) == 2
        assert bus.log[0].type == EventType.WORKFLOW_START

    @pytest.mark.asyncio
    async def test_make_event_prefills(self, bus: EventBus) -> None:
        event = bus.make_event(EventType.NODE_ADDED, node_id="n1", seq=5)
        assert event.session_id == "test-session"
        assert event.node_id == "n1"
        assert event.seq == 5
        assert event.timestamp > 0
