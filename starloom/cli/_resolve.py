"""Session resolution helpers shared across CLI commands."""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

import click

from starloom.session.manager import SessionManager

if TYPE_CHECKING:
    from starloom.session.state import Session


def resolve_session_id(session_id: str | None) -> str:
    """Resolve session ID: explicit, env var, or last used."""
    try:
        return SessionManager.resolve(session_id).id
    except FileNotFoundError as exc:
        raise click.UsageError(
            "No session specified and no last session found.\n"
            "Hint: provide SESSION_ID or run 'starloom session create' first."
        ) from exc


def write_last_session_id(session_id: str) -> None:
    """Write session ID to ~/.starloom/sessions/.last."""
    SessionManager.set_last(session_id)


def load_session(session_id: str) -> Session:
    """Load a session by ID from disk."""
    try:
        return SessionManager.load(session_id)
    except FileNotFoundError as exc:
        raise click.ClickException(f"Session not found: {session_id}") from exc


def parse_params(raw: tuple[str, ...]) -> dict[str, str]:
    """Parse KEY=VALUE pairs from CLI -p options."""
    params: dict[str, str] = {}
    for item in raw:
        if "=" not in item:
            raise click.UsageError(f"Invalid param (expected KEY=VALUE): {item}")
        key, value = item.split("=", 1)
        params[key] = value
    return params


def parse_duration(duration: str) -> float:
    """Parse a duration string like '7d', '24h', '30m' to seconds."""
    units = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    if not duration:
        raise click.UsageError("Empty duration.")
    unit = duration[-1].lower()
    if unit not in units:
        raise click.UsageError(f"Unknown duration unit '{unit}'. Use s/m/h/d.")
    try:
        value = float(duration[:-1])
    except ValueError as exc:
        raise click.UsageError(f"Invalid duration: {duration}") from exc
    return value * units[unit]


def parse_timedelta(duration: str) -> timedelta:
    """Parse a duration string to a timedelta."""
    return timedelta(seconds=parse_duration(duration))
