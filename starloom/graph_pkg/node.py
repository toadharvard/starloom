"""TraceNode — a single node in the execution DAG."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from starloom.types import AgentSpecData, NodePatch, NodeStatus

NODE_KIND_AGENT: Literal["agent"] = "agent"
NODE_KIND_CHECKPOINT: Literal["checkpoint"] = "checkpoint"
NodeKind = Literal["agent", "checkpoint"]


@dataclass(frozen=True, slots=True)
class AgentNodePayload:
    spec: AgentSpecData

    @property
    def prompt_preview(self) -> str:
        return self.spec.prompt_preview


@dataclass(frozen=True, slots=True)
class CheckpointNodePayload:
    question: str

    @property
    def prompt_preview(self) -> str:
        return self.question


NodePayload = AgentNodePayload | CheckpointNodePayload


@dataclass(slots=True)
class TraceNode:
    """A single node in the execution DAG."""

    id: str
    seq: int
    parent_id: str | None
    kind: NodeKind
    payload: NodePayload
    status: NodeStatus = NodeStatus.PENDING
    result: str | None = None
    error: str | None = None
    start_time: float | None = None
    end_time: float | None = None
    parallel_group: str | None = None
    backend_name: str | None = None
    cost_usd: float | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    patches: list[NodePatch] = field(default_factory=list)

    @property
    def prompt_preview(self) -> str:
        return self.payload.prompt_preview

    @property
    def duration(self) -> float | None:
        if self.start_time is not None and self.end_time is not None:
            return self.end_time - self.start_time
        return None

    @property
    def spec(self) -> AgentSpecData:
        if isinstance(self.payload, AgentNodePayload):
            return self.payload.spec
        return AgentSpecData(prompt=self.payload.question)

    @property
    def effective_spec(self) -> AgentSpecData:
        """Spec with all patches applied in order."""
        spec = self.spec
        for patch in self.patches:
            spec = patch.apply(spec)
        return spec

    def to_dict(self) -> dict[str, object]:
        """Serialise to a plain dict for JSON output."""
        return {
            "id": self.id,
            "seq": self.seq,
            "parent_id": self.parent_id,
            "status": self.status.value,
            "kind": self.kind,
            "payload_type": self.kind,
            "prompt_preview": self.prompt_preview,
            "result": self.result,
            "error": self.error,
            "cost_usd": self.cost_usd,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "duration": self.duration,
            "parallel_group": self.parallel_group,
            "backend_name": self.backend_name,
        }
