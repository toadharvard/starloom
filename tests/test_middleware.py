"""Tests for middleware layer: protocol, MiddlewareChain, replay."""

from __future__ import annotations

import pytest

from starloom.graph_pkg import TraceGraph
from starloom.middleware.protocol import (
    Accept,
    AfterAction,
    BeforeAction,
    Cancel,
    EditResult,
    EditSpec,
    MiddlewareChain,
    Run,
    Skip,
)
from starloom.middleware.replay import (
    CacheEntry,
    ExecutionLog,
    ReplayMiddleware,
    _is_independent_sibling,
)
from starloom.types import AgentSpecData, NodePatch


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def spec() -> AgentSpecData:
    return AgentSpecData(
        prompt="Test Agent\nDo something",
        flags="--model haiku",
    )


@pytest.fixture
def alt_spec() -> AgentSpecData:
    return AgentSpecData(
        prompt="Alt Agent\nDo other thing",
        flags="--model sonnet",
    )


# ---------------------------------------------------------------------------
# Concrete middleware for testing
# ---------------------------------------------------------------------------


class PassthroughMiddleware:
    """Always runs, always accepts."""

    def before_call(self, spec: AgentSpecData) -> BeforeAction:
        return Run()

    def after_call(self, spec: AgentSpecData, result: str) -> AfterAction:
        return Accept()


class SkipMiddleware:
    """Always skips with a cached result."""

    def __init__(self, cached: str = "cached") -> None:
        self._cached = cached

    def before_call(self, spec: AgentSpecData) -> BeforeAction:
        return Skip(default_result=self._cached)

    def after_call(self, spec: AgentSpecData, result: str) -> AfterAction:
        return Accept()


class CancelMiddleware:
    """Always cancels."""

    def __init__(self, reason: str = "nope") -> None:
        self._reason = reason

    def before_call(self, spec: AgentSpecData) -> BeforeAction:
        return Cancel(reason=self._reason)

    def after_call(self, spec: AgentSpecData, result: str) -> AfterAction:
        return Accept()


class EditSpecMiddleware:
    """Edits the spec by changing raw backend flags."""

    def __init__(self, new_model: str = "opus") -> None:
        self._new_model = new_model

    def before_call(self, spec: AgentSpecData) -> BeforeAction:
        new = AgentSpecData(
            prompt=spec.prompt,
            flags=f"--model {self._new_model}",
        )
        return EditSpec(new_spec=new)

    def after_call(self, spec: AgentSpecData, result: str) -> AfterAction:
        return Accept()


class EditResultMiddleware:
    """Edits the result by appending a suffix."""

    def __init__(self, suffix: str = " [edited]") -> None:
        self._suffix = suffix

    def before_call(self, spec: AgentSpecData) -> BeforeAction:
        return Run()

    def after_call(self, spec: AgentSpecData, result: str) -> AfterAction:
        return EditResult(new_result=result + self._suffix)


class RecordingMiddleware:
    """Records specs seen in before_call for assertions."""

    def __init__(self) -> None:
        self.seen_specs: list[AgentSpecData] = []
        self.seen_results: list[str] = []

    def before_call(self, spec: AgentSpecData) -> BeforeAction:
        self.seen_specs.append(spec)
        return Run()

    def after_call(self, spec: AgentSpecData, result: str) -> AfterAction:
        self.seen_results.append(result)
        return Accept()


# ===========================================================================
# Protocol action types
# ===========================================================================


class TestActions:
    def test_run(self) -> None:
        r = Run()
        assert isinstance(r, BeforeAction)

    def test_skip(self) -> None:
        s = Skip(default_result="cached")
        assert s.default_result == "cached"
        assert isinstance(s, BeforeAction)

    def test_cancel(self) -> None:
        c = Cancel(reason="bad")
        assert c.reason == "bad"

    def test_edit_spec(self, spec: AgentSpecData) -> None:
        e = EditSpec(new_spec=spec)
        assert e.new_spec is spec

    def test_accept(self) -> None:
        assert isinstance(Accept(), AfterAction)

    def test_edit_result(self) -> None:
        e = EditResult(new_result="new")
        assert e.new_result == "new"


# ===========================================================================
# MiddlewareChain
# ===========================================================================


class TestMiddlewareChain:
    def test_empty_chain_runs(self, spec: AgentSpecData) -> None:
        chain = MiddlewareChain()
        action, final = chain.before_call(spec)
        assert isinstance(action, Run)
        assert final is spec

    def test_empty_chain_accepts(self, spec: AgentSpecData) -> None:
        chain = MiddlewareChain()
        action, result = chain.after_call(spec, "ok")
        assert isinstance(action, Accept)
        assert result == "ok"

    def test_passthrough(self, spec: AgentSpecData) -> None:
        chain = MiddlewareChain([PassthroughMiddleware()])
        action, final = chain.before_call(spec)
        assert isinstance(action, Run)
        assert final is spec

    def test_skip_short_circuits(self, spec: AgentSpecData) -> None:
        recorder = RecordingMiddleware()
        chain = MiddlewareChain([SkipMiddleware("cached"), recorder])
        action, _ = chain.before_call(spec)
        assert isinstance(action, Skip)
        assert action.default_result == "cached"
        assert len(recorder.seen_specs) == 0  # recorder never reached

    def test_cancel_short_circuits(self, spec: AgentSpecData) -> None:
        recorder = RecordingMiddleware()
        chain = MiddlewareChain([CancelMiddleware("stop"), recorder])
        action, _ = chain.before_call(spec)
        assert isinstance(action, Cancel)
        assert action.reason == "stop"
        assert len(recorder.seen_specs) == 0

    def test_edit_spec_passes_updated_to_next(self, spec: AgentSpecData) -> None:
        recorder = RecordingMiddleware()
        chain = MiddlewareChain([EditSpecMiddleware("opus"), recorder])
        action, final = chain.before_call(spec)
        assert isinstance(action, Run)
        assert final.flags == "--model opus"
        assert recorder.seen_specs[0].flags == "--model opus"

    def test_chained_edits(self, spec: AgentSpecData) -> None:
        chain = MiddlewareChain(
            [
                EditSpecMiddleware("sonnet"),
                EditSpecMiddleware("opus"),
            ]
        )
        _, final = chain.before_call(spec)
        assert final.flags == "--model opus"

    def test_after_call_edit_result(self, spec: AgentSpecData) -> None:
        chain = MiddlewareChain(
            [EditResultMiddleware(" [a]"), EditResultMiddleware(" [b]")]
        )
        _, result = chain.after_call(spec, "orig")
        assert result == "orig [a] [b]"

    def test_after_call_records_edited_result(self, spec: AgentSpecData) -> None:
        recorder = RecordingMiddleware()
        chain = MiddlewareChain([EditResultMiddleware(" [x]"), recorder])
        _, result = chain.after_call(spec, "base")
        assert result == "base [x]"
        assert recorder.seen_results == ["base [x]"]

    def test_add_method(self, spec: AgentSpecData) -> None:
        chain = MiddlewareChain()
        chain.add(SkipMiddleware("added"))
        action, _ = chain.before_call(spec)
        assert isinstance(action, Skip)

    def test_layers_property(self) -> None:
        a = PassthroughMiddleware()
        b = SkipMiddleware()
        chain = MiddlewareChain([a, b])
        layers = chain.layers
        assert len(layers) == 2
        # Returns a copy
        layers.append(PassthroughMiddleware())
        assert len(chain.layers) == 2


# ===========================================================================
# ExecutionLog
# ===========================================================================


def _make_cache_entry(
    seq: int,
    result: str = "result",
    parallel_group: str | None = None,
    valid: bool = True,
) -> CacheEntry:
    return CacheEntry(
        seq=seq,
        prompt=f"prompt-{seq}",
        flags="--model haiku",
        result=result,
        parallel_group=parallel_group,
        valid=valid,
    )


class TestExecutionLog:
    def test_get_cached_valid(self) -> None:
        log = ExecutionLog([_make_cache_entry(1, "r1")])
        entry = log.get_cached(1)
        assert entry is not None
        assert entry.result == "r1"

    def test_get_cached_missing(self) -> None:
        log = ExecutionLog([_make_cache_entry(1)])
        assert log.get_cached(99) is None

    def test_get_cached_invalid(self) -> None:
        log = ExecutionLog([_make_cache_entry(1, valid=False)])
        assert log.get_cached(1) is None

    def test_invalidate_downstream(self) -> None:
        entries = [_make_cache_entry(i) for i in range(1, 5)]
        log = ExecutionLog(entries)
        log.invalidate_from_seq(2)
        assert log.get_cached(1) is not None  # upstream preserved
        assert log.get_cached(2) is None  # invalidated
        assert log.get_cached(3) is None  # downstream invalidated
        assert log.get_cached(4) is None  # downstream invalidated

    def test_invalidate_preserves_parallel_siblings(self) -> None:
        entries = [
            _make_cache_entry(1, parallel_group="pg1"),
            _make_cache_entry(2, parallel_group="pg1"),
            _make_cache_entry(3, parallel_group="pg1"),
            _make_cache_entry(4),  # sequential after parallel
        ]
        log = ExecutionLog(entries)
        log.invalidate_from_seq(1)
        # Siblings in same group are independent
        assert log.get_cached(2) is not None
        assert log.get_cached(3) is not None
        # Sequential node after parallel is invalidated
        assert log.get_cached(4) is None

    def test_invalidate_sequential_preserves_parallel(self) -> None:
        """Sequential node edit doesn't invalidate parallel nodes."""
        entries = [
            _make_cache_entry(1),  # sequential
            _make_cache_entry(2, parallel_group="pg1"),
            _make_cache_entry(3, parallel_group="pg1"),
        ]
        log = ExecutionLog(entries)
        log.invalidate_from_seq(1)
        # Parallel nodes after sequential are independent
        assert log.get_cached(2) is not None
        assert log.get_cached(3) is not None

    def test_patch_node(self) -> None:
        entries = [_make_cache_entry(1), _make_cache_entry(2)]
        log = ExecutionLog(entries)
        patch = NodePatch(flags="--model opus")
        log.patch_node(1, patch)
        assert log.get_patch(1) is patch
        assert log.get_cached(1) is None  # invalidated
        assert log.get_cached(2) is None  # downstream invalidated

    def test_patch_prompt(self) -> None:
        entries = [_make_cache_entry(1)]
        log = ExecutionLog(entries)
        log.patch_prompt(1, "new prompt")
        patch = log.get_patch(1)
        assert patch is not None
        assert patch.prompt == "new prompt"

    def test_entries_returns_copy(self) -> None:
        entries = [_make_cache_entry(1)]
        log = ExecutionLog(entries)
        copy = log.entries
        copy.append(_make_cache_entry(99))
        assert len(log.entries) == 1

    def test_from_graph(self) -> None:
        graph = TraceGraph()
        spec = AgentSpecData(prompt="Test\nAgent", flags="--model haiku")
        graph.add_node("n1", spec)
        graph.start_node("n1")
        graph.finish_node("n1", "result-1", 0.01, 100, 50)
        graph.add_node("n2", spec, parallel_group="pg")
        graph.start_node("n2")
        graph.finish_node("n2", "result-2", 0.02, 200, 100)

        log = ExecutionLog.from_graph(graph)
        assert len(log.entries) == 2
        assert log.entries[0].result == "result-1"
        assert log.entries[1].parallel_group == "pg"


# ===========================================================================
# _is_independent_sibling
# ===========================================================================


class TestIsIndependentSibling:
    def test_sequential_entry(self) -> None:
        e = _make_cache_entry(1, parallel_group=None)
        assert not _is_independent_sibling(e, None)

    def test_parallel_entry_sequential_edit(self) -> None:
        e = _make_cache_entry(1, parallel_group="pg1")
        assert _is_independent_sibling(e, None)

    def test_same_group(self) -> None:
        e = _make_cache_entry(1, parallel_group="pg1")
        assert _is_independent_sibling(e, "pg1")

    def test_different_group(self) -> None:
        e = _make_cache_entry(1, parallel_group="pg2")
        assert not _is_independent_sibling(e, "pg1")


# ===========================================================================
# ReplayMiddleware
# ===========================================================================


class TestReplayMiddleware:
    def test_cached_entry_returns_skip(self) -> None:
        entries = [_make_cache_entry(1, "cached-result")]
        log = ExecutionLog(entries)
        mw = ReplayMiddleware(log)
        spec = AgentSpecData(prompt="test", flags="--model haiku")
        action = mw.before_call(spec)
        assert isinstance(action, Skip)
        assert action.default_result == "cached-result"

    def test_invalid_entry_returns_run(self) -> None:
        entries = [_make_cache_entry(1, valid=False)]
        log = ExecutionLog(entries)
        mw = ReplayMiddleware(log)
        spec = AgentSpecData(prompt="test", flags="--model haiku")
        action = mw.before_call(spec)
        assert isinstance(action, Run)

    def test_missing_entry_returns_run(self) -> None:
        log = ExecutionLog([])
        mw = ReplayMiddleware(log)
        spec = AgentSpecData(prompt="test", flags="--model haiku")
        action = mw.before_call(spec)
        assert isinstance(action, Run)

    def test_patched_entry_returns_edit_spec(self) -> None:
        entries = [_make_cache_entry(1)]
        log = ExecutionLog(entries)
        patch = NodePatch(flags="--model opus")
        log.patch_node(1, patch)
        mw = ReplayMiddleware(log)
        spec = AgentSpecData(prompt="test", flags="--model haiku")
        action = mw.before_call(spec)
        assert isinstance(action, EditSpec)
        assert action.new_spec.flags == "--model opus"

    def test_call_seq_increments(self) -> None:
        entries = [_make_cache_entry(1), _make_cache_entry(2)]
        mw = ReplayMiddleware(ExecutionLog(entries))
        spec = AgentSpecData(prompt="test", flags="--model haiku")
        mw.before_call(spec)
        assert mw.call_seq == 1
        mw.before_call(spec)
        assert mw.call_seq == 2

    def test_after_call_accepts(self) -> None:
        mw = ReplayMiddleware(ExecutionLog([]))
        spec = AgentSpecData(prompt="test", flags="--model haiku")
        action = mw.after_call(spec, "result")
        assert isinstance(action, Accept)

    def test_from_graph(self) -> None:
        graph = TraceGraph()
        spec = AgentSpecData(prompt="Test\nAgent", flags="--model haiku")
        graph.add_node("n1", spec)
        graph.start_node("n1")
        graph.finish_node("n1", "r1", 0.01, 100, 50)

        mw = ReplayMiddleware.from_graph(graph)
        action = mw.before_call(spec)
        assert isinstance(action, Skip)
        assert action.default_result == "r1"

    def test_from_session_uses_graph_local_patches(self) -> None:
        graph = TraceGraph()
        spec = AgentSpecData(prompt="Test", flags="--model haiku")
        graph.add_node("n1", spec)
        graph.finish_node("n1", "r1", 0.01, 100, 50)
        graph.patch_node("n1", NodePatch(flags="--model opus"))

        class SessionStub:
            def load_graph(self) -> TraceGraph:
                return graph

        mw = ReplayMiddleware.from_session(SessionStub())
        action = mw.before_call(spec)
        assert isinstance(action, EditSpec)
        assert action.new_spec.flags == "--model opus"

    def test_multi_call_sequence(self) -> None:
        """Simulate a 3-node workflow replay with one patched node."""
        entries = [
            _make_cache_entry(1, "r1"),
            _make_cache_entry(2, "r2"),
            _make_cache_entry(3, "r3"),
        ]
        log = ExecutionLog(entries)
        log.patch_node(2, NodePatch(flags="--model opus"))
        mw = ReplayMiddleware(log)
        spec = AgentSpecData(prompt="test", flags="--model haiku")

        # Call 1: cached
        a1 = mw.before_call(spec)
        assert isinstance(a1, Skip)
        assert a1.default_result == "r1"

        # Call 2: patched -> EditSpec
        a2 = mw.before_call(spec)
        assert isinstance(a2, EditSpec)
        assert a2.new_spec.flags == "--model opus"

        # Call 3: invalidated downstream -> Run
        a3 = mw.before_call(spec)
        assert isinstance(a3, Run)

    def test_log_property(self) -> None:
        log = ExecutionLog([])
        mw = ReplayMiddleware(log)
        assert mw.log is log
