"""TraceGraph — DAG of agent executions with node state tracking.

Mutated ONLY from the event loop thread (no locks needed). Does NOT
emit events — the runtime layer emits events AND updates the graph.
"""

from __future__ import annotations

import time

from starloom.graph_pkg.node import (
    AgentNodePayload,
    CheckpointNodePayload,
    NODE_KIND_AGENT,
    NodeKind,
    TraceNode,
)
from starloom.types import AgentSpecData, NodePatch, NodeStatus


class TraceGraph:
    """Execution DAG. Mutated only from event loop thread.

    Tracks parent->child edges via TraceNode.parent_id.
    """

    def __init__(self) -> None:
        self._nodes: dict[str, TraceNode] = {}
        self._order: list[str] = []
        self._seq = 0

    @property
    def nodes(self) -> list[TraceNode]:
        return [self._nodes[node_id] for node_id in self._order]

    def get_node(self, node_id: str) -> TraceNode | None:
        return self._nodes.get(node_id)

    @property
    def total_cost_usd(self) -> float | None:
        costs = [n.cost_usd for n in self.nodes if n.cost_usd is not None]
        return sum(costs) if costs else None

    def add_node(
        self,
        node_id: str,
        spec: AgentSpecData,
        parent_id: str | None = None,
        parallel_group: str | None = None,
        kind: NodeKind = NODE_KIND_AGENT,
        backend_name: str | None = None,
    ) -> TraceNode:
        self._seq += 1
        payload = (
            AgentNodePayload(spec)
            if kind == NODE_KIND_AGENT
            else CheckpointNodePayload(spec.prompt)
        )
        node = TraceNode(
            id=node_id,
            seq=self._seq,
            parent_id=parent_id,
            kind=kind,
            payload=payload,
            parallel_group=parallel_group,
            backend_name=backend_name,
        )
        self._nodes[node_id] = node
        self._order.append(node_id)
        return node

    def add_checkpoint_node(
        self,
        node_id: str,
        question: str,
        parent_id: str | None = None,
        parallel_group: str | None = None,
    ) -> TraceNode:
        self._seq += 1
        node = TraceNode(
            id=node_id,
            seq=self._seq,
            parent_id=parent_id,
            kind="checkpoint",
            payload=CheckpointNodePayload(question),
            parallel_group=parallel_group,
        )
        self._nodes[node_id] = node
        self._order.append(node_id)
        return node

    def start_node(self, node_id: str) -> None:
        node = self._nodes[node_id]
        node.status = NodeStatus.RUNNING
        node.start_time = time.time()

    def finish_node(
        self,
        node_id: str,
        result: str,
        cost_usd: float | None,
        input_tokens: int | None,
        output_tokens: int | None,
    ) -> None:
        node = self._end_node(node_id, NodeStatus.COMPLETED)
        node.result = result
        node.cost_usd = cost_usd
        node.input_tokens = input_tokens
        node.output_tokens = output_tokens

    def fail_node(self, node_id: str, error: str) -> None:
        self._end_node(node_id, NodeStatus.ERROR).error = error

    def skip_node(self, node_id: str) -> None:
        self._end_node(node_id, NodeStatus.SKIPPED)

    def stop_node(self, node_id: str) -> None:
        self._end_node(node_id, NodeStatus.STOPPED)

    def cache_node(self, node_id: str, result: str) -> None:
        self._end_node(node_id, NodeStatus.CACHED).result = result

    def patch_node(self, node_id: str, patch: NodePatch) -> None:
        """Apply a patch to a node. Resets it and dependents."""
        node = self._nodes[node_id]
        node.patches.append(patch)
        _reset_node(node)
        self._reset_dependents(node_id)

    def _end_node(self, node_id: str, status: NodeStatus) -> TraceNode:
        """Transition node to a terminal state and stamp end_time."""
        node = self._nodes[node_id]
        node.status = status
        node.end_time = time.time()
        return node

    def _reset_dependents(self, node_id: str) -> None:
        """Reset all nodes whose parent_id matches node_id."""
        for child_id in self._order:
            node = self._nodes[child_id]
            if node.parent_id == node_id:
                _reset_node(node)
                self._reset_dependents(child_id)

    def to_dict(self) -> dict[str, object]:
        from starloom.graph_pkg.serialization import graph_to_dict

        return graph_to_dict(self)

    def to_json(self) -> str:
        from starloom.graph_pkg.serialization import graph_to_json

        return graph_to_json(self)

    @classmethod
    def from_dict(cls, d: dict[str, object]) -> TraceGraph:
        from starloom.graph_pkg.serialization import graph_from_dict

        return graph_from_dict(d)

    @classmethod
    def from_nodes(cls, nodes: list[TraceNode], seq: int | None = None) -> TraceGraph:
        """Rebuild a graph from an ordered node list."""
        graph = cls()
        for node in nodes:
            graph._nodes[node.id] = node
            graph._order.append(node.id)
        graph._seq = seq if seq is not None else len(graph._order)
        return graph


def _reset_node(node: TraceNode) -> None:
    """Reset a node to pending state, clearing all execution data."""
    node.status = NodeStatus.PENDING
    node.result = None
    node.error = None
    node.start_time = None
    node.end_time = None
    node.cost_usd = None
    node.input_tokens = None
    node.output_tokens = None
