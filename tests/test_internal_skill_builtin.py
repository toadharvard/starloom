from __future__ import annotations

from pathlib import Path

import pytest

from starloom.backend.protocol import AgentResult
from starloom.checkpoint import CheckpointGate
from starloom.events import EventBus
from starloom.runtime import execute
from starloom.types import AgentSpecData, WorkflowConfig

from tests.conftest import collect_outputs


class FakeBackend:
    async def run(
        self, spec: AgentSpecData, _node_id: str, _bus: EventBus
    ) -> AgentResult:
        return AgentResult(
            output=spec.prompt,
            cost_usd=None,
            input_tokens=None,
            output_tokens=None,
        )

    async def stop(self, _node_id: str) -> None:
        return None


def _config(workflow_file: str) -> WorkflowConfig:
    return WorkflowConfig(
        workflow_file=workflow_file,
        params={},
        backend="claude",
        dry_run=False,
    )


@pytest.mark.asyncio
async def test_load_internal_skill_reads_file_relative_to_workflow_root(
    tmp_path: Path,
) -> None:
    root = tmp_path / "bundle"
    skills = root / "build-flow" / "skills" / "starloom-dod-designer"
    skills.mkdir(parents=True)
    workflow = root / "build.star"
    skill_file = skills / "SKILL.md"
    skill_file.write_text("dod-skill")
    workflow.write_text(
        'output(load_internal_skill("build-flow/skills/starloom-dod-designer/SKILL.md"))'
    )

    bus = EventBus(session_id="test")
    result = await execute(
        workflow.read_text(),
        _config(str(workflow)),
        bus,
        FakeBackend(),
        CheckpointGate(bus),
    )

    assert result.error is None
    assert collect_outputs(bus) == ["dod-skill"]


@pytest.mark.asyncio
async def test_load_internal_skill_rejects_path_traversal(tmp_path: Path) -> None:
    root = tmp_path / "bundle"
    root.mkdir()
    workflow = root / "build.star"
    workflow.write_text('output(load_internal_skill("../secret.txt"))')

    bus = EventBus(session_id="test")
    result = await execute(
        workflow.read_text(),
        _config(str(workflow)),
        bus,
        FakeBackend(),
        CheckpointGate(bus),
    )

    assert result.error is not None
    assert "path traversal" in result.error


@pytest.mark.asyncio
async def test_load_internal_skill_errors_for_missing_file(tmp_path: Path) -> None:
    root = tmp_path / "bundle"
    root.mkdir()
    workflow = root / "build.star"
    workflow.write_text(
        'output(load_internal_skill("build-flow/skills/missing/SKILL.md"))'
    )

    bus = EventBus(session_id="test")
    result = await execute(
        workflow.read_text(),
        _config(str(workflow)),
        bus,
        FakeBackend(),
        CheckpointGate(bus),
    )

    assert result.error is not None
    assert "Internal skill not found" in result.error
