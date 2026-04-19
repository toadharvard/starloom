"""Replay middleware — caches results and supports selective re-execution.

On session resume, an ExecutionLog is built from the prior run's TraceGraph.
Nodes whose specs haven't changed are served from cache; patched or
invalidated nodes are re-executed via the normal agent backend.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from starloom.graph_pkg import TraceGraph, TraceNode
from starloom.middleware.protocol import (
    Accept,
    AfterAction,
    BeforeAction,
    EditSpec,
    Run,
    Skip,
)
from starloom.types import AgentSpecData, NodePatch

if TYPE_CHECKING:
    from typing import Protocol

    class SessionLike(Protocol):
        """Minimal interface for building replay from a saved session."""

        def load_graph(self) -> TraceGraph: ...


# ---------------------------------------------------------------------------
# CacheEntry
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class CacheEntry:
    """Cached result from a previous execution."""

    seq: int
    prompt: str
    flags: str
    result: str
    parallel_group: str | None
    cost_usd: float | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    valid: bool = True


# ---------------------------------------------------------------------------
# ExecutionLog
# ---------------------------------------------------------------------------


class ExecutionLog:
    """Ordered cache of all calls from a completed workflow run.

    On replay a NEW TraceGraph and NEW EventBus are created.  The old
    session's graph is loaded read-only to build this log, which
    determines which nodes to re-execute vs serve from cache.
    """

    def __init__(self, entries: list[CacheEntry] | None = None) -> None:
        self._entries: list[CacheEntry] = entries or []
        self._patches: dict[int, NodePatch] = {}

    # -- Construction -------------------------------------------------------

    @classmethod
    def from_graph(cls, graph: TraceGraph) -> ExecutionLog:
        """Build a log from every node in *graph*."""
        log = cls([_entry_from_node(node) for node in graph.nodes])
        for node in graph.nodes:
            for patch in node.patches:
                log.patch_node(node.seq, patch)
        return log

    # -- Invalidation -------------------------------------------------------

    def invalidate_from_seq(self, edited_seq: int) -> None:
        """Invalidate a node and all downstream dependents.

        Invariant: after this call, ``entry.valid is False`` for the
        edited node and every entry with ``seq > edited_seq`` that is
        NOT an independent sibling (same parallel_group or sequential
        node when the edited node is parallel). Entries with
        ``seq < edited_seq`` are never touched.
        """
        edited_entry = self._find_entry(edited_seq)
        if edited_entry is None:
            return
        edited_group = edited_entry.parallel_group
        for entry in self._entries:
            if entry.seq == edited_seq:
                entry.valid = False
            elif entry.seq > edited_seq:
                if _is_independent_sibling(entry, edited_group):
                    continue
                entry.valid = False

    # -- Patching -----------------------------------------------------------

    def patch_node(self, seq: int, patch: NodePatch) -> None:
        """Apply *patch* to node at *seq* and invalidate downstream."""
        self._patches[seq] = patch
        self.invalidate_from_seq(seq)

    def patch_prompt(self, seq: int, new_prompt: str) -> None:
        """Convenience: patch only the prompt."""
        existing = self._patches.get(seq, NodePatch())
        merged = NodePatch(
            prompt=new_prompt,
            flags=existing.flags,
        )
        self._patches[seq] = merged
        self.invalidate_from_seq(seq)

    # -- Lookup -------------------------------------------------------------

    def get_cached(self, call_seq: int) -> CacheEntry | None:
        """Return the cache entry if valid, None if invalidated or missing."""
        entry = self._find_entry(call_seq)
        if entry is not None and entry.valid:
            return entry
        return None

    def get_patch(self, call_seq: int) -> NodePatch | None:
        """Return the patch for *call_seq*, if any."""
        return self._patches.get(call_seq)

    @property
    def entries(self) -> list[CacheEntry]:
        """Snapshot of all entries (defensive copy)."""
        return list(self._entries)

    # -- Internal -----------------------------------------------------------

    def _find_entry(self, seq: int) -> CacheEntry | None:
        return next((e for e in self._entries if e.seq == seq), None)


def _entry_from_node(node: TraceNode) -> CacheEntry:
    """Build a CacheEntry from a TraceNode."""
    spec = node.effective_spec
    return CacheEntry(
        seq=node.seq,
        prompt=spec.prompt,
        flags=spec.flags,
        result=node.result or "",
        parallel_group=node.parallel_group,
        cost_usd=node.cost_usd,
        input_tokens=node.input_tokens,
        output_tokens=node.output_tokens,
    )


def _is_independent_sibling(entry: CacheEntry, edited_group: str | None) -> bool:
    """True if *entry* is a parallel sibling that should NOT be invalidated."""
    if entry.parallel_group is None:
        return False
    # Sequential edited node: parallel nodes are independent branches.
    if edited_group is None:
        return True
    # Same parallel group: siblings are independent.
    return entry.parallel_group == edited_group


# ---------------------------------------------------------------------------
# ReplayMiddleware
# ---------------------------------------------------------------------------


class ReplayMiddleware:
    """Uses an ExecutionLog to cache valid calls and re-run invalidated ones.

    When a cached entry is used the cached cost is preserved so the
    session's total cost reflects what was actually spent (cached nodes
    cost $0 for the replay but we record their original cost for
    accurate session accounting).
    """

    def __init__(self, log: ExecutionLog) -> None:
        self._log = log
        self._call_seq = 0

    @classmethod
    def from_graph(cls, graph: TraceGraph) -> ReplayMiddleware:
        """Build directly from a TraceGraph (no patches)."""
        return cls(ExecutionLog.from_graph(graph))

    @classmethod
    def from_session(cls, session: SessionLike) -> ReplayMiddleware:
        """Build from a saved Session using graph-local patch state only."""
        graph = session.load_graph()
        return cls(ExecutionLog.from_graph(graph))

    @property
    def call_seq(self) -> int:
        """Current call sequence counter."""
        return self._call_seq

    @property
    def log(self) -> ExecutionLog:
        """The underlying execution log."""
        return self._log

    def before_call(self, spec: AgentSpecData) -> BeforeAction:
        """Decide whether to run, skip, or edit the spec for this call."""
        self._call_seq += 1
        seq = self._call_seq

        patch = self._log.get_patch(seq)
        if patch is not None:
            return EditSpec(new_spec=patch.apply(spec))

        cached = self._log.get_cached(seq)
        if cached is not None:
            return Skip(default_result=cached.result)

        return Run()

    def after_call(self, spec: AgentSpecData, result: str) -> AfterAction:
        """Post-execution hook (always accepts for replay)."""
        return Accept()
