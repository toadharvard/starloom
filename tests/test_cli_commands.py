"""CLI tests for explain, completions, and shared command surfaces."""

from __future__ import annotations

import pytest
from click.testing import CliRunner

from starloom.cli import main
from starloom.cli.session import _build_config


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


# ---------------------------------------------------------------------------
# explain
# ---------------------------------------------------------------------------


class TestExplain:
    def test_list_topics(self, runner: CliRunner) -> None:
        result = runner.invoke(main, ["explain"])
        assert result.exit_code == 0
        assert "overview" in result.output

    def test_known_topic(self, runner: CliRunner) -> None:
        result = runner.invoke(main, ["explain", "session"])
        assert result.exit_code == 0
        assert "session" in result.output.lower()
        assert len(result.output.strip()) > 0

    def test_overview_topic(self, runner: CliRunner) -> None:
        result = runner.invoke(main, ["explain", "overview"])
        assert result.exit_code == 0
        assert "starloom" in result.output.lower()

    def test_unknown_topic(self, runner: CliRunner) -> None:
        result = runner.invoke(main, ["explain", "nonexistent-topic"])
        assert result.exit_code != 0

    def test_all_known_topics(self, runner: CliRunner) -> None:
        topics = [
            "node",
            "node.status",
            "node.cost",
            "checkpoint",
            "event",
            "event.types",
            "workflow",
            "workflow.params",
            "workflow.builtins",
            "models",
            "cost",
        ]
        for topic in topics:
            result = runner.invoke(main, ["explain", topic])
            assert result.exit_code == 0, f"explain {topic} failed"

    def test_removed_legacy_topic(self, runner: CliRunner) -> None:
        result = runner.invoke(main, ["explain", "permission-mode"])
        assert result.exit_code != 0
        assert "Unknown topic: permission-mode" in result.output

    def test_workflow_builtins_explains_agent_vs_call_agent(
        self, runner: CliRunner
    ) -> None:
        result = runner.invoke(main, ["explain", "workflow.builtins"])
        assert result.exit_code == 0
        assert "Run an agent immediately" in result.output
        assert "does not execute by itself" in result.output


# ---------------------------------------------------------------------------
# completions
# ---------------------------------------------------------------------------


class TestSessionConfig:
    def test_build_config_defaults_to_claude_backend(self) -> None:
        config = _build_config("workflow.star", {}, "claude", False)
        assert config.backend == "claude"

    def test_build_config_accepts_pi_backend(self) -> None:
        config = _build_config("workflow.star", {}, "pi", False)
        assert config.backend == "pi"


class TestCompletions:
    def test_bash(self, runner: CliRunner) -> None:
        result = runner.invoke(main, ["completions", "bash"])
        assert result.exit_code == 0
        assert "starloom" in result.output

    def test_zsh(self, runner: CliRunner) -> None:
        result = runner.invoke(main, ["completions", "zsh"])
        assert result.exit_code == 0
        assert "starloom" in result.output

    def test_fish(self, runner: CliRunner) -> None:
        result = runner.invoke(main, ["completions", "fish"])
        assert result.exit_code == 0
        assert "starloom" in result.output

    def test_invalid_shell(self, runner: CliRunner) -> None:
        result = runner.invoke(main, ["completions", "powershell"])
        assert result.exit_code != 0
