"""Graph serialization — to_dict / from_dict for TraceNode and TraceGraph."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, cast

from starloom.graph_pkg.node import (
    AgentNodePayload,
    CheckpointNodePayload,
    NODE_KIND_AGENT,
    NODE_KIND_CHECKPOINT,
    NodeKind,
    TraceNode,
)
from starloom.serialization import (
    agent_spec_from_dict,
    agent_spec_to_dict,
    node_patch_from_dict,
    node_patch_to_dict,
)
from starloom.types import AgentSpecData, NodePatch, NodeStatus

if TYPE_CHECKING:
    from starloom.graph_pkg.trace_graph import TraceGraph


def spec_to_dict(spec: AgentSpecData) -> dict[str, object]:
    return agent_spec_to_dict(spec)


def patch_to_dict(patch: NodePatch) -> dict[str, object]:
    return node_patch_to_dict(patch)


def payload_to_dict(node: TraceNode) -> dict[str, object]:
    if isinstance(node.payload, AgentNodePayload):
        return {
            "type": NODE_KIND_AGENT,
            "spec": spec_to_dict(node.payload.spec),
        }
    return {
        "type": NODE_KIND_CHECKPOINT,
        "question": node.payload.question,
    }


def node_to_dict(node: TraceNode) -> dict[str, object]:
    return {
        "id": node.id,
        "seq": node.seq,
        "parent_id": node.parent_id,
        "kind": node.kind,
        "payload": payload_to_dict(node),
        "status": node.status.value,
        "result": node.result,
        "error": node.error,
        "start_time": node.start_time,
        "end_time": node.end_time,
        "parallel_group": node.parallel_group,
        "backend_name": node.backend_name,
        "cost_usd": node.cost_usd,
        "input_tokens": node.input_tokens,
        "output_tokens": node.output_tokens,
        "patches": [patch_to_dict(p) for p in node.patches],
    }


def _opt_str(v: object) -> str | None:
    return str(v) if v is not None else None


def _opt_int(v: object) -> int | None:
    return int(str(v)) if v is not None else None


def _opt_float(v: object) -> float | None:
    return float(str(v)) if v is not None else None


def spec_from_dict(d: dict[str, object]) -> AgentSpecData:
    return agent_spec_from_dict(d)


def patch_from_dict(d: dict[str, object]) -> NodePatch:
    return node_patch_from_dict(d)


def _payload_from_dict(
    kind: NodeKind,
    d: dict[str, object],
) -> AgentNodePayload | CheckpointNodePayload:
    if kind == NODE_KIND_CHECKPOINT:
        return CheckpointNodePayload(question=str(d["question"]))
    return AgentNodePayload(spec=spec_from_dict(cast(dict[str, object], d["spec"])))


def node_from_dict(d: dict[str, object]) -> TraceNode:
    patches_raw = cast(list[dict[str, object]], d.get("patches", []))
    kind = _parse_node_kind(d.get("kind", NODE_KIND_AGENT))
    payload = _payload_from_dict(kind, cast(dict[str, object], d["payload"]))
    return TraceNode(
        id=str(d["id"]),
        seq=int(str(d["seq"])),
        parent_id=cast(str | None, d.get("parent_id")),
        kind=kind,
        payload=payload,
        status=NodeStatus(str(d["status"])),
        result=cast(str | None, d.get("result")),
        error=cast(str | None, d.get("error")),
        start_time=cast(float | None, d.get("start_time")),
        end_time=cast(float | None, d.get("end_time")),
        parallel_group=cast(str | None, d.get("parallel_group")),
        backend_name=cast(str | None, d.get("backend_name")),
        cost_usd=_opt_float(d.get("cost_usd")),
        input_tokens=_opt_int(d.get("input_tokens")),
        output_tokens=_opt_int(d.get("output_tokens")),
        patches=[patch_from_dict(p) for p in patches_raw],
    )


def _parse_node_kind(raw: object) -> NodeKind:
    if raw == NODE_KIND_CHECKPOINT:
        return NODE_KIND_CHECKPOINT
    return NODE_KIND_AGENT


def graph_to_dict(graph: TraceGraph) -> dict[str, object]:
    return {
        "nodes": [node_to_dict(n) for n in graph.nodes],
        "seq": graph._seq,
    }


def graph_to_json(graph: TraceGraph) -> str:
    return json.dumps(graph_to_dict(graph), indent=2, default=str)


def graph_from_dict(d: dict[str, object]) -> TraceGraph:
    from starloom.graph_pkg.trace_graph import TraceGraph

    nodes_raw = cast(list[dict[str, object]], d.get("nodes", []))
    nodes = [node_from_dict(node_d) for node_d in nodes_raw]
    seq = int(str(d.get("seq", len(nodes))))
    return TraceGraph.from_nodes(nodes, seq=seq)
