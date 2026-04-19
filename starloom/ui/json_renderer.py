"""JsonRenderer -- pretty-printed JSON output for each event."""

from __future__ import annotations

import json
import sys
from dataclasses import fields

from starloom.events import Event
from starloom.ui.protocol import SessionSnapshot


class JsonRenderer:
    """Prints each event and snapshot as pretty-printed JSON.

    Satisfies event, snapshot, and close renderer protocols.
    """

    def on_event(self, event: Event) -> None:
        """Print event as indented JSON."""
        sys.stdout.write(json.dumps(event.to_dict(), indent=2, default=str) + "\n")
        sys.stdout.flush()

    def on_snapshot(self, snapshot: SessionSnapshot) -> None:
        """Print snapshot as indented JSON."""
        d = {f.name: getattr(snapshot, f.name) for f in fields(snapshot)}
        sys.stdout.write(json.dumps(d, indent=2, default=str) + "\n")
        sys.stdout.flush()

    def on_closed(self, reason: str) -> None:
        """Print close event as JSON."""
        sys.stdout.write(json.dumps({"closed": reason}) + "\n")
        sys.stdout.flush()
