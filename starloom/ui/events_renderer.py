"""EventsRenderer -- raw JSONL event stream (one line per event)."""

from __future__ import annotations

import json
import sys

from starloom.events import Event


class EventsRenderer:
    """Prints each event as a single JSONL line.

    Satisfies the event-stream renderer protocol. Compact, streamable output.
    """

    def on_event(self, event: Event) -> None:
        """Print event as single-line JSON."""
        sys.stdout.write(json.dumps(event.to_dict(), default=str) + "\n")
        sys.stdout.flush()
