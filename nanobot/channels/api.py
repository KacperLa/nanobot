"""API channel - Unix-socket JSON-RPC server for external clients.

nanobot listens on a Unix domain socket. Any client (for example the voice web
UI or an MCP bridge) connects, sends newline-delimited JSON-RPC 2.0 messages,
and receives newline-delimited JSON-RPC 2.0 notifications/responses.

Wire protocol
-------------
Client -> server (JSON-RPC 2.0, one object per line)::

    {"jsonrpc": "2.0", "method": "message.send",
     "params": {"content": "hello", "chat_id": "web", "sender_id": "user"}}
    {"jsonrpc": "2.0", "method": "card.respond",
     "params": {"card_id": "card_123", "value": "Option A"}}
    {"jsonrpc": "2.0", "method": "command.execute",
     "params": {"command": "reset", "chat_id": "web"}}
    {"jsonrpc": "2.0", "id": "req-1", "method": "card.upsert",
     "params": {"kind": "text", "template_key": "sensor-live",
                "template_state": {"title": "Bedroom CO2",
                                   "source_url": "/ha/proxy/states/sensor.co2"},
                "slot": "weather:home"}}
    {"jsonrpc": "2.0", "id": "req-2", "method": "card.ask",
     "params": {"question": "...", "choices": ["A", "B"], "slot": "confirm:restart"}}

Server -> client (JSON-RPC 2.0 notifications / responses, one object per line)::

    {"jsonrpc": "2.0", "method": "message",
     "params": {"content": "Hi there!", "chat_id": "web", "is_progress": false}}
    {"jsonrpc": "2.0", "method": "agent_state",
     "params": {"state": "thinking", "chat_id": "web"}}
    {"jsonrpc": "2.0", "method": "card",
     "params": {"id": "card_123", "kind": "text", "title": "Weather", "lane": "context", "state": "active"}}
    {"jsonrpc": "2.0", "id": "req-1", "result": {"status": "ok", "card_id": "card_123"}}
    {"jsonrpc": "2.0", "id": "req-2", "result": {"card_id": "card_456", "value": "A"}}

``is_progress: true`` marks intermediate tool-call updates; ``false`` (or
absent) marks the final answer for a turn.

The socket path defaults to ``~/.nanobot/api.sock`` and is configurable via
``channels.api.socket_path`` in ``~/.nanobot/config.json``.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from loguru import logger

from nanobot.bus.events import InboundMessage, OutboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.channels.base import BaseChannel

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_JSONRPC_VERSION = "2.0"
_RPC_PARSE_ERROR = -32700
_RPC_INVALID_REQUEST = -32600
_RPC_METHOD_NOT_FOUND = -32601
_RPC_INVALID_PARAMS = -32602
_RPC_INTERNAL_ERROR = -32603
_RPC_APPLICATION_ERROR = -32000
_CARD_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_-]{1,128}$")
_CARD_KINDS = {"text", "question"}
_CARD_STATES = {"active", "stale", "superseded", "resolved", "archived"}
_CARD_LANES = {"attention", "work", "context", "history"}
_MAX_CONTENT_CHARS = 200_000
_MAX_TITLE_CHARS = 2_000
_MAX_QUESTION_CHARS = 8_000
_MAX_SLOT_CHARS = 256
_MAX_TEMPLATE_KEY_CHARS = 128
_MAX_CONTEXT_SUMMARY_CHARS = 8_000
_MAX_RESPONSE_CHARS = 8_000
_MAX_TEMPLATE_STATE_CHARS = 50_000
_MAX_CHOICES = 6


class _RpcError(Exception):
    """JSON-RPC application error."""

    def __init__(self, code: int, message: str, data: Any | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data


def _default_socket_path() -> str:
    return str(Path.home() / ".nanobot" / "api.sock")


def _encode(obj: dict[str, Any]) -> bytes:
    return (json.dumps(obj, ensure_ascii=False) + "\n").encode()


def _jsonrpc_notification(method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "jsonrpc": _JSONRPC_VERSION,
        "method": method,
    }
    if params is not None:
        payload["params"] = params
    return payload


def _jsonrpc_success(request_id: Any, result: Any) -> dict[str, Any]:
    return {
        "jsonrpc": _JSONRPC_VERSION,
        "id": request_id,
        "result": result,
    }


def _jsonrpc_error(
    request_id: Any,
    code: int,
    message: str,
    data: Any | None = None,
) -> dict[str, Any]:
    error: dict[str, Any] = {
        "code": code,
        "message": message,
    }
    if data is not None:
        error["data"] = data
    return {
        "jsonrpc": _JSONRPC_VERSION,
        "id": request_id,
        "error": error,
    }


def _clamp_priority(raw: Any, *, default: int = 50) -> int:
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = default
    return max(0, min(value, 100))


def _truncate(raw: Any, limit: int) -> str:
    text = str(raw or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit]


def _normalize_card_id(raw: Any) -> str:
    card_id = str(raw or "").strip()
    return card_id if _CARD_ID_PATTERN.fullmatch(card_id) else ""


def _normalize_card_kind(raw: Any, *, default: str = "text") -> str:
    kind = str(raw or "").strip().lower() or default
    return kind if kind in _CARD_KINDS else default


def _normalize_card_state(raw: Any, *, default: str = "active") -> str:
    state = str(raw or "").strip().lower() or default
    return state if state in _CARD_STATES else default


def _normalize_card_lane(raw: Any, *, default: str = "context") -> str:
    lane = str(raw or "").strip().lower() or default
    return lane if lane in _CARD_LANES else default


def _coerce_choices(raw: Any) -> list[str]:
    if not isinstance(raw, list):
        return []
    values = [_truncate(item, 256) for item in raw]
    cleaned = [item for item in values if item]
    return cleaned[:_MAX_CHOICES]


def _coerce_metadata(raw: Any) -> dict[str, Any]:
    return dict(raw) if isinstance(raw, dict) else {}


def _coerce_template_state(raw: Any) -> dict[str, Any]:
    return dict(raw) if isinstance(raw, dict) else {}


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
        self.chat_id: str = "api"

    async def send(self, obj: dict[str, Any]) -> None:
        """Write one JSON line to the client, ignoring broken-pipe errors."""
        async with self._send_lock:
            try:
                self._writer.write(_encode(obj))
                await self._writer.drain()
            except (ConnectionResetError, BrokenPipeError, OSError):
                pass

    async def send_notification(self, method: str, params: dict[str, Any] | None = None) -> None:
        await self.send(_jsonrpc_notification(method, params))

    async def send_result(self, request_id: Any, result: Any) -> None:
        await self.send(_jsonrpc_success(request_id, result))

    async def send_error(
        self,
        request_id: Any,
        code: int,
        message: str,
        data: Any | None = None,
    ) -> None:
        await self.send(_jsonrpc_error(request_id, code, message, data))

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
                    break
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
            await self.send_error(None, _RPC_PARSE_ERROR, f"JSON parse error: {exc}")
            return
        if not isinstance(obj, dict):
            await self.send_error(None, _RPC_INVALID_REQUEST, "request must be a JSON object")
            return

        request_id = obj.get("id")
        has_response = "id" in obj

        if obj.get("jsonrpc") != _JSONRPC_VERSION:
            if has_response:
                await self.send_error(request_id, _RPC_INVALID_REQUEST, "jsonrpc must be '2.0'")
            return

        method = obj.get("method")
        if not isinstance(method, str) or not method.strip():
            if has_response:
                await self.send_error(request_id, _RPC_INVALID_REQUEST, "method must be a string")
            return

        params = obj.get("params", {})
        if params is None:
            params = {}
        if not isinstance(params, dict):
            if has_response:
                await self.send_error(request_id, _RPC_INVALID_PARAMS, "params must be an object")
            return

        try:
            result = await self._dispatch_method(method.strip(), params)
        except _RpcError as exc:
            if has_response:
                await self.send_error(request_id, exc.code, exc.message, exc.data)
            return
        except Exception:
            logger.exception("API channel JSON-RPC method failed: {}", method)
            if has_response:
                await self.send_error(request_id, _RPC_INTERNAL_ERROR, "internal error")
            return

        if has_response:
            await self.send_result(request_id, result if result is not None else {"status": "ok"})

    async def _dispatch_method(self, method: str, params: dict[str, Any]) -> dict[str, Any] | None:
        if method == "ping":
            return {"pong": True}

        if method == "message.send":
            content = str(params.get("content", "")).strip()
            if not content:
                raise _RpcError(_RPC_INVALID_PARAMS, "content is required")
            chat_id = str(params.get("chat_id", self.chat_id)).strip() or "api"
            sender_id = str(params.get("sender_id", "user")).strip() or "user"
            self.chat_id = chat_id

            inbound = InboundMessage(
                channel=self._channel.name,
                sender_id=sender_id,
                chat_id=chat_id,
                content=content,
                media=list(params.get("media", [])),
                metadata=_coerce_metadata(params.get("metadata", {})),
            )
            await self._channel.bus.publish_inbound(inbound)
            await self._channel.push_agent_state(chat_id, "thinking")
            return {"status": "accepted"}

        if method == "card.respond":
            card_id = _normalize_card_id(params.get("card_id", ""))
            value = _truncate(params.get("value", ""), _MAX_RESPONSE_CHARS)
            if not card_id:
                raise _RpcError(_RPC_INVALID_PARAMS, "card_id is required")
            if not value:
                raise _RpcError(_RPC_INVALID_PARAMS, "value is required")
            await self._channel.resolve_card_response(card_id, value)
            chat_id = str(params.get("chat_id", self.chat_id)).strip() or self.chat_id or "api"
            await self._channel.push_agent_state(chat_id, "thinking")
            return {"status": "ok", "card_id": card_id, "value": value}

        if method == "command.execute":
            command = str(params.get("command", "")).strip().lower()
            chat_id = str(params.get("chat_id", self.chat_id)).strip() or "api"
            self.chat_id = chat_id
            if command != "reset":
                raise _RpcError(_RPC_INVALID_PARAMS, f"unknown command: {command!r}")
            inbound = InboundMessage(
                channel=self._channel.name,
                sender_id="user",
                chat_id=chat_id,
                content="/new",
            )
            await self._channel.bus.publish_inbound(inbound)
            return {"status": "accepted"}

        if method == "card.upsert":
            card = self._channel.build_card(params)
            self.chat_id = str(card.get("chat_id", self.chat_id)).strip() or self.chat_id
            await self._channel.push_card(card)
            return {"status": "ok", "card_id": card["id"]}

        if method == "card.ask":
            raw_timeout = params.get("timeout", 120.0)
            try:
                timeout = float(raw_timeout)
            except (TypeError, ValueError):
                timeout = 120.0
            timeout = max(5.0, min(timeout, 600.0))

            card = self._channel.build_card({**params, "kind": "question", "state": "active"})
            self.chat_id = str(card.get("chat_id", self.chat_id)).strip() or self.chat_id
            try:
                value = await self._channel.ask_card(card, timeout=timeout)
            except TimeoutError as exc:
                raise _RpcError(
                    _RPC_APPLICATION_ERROR,
                    f"card response timed out after {int(timeout)}s",
                    {"card_id": card["id"]},
                ) from exc
            return {
                "card_id": card["id"],
                "value": value,
            }

        raise _RpcError(_RPC_METHOD_NOT_FOUND, f"unknown method: {method!r}")


# ---------------------------------------------------------------------------
# ApiChannel
# ---------------------------------------------------------------------------


class ApiChannel(BaseChannel):
    """Unix-socket server channel."""

    name: str = "api"

    def __init__(self, config: Any, bus: MessageBus) -> None:
        super().__init__(config, bus)
        raw_path = getattr(config, "socket_path", None) or _default_socket_path()
        self._socket_path: str = str(Path(raw_path).expanduser())
        self.on_connect_prompt: str = str(getattr(config, "on_connect_prompt", "") or "")
        self._clients: dict[int, _ClientConnection] = {}
        self._server: asyncio.AbstractServer | None = None
        self._pending_card_responses: dict[str, asyncio.Future[str]] = {}
        self._cards: dict[str, dict[str, Any]] = {}
        self._card_slots: dict[tuple[str, str], str] = {}

    async def start(self) -> None:
        path = Path(self._socket_path)
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
        self._running = False
        if self._server:
            self._server.close()
            with contextlib.suppress(Exception):
                await self._server.wait_closed()
            self._server = None

        for conn in list(self._clients.values()):
            with contextlib.suppress(Exception):
                conn._writer.close()
                await conn._writer.wait_closed()
        self._clients.clear()

        with contextlib.suppress(FileNotFoundError):
            Path(self._socket_path).unlink()

        logger.info("API channel stopped")

    async def send(self, msg: OutboundMessage) -> None:
        is_progress = bool(msg.metadata.get("_progress"))
        is_tool_hint = bool(msg.metadata.get("_tool_hint"))
        payload: dict[str, Any] = {
            "content": msg.content,
            "chat_id": msg.chat_id,
            "is_progress": is_progress,
            "is_tool_hint": is_tool_hint,
        }
        targets = self._target_clients(msg.chat_id)
        await asyncio.gather(
            *(c.send_notification("message", payload) for c in targets),
            return_exceptions=True,
        )

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

    def _target_clients(self, chat_id: str) -> list[_ClientConnection]:
        matching = [c for c in self._clients.values() if c.chat_id == chat_id]
        return matching if matching else list(self._clients.values())

    def build_card(self, params: dict[str, Any]) -> dict[str, Any]:
        now = datetime.now(timezone.utc).isoformat()
        chat_id = str(params.get("chat_id", "web")).strip() or "web"
        slot = _truncate(params.get("slot", ""), _MAX_SLOT_CHARS)
        provided_id = _normalize_card_id(params.get("card_id", ""))
        existing_id = self._card_slots.get((chat_id, slot)) if slot else ""
        card_id = provided_id or existing_id or str(uuid.uuid4())
        previous = dict(self._cards.get(card_id, {}))

        kind_default = "question" if str(params.get("kind", "")).strip().lower() == "question" else "text"
        kind = _normalize_card_kind(params.get("kind", previous.get("kind", kind_default)), default=kind_default)
        title = _truncate(params.get("title", previous.get("title", "")), _MAX_TITLE_CHARS)
        raw_content = str(params.get("content", previous.get("content", "")))
        content = raw_content if kind == "question" else ""
        question = _truncate(params.get("question", previous.get("question", "")), _MAX_QUESTION_CHARS)
        choices = _coerce_choices(params.get("choices", previous.get("choices", [])))
        lane_default = "attention" if kind == "question" else "context"
        lane = _normalize_card_lane(params.get("lane", previous.get("lane", lane_default)), default=lane_default)
        state = _normalize_card_state(params.get("state", previous.get("state", "active")), default="active")
        priority_default = 90 if kind == "question" else 50
        priority = _clamp_priority(params.get("priority", previous.get("priority", priority_default)), default=priority_default)
        template_key = _truncate(params.get("template_key", previous.get("template_key", "")), _MAX_TEMPLATE_KEY_CHARS)
        template_state = _coerce_template_state(
            params.get("template_state", previous.get("template_state", {}))
        )
        context_summary = _truncate(
            params.get("context_summary", previous.get("context_summary", "")),
            _MAX_CONTEXT_SUMMARY_CHARS,
        )
        response_value = _truncate(
            params.get("response_value", previous.get("response_value", "")),
            _MAX_RESPONSE_CHARS,
        )

        if len(content) > _MAX_CONTENT_CHARS:
            raise _RpcError(
                _RPC_INVALID_PARAMS,
                f"content too large ({len(content)} > {_MAX_CONTENT_CHARS})",
            )
        template_state_raw = json.dumps(template_state, ensure_ascii=False)
        if len(template_state_raw) > _MAX_TEMPLATE_STATE_CHARS:
            raise _RpcError(
                _RPC_INVALID_PARAMS,
                "template_state too large "
                f"({len(template_state_raw)} > {_MAX_TEMPLATE_STATE_CHARS})",
            )
        if kind == "text" and not template_key:
            raise _RpcError(_RPC_INVALID_PARAMS, "template_key is required for text cards")
        if kind == "question":
            if not question:
                raise _RpcError(_RPC_INVALID_PARAMS, "question is required for question cards")
            if len(choices) < 2:
                raise _RpcError(
                    _RPC_INVALID_PARAMS,
                    "question cards require at least two choices",
                )

        previous_slot = str(previous.get("slot", "")).strip()
        if previous_slot and previous_slot != slot:
            self._card_slots.pop((chat_id, previous_slot), None)
        if slot:
            self._card_slots[(chat_id, slot)] = card_id

        card = {
            "id": card_id,
            "kind": kind,
            "title": title,
            "content": content,
            "question": question,
            "choices": choices,
            "response_value": response_value,
            "chat_id": chat_id,
            "slot": slot,
            "lane": lane,
            "priority": priority,
            "state": state,
            "template_key": template_key,
            "template_state": template_state,
            "context_summary": context_summary,
            "created_at": str(previous.get("created_at", params.get("created_at", now))) or now,
            "updated_at": now,
        }
        self._cards[card_id] = card
        return card

    async def push_agent_state(self, chat_id: str, state: str) -> None:
        payload = {"state": state, "chat_id": chat_id}
        targets = self._target_clients(chat_id)
        await asyncio.gather(
            *(c.send_notification("agent_state", payload) for c in targets),
            return_exceptions=True,
        )

    async def push_card(self, card: dict[str, Any]) -> None:
        payload = dict(card)
        chat_id = str(payload.get("chat_id", "web")).strip() or "web"
        targets = self._target_clients(chat_id)
        await asyncio.gather(
            *(c.send_notification("card", payload) for c in targets),
            return_exceptions=True,
        )

    async def ask_card(self, card: dict[str, Any], *, timeout: float) -> str:
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[str] = loop.create_future()
        self._pending_card_responses[card["id"]] = fut
        await self.push_card(card)
        try:
            return await asyncio.wait_for(fut, timeout=timeout)
        except asyncio.TimeoutError:
            self._pending_card_responses.pop(card["id"], None)
            timed_out = dict(self._cards.get(card["id"], card))
            timed_out["state"] = "stale"
            timed_out["updated_at"] = datetime.now(timezone.utc).isoformat()
            self._cards[card["id"]] = timed_out
            await self.push_card(timed_out)
            raise TimeoutError(f"No card.respond received for card_id={card['id']!r}")

    async def resolve_card_response(self, card_id: str, value: str) -> None:
        fut = self._pending_card_responses.pop(card_id, None)
        if fut is not None and not fut.done():
            fut.set_result(value)

        existing = self._cards.get(card_id)
        if existing is None:
            return

        updated = dict(existing)
        updated["response_value"] = value
        updated["state"] = "resolved"
        updated["updated_at"] = datetime.now(timezone.utc).isoformat()
        self._cards[card_id] = updated
        await self.push_card(updated)
