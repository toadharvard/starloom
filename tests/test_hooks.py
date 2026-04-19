"""Tests for HookServer — HTTP-based Claude tool-use interception checkpoints."""

from __future__ import annotations

import asyncio
from typing import cast

import pytest
from aiohttp import ClientSession, web

from starloom.checkpoint import (
    ApproveAction,
    CheckpointGate,
    RejectAction,
)
from starloom.events import EventBus
from starloom.hooks import (
    HookRequest,
    HookServer,
    _decision_response,
    _decision_to_response,
)


@pytest.fixture
def bus() -> EventBus:
    return EventBus(session_id="hook-test")


@pytest.fixture
def gate(bus: EventBus) -> CheckpointGate:
    return CheckpointGate(bus)


class TestHookRequest:
    def test_fields(self) -> None:
        req = HookRequest(tool_name="Bash", tool_input_preview='{"cmd":"ls"}')
        assert req.tool_name == "Bash"
        assert req.tool_input_preview == '{"cmd":"ls"}'


def test_decision_response_includes_reason_only_when_present() -> None:
    allowed = _decision_response("allow")
    denied = _decision_response("deny", "because")
    assert isinstance(allowed, web.Response)
    assert isinstance(denied, web.Response)
    assert allowed.text is not None
    assert denied.text is not None
    assert "permissionDecisionReason" not in allowed.text
    assert "because" in denied.text


class TestHookServer:
    @pytest.mark.asyncio
    async def test_hook_creates_checkpoint_and_allows_on_approve(
        self,
        gate: CheckpointGate,
    ) -> None:
        srv = HookServer(gate)
        port = await srv.start()
        try:
            hook_task = asyncio.create_task(self._call_hook(port))
            await asyncio.sleep(0.1)

            assert len(gate.pending_ids) == 1
            cp_id = gate.pending_ids[0]
            gate.decide(cp_id, ApproveAction(checkpoint_id=cp_id))

            data = await asyncio.wait_for(hook_task, timeout=5.0)
            output = cast(dict[str, object], data["hookSpecificOutput"])
            assert output["permissionDecision"] == "allow"
        finally:
            await srv.stop()

    @pytest.mark.asyncio
    async def test_hook_creates_checkpoint_and_denies_on_reject(
        self,
        gate: CheckpointGate,
    ) -> None:
        srv = HookServer(gate)
        port = await srv.start()
        try:
            hook_task = asyncio.create_task(self._call_hook(port))
            await asyncio.sleep(0.1)

            assert len(gate.pending_ids) == 1
            cp_id = gate.pending_ids[0]
            gate.decide(
                cp_id,
                RejectAction(checkpoint_id=cp_id, reason="denied"),
            )

            data = await asyncio.wait_for(hook_task, timeout=5.0)
            output = cast(dict[str, object], data["hookSpecificOutput"])
            assert output["permissionDecision"] == "deny"
            reason = cast(str, output["permissionDecisionReason"])
            assert "denied" in reason
        finally:
            await srv.stop()

    @staticmethod
    async def _call_hook(port: int) -> dict[str, object]:
        async with ClientSession() as session:
            resp = await session.post(
                f"http://127.0.0.1:{port}/hook",
                json={"tool_name": "Bash", "tool_input": {"cmd": "rm -rf /"}},
            )
            data = await resp.json()
            assert isinstance(data, dict)
            return data


class TestDecisionToResponse:
    def test_reject_includes_reason(self) -> None:
        response = _decision_to_response(
            RejectAction(checkpoint_id="cp", reason="denied"),
        )
        assert response.body is not None
        body = cast(bytes, response.body)
        assert b'"permissionDecision": "deny"' in body
        assert b'"permissionDecisionReason": "denied"' in body


class TestHookServerLifecycle:
    @pytest.mark.asyncio
    async def test_start_returns_port(self, gate: CheckpointGate) -> None:
        srv = HookServer(gate)
        port = await srv.start()
        assert port > 0
        assert srv.port == port
        await srv.stop()

    @pytest.mark.asyncio
    async def test_double_stop_is_safe(self, gate: CheckpointGate) -> None:
        srv = HookServer(gate)
        await srv.start()
        await srv.stop()
        await srv.stop()
