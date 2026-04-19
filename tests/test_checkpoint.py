"""Tests for checkpoint.py — CheckpointGate, Decision types, validation."""

from __future__ import annotations

import asyncio

import pytest

from starloom.checkpoint import (
    AnswerAction,
    ApproveAction,
    Checkpoint,
    CheckpointGate,
    InvalidDecision,
    RejectAction,
    make_checkpoint_id,
    validate_decision,
)
from starloom.events import EventBus
from starloom.types import CheckpointKind, EventType


def _tool_call_checkpoint(cp_id: str = "cp1") -> Checkpoint:
    return Checkpoint(
        id=cp_id,
        kind=CheckpointKind.TOOL_CALL,
        node_id="n1",
        description="Bash call",
        tool="Bash",
    )


def _workflow_checkpoint(cp_id: str = "cp3") -> Checkpoint:
    return Checkpoint(
        id=cp_id,
        kind=CheckpointKind.CHECKPOINT,
        node_id="n3",
        description="Deploy to prod?",
    )


class TestDecisionTypes:
    def test_approve(self) -> None:
        d = ApproveAction(checkpoint_id="cp1")
        assert d.checkpoint_id == "cp1"

    def test_reject(self) -> None:
        d = RejectAction(checkpoint_id="cp1", reason="too dangerous")
        assert d.reason == "too dangerous"

    def test_reject_default_reason(self) -> None:
        d = RejectAction(checkpoint_id="cp1")
        assert d.reason == ""

    def test_answer(self) -> None:
        d = AnswerAction(checkpoint_id="cp1", answer="yes, deploy")
        assert d.answer == "yes, deploy"

    def test_frozen(self) -> None:
        d = ApproveAction(checkpoint_id="cp1")
        with pytest.raises(AttributeError):
            d.checkpoint_id = "cp2"  # type: ignore[misc]


class TestValidation:
    def test_answer_on_tool_call_invalid(self) -> None:
        cp = _tool_call_checkpoint()
        with pytest.raises(InvalidDecision):
            validate_decision(cp, AnswerAction(checkpoint_id="cp1", answer="x"))

    def test_approve_on_checkpoint_invalid(self) -> None:
        cp = _workflow_checkpoint()
        with pytest.raises(InvalidDecision):
            validate_decision(cp, ApproveAction(checkpoint_id="cp3"))

    def test_reject_always_valid(self) -> None:
        for cp in [_tool_call_checkpoint(), _workflow_checkpoint()]:
            validate_decision(cp, RejectAction(checkpoint_id=cp.id))

    def test_approve_on_tool_call_valid(self) -> None:
        validate_decision(_tool_call_checkpoint(), ApproveAction(checkpoint_id="cp1"))

    def test_answer_on_checkpoint_valid(self) -> None:
        validate_decision(
            _workflow_checkpoint(),
            AnswerAction(checkpoint_id="cp3", answer="yes"),
        )


class TestMakeCheckpointId:
    def test_length(self) -> None:
        cid = make_checkpoint_id()
        assert len(cid) == 12

    def test_unique(self) -> None:
        ids = {make_checkpoint_id() for _ in range(100)}
        assert len(ids) == 100


class TestCheckpointGate:
    @pytest.mark.asyncio
    async def test_wait_and_decide(self, bus: EventBus) -> None:
        gate = CheckpointGate(bus)
        cp = _tool_call_checkpoint()

        async def decide_soon() -> None:
            await asyncio.sleep(0.01)
            gate.decide(cp.id, ApproveAction(checkpoint_id=cp.id))

        asyncio.create_task(decide_soon())
        decision = await gate.wait(cp, timeout=5.0)
        assert isinstance(decision, ApproveAction)

    @pytest.mark.asyncio
    async def test_timeout_returns_reject(self, bus: EventBus) -> None:
        gate = CheckpointGate(bus)
        cp = _tool_call_checkpoint()
        decision = await gate.wait(cp, timeout=0.01)
        assert isinstance(decision, RejectAction)

    @pytest.mark.asyncio
    async def test_decide_unknown_returns_false(self, bus: EventBus) -> None:
        gate = CheckpointGate(bus)
        result = gate.decide("nonexistent", ApproveAction(checkpoint_id="x"))
        assert result is False

    @pytest.mark.asyncio
    async def test_decide_emits_events(self, bus: EventBus) -> None:
        events: list[EventType] = []

        async def track(event: object) -> None:
            events.append(event.type)  # type: ignore[attr-defined]

        bus.subscribe(track)
        gate = CheckpointGate(bus)
        cp = _tool_call_checkpoint()

        async def decide_soon() -> None:
            await asyncio.sleep(0.01)
            gate.decide(cp.id, ApproveAction(checkpoint_id=cp.id))

        asyncio.create_task(decide_soon())
        await gate.wait(cp, timeout=5.0)
        await asyncio.sleep(0.05)
        assert EventType.CHECKPOINT_PENDING in events
        assert EventType.CHECKPOINT_RESOLVED in events

    @pytest.mark.asyncio
    async def test_pending_ids(self, bus: EventBus) -> None:
        gate = CheckpointGate(bus)
        cp = _tool_call_checkpoint()

        async def decide_later() -> None:
            await asyncio.sleep(0.05)
            gate.decide(cp.id, ApproveAction(checkpoint_id=cp.id))

        task = asyncio.create_task(decide_later())
        wait_task = asyncio.create_task(gate.wait(cp, timeout=5.0))
        await asyncio.sleep(0.01)
        assert cp.id in gate.pending_ids
        await wait_task
        assert cp.id not in gate.pending_ids
        await task

    @pytest.mark.asyncio
    async def test_get_checkpoint(self, bus: EventBus) -> None:
        gate = CheckpointGate(bus)
        cp = _tool_call_checkpoint()

        async def decide_later() -> None:
            await asyncio.sleep(0.05)
            gate.decide(cp.id, ApproveAction(checkpoint_id=cp.id))

        task = asyncio.create_task(decide_later())
        wait_task = asyncio.create_task(gate.wait(cp, timeout=5.0))
        await asyncio.sleep(0.01)
        retrieved = gate.get_checkpoint(cp.id)
        assert retrieved is not None
        assert retrieved.id == cp.id
        assert gate.get_checkpoint("missing") is None
        await wait_task
        await task

    @pytest.mark.asyncio
    async def test_invalid_decision_raises(self, bus: EventBus) -> None:
        gate = CheckpointGate(bus)
        cp = _tool_call_checkpoint()

        async def decide_invalid() -> None:
            await asyncio.sleep(0.01)
            with pytest.raises(InvalidDecision):
                gate.decide(
                    cp.id,
                    AnswerAction(checkpoint_id=cp.id, answer="x"),
                )

        task = asyncio.create_task(decide_invalid())
        wait_task = asyncio.create_task(gate.wait(cp, timeout=0.1))
        await task
        decision = await wait_task
        assert isinstance(decision, RejectAction)
