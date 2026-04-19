"""Background worker -- runs/resumes workflow in a separate process."""

from __future__ import annotations

import asyncio
import logging
import sys

from starloom.backend.dry_run import DryRunBackend
from starloom.backend.protocol import AgentBackend
from starloom.session.persistence import load_config, load_meta, load_workflow_source
from starloom.session.state import SESSIONS_DIR, Session, session_from_meta
from starloom.types import WorkflowConfig


def main() -> None:
    """Entry point: ``python -m starloom._worker run|resume <session_id>``."""
    logging.basicConfig(level=logging.INFO)
    mode, session_id = sys.argv[1], sys.argv[2]
    if mode == "run":
        asyncio.run(_run(session_id))
    elif mode == "resume":
        asyncio.run(_resume(session_id))
    else:
        sys.exit(f"Unknown mode: {mode}")


def _load_session(session_id: str) -> Session:
    """Load a session from disk by ID."""
    session_dir = SESSIONS_DIR / session_id
    meta = load_meta(session_dir)
    return session_from_meta(meta)


def _make_backend(config: WorkflowConfig) -> AgentBackend:
    """Create the appropriate backend from config."""
    if config.dry_run:
        return DryRunBackend()
    if config.backend == "pi":
        from starloom.backend.pi import PiBackend

        return PiBackend()
    from starloom.backend.claude_cli import ClaudeCLIBackend

    return ClaudeCLIBackend()


async def _run(session_id: str) -> None:
    """Run a new workflow session (session already created on disk by CLI)."""
    from starloom.orchestrator import run_workflow

    session = _load_session(session_id)
    config = load_config(session)
    source = load_workflow_source(session)
    backend = _make_backend(config)
    await run_workflow(config, backend, source=source, session=session)


async def _resume(session_id: str) -> None:
    """Resume an existing workflow session."""
    from starloom.orchestrator import resume_workflow

    session = _load_session(session_id)
    config = load_config(session)
    backend = _make_backend(config)
    await resume_workflow(session, config, backend)


if __name__ == "__main__":
    main()
