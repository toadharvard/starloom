from __future__ import annotations

from starloom.event_data import (
    CheckpointPendingData,
    WorkflowOutputData,
    event_data_from_dict,
)
from starloom.types import CheckpointKind


def test_event_data_from_dict_nested_agent_spec() -> None:
    data = event_data_from_dict(
        "checkpoint.pending",
        {
            "checkpoint_id": "cp-1",
            "kind": "checkpoint",
            "node_id": "n1",
            "description": "Need input",
            "spec": {"prompt": "hello", "flags": "--model haiku"},
        },
    )
    assert isinstance(data, CheckpointPendingData)
    assert data.spec is not None
    assert data.spec.prompt == "hello"
    assert data.spec.flags == "--model haiku"
    assert data.kind == CheckpointKind.CHECKPOINT


def test_event_data_from_dict_workflow_output() -> None:
    data = event_data_from_dict(
        "workflow.output",
        {
            "output": "hello",
        },
    )
    assert isinstance(data, WorkflowOutputData)
    assert data.output == "hello"
