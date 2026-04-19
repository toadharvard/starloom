"""Session deletion helpers — find targets, confirm, remove."""

from __future__ import annotations

from pathlib import Path

import click

from starloom.cli.output import SessionDeleted, render_json, render_text


def delete_one(session_id: str, confirm: bool = False) -> None:
    """Delete a single session directory."""
    import shutil
    from starloom.session.state import SESSIONS_DIR

    session_dir = SESSIONS_DIR / session_id
    if not session_dir.exists():
        raise click.ClickException(f"Session not found: {session_id}")
    if not confirm:
        click.confirm(f"Delete session {session_id}?", abort=True)
    shutil.rmtree(session_dir)
    render_json(SessionDeleted(session_id=session_id))


def delete_all_sessions(
    older_than: str | None,
    confirm: bool = False,
) -> None:
    """Delete all (optionally filtered) sessions."""
    from starloom.session.state import SESSIONS_DIR

    if not SESSIONS_DIR.exists():
        render_text("No sessions to delete.")
        return
    targets = _resolve_delete_targets(SESSIONS_DIR, older_than)
    if not targets:
        render_text("No matching sessions.")
        return
    _confirm_and_delete(targets, confirm)


def _resolve_delete_targets(
    sessions_dir: Path,
    older_than: str | None,
) -> list[Path]:
    """Parse duration filter and find matching session directories."""
    import time
    from starloom.cli._resolve import parse_duration

    max_age = parse_duration(older_than) if older_than else None
    return _find_delete_targets(sessions_dir, max_age, time.time())


def _confirm_and_delete(targets: list[Path], confirm: bool) -> None:
    """Prompt for confirmation, then remove session directories."""
    import shutil

    render_text(f"About to delete {len(targets)} session(s).")
    if not confirm:
        click.confirm("Proceed?", abort=True)
    for d in targets:
        shutil.rmtree(d)
    render_text(f"Deleted {len(targets)} session(s).")


def _find_delete_targets(
    sessions_dir: Path,
    max_age: float | None,
    now: float,
) -> list[Path]:
    """Find session directories matching deletion criteria."""
    targets: list[Path] = []
    for d in list(sessions_dir.iterdir()):
        if not d.is_dir() or d.name.startswith("."):
            continue
        if max_age and (now - d.stat().st_mtime) < max_age:
            continue
        targets.append(d)
    return targets
