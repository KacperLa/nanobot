#!/usr/bin/env python3
"""Display MCP server.

Provides UI interaction tools over nanobot's card API:
- render_card(template_key, template_state, title, chat_id, slot, lane, priority, context_summary)
- validate_card_state(template_key, template_state)
- ask_user(question, choices, title, chat_id, slot, lane, priority, template_key, context_summary, timeout_seconds)
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlparse, urlsplit, urlunparse
from urllib.request import Request, urlopen

from nanobot.card_validation import (
    find_direct_api_urls_in_state,
    inspect_state_proxy_references,
)

DEFAULT_SOCKET_PATH = Path(
    os.getenv("NANOBOT_API_SOCKET", str(Path.home() / ".nanobot" / "api.sock"))
).expanduser()
WORKSPACE_PATH = Path(os.getenv("NANOBOT_WORKSPACE", str(Path.home() / ".nanobot"))).expanduser()
SCRIPT_WORKSPACE_PATH = (WORKSPACE_PATH / "workspace").expanduser()
CONFIG_PATH = WORKSPACE_PATH / "config.json"
DEFAULT_CHAT_ID = str(os.getenv("DISPLAY_MCP_CHAT_ID", "web")).strip() or "web"
DEFAULT_TIMEOUT_SECONDS = int(os.getenv("DISPLAY_MCP_TIMEOUT_SECONDS", "120"))
DEFAULT_VALIDATION_TIMEOUT_SECONDS = int(
    os.getenv("DISPLAY_MCP_VALIDATION_TIMEOUT_SECONDS", "10")
)
MAX_TIMEOUT_SECONDS = 600
MAX_TITLE_CHARS = 2_000
MAX_QUESTION_CHARS = 8_000
MAX_SLOT_CHARS = 256
MAX_CONTEXT_SUMMARY_CHARS = 8_000
MAX_TEMPLATE_KEY_CHARS = 128
MAX_TEMPLATE_STATE_CHARS = 50_000
MIN_CHOICES = 2
MAX_CHOICES = 6
MAX_PROXY_REFS = 12
MAX_SCRIPT_ARGS = 16
_JSONRPC_VERSION = "2.0"


def _encode(payload: dict[str, Any]) -> bytes:
    return (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")


def _jsonrpc_request(request_id: str, method: str, params: dict[str, Any]) -> dict[str, Any]:
    return {
        "jsonrpc": _JSONRPC_VERSION,
        "id": request_id,
        "method": method,
        "params": params,
    }


def _normalize_chat_id(chat_id: str) -> str:
    normalized = str(chat_id or "").strip()
    return normalized or DEFAULT_CHAT_ID


def _truncate(value: str, limit: int) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit]


def _clamp_timeout(timeout_seconds: int | float) -> float:
    try:
        timeout = float(timeout_seconds)
    except (TypeError, ValueError):
        timeout = float(DEFAULT_TIMEOUT_SECONDS)
    return max(5.0, min(timeout, float(MAX_TIMEOUT_SECONDS)))


def _normalize_choices(choices: list[str]) -> list[str]:
    normalized = [str(choice).strip() for choice in choices if str(choice).strip()]
    if len(normalized) < MIN_CHOICES:
        raise RuntimeError("choices must include at least two non-empty values")
    if len(normalized) > MAX_CHOICES:
        normalized = normalized[:MAX_CHOICES]
    return normalized


def _coerce_template_state(raw: dict[str, Any] | str) -> dict[str, Any]:
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return {}
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"template_state is not valid JSON: {exc}") from exc
    else:
        parsed = raw

    if not isinstance(parsed, dict):
        raise RuntimeError("template_state must be a JSON object")
    return parsed


def _load_nanobot_config() -> dict[str, Any]:
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise RuntimeError(f"nanobot config not found at {CONFIG_PATH}") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"nanobot config is invalid JSON: {exc}") from exc


def _get_home_assistant_server_config() -> tuple[str, dict[str, str]]:
    config = _load_nanobot_config()
    tools = config.get("tools", {})
    if not isinstance(tools, dict):
        raise RuntimeError("nanobot config missing tools section")

    mcp_servers = tools.get("mcpServers", {})
    if not isinstance(mcp_servers, dict):
        raise RuntimeError("nanobot config missing tools.mcpServers section")

    raw_server = mcp_servers.get("home assistant") or mcp_servers.get("home_assistant")
    if not isinstance(raw_server, dict):
        raise RuntimeError("home assistant MCP server is not configured")

    url = str(raw_server.get("url", "")).strip()
    if not url:
        raise RuntimeError("home assistant MCP server URL is empty")

    raw_headers = raw_server.get("headers", {})
    headers: dict[str, str] = {}
    if isinstance(raw_headers, dict):
        for key, value in raw_headers.items():
            headers[str(key)] = str(value)
    return url, headers


def _home_assistant_origin(mcp_url: str) -> str:
    parsed = urlparse(mcp_url.strip())
    return urlunparse(parsed._replace(path="", params="", query="", fragment="")).rstrip("/")


def _normalize_home_assistant_api_path(target_path: str) -> str:
    normalized = "/" + target_path.lstrip("/")
    if normalized == "/":
        raise RuntimeError("target path is required")
    if normalized == "/api" or normalized.startswith("/api/"):
        return normalized
    return f"/api{normalized}"


def _probe_home_assistant_path(target_path: str, *, timeout_seconds: int) -> None:
    mcp_url, auth_headers = _get_home_assistant_server_config()
    origin = _home_assistant_origin(mcp_url)
    api_path = _normalize_home_assistant_api_path(target_path)
    request = Request(
        f"{origin}{api_path}",
        headers={
            **auth_headers,
            "Accept": "application/json, text/plain;q=0.5, */*;q=0.1",
        },
        method="GET",
    )

    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            status = getattr(response, "status", response.getcode())
            body = response.read()
            content_type = response.headers.get("content-type", "")
    except HTTPError as exc:
        raise RuntimeError(f"{target_path} returned HTTP {exc.code}") from exc
    except URLError as exc:
        raise RuntimeError(f"{target_path} could not be reached: {exc.reason}") from exc
    except OSError as exc:
        raise RuntimeError(f"{target_path} probe failed: {exc}") from exc

    if status < 200 or status >= 300:
        raise RuntimeError(f"{target_path} returned HTTP {status}")

    if target_path == "/calendars":
        try:
            payload = json.loads(body.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"{target_path} did not return JSON") from exc
        if not isinstance(payload, list):
            raise RuntimeError(f"{target_path} did not return a calendar list")
        return

    if target_path.startswith("/states/"):
        if "json" not in content_type.lower():
            raise RuntimeError(f"{target_path} did not return JSON state data")
        try:
            payload = json.loads(body.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"{target_path} did not return JSON") from exc
        if not isinstance(payload, dict) or "state" not in payload:
            raise RuntimeError(f"{target_path} did not return a Home Assistant state object")


def _resolve_workspace_script_target(target_path: str) -> tuple[Path, list[str]]:
    parsed = urlsplit(target_path)
    script_rel = parsed.path.strip().lstrip("/")
    if not script_rel:
        raise RuntimeError("script path is required")

    root = SCRIPT_WORKSPACE_PATH.resolve()
    candidate = (root / script_rel).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise RuntimeError("script path escapes workspace") from exc

    if not candidate.is_file():
        raise RuntimeError(f"script not found: {script_rel}")
    if candidate.suffix.lower() != ".py":
        raise RuntimeError("only Python workspace scripts are supported")

    params = parse_qs(parsed.query, keep_blank_values=True)
    unknown = sorted(key for key in params if key != "arg")
    if unknown:
        raise RuntimeError(
            "unsupported script query parameters: " + ", ".join(unknown)
        )

    args = [str(value) for value in params.get("arg", [])]
    if len(args) > MAX_SCRIPT_ARGS:
        raise RuntimeError(f"too many script arguments ({len(args)} > {MAX_SCRIPT_ARGS})")
    return candidate, args


def _probe_workspace_script_path(target_path: str, *, timeout_seconds: int) -> None:
    script_path, args = _resolve_workspace_script_target(target_path)
    try:
        completed = subprocess.run(
            [sys.executable, str(script_path), *args],
            cwd=str(script_path.parent),
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"{target_path} timed out") from exc
    except OSError as exc:
        raise RuntimeError(f"{target_path} could not be executed: {exc}") from exc

    if completed.returncode != 0:
        stderr = (completed.stderr or "").strip()
        detail = f": {stderr}" if stderr else ""
        raise RuntimeError(
            f"{target_path} exited with code {completed.returncode}{detail}"
        )

    try:
        json.loads((completed.stdout or "").strip())
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{target_path} did not return valid JSON") from exc


def _template_path(template_key: str) -> Path:
    safe_key = _truncate(template_key, MAX_TEMPLATE_KEY_CHARS)
    return WORKSPACE_PATH / "cards" / "templates" / safe_key / "template.html"


def _validate_template_state(template_key: str, template_state: dict[str, Any]) -> None:
    issues: list[str] = []

    template_path = _template_path(template_key)
    if not template_key:
        issues.append("template_key is required")
    elif not template_path.is_file():
        issues.append(f"template not found: {template_key}")

    state_raw = json.dumps(template_state, ensure_ascii=False)
    if len(state_raw) > MAX_TEMPLATE_STATE_CHARS:
        issues.append(
            f"template_state too large ({len(state_raw)} > {MAX_TEMPLATE_STATE_CHARS})"
        )

    direct_api_urls = find_direct_api_urls_in_state(template_state)
    for url in direct_api_urls:
        issues.append(f"direct Home Assistant API URL is not allowed: {url}. Use /ha/proxy/... instead")

    proxy_refs, proxy_errors = inspect_state_proxy_references(template_state)
    issues.extend(proxy_errors)

    if len(proxy_refs) > MAX_PROXY_REFS:
        issues.append(f"too many /ha/proxy references ({len(proxy_refs)} > {MAX_PROXY_REFS})")

    if issues:
        raise RuntimeError("card validation failed: " + "; ".join(issues))

    timeout_seconds = max(3, min(DEFAULT_VALIDATION_TIMEOUT_SECONDS, 30))
    for ref in proxy_refs:
        try:
            if ref.source_kind == "script":
                _probe_workspace_script_path(ref.probe_path, timeout_seconds=timeout_seconds)
            else:
                _probe_home_assistant_path(ref.probe_path, timeout_seconds=timeout_seconds)
        except RuntimeError as exc:
            if ref.source_kind == "script":
                raise RuntimeError(
                    "card validation failed: invalid workspace script endpoint "
                    f"{ref.raw} ({exc})."
                ) from exc
            raise RuntimeError(
                "card validation failed: invalid Home Assistant endpoint "
                f"{ref.raw} ({exc}). Use mcp_card_discover_live_card_source first."
            ) from exc


async def _open_api_socket() -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    if not DEFAULT_SOCKET_PATH.exists():
        raise RuntimeError(
            f"nanobot API socket not found at {DEFAULT_SOCKET_PATH}. "
            "Enable channels.api and start `nanobot gateway`."
        )
    try:
        return await asyncio.open_unix_connection(path=str(DEFAULT_SOCKET_PATH))
    except OSError as exc:
        raise RuntimeError(f"failed to connect to nanobot API socket: {exc}") from exc


async def _send_and_wait(
    method: str,
    params: dict[str, Any],
    *,
    timeout_seconds: float,
    request_id: str,
) -> Any:
    reader, writer = await _open_api_socket()
    try:
        writer.write(_encode(_jsonrpc_request(request_id, method, params)))
        await writer.drain()

        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout_seconds

        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise RuntimeError(f"timed out waiting for JSON-RPC response to {method}")

            line = await asyncio.wait_for(reader.readline(), timeout=remaining)
            if not line:
                raise RuntimeError("nanobot API socket closed before tool response")

            try:
                msg = json.loads(line.decode("utf-8", errors="replace"))
            except json.JSONDecodeError:
                continue
            if not isinstance(msg, dict):
                continue
            if msg.get("jsonrpc") != _JSONRPC_VERSION:
                continue
            if "method" in msg:
                continue
            if str(msg.get("id", "")).strip() != request_id:
                continue
            if "error" in msg:
                error = msg.get("error", {})
                if isinstance(error, dict):
                    raise RuntimeError(str(error.get("message", "unknown api error")))
                raise RuntimeError(str(error))
            return msg.get("result")
    finally:
        writer.close()
        await writer.wait_closed()


def _register_tools(server: Any) -> None:
    @server.tool()
    async def render_card(
        template_key: str,
        template_state: dict[str, Any] | str,
        title: str = "",
        chat_id: str = DEFAULT_CHAT_ID,
        slot: str = "",
        lane: str = "context",
        priority: int = 50,
        context_summary: str = "",
    ) -> str:
        """Upsert a saved template card into the web UI."""
        safe_template_key = _truncate(template_key, MAX_TEMPLATE_KEY_CHARS)
        state_payload = _coerce_template_state(template_state)
        await asyncio.to_thread(_validate_template_state, safe_template_key, state_payload)

        header = _truncate(title, MAX_TITLE_CHARS)

        payload = {
            "kind": "text",
            "title": header,
            "chat_id": _normalize_chat_id(chat_id),
            "slot": _truncate(slot, MAX_SLOT_CHARS),
            "lane": _truncate(lane, 32) or "context",
            "priority": int(priority),
            "template_key": safe_template_key,
            "template_state": state_payload,
            "context_summary": _truncate(context_summary, MAX_CONTEXT_SUMMARY_CHARS),
        }
        request_id = str(uuid.uuid4())
        result = await _send_and_wait(
            "card.upsert",
            payload,
            timeout_seconds=15.0,
            request_id=request_id,
        )
        if not isinstance(result, dict):
            raise RuntimeError("card.upsert returned an invalid response payload")
        card_id = str(result.get("card_id", "")).strip()
        if not card_id:
            raise RuntimeError("card.upsert did not return a card_id")
        return f"Card {card_id} upserted to chat_id={payload['chat_id']}"

    @server.tool()
    async def validate_card_state(
        template_key: str,
        template_state: dict[str, Any] | str,
    ) -> str:
        """Validate a template key and state payload before rendering a card."""
        safe_template_key = _truncate(template_key, MAX_TEMPLATE_KEY_CHARS)
        state_payload = _coerce_template_state(template_state)
        await asyncio.to_thread(_validate_template_state, safe_template_key, state_payload)
        return f"template_state for {safe_template_key} is valid"

    @server.tool()
    async def ask_user(
        question: str,
        choices: list[str],
        title: str = "",
        chat_id: str = DEFAULT_CHAT_ID,
        slot: str = "",
        lane: str = "attention",
        priority: int = 90,
        template_key: str = "",
        context_summary: str = "",
        timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    ) -> str:
        """Create a question card in the web UI and wait for the user's response."""
        prompt = str(question or "").strip()
        if not prompt:
            raise RuntimeError("question is required")
        if len(prompt) > MAX_QUESTION_CHARS:
            raise RuntimeError(
                f"question too large ({len(prompt)} > {MAX_QUESTION_CHARS} characters)"
            )

        normalized_choices = _normalize_choices(choices)
        header = _truncate(title, MAX_TITLE_CHARS)

        request_id = str(uuid.uuid4())
        timeout = _clamp_timeout(timeout_seconds)
        payload = {
            "question": prompt,
            "choices": normalized_choices,
            "title": header,
            "chat_id": _normalize_chat_id(chat_id),
            "slot": _truncate(slot, MAX_SLOT_CHARS),
            "lane": _truncate(lane, 32) or "attention",
            "priority": int(priority),
            "template_key": _truncate(template_key, MAX_TEMPLATE_KEY_CHARS),
            "context_summary": _truncate(context_summary, MAX_CONTEXT_SUMMARY_CHARS),
            "timeout": timeout,
        }
        result = await _send_and_wait(
            "card.ask",
            payload,
            timeout_seconds=timeout + 5.0,
            request_id=request_id,
        )
        if not isinstance(result, dict):
            raise RuntimeError("card.ask returned an invalid response payload")
        value = str(result.get("value", "")).strip()
        if not value:
            raise RuntimeError("ask_user returned an empty response")
        return value


def main() -> None:
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as exc:
        raise RuntimeError("Missing dependency 'mcp'. Install project dependencies first.") from exc

    server = FastMCP("display")
    _register_tools(server)
    server.run()


if __name__ == "__main__":
    main()
