from __future__ import annotations

from starloom.orchestrator import _GRAPH_EVENTS
from starloom.types import EventType


def test_graph_events_include_live_status_persistence_events() -> None:
    assert EventType.NODE_CACHED in _GRAPH_EVENTS
    assert EventType.NODE_SKIPPED in _GRAPH_EVENTS
    assert EventType.NODE_STOPPED in _GRAPH_EVENTS
