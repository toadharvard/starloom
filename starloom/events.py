"""EventBus — async pub/sub for all starloom observability.

Handlers are async and called sequentially via await. Handlers must be
fast (file write OK, network call NO).
"""

from __future__ import annotations

import json
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import cast

from starloom.event_data import EventData
from starloom.serialization import dataclass_to_dict
from starloom.types import EventType


@dataclass(frozen=True, slots=True)
class Event:
    """Immutable event record with typed data."""

    type: EventType
    timestamp: float
    session_id: str
    node_id: str | None = None
    seq: int | None = None
    data: EventData | None = None

    def to_dict(self) -> dict[str, object]:
        """Serialize to plain dict for JSON encoding."""
        d: dict[str, object] = {
            "type": self.type.value,
            "timestamp": self.timestamp,
            "session_id": self.session_id,
            "node_id": self.node_id,
            "seq": self.seq,
        }
        d["data"] = _serialize_event_data(self.data)
        return d

    def to_jsonl(self) -> str:
        return json.dumps(self.to_dict(), default=str)

    @classmethod
    def from_dict(cls, d: dict[str, object]) -> Event:
        """Reconstruct an Event from a serialized dict."""
        from starloom.event_data import event_data_from_dict

        event_type = EventType(d["type"])
        raw_data = d.get("data", {})
        data = event_data_from_dict(
            event_type.value,
            cast(dict[str, object], raw_data),
        )
        return cls(
            type=event_type,
            timestamp=cast(float, d["timestamp"]),
            session_id=cast(str, d["session_id"]),
            node_id=cast("str | None", d.get("node_id")),
            seq=cast("int | None", d.get("seq")),
            data=data,
        )

    @classmethod
    def from_jsonl(cls, line: str) -> Event:
        """Reconstruct an Event from a JSONL line with typed EventData."""
        return cls.from_dict(json.loads(line))


def _serialize_event_data(data: EventData | None) -> dict[str, object]:
    """Convert a typed EventData to a plain dict."""
    if data is None:
        return {}
    return dataclass_to_dict(data)


EventHandler = Callable[[Event], Awaitable[None]]


class EventBus:
    """Async pub/sub bus. Handlers called sequentially via await.

    All operations run on a single asyncio event loop — no locks needed.
    """

    def __init__(self, session_id: str) -> None:
        self._session_id = session_id
        self._handlers: dict[EventType | None, list[EventHandler]] = {}
        self._event_log: list[Event] = []

    @property
    def session_id(self) -> str:
        return self._session_id

    def subscribe(
        self,
        handler: EventHandler,
        event_type: EventType | None = None,
    ) -> None:
        """Subscribe handler. None = all events."""
        self._handlers.setdefault(event_type, []).append(handler)

    def unsubscribe(self, handler: EventHandler) -> None:
        for handlers in self._handlers.values():
            try:
                handlers.remove(handler)
            except ValueError:
                pass

    async def emit(self, event: Event) -> None:
        """Emit event: log it, then call handlers sequentially."""
        self._event_log.append(event)
        await self._dispatch(event)

    async def _dispatch(self, event: Event) -> None:
        """Call matching handlers in subscription order."""
        for key in (event.type, None):
            for handler in list(self._handlers.get(key, [])):
                await handler(event)

    def make_event(
        self,
        event_type: EventType,
        node_id: str | None = None,
        seq: int | None = None,
        data: EventData | None = None,
    ) -> Event:
        """Factory — pre-fills session_id and timestamp."""
        return Event(
            type=event_type,
            timestamp=time.time(),
            session_id=self._session_id,
            node_id=node_id,
            seq=seq,
            data=data,
        )

    @property
    def log(self) -> list[Event]:
        return list(self._event_log)
