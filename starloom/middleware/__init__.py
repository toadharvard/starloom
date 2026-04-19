"""Middleware layer — before/after hooks for agent calls."""

from starloom.middleware.protocol import (
    Accept,
    AfterAction,
    AgentMiddleware,
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
)

__all__ = [
    "Accept",
    "AfterAction",
    "AgentMiddleware",
    "BeforeAction",
    "CacheEntry",
    "Cancel",
    "EditResult",
    "EditSpec",
    "ExecutionLog",
    "MiddlewareChain",
    "ReplayMiddleware",
    "Run",
    "Skip",
]
