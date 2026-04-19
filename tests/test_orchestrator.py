"""Integration tests for orchestrator.py — wires all layers end-to-end.

Uses DryRunBackend so no real Claude invocations happen.
"""

from __future__ import annotations

import asyncio
import tempfile
from collections.abc import Generator
from pathlib import Path

import pytest

import starloom.session.manager as mgr_mod
import starloom.session.state as state_mod
from starloom.backend.dry_run import DryRunBackend
from starloom.backend.protocol import AgentResult
from starloom.events import EventBus
from starloom.orchestrator import (
    _make_backend_resolver,
    resume_workflow,
    run_workflow,
)
from starloom.session.manager import SessionManager
from starloom.session.persistence import load_events
from starloom.types import AgentSpecData, EventType, SessionStatus, WorkflowConfig


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def sessions_dir(monkeypatch: pytest.MonkeyPatch) -> Generator[Path, None, None]:
    """Redirect SESSIONS_DIR to a short /tmp path (Unix socket path limit)."""
    with tempfile.TemporaryDirectory(prefix="sl", dir="/tmp") as td:
        sessions = Path(td) / "s"
        sessions.mkdir()
        last_file = sessions / ".last"
        monkeypatch.setattr(state_mod, "SESSIONS_DIR", sessions)
        monkeypatch.setattr(state_mod, "LAST_SESSION_FILE", last_file)
        monkeypatch.setattr(mgr_mod, "SESSIONS_DIR", sessions)
        monkeypatch.setattr(mgr_mod, "LAST_SESSION_FILE", last_file)
        yield sessions


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _config(backend: str = "claude") -> WorkflowConfig:
    return WorkflowConfig(workflow_file="test.star", params={}, backend=backend)


# ---------------------------------------------------------------------------
# _make_backend_resolver
# ---------------------------------------------------------------------------


class TestMakeBackendResolver:
    def test_resolver_uses_explicit_backend(self) -> None:
        config = _config(backend="dry_run")
        backend = DryRunBackend()
        resolver = _make_backend_resolver(config, backend)
        assert resolver.resolve("dry_run") is backend

    def test_resolver_reuses_main_backend_for_alias(self) -> None:
        config = _config(backend="claude")
        backend = DryRunBackend()
        resolver = _make_backend_resolver(config, backend)
        assert resolver.resolve("claude") is backend


# ---------------------------------------------------------------------------
# run_workflow
# ---------------------------------------------------------------------------


class TestRunWorkflow:
    @pytest.mark.asyncio
    async def test_returns_successful_result(self, sessions_dir: Path) -> None:
        result = await run_workflow(_config(), DryRunBackend(), source='output("done")')
        assert result.error is None

    @pytest.mark.asyncio
    async def test_persists_events(self, sessions_dir: Path) -> None:
        session = SessionManager.create(
            "test.star", source='output("done")', config=_config()
        )
        result = await run_workflow(
            _config(), DryRunBackend(), source='output("done")', session=session
        )
        assert result.error is None
        events = load_events(SessionManager.load(session.id))
        assert [event.type for event in events] == [
            EventType.WORKFLOW_START,
            EventType.WORKFLOW_OUTPUT,
            EventType.WORKFLOW_END,
        ]

    @pytest.mark.asyncio
    async def test_sets_completed_status(self, sessions_dir: Path) -> None:
        await run_workflow(_config(), DryRunBackend(), source='output("done")')
        session = SessionManager.resolve()
        assert session.status == SessionStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_sets_error_status_for_bad_source(self, sessions_dir: Path) -> None:
        await run_workflow(_config(), DryRunBackend(), source="bad syntax (")
        sessions = SessionManager.list_all()
        assert sessions[0].status == SessionStatus.ERROR


class TestStopSessionPersistence:
    @pytest.mark.asyncio
    async def test_reconcile_does_not_emit_duplicate_node_stopped(
        self, sessions_dir: Path
    ) -> None:
        class BlockingBackend(DryRunBackend):
            def __init__(self) -> None:
                super().__init__()
                self._release = asyncio.Event()
                self._stopped = False

            async def run(
                self, _spec: AgentSpecData, _node_id: str, _bus: EventBus
            ) -> AgentResult:
                await self._release.wait()
                if self._stopped:
                    from starloom.backend.protocol import StopRequestedError

                    raise StopRequestedError("stop requested")
                return AgentResult(output="done")

            async def stop(self, _node_id: str) -> None:
                self._stopped = True
                self._release.set()

        config = _config()
        session = SessionManager.create(
            "test.star", source='call_agent("x")', config=config
        )
        task = asyncio.create_task(
            run_workflow(
                config, BlockingBackend(), source='call_agent("x")', session=session
            )
        )
        await asyncio.sleep(0.05)
        from starloom.session.service import _send_stop_session_async

        delivery = await _send_stop_session_async(session.dir / "session.sock")
        assert delivery.accepted is True
        await task

        events = load_events(SessionManager.load(session.id))
        assert [event.type for event in events] == [
            EventType.WORKFLOW_START,
            EventType.NODE_ADDED,
            EventType.NODE_STARTED,
            EventType.NODE_STOPPED,
            EventType.WORKFLOW_END,
        ]


class TestSessionStatus:
    @pytest.mark.asyncio
    async def test_completed_on_success(self, sessions_dir: Path) -> None:
        await run_workflow(_config(), DryRunBackend(), source="")
        session = SessionManager.resolve()
        assert session.status == SessionStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_error_on_bad_source(self, sessions_dir: Path) -> None:
        await run_workflow(_config(), DryRunBackend(), source="bad syntax (")
        sessions = SessionManager.list_all()
        assert sessions[0].status == SessionStatus.ERROR

    @pytest.mark.asyncio
    async def test_lock_released_after_run(self, sessions_dir: Path) -> None:
        from starloom.session.persistence import SessionLock

        await run_workflow(_config(), DryRunBackend(), source="")
        session = SessionManager.resolve()
        lock = SessionLock(session)
        lock.acquire()
        lock.release()

    @pytest.mark.asyncio
    async def test_stop_request_marks_session_stopped(self, sessions_dir: Path) -> None:
        class BlockingBackend(DryRunBackend):
            def __init__(self) -> None:
                super().__init__()
                self._stopped = False

            async def run(
                self, _spec: AgentSpecData, _node_id: str, _bus: EventBus
            ) -> AgentResult:
                await asyncio.sleep(0.2)
                if self._stopped:
                    from starloom.backend.protocol import StopRequestedError

                    raise StopRequestedError("stop requested")
                return AgentResult(output="done")

            async def stop(self, _node_id: str) -> None:
                self._stopped = True

        config = _config()
        session = SessionManager.create(
            "test.star", source='call_agent("x")', config=config
        )

        async def _run() -> None:
            await run_workflow(
                config, BlockingBackend(), source='call_agent("x")', session=session
            )

        task = asyncio.create_task(_run())
        await asyncio.sleep(0.05)
        sock = session.dir / "session.sock"
        assert sock.exists()

        from starloom.session.service import _send_stop_session_async

        delivery = await _send_stop_session_async(sock)
        assert delivery.accepted is True
        assert delivery.error is None
        await task

        reloaded = SessionManager.load(session.id)
        assert reloaded.status == SessionStatus.STOPPED


# ---------------------------------------------------------------------------
# resume_workflow
# ---------------------------------------------------------------------------


class TestResumeWorkflow:
    @pytest.mark.asyncio
    async def test_resume_replays_real_stopped_session(
        self, sessions_dir: Path
    ) -> None:
        class BlockingBackend(DryRunBackend):
            def __init__(self) -> None:
                super().__init__()
                self._release = asyncio.Event()
                self._stopped = False

            async def run(
                self, _spec: AgentSpecData, _node_id: str, _bus: EventBus
            ) -> AgentResult:
                await self._release.wait()
                if self._stopped:
                    from starloom.backend.protocol import StopRequestedError

                    raise StopRequestedError("stop requested")
                return AgentResult(output="done")

            async def stop(self, _node_id: str) -> None:
                self._stopped = True
                self._release.set()

        backend = BlockingBackend()
        src = 'call_agent("step one")\noutput("done")'
        session = SessionManager.create("test.star", source=src, config=_config())
        task = asyncio.create_task(
            run_workflow(_config(), backend, source=src, session=session)
        )
        await asyncio.sleep(0.05)
        delivery = await __import__(
            "starloom.session.service",
            fromlist=["_send_stop_session_async"],
        )._send_stop_session_async(session.dir / "session.sock")
        assert delivery.accepted is True
        await task

        stopped_session = SessionManager.load(session.id)
        assert stopped_session.status == SessionStatus.STOPPED
        result = await resume_workflow(stopped_session, _config(), DryRunBackend())
        assert result.error is None

    @pytest.mark.asyncio
    async def test_resume_sets_session_completed_after_real_stop(
        self, sessions_dir: Path
    ) -> None:
        class BlockingBackend(DryRunBackend):
            def __init__(self) -> None:
                super().__init__()
                self._release = asyncio.Event()
                self._stopped = False

            async def run(
                self, _spec: AgentSpecData, _node_id: str, _bus: EventBus
            ) -> AgentResult:
                await self._release.wait()
                if self._stopped:
                    from starloom.backend.protocol import StopRequestedError

                    raise StopRequestedError("stop requested")
                return AgentResult(output="done")

            async def stop(self, _node_id: str) -> None:
                self._stopped = True
                self._release.set()

        backend = BlockingBackend()
        session = SessionManager.create(
            "test.star", source='call_agent("ok")', config=_config()
        )
        task = asyncio.create_task(
            run_workflow(_config(), backend, source='call_agent("ok")', session=session)
        )
        await asyncio.sleep(0.05)
        delivery = await __import__(
            "starloom.session.service",
            fromlist=["_send_stop_session_async"],
        )._send_stop_session_async(session.dir / "session.sock")
        assert delivery.accepted is True
        await task

        stopped = SessionManager.load(session.id)
        assert stopped.status == SessionStatus.STOPPED
        result = await resume_workflow(stopped, _config(), DryRunBackend())
        assert result.error is None
        resumed = SessionManager.load(session.id)
        assert resumed.status == SessionStatus.COMPLETED
