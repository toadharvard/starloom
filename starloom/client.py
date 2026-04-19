"""SessionClient — connect, subscribe, send decisions.

Typed API: returns ServerMsg variants, accepts GraphAction for decisions.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from starloom.checkpoint import (
    AnswerAction,
    ApproveAction,
    GraphAction,
    PatchNodeAction,
    RejectAction,
)
from starloom.messages import (
    ActionResultMsg,
    ServerMsg,
    parse_server_msg,
    serialize_action,
)
from starloom.types import NodePatch


class SessionClient:
    """Connects to a SessionServer over a Unix socket.

    Supports subscribing for events (snapshot + live stream) and sending
    typed GraphActions for pending checkpoints and node operations.
    """

    def __init__(self, sock_path: Path) -> None:
        self._sock_path = sock_path
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None

    async def connect(self) -> None:
        """Connect to the Unix socket and subscribe for event streaming."""
        await self._open_connection()
        await self._send_subscribe()

    async def connect_control(self) -> None:
        """Connect to the Unix socket without subscribing to event streaming."""
        await self._open_connection()

    async def disconnect(self) -> None:
        """Close the connection."""
        if self._writer is not None:
            self._writer.close()
            await self._writer.wait_closed()
            self._writer = None
            self._reader = None

    # ── Reading messages ─���───────────────────────────────────────

    async def read_message(self) -> ServerMsg | None:
        """Read the next typed message from the server.

        Returns None on EOF.
        """
        reader = self._require_reader()
        line = await reader.readline()
        return parse_server_msg(line)

    # ── Sending actions ────���─────────────────────────────────────

    async def send_action(self, action: GraphAction) -> ActionResultMsg:
        """Send a typed action and wait for the result."""
        writer = self._require_writer()
        writer.write(serialize_action(action).encode())
        await writer.drain()
        return await self._read_action_result()

    async def approve(self, checkpoint_id: str) -> bool:
        """Approve a pending checkpoint."""
        result = await self.send_action(
            ApproveAction(checkpoint_id=checkpoint_id),
        )
        return result.ok

    async def reject(self, checkpoint_id: str, reason: str = "") -> bool:
        """Reject a pending checkpoint."""
        result = await self.send_action(
            RejectAction(checkpoint_id=checkpoint_id, reason=reason),
        )
        return result.ok

    async def answer(self, checkpoint_id: str, text: str) -> bool:
        """Answer a checkpoint that expects operator text."""
        result = await self.send_action(
            AnswerAction(checkpoint_id=checkpoint_id, answer=text),
        )
        return result.ok

    async def patch_node(self, node_id: str, patch: NodePatch) -> bool:
        """Patch a node (for offline patching or pre-approve edits)."""
        result = await self.send_action(
            PatchNodeAction(node_id=node_id, patch=patch),
        )
        return result.ok

    # ── Internal helpers ────────────────────────────────────────

    async def _open_connection(self) -> None:
        self._reader, self._writer = await asyncio.open_unix_connection(
            str(self._sock_path),
            limit=16 * 1024 * 1024,
        )

    async def _send_subscribe(self) -> None:
        writer = self._require_writer()
        writer.write(b'{"msg":"subscribe"}\n')
        await writer.drain()

    async def _read_action_result(self) -> ActionResultMsg:
        """Read messages until an ActionResultMsg arrives.

        Non-result messages (events) are discarded; in practice, the
        caller should drain events on a separate task.
        """
        while True:
            msg = await self.read_message()
            if msg is None:
                return ActionResultMsg(ok=False, error="connection closed")
            if isinstance(msg, ActionResultMsg):
                return msg

    def _require_reader(self) -> asyncio.StreamReader:
        if self._reader is None:
            raise RuntimeError("Not connected")
        return self._reader

    def _require_writer(self) -> asyncio.StreamWriter:
        if self._writer is None:
            raise RuntimeError("Not connected")
        return self._writer
