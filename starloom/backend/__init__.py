"""Backend layer -- pluggable agent execution."""

from starloom.backend.claude_cli import ClaudeCLIBackend
from starloom.backend.dry_run import DryRunBackend
from starloom.backend.pi import PiBackend
from starloom.backend.protocol import AgentBackend, AgentResult

__all__ = [
    "AgentBackend",
    "AgentResult",
    "ClaudeCLIBackend",
    "DryRunBackend",
    "PiBackend",
]
