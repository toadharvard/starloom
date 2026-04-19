"""Shared fixtures for starloom v2 tests."""

from __future__ import annotations

import pytest

from starloom.event_data import WorkflowOutputData
from starloom.events import EventBus
from starloom.types import AgentSpecData, EventType


@pytest.fixture
def bus() -> EventBus:
    return EventBus(session_id="test-session")


@pytest.fixture
def sample_spec() -> AgentSpecData:
    return AgentSpecData(
        prompt="Test Agent\nDo something useful",
        flags="--model haiku --verbose",
    )


def collect_outputs(bus: EventBus) -> list[str | None]:
    """Reconstruct the ordered list of values passed to output() from events."""
    values: list[str | None] = []
    for event in bus.log:
        if event.type == EventType.WORKFLOW_OUTPUT and isinstance(
            event.data,
            WorkflowOutputData,
        ):
            values.append(event.data.output)
    return values
