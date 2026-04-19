"""Tests for backend layer: protocol, dry_run, claude_cli, _stream_parser."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import cast
from unittest.mock import MagicMock

import pytest

from starloom.backend.protocol import AgentBackend, AgentResult
from starloom.events import Event, EventBus
from starloom.backend.dry_run import DryRunBackend
from starloom.backend.claude_cli import (
    ClaudeCLIBackend,
    _build_cmd,
    _hook_settings_json,
    _HOOK_TIMEOUT_SECS,
)
from starloom.backend.pi import PiBackend, _build_cmd as _build_pi_cmd
from starloom.backend._msg_handlers import (
    handle_assistant,
    handle_block_delta,
    handle_block_start,
    handle_block_stop,
    handle_tool_result,
    handle_user,
)
from starloom.backend._stream_parser import (
    BlockState,
    StreamAccumulator,
    get_content_blocks,
    input_preview,
    parse_cost_usd,
    parse_usage,
    try_parse_json,
)
from starloom.cost import TokenUsage
from starloom.types import AgentSpecData


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def bus() -> EventBus:
    return EventBus(session_id="test-backend")


@pytest.fixture
def spec() -> AgentSpecData:
    return AgentSpecData(
        prompt="Test Agent\nDo the thing",
        flags="--model haiku",
    )


@pytest.fixture
def full_spec() -> AgentSpecData:
    return AgentSpecData(
        prompt="Full Agent\nWith raw backend flags",
        flags="--model sonnet --verbose --max-turns 120 --append-system-prompt 'Helpful reviewer' --output-format text",
    )


# ===========================================================================
# AgentResult
# ===========================================================================


class TestAgentResult:
    def test_minimal(self) -> None:
        r = AgentResult(
            output="hello", cost_usd=0.01, input_tokens=100, output_tokens=50
        )
        assert r.output == "hello"
        assert r.cost_usd == 0.01
        assert r.error is None
        assert r.backend_session_id is None

    def test_with_error(self) -> None:
        r = AgentResult(
            output="[error]",
            cost_usd=None,
            input_tokens=None,
            output_tokens=None,
            error="timeout",
            backend_session_id="sess-123",
        )
        assert r.error == "timeout"
        assert r.backend_session_id == "sess-123"

    def test_frozen(self) -> None:
        r = AgentResult(
            output="x", cost_usd=None, input_tokens=None, output_tokens=None
        )
        with pytest.raises(AttributeError):
            r.output = "y"  # type: ignore[misc]


# ===========================================================================
# AgentBackend protocol
# ===========================================================================


class TestAgentBackendProtocol:
    def test_dry_run_satisfies_protocol(self) -> None:
        """DryRunBackend structurally satisfies AgentBackend."""
        backend: AgentBackend = DryRunBackend()
        assert hasattr(backend, "run")
        assert hasattr(backend, "stop")

    def test_cli_satisfies_protocol(self) -> None:
        backend: AgentBackend = ClaudeCLIBackend()
        assert hasattr(backend, "run")
        assert hasattr(backend, "stop")

    def test_pi_satisfies_protocol(self) -> None:
        backend: AgentBackend = PiBackend()
        assert hasattr(backend, "run")
        assert hasattr(backend, "stop")


# ===========================================================================
# DryRunBackend
# ===========================================================================


class TestDryRunBackend:
    @pytest.mark.asyncio
    async def test_run_returns_placeholder(
        self, spec: AgentSpecData, bus: EventBus
    ) -> None:
        backend = DryRunBackend()
        result = await backend.run(spec, "node-1", bus)
        assert result.output == "[dry-run] not executed"
        assert result.cost_usd is not None and result.cost_usd > 0
        assert result.input_tokens is not None and result.input_tokens > 0
        assert result.output_tokens == 500
        assert result.error is None

    @pytest.mark.asyncio
    async def test_stop_is_noop(self) -> None:
        backend = DryRunBackend()
        await backend.stop("node-1")  # should not raise

    def test_estimate(self, spec: AgentSpecData) -> None:
        backend = DryRunBackend()
        est = backend.estimate(spec)
        assert est.cost_usd > 0
        assert est.usage.input_tokens > 0

    @pytest.mark.asyncio
    async def test_cost_scales_with_prompt_length(self, bus: EventBus) -> None:
        backend = DryRunBackend()
        short = AgentSpecData(prompt="hi", flags="--model haiku")
        long = AgentSpecData(prompt="x" * 10000, flags="--model haiku")
        r_short = await backend.run(short, "n1", bus)
        r_long = await backend.run(long, "n2", bus)
        assert r_long.cost_usd is not None
        assert r_short.cost_usd is not None
        assert r_long.cost_usd > r_short.cost_usd

    @pytest.mark.asyncio
    async def test_flags_do_not_change_token_estimate(self, bus: EventBus) -> None:
        backend = DryRunBackend()
        without = AgentSpecData(prompt="test")
        with_flags = AgentSpecData(prompt="test", flags="--model haiku --verbose")
        r1 = await backend.run(without, "n1", bus)
        r2 = await backend.run(with_flags, "n2", bus)
        assert r2.input_tokens == r1.input_tokens


# ===========================================================================
# ClaudeCLIBackend — construction and command building
# ===========================================================================


class TestClaudeCLIBackendConstruction:
    def test_default_has_no_hook_port(self) -> None:
        backend = ClaudeCLIBackend()
        assert backend._hook_port is None

    def test_configure_hook_port_updates_port(self) -> None:
        backend = ClaudeCLIBackend()
        backend.configure_hook_port(8080)
        assert backend._hook_port == 8080


class TestClaudeCLIBuildCmd:
    def test_basic_cmd(self, spec: AgentSpecData) -> None:
        cmd = _build_cmd(spec)
        assert cmd[0] == "claude"
        assert "-p" in cmd
        assert spec.prompt in cmd
        assert "--model" in cmd
        assert "haiku" in cmd
        assert "--permission-mode" in cmd
        assert "bypassPermissions" in cmd

    def test_full_spec_cmd(self, full_spec: AgentSpecData) -> None:
        cmd = _build_cmd(full_spec)
        assert "--verbose" in cmd
        assert "--append-system-prompt" in cmd
        assert "Helpful reviewer" in cmd
        assert "--max-turns" in cmd
        assert "120" in cmd
        assert cmd[-2:] == ["--output-format", "text"]

    def test_hook_port_cmd(self, spec: AgentSpecData) -> None:
        cmd = _build_cmd(spec, hook_port=8080)
        assert "dontAsk" in cmd

    def test_backend_flags_append_after_default_transport_flags(
        self, spec: AgentSpecData
    ) -> None:
        cmd = _build_cmd(
            AgentSpecData(prompt=spec.prompt, flags="--verbose --output-format text"),
            hook_port=8080,
        )
        permission_idx = [
            i for i, part in enumerate(cmd) if part == "--permission-mode"
        ]
        assert len(permission_idx) == 1
        assert cmd[permission_idx[0] + 1] == "dontAsk"
        assert cmd[-2:] == ["--output-format", "text"]


class TestHookSettingsJson:
    def test_no_hook_no_settings_flag(self) -> None:
        cmd = _build_cmd(
            AgentSpecData(prompt="hi", flags="--model haiku"),
            hook_port=None,
        )
        assert "--settings" not in cmd

    def test_hook_port_adds_tool_use_hook_settings(self) -> None:
        cmd = _build_cmd(
            AgentSpecData(prompt="hi", flags="--model haiku"),
            hook_port=8080,
        )
        idx = cmd.index("--settings")
        settings = json.loads(cmd[idx + 1])
        hooks = settings["hooks"]["PreToolUse"]
        assert len(hooks) == 1
        http_hook = hooks[0]["hooks"][0]
        assert http_hook["type"] == "http"
        assert "8080" in http_hook["url"]
        assert http_hook["timeout"] == _HOOK_TIMEOUT_SECS


class TestPiBuildCmd:
    def test_basic_cmd(self, spec: AgentSpecData) -> None:
        cmd = _build_pi_cmd(spec)
        assert cmd[0] == "pi"
        assert "--mode" in cmd
        assert "json" in cmd
        assert "--print" in cmd
        assert "--no-session" in cmd
        assert spec.prompt in cmd
        assert "--model" in cmd
        assert "codex-lb/gpt-5.4" in cmd
        assert "haiku" in cmd

    def test_full_spec_cmd(self, full_spec: AgentSpecData) -> None:
        cmd = _build_pi_cmd(full_spec)
        assert "--verbose" in cmd
        assert "--append-system-prompt" in cmd
        assert "Helpful reviewer" in cmd
        assert "--output-format" in cmd
        assert "text" in cmd


class TestClaudeCLIStop:
    @pytest.mark.asyncio
    async def test_stop_unknown_node_is_noop(self) -> None:
        backend = ClaudeCLIBackend()
        await backend.stop("nonexistent")  # should not raise

    @pytest.mark.asyncio
    async def test_stop_terminates_process(self) -> None:
        backend = ClaudeCLIBackend()
        mock_proc = MagicMock()
        mock_proc.returncode = None
        backend._processes["node-1"] = mock_proc
        await backend.stop("node-1")
        mock_proc.terminate.assert_called_once()


class TestPiStop:
    @pytest.mark.asyncio
    async def test_stop_unknown_node_is_noop(self) -> None:
        backend = PiBackend()
        await backend.stop("nonexistent")

    @pytest.mark.asyncio
    async def test_stop_terminates_process(self) -> None:
        backend = PiBackend()
        mock_proc = MagicMock()
        mock_proc.returncode = None
        backend._processes["node-1"] = mock_proc
        await backend.stop("node-1")
        mock_proc.terminate.assert_called_once()


# ===========================================================================
# _write_hook_settings
# ===========================================================================


class TestHookSettingsJsonFormat:
    def test_produces_valid_tool_use_hook_json(self) -> None:
        result = _hook_settings_json(4567)
        settings = json.loads(result)
        hooks = settings["hooks"]["PreToolUse"]
        assert len(hooks) == 1
        http_hook = hooks[0]["hooks"][0]
        assert http_hook["type"] == "http"
        assert "4567" in http_hook["url"]
        assert http_hook["timeout"] == 3600

    def test_url_points_to_localhost(self) -> None:
        result = _hook_settings_json(1234)
        settings = json.loads(result)
        url = settings["hooks"]["PreToolUse"][0]["hooks"][0]["url"]
        assert "127.0.0.1:1234" in url
        assert "/hook" in url


# ===========================================================================
# _stream_parser helpers
# ===========================================================================


class TestTryParseJson:
    def test_valid_json(self) -> None:
        assert try_parse_json('{"type": "result"}') == {"type": "result"}

    def test_invalid_json(self) -> None:
        assert try_parse_json("not json") is None

    def test_non_dict_json(self) -> None:
        assert try_parse_json("[1, 2]") is None

    def test_empty_string(self) -> None:
        assert try_parse_json("") is None


class TestInputPreview:
    def test_dict_input(self) -> None:
        result = input_preview(cast(Mapping[str, object], {"command": "ls -la"}))
        assert "command" in result
        assert "ls -la" in result

    def test_preserves_full_content(self) -> None:
        big: Mapping[str, object] = {"data": "x" * 500}
        result = input_preview(big)
        assert "x" * 500 in result


class TestGetContentBlocks:
    def test_valid_message(self) -> None:
        msg: dict[str, object] = {
            "message": {"content": [{"type": "text", "text": "hi"}]}
        }
        blocks = get_content_blocks(msg)
        assert len(blocks) == 1
        assert blocks[0]["text"] == "hi"

    def test_no_message(self) -> None:
        assert get_content_blocks({}) == []

    def test_non_dict_message(self) -> None:
        assert get_content_blocks({"message": "string"}) == []

    def test_non_list_content(self) -> None:
        assert get_content_blocks({"message": {"content": "string"}}) == []


class TestParseUsage:
    def test_full_usage(self) -> None:
        raw = {
            "input_tokens": 100,
            "output_tokens": 200,
            "cache_read_input_tokens": 50,
            "cache_creation_input_tokens": 25,
        }
        usage = parse_usage(cast(dict[str, object], raw))
        assert usage.input_tokens == 100
        assert usage.output_tokens == 200
        assert usage.cache_read_tokens == 50
        assert usage.cache_write_tokens == 25

    def test_partial_usage(self) -> None:
        usage = parse_usage(cast(dict[str, object], {"input_tokens": 10}))
        assert usage.input_tokens == 10
        assert usage.output_tokens == 0

    def test_empty_usage(self) -> None:
        usage = parse_usage({})
        assert usage == TokenUsage()


class TestParseCostUSD:
    def test_parses_total_cost(self) -> None:
        assert (
            parse_cost_usd(cast(dict[str, object], {"total_cost_usd": 0.123})) == 0.123
        )

    def test_missing_cost(self) -> None:
        assert parse_cost_usd({}) is None


class TestBlockState:
    def test_reset_clears_all(self) -> None:
        b = BlockState()
        b.block_type = "tool_use"
        b.tool_name = "Bash"
        b.tool_id = "abc"
        b.input_json = '{"x": 1}'
        b.text_buffer = "text"
        b.tool_start_time = 1.0
        b.reset()
        assert b.block_type is None
        assert b.tool_name is None
        assert b.tool_id is None
        assert b.input_json == ""
        assert b.text_buffer == ""
        assert b.tool_start_time is None


class TestMessageHandlers:
    @pytest.mark.asyncio
    async def test_tool_and_text_translation(self, bus: EventBus) -> None:
        stream = StreamAccumulator()
        block = BlockState()
        seen: list[Event] = []

        async def track(event: Event) -> None:
            seen.append(event)

        bus.subscribe(track)

        await handle_assistant(
            {
                "message": {
                    "content": [
                        {"type": "text", "text": "hello"},
                        {
                            "type": "tool_use",
                            "id": "tool-1",
                            "name": "Bash",
                            "input": {"cmd": "ls"},
                        },
                    ],
                },
            },
            stream,
            block,
            "node-1",
            bus,
        )
        await handle_user(
            {
                "message": {
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "tool-1",
                            "content": "ok",
                        }
                    ]
                }
            },
            stream,
            block,
            "node-1",
            bus,
        )

        assert [event.type.value for event in seen] == [
            "agent.text",
            "tool.call.start",
            "tool.call.end",
        ]
        assert seen[1].data is not None and seen[2].data is not None

    @pytest.mark.asyncio
    async def test_streaming_tool_blocks_translate(self, bus: EventBus) -> None:
        stream = StreamAccumulator()
        block = BlockState()
        seen: list[Event] = []

        async def track(event: Event) -> None:
            seen.append(event)

        bus.subscribe(track)
        await handle_block_start(
            {"content_block": {"type": "tool_use", "id": "tool-2", "name": "Read"}},
            stream,
            block,
            "node-1",
            bus,
        )
        await handle_block_delta(
            {"delta": {"type": "input_json_delta", "partial_json": '{"path":"x"}'}},
            stream,
            block,
            "node-1",
            bus,
        )
        await handle_block_stop({}, stream, block, "node-1", bus)
        await handle_tool_result(
            {"tool_use_id": "tool-2", "content": "file"},
            stream,
            block,
            "node-1",
            bus,
        )
        assert [event.type.value for event in seen] == [
            "tool.call.start",
            "tool.call.end",
        ]


class TestStreamAccumulator:
    def test_defaults(self) -> None:
        acc = StreamAccumulator()
        assert acc.final_output == ""
        assert acc.usage == TokenUsage()
        assert acc.cost_usd is None
        assert acc.backend_session_id is None
        assert acc.call_seq == 0
        assert acc.pending_tools == {}
        assert acc.reasoning == []
