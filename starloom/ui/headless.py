"""Headless renderer -- no-op event sink for background sessions."""

from __future__ import annotations

from starloom.events import Event


class Headless:
    """No-op event renderer for background/headless sessions."""

    def on_event(self, event: Event) -> None:
        pass
