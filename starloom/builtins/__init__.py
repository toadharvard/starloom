"""Starlark DSL builtins — call_agent, agent, checkpoint, param, etc.

Decomposed into submodules to stay under 300 lines per file.
"""

from __future__ import annotations

from starloom.builtins.agents import make_call_agent
from starloom.builtins.parallel import make_agent, make_run_parallel
from starloom.builtins.context import BranchContext, RuntimeContext
from starloom.middleware.protocol import AgentMiddleware
from starloom.builtins.workflow import (
    make_checkpoint,
    make_fail,
    make_load_internal_skill,
    make_output,
)
from starloom.params import make_param_builtin

PRELUDE = """\
def parallel_map(fn, items):
    specs = [fn(item) for item in items]
    return _run_parallel(specs)
"""


def make_builtins(
    ctx: RuntimeContext,
    middleware: list[AgentMiddleware],
    branch: BranchContext,
) -> dict[str, object]:
    """Return dict of Starlark-callable functions."""
    cp = make_checkpoint(ctx, branch)
    return {
        "call_agent": make_call_agent(ctx, middleware, branch),
        "agent": make_agent(),
        "_run_parallel": make_run_parallel(ctx, branch),
        "output": make_output(ctx),
        "fail": make_fail(),
        "checkpoint": cp,
        "load_internal_skill": make_load_internal_skill(ctx),
        "param": make_param_builtin(),
        "True": True,
        "False": False,
        "None": None,
    }


__all__ = [
    "PRELUDE",
    "BranchContext",
    "RuntimeContext",
    "make_builtins",
]
