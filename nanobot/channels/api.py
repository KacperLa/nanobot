"""API channel — Unix-socket server for external clients.

nanobot listens on a Unix domain socket.  Any client (e.g. the voice web UI)
connects, sends newline-delimited JSON messages, and receives newline-delimited
JSON responses.

Wire protocol
-------------
Client → server (one JSON object per line)::

    {"type": "message",     "content": "hello", "chat_id": "web", "sender_id": "user"}
    {"type": "ping"}
    {"type": "ui-response", "request_id": "<uuid>", "value": "Option A", "chat_id": "web"}
    {"type": "command",     "command": "reset", "chat_id": "web"}

Server → client (one JSON object per line)::

    {"type": "message",     "content": "Hi there!", "chat_id": "web", "is_progress": false}
    {"type": "agent_state", "state": "thinking"}
    {"type": "toast",       "kind": "text"|"image", "content": "...", "title": "...", "duration_ms": 5000}
    {"type": "choice",      "request_id": "<uuid>", "question": "...", "choices": ["A", "B"],
                            "title": "...", "chat_id": "web"}
    {"type": "pong"}
    {"type": "error",       "error": "..."}

``is_progress: true`` marks intermediate tool-call updates; ``false`` (or
absent) marks the final answer for a turn.

The socket path defaults to ``~/.nanobot/api.sock`` and is configurable via
``channels.api.socket_path`` in ``~/.nanobot/config.json``.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from pathlib import Path
from typing import Any

from loguru import logger

from nanobot.bus.events import InboundMessage, OutboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.channels.base import BaseChannel


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _default_socket_path() -> str:
    return str(Path.home() / ".nanobot" / "api.sock")


def _encode(obj: dict) -> bytes:
    return (json.dumps(obj, ensure_ascii=False) + "\n").encode()


# ---------------------------------------------------------------------------
# Per-connection state
# ---------------------------------------------------------------------------


class _ClientConnection:
    """Represents one connected client."""

    def __init__(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        channel: "ApiChannel",
    ) -> None:
        self._reader = reader
        self._writer = writer
        self._channel = channel
        self._send_lock = asyncio.Lock()
        # Chat ID provided by this client (set on first message, or "api")
        self.chat_id: str = "api"

    async def send(self, obj: dict) -> None:
        """Write one JSON line to the client, ignoring broken-pipe errors."""
        async with self._send_lock:
            try:
                self._writer.write(_encode(obj))
                await self._writer.drain()
            except (ConnectionResetError, BrokenPipeError, OSError):
                pass

    async def run(self) -> None:
        """Read loop — process lines until the client disconnects."""
        peer = self._writer.get_extra_info("peername") or "unix"
        logger.info("API channel: client connected ({})", peer)
        try:
            while True:
                try:
                    line = await self._reader.readline()
                except (ConnectionResetError, OSError):
                    break
                if not line:
                    break  # EOF
                await self._handle_line(line)
        finally:
            self._channel._remove_client(self)
            with contextlib.suppress(Exception):
                self._writer.close()
                await self._writer.wait_closed()
            logger.info("API channel: client disconnected ({})", peer)

    async def _handle_line(self, line: bytes) -> None:
        raw = line.decode(errors="replace").strip()
        if not raw:
            return
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError as exc:
            await self.send({"type": "error", "error": f"JSON parse error: {exc}"})
            return

        msg_type = str(obj.get("type", "")).strip()

        if msg_type == "ping":
            await self.send({"type": "pong"})

        elif msg_type == "message":
            content = str(obj.get("content", "")).strip()
            if not content:
                await self.send({"type": "error", "error": "empty content"})
                return
            chat_id = str(obj.get("chat_id", self.chat_id)).strip() or "api"
            sender_id = str(obj.get("sender_id", "user")).strip() or "user"
            self.chat_id = chat_id  # remember for future turns

            inbound = InboundMessage(
                channel=self._channel.name,
                sender_id=sender_id,
                chat_id=chat_id,
                content=content,
                media=list(obj.get("media", [])),
                metadata=dict(obj.get("metadata", {})),
            )
            await self._channel.bus.publish_inbound(inbound)

        elif msg_type == "ui-response":
            request_id = str(obj.get("request_id", "")).strip()
            value = str(obj.get("value", "")).strip()
            if not request_id:
                await self.send({"type": "error", "error": "ui-response missing request_id"})
                return
            fut = self._channel._pending_choices.pop(request_id, None)
            if fut is not None and not fut.done():
                fut.set_result(value)

        elif msg_type == "command":
            command = str(obj.get("command", "")).strip().lower()
            chat_id = str(obj.get("chat_id", self.chat_id)).strip() or "api"
            self.chat_id = chat_id
            if command == "reset":
                inbound = InboundMessage(
                    channel=self._channel.name,
                    sender_id="user",
                    chat_id=chat_id,
                    content="/new",
                )
                await self._channel.bus.publish_inbound(inbound)
            else:
                await self.send({"type": "error", "error": f"unknown command: {command!r}"})

        else:
            await self.send({"type": "error", "error": f"unknown message type: {msg_type!r}"})


# ---------------------------------------------------------------------------
# ApiChannel
# ---------------------------------------------------------------------------


class ApiChannel(BaseChannel):
    """Unix-socket server channel.

    Registers itself with the :class:`~nanobot.channels.manager.ChannelManager`
    like any other channel.  Clients connect to the socket, exchange
    newline-delimited JSON, and receive agent responses.
    """

    name: str = "api"

    def __init__(self, config: Any, bus: MessageBus) -> None:
        super().__init__(config, bus)
        raw_path = getattr(config, "socket_path", None) or _default_socket_path()
        self._socket_path: str = str(Path(raw_path).expanduser())
        self.on_connect_prompt: str = str(getattr(config, "on_connect_prompt", "") or "")
        self._clients: dict[int, _ClientConnection] = {}  # id(conn) → conn
        self._server: asyncio.AbstractServer | None = None
        # Maps request_id → Future[str] for in-flight ask_user calls.
        self._pending_choices: dict[str, asyncio.Future[str]] = {}

    # ------------------------------------------------------------------
    # BaseChannel interface
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Create and start the Unix socket server."""
        path = Path(self._socket_path)
        # Remove stale socket file if present
        path.unlink(missing_ok=True)
        path.parent.mkdir(parents=True, exist_ok=True)

        self._server = await asyncio.start_unix_server(
            self._on_client_connected,
            path=str(path),
        )
        self._running = True
        logger.info("API channel listening on {}", path)
        async with self._server:
            await self._server.serve_forever()

    async def stop(self) -> None:
        """Stop the server and close all client connections."""
        self._running = False
        if self._server:
            self._server.close()
            with contextlib.suppress(Exception):
                await self._server.wait_closed()
            self._server = None

        # Close all active clients
        for conn in list(self._clients.values()):
            with contextlib.suppress(Exception):
                conn._writer.close()
                await conn._writer.wait_closed()
        self._clients.clear()

        with contextlib.suppress(FileNotFoundError):
            Path(self._socket_path).unlink()

        logger.info("API channel stopped")

    async def send(self, msg: OutboundMessage) -> None:
        """Route an outbound message to the client whose chat_id matches."""
        is_progress = bool(msg.metadata.get("_progress"))
        payload: dict[str, Any] = {
            "type": "message",
            "content": msg.content,
            "chat_id": msg.chat_id,
            "is_progress": is_progress,
        }
        # Fan out to all clients that are listening on this chat_id.
        # If no client has that chat_id yet, broadcast to everyone (handles
        # the case where the client hasn't sent a message yet).
        matching = [c for c in self._clients.values() if c.chat_id == msg.chat_id]
        targets = matching if matching else list(self._clients.values())
        await asyncio.gather(*(c.send(payload) for c in targets), return_exceptions=True)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _on_client_connected(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        conn = _ClientConnection(reader, writer, self)
        self._clients[id(conn)] = conn
        await conn.run()

    def _remove_client(self, conn: _ClientConnection) -> None:
        self._clients.pop(id(conn), None)

    # ------------------------------------------------------------------
    # Progress push (called by the agent loop via the bus)
    # ------------------------------------------------------------------

    async def push_agent_state(self, chat_id: str, state: str) -> None:
        """Push an agent-state event to clients watching *chat_id*."""
        payload = {"type": "agent_state", "state": state, "chat_id": chat_id}
        matching = [c for c in self._clients.values() if c.chat_id == chat_id]
        targets = matching if matching else list(self._clients.values())
        await asyncio.gather(*(c.send(payload) for c in targets), return_exceptions=True)

    async def push_toast(
        self,
        kind: str,
        content: str,
        title: str = "",
        duration_ms: int = 5000,
        chat_id: str = "web",
    ) -> None:
        """Push a toast notification to clients watching *chat_id*.

        Args:
            kind: ``"text"`` or ``"image"`` (base-64 data-URI or URL).
            content: The message body, or an image data-URI / URL.
            title: Optional heading shown above the content.
            duration_ms: Auto-dismiss after this many milliseconds (0 = sticky).
            chat_id: Route to clients whose ``chat_id`` matches.
        """
        payload: dict[str, Any] = {
            "type": "toast",
            "kind": kind,
            "content": content,
            "title": title,
            "duration_ms": duration_ms,
            "chat_id": chat_id,
        }
        matching = [c for c in self._clients.values() if c.chat_id == chat_id]
        targets = matching if matching else list(self._clients.values())
        await asyncio.gather(*(c.send(payload) for c in targets), return_exceptions=True)

    async def push_choice(
        self,
        question: str,
        choices: list[str],
        request_id: str,
        title: str = "",
        chat_id: str = "web",
        timeout: float = 120.0,
    ) -> str:
        """Send a choice card to the UI and wait for the user's selection.

        Registers a Future in ``_pending_choices`` keyed by *request_id*.
        When the client sends back ``{"type": "ui-response", "request_id": ...,
        "value": ...}`` the Future is resolved and this coroutine returns the
        chosen value.

        Args:
            question: The question to display.
            choices: List of option labels (2–6 items).
            request_id: Unique identifier for this request (UUID string).
            title: Optional heading shown above the question.
            chat_id: Route to clients whose ``chat_id`` matches.
            timeout: Seconds to wait before raising ``TimeoutError``.

        Returns:
            The string value chosen by the user.

        Raises:
            TimeoutError: If no response arrives within *timeout* seconds.
        """
        loop = asyncio.get_event_loop()
        fut: asyncio.Future[str] = loop.create_future()
        self._pending_choices[request_id] = fut

        payload: dict[str, Any] = {
            "type": "choice",
            "request_id": request_id,
            "question": question,
            "choices": choices,
            "title": title,
            "chat_id": chat_id,
        }
        matching = [c for c in self._clients.values() if c.chat_id == chat_id]
        targets = matching if matching else list(self._clients.values())
        await asyncio.gather(*(c.send(payload) for c in targets), return_exceptions=True)

        try:
            return await asyncio.wait_for(fut, timeout=timeout)
        except asyncio.TimeoutError:
            self._pending_choices.pop(request_id, None)
            raise TimeoutError(f"No ui-response received for request_id={request_id!r}")
