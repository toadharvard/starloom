"""Session application services.

This layer owns session lifecycle use-cases. CLI should delegate here
instead of mutating persistence or coordinating worker state directly.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import subprocess
import sys

from starloom.session import SessionManager
from starloom.session.persistence import detect_crash
from starloom.types import SessionStatus, WorkflowConfig


@dataclass(frozen=True, slots=True)
class StopSessionResult:
    """Result of a stop request."""

    session_id: str
    accepted: bool
    status: str
    message: str | None = None


@dataclass(frozen=True, slots=True)
class StopDeliveryResult:
    accepted: bool
    error: str | None = None


@dataclass(frozen=True, slots=True)
class ResumeValidationResult:
    """Normalized resume validation outcome."""

    session_id: str
    status: SessionStatus


@dataclass(frozen=True, slots=True)
class CreatedSessionResult:
    """Result of session creation and worker launch."""

    session_id: str
    workflow_file: str
    status: str


def create_session(
    config: WorkflowConfig,
    workflow_path: Path,
) -> CreatedSessionResult:
    """Create session state, persist source/config, launch worker."""
    sess = SessionManager.create(
        workflow_file=config.workflow_file,
        params=config.params,
        source=workflow_path.read_text(),
        config=config,
    )
    SessionManager.set_last(sess.id)
    _launch_worker(sess.id, mode="run")
    return CreatedSessionResult(
        session_id=sess.id,
        workflow_file=config.workflow_file,
        status=sess.status.value,
    )


def validate_resumable(session_id: str) -> ResumeValidationResult:
    """Ensure a session can be resumed; normalize crash detection."""
    sess = SessionManager.load(session_id)
    status = sess.status
    if status == SessionStatus.RUNNING:
        if detect_crash(sess):
            return ResumeValidationResult(session_id=session_id, status=sess.status)
        msg = "Session is already running."
        raise ValueError(msg)
    if status == SessionStatus.COMPLETED:
        msg = "Session already completed."
        raise ValueError(msg)
    return ResumeValidationResult(session_id=session_id, status=status)


def launch_resume(session_id: str) -> None:
    """Launch background worker to resume a session."""
    SessionManager.set_last(session_id)
    _launch_worker(session_id, mode="resume")


def request_stop(session_id: str) -> StopSessionResult:
    """Request stop for a running session without mutating terminal state.

    Terminal state is owned by the worker/orchestrator. This service only
    sends the stop intent to the running session server.
    """
    sess = SessionManager.load(session_id)
    if sess.status != SessionStatus.RUNNING:
        return StopSessionResult(
            session_id=session_id,
            accepted=False,
            status=sess.status.value,
            message=(
                f"Session {session_id} is not running "
                f"(status: {sess.status.value}). No action taken."
            ),
        )
    sock = sess.dir / "session.sock"
    if not sock.exists():
        return StopSessionResult(
            session_id=session_id,
            accepted=False,
            status=sess.status.value,
            message="stop delivery failed",
        )
    delivery = _send_stop_session(sock)
    if not delivery.accepted:
        return StopSessionResult(
            session_id=session_id,
            accepted=False,
            status=sess.status.value,
            message=delivery.error or "stop delivery failed",
        )
    return StopSessionResult(
        session_id=session_id,
        accepted=True,
        status="stop_requested",
        message=delivery.error or "stop request accepted",
    )


def _launch_worker(session_id: str, mode: str) -> None:
    """Spawn background worker process."""
    session_dir = SessionManager.session_dir(session_id)
    log_file = session_dir / "worker.log"
    with open(log_file, "w") as log_fd:
        subprocess.Popen(
            [sys.executable, "-m", "starloom._worker", mode, session_id],
            start_new_session=True,
            stdout=log_fd,
            stderr=subprocess.STDOUT,
        )


def _send_stop_session(sock_path: Path) -> StopDeliveryResult:
    """Send StopSessionAction via socket to a running session server."""
    import asyncio

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        try:
            return asyncio.run(_send_stop_session_async(sock_path))
        except (ConnectionError, OSError, ValueError) as exc:
            return StopDeliveryResult(False, str(exc))
    msg = "_send_stop_session() cannot be called from an active event loop"
    raise RuntimeError(msg)


async def _send_stop_session_async(sock_path: Path) -> StopDeliveryResult:
    """Async stop request helper for worker/integration paths."""
    from starloom.checkpoint import StopSessionAction
    from starloom.client import SessionClient

    client = SessionClient(sock_path)
    await client.connect_control()
    try:
        result = await client.send_action(StopSessionAction())
        return StopDeliveryResult(accepted=result.ok, error=result.error)
    finally:
        await client.disconnect()
