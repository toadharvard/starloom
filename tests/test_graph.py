"""Tests for the trace graph — TraceGraph and TraceNode."""

from __future__ import annotations

import json

from starloom.graph_pkg import TraceGraph
from starloom.types import AgentSpecData, NodePatch, NodeStatus


def _spec(prompt: str = "test", flags: str = "--model haiku") -> AgentSpecData:
    return AgentSpecData(prompt=prompt, flags=flags)


class TestTraceNode:
    def test_prompt_preview(self) -> None:
        g = TraceGraph()
        node = g.add_node("n1", spec=_spec("Agent Name\nDo stuff"))
        assert node.prompt_preview == "Agent Name"

    def test_duration_none_when_not_started(self) -> None:
        g = TraceGraph()
        node = g.add_node("n1", spec=_spec())
        assert node.duration is None

    def test_effective_spec_no_patches(self) -> None:
        spec = _spec("hello", "--model sonnet")
        g = TraceGraph()
        node = g.add_node("n1", spec=spec)
        assert node.effective_spec is spec

    def test_effective_spec_with_patches(self) -> None:
        spec = _spec("hello", "--model haiku")
        g = TraceGraph()
        node = g.add_node("n1", spec=spec)
        node.patches.append(NodePatch(flags="--model opus"))
        eff = node.effective_spec
        assert eff.flags == "--model opus"
        assert eff.prompt == "hello"


class TestTraceGraph:
    def test_add_node(self) -> None:
        g = TraceGraph()
        node = g.add_node("n1", spec=_spec(), backend_name="claude")
        assert node.id == "n1"
        assert node.seq == 1
        assert node.status == NodeStatus.PENDING
        assert node.backend_name == "claude"

    def test_sequential_seq(self) -> None:
        g = TraceGraph()
        n1 = g.add_node("a", spec=_spec())
        n2 = g.add_node("b", spec=_spec())
        assert n1.seq == 1
        assert n2.seq == 2

    def test_nodes_property_order(self) -> None:
        g = TraceGraph()
        g.add_node("a", spec=_spec())
        g.add_node("b", spec=_spec())
        g.add_node("c", spec=_spec())
        ids = [n.id for n in g.nodes]
        assert ids == ["a", "b", "c"]

    def test_get_node(self) -> None:
        g = TraceGraph()
        g.add_node("n1", spec=_spec())
        assert g.get_node("n1") is not None
        assert g.get_node("missing") is None

    def test_start_node(self) -> None:
        g = TraceGraph()
        g.add_node("n1", spec=_spec())
        g.start_node("n1")
        node = g.get_node("n1")
        assert node is not None
        assert node.status == NodeStatus.RUNNING
        assert node.start_time is not None

    def test_finish_node(self) -> None:
        g = TraceGraph()
        g.add_node("n1", spec=_spec())
        g.start_node("n1")
        g.finish_node(
            "n1", result="ok", cost_usd=0.01, input_tokens=100, output_tokens=50
        )
        node = g.get_node("n1")
        assert node is not None
        assert node.status == NodeStatus.COMPLETED
        assert node.result == "ok"
        assert node.cost_usd == 0.01

    def test_fail_node(self) -> None:
        g = TraceGraph()
        g.add_node("n1", spec=_spec())
        g.fail_node("n1", error="boom")
        node = g.get_node("n1")
        assert node is not None
        assert node.status == NodeStatus.ERROR
        assert node.error == "boom"

    def test_skip_node(self) -> None:
        g = TraceGraph()
        g.add_node("n1", spec=_spec())
        g.skip_node("n1")
        assert g.get_node("n1").status == NodeStatus.SKIPPED  # type: ignore[union-attr]

    def test_stop_node(self) -> None:
        g = TraceGraph()
        g.add_node("n1", spec=_spec())
        g.stop_node("n1")
        assert g.get_node("n1").status == NodeStatus.STOPPED  # type: ignore[union-attr]

    def test_cache_node(self) -> None:
        g = TraceGraph()
        g.add_node("n1", spec=_spec())
        g.cache_node("n1", result="cached")
        node = g.get_node("n1")
        assert node is not None
        assert node.status == NodeStatus.CACHED
        assert node.result == "cached"

    def test_parent_id(self) -> None:
        g = TraceGraph()
        g.add_node("parent", spec=_spec())
        g.add_node("child", spec=_spec(), parent_id="parent")
        child = g.get_node("child")
        assert child is not None
        assert child.parent_id == "parent"

    def test_parallel_group(self) -> None:
        g = TraceGraph()
        g.add_node("n1", spec=_spec(), parallel_group="pg1")
        assert g.get_node("n1").parallel_group == "pg1"  # type: ignore[union-attr]

    def test_total_cost(self) -> None:
        g = TraceGraph()
        g.add_node("n1", spec=_spec())
        g.add_node("n2", spec=_spec())
        g.finish_node("n1", "ok", cost_usd=0.01, input_tokens=100, output_tokens=50)
        g.finish_node("n2", "ok", cost_usd=0.02, input_tokens=200, output_tokens=100)
        total = g.total_cost_usd
        assert total is not None
        assert abs(total - 0.03) < 1e-9

    def test_patch_resets_node_and_dependents(self) -> None:
        g = TraceGraph()
        g.add_node("parent", spec=_spec())
        g.add_node("child", spec=_spec(), parent_id="parent")
        g.finish_node(
            "parent", "done", cost_usd=0.01, input_tokens=100, output_tokens=50
        )
        g.finish_node(
            "child", "done", cost_usd=0.01, input_tokens=100, output_tokens=50
        )
        g.patch_node("parent", NodePatch(flags="--model opus"))
        parent = g.get_node("parent")
        child = g.get_node("child")
        assert parent is not None
        assert parent.status == NodeStatus.PENDING
        assert parent.result is None
        assert child is not None
        assert child.status == NodeStatus.PENDING

    def test_deserialization_defaults_backend_name_to_none(self) -> None:
        restored = TraceGraph.from_dict(
            {
                "nodes": [
                    {
                        "id": "n1",
                        "seq": 1,
                        "parent_id": None,
                        "kind": "agent",
                        "payload": {
                            "type": "agent",
                            "spec": {"prompt": "p", "flags": ""},
                        },
                        "status": "pending",
                        "result": None,
                        "error": None,
                        "start_time": None,
                        "end_time": None,
                        "parallel_group": None,
                        "cost_usd": None,
                        "input_tokens": None,
                        "output_tokens": None,
                        "patches": [],
                    }
                ],
                "seq": 1,
            }
        )
        assert restored.get_node("n1").backend_name is None  # type: ignore[union-attr]

    def test_serialization_roundtrip(self) -> None:
        g = TraceGraph()
        g.add_node(
            "n1",
            spec=_spec("prompt1", "--model haiku"),
            parallel_group="pg",
            backend_name="pi",
        )
        g.start_node("n1")
        g.finish_node(
            "n1", "result1", cost_usd=0.01, input_tokens=100, output_tokens=50
        )
        d = g.to_dict()
        json_str = json.dumps(d)
        restored = TraceGraph.from_dict(json.loads(json_str))
        assert len(restored.nodes) == 1
        node = restored.get_node("n1")
        assert node is not None
        assert node.spec.prompt == "prompt1"
        assert node.status == NodeStatus.COMPLETED
        assert node.parallel_group == "pg"
        assert node.backend_name == "pi"

    def test_from_nodes_rebuilds_publicly(self) -> None:
        g = TraceGraph()
        first = g.add_node("n1", spec=_spec("prompt1"), backend_name="claude")
        second = g.add_node("n2", spec=_spec("prompt2"))
        rebuilt = TraceGraph.from_nodes([first, second], seq=9)
        assert [n.id for n in rebuilt.nodes] == ["n1", "n2"]
        assert rebuilt.get_node("n2") is second
        assert rebuilt.to_dict()["seq"] == 9
        assert rebuilt.get_node("n1").backend_name == "claude"  # type: ignore[union-attr]
