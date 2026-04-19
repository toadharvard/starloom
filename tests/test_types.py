"""Tests for types.py — enums, AgentSpecData, NodePatch, WorkflowConfig."""

from __future__ import annotations

from starloom.types import (
    AgentSpecData,
    CheckpointKind,
    DecisionKind,
    EventType,
    NodePatch,
    NodeStatus,
    ParamType,
    SessionStatus,
    WorkflowConfig,
)


class TestEnums:
    def test_session_status_values(self) -> None:
        assert SessionStatus.RUNNING.value == "running"
        assert SessionStatus.COMPLETED.value == "completed"
        assert SessionStatus.ERROR.value == "error"
        assert SessionStatus.STOPPED.value == "stopped"

    def test_node_status_values(self) -> None:
        assert NodeStatus.PENDING.value == "pending"
        assert NodeStatus.RUNNING.value == "running"
        assert NodeStatus.COMPLETED.value == "completed"
        assert NodeStatus.STOPPED.value == "stopped"
        assert NodeStatus.CACHED.value == "cached"

    def test_checkpoint_kind_values(self) -> None:
        assert CheckpointKind.TOOL_CALL.value == "tool_call"
        assert CheckpointKind.CHECKPOINT.value == "checkpoint"

    def test_decision_kind_values(self) -> None:
        assert DecisionKind.APPROVED.value == "approved"
        assert DecisionKind.REJECTED.value == "rejected"
        assert DecisionKind.ANSWERED.value == "answered"

    def test_event_type_has_checkpoint_events(self) -> None:
        assert EventType.CHECKPOINT_PENDING.value == "checkpoint.pending"
        assert EventType.CHECKPOINT_RESOLVED.value == "checkpoint.resolved"

    def test_param_type_values(self) -> None:
        assert ParamType.STRING.value == "string"
        assert ParamType.INT.value == "int"
        assert ParamType.BOOL.value == "bool"


class TestAgentSpecData:
    def test_frozen(self) -> None:
        spec = AgentSpecData(prompt="hello", flags="--model haiku")
        try:
            spec.prompt = "bye"  # type: ignore[misc]
            assert False, "Should be frozen"
        except AttributeError:
            pass

    def test_prompt_preview_first_line(self) -> None:
        spec = AgentSpecData(prompt="Agent Name\nDo stuff")
        assert spec.prompt_preview == "Agent Name"

    def test_prompt_preview_single_line(self) -> None:
        spec = AgentSpecData(prompt="Simple prompt")
        assert spec.prompt_preview == "Simple prompt"

    def test_defaults(self) -> None:
        spec = AgentSpecData(prompt="p")
        assert spec.flags == ""


class TestNodePatch:
    def test_apply_overrides(self) -> None:
        spec = AgentSpecData(prompt="original", flags="--model haiku")
        patch = NodePatch(flags="--model sonnet")
        result = patch.apply(spec)
        assert result.prompt == "original"
        assert result.flags == "--model sonnet"

    def test_apply_preserves_when_none(self) -> None:
        spec = AgentSpecData(prompt="p", flags="--model m")
        patch = NodePatch()
        result = patch.apply(spec)
        assert result.prompt == "p"
        assert result.flags == "--model m"

    def test_frozen(self) -> None:
        patch = NodePatch(flags="--model opus")
        try:
            patch.flags = "--model haiku"  # type: ignore[misc]
            assert False, "Should be frozen"
        except AttributeError:
            pass


class TestWorkflowConfig:
    def test_defaults(self) -> None:
        cfg = WorkflowConfig(workflow_file="test.star", params={})
        assert cfg.backend == "claude"
        assert cfg.dry_run is False

    def test_frozen(self) -> None:
        cfg = WorkflowConfig(workflow_file="f", params={})
        try:
            cfg.workflow_file = "other"  # type: ignore[misc]
            assert False, "Should be frozen"
        except AttributeError:
            pass
