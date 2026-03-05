"""MCP client: connects to MCP servers and wraps their tools as native nanobot tools."""

import asyncio
import hashlib
import re
from contextlib import AsyncExitStack
from typing import Any

import httpx
from loguru import logger

from nanobot.agent.tools.base import Tool
from nanobot.agent.tools.registry import ToolRegistry


_INVALID_TOOL_NAME_CHARS = re.compile(r"[^a-zA-Z0-9_-]+")


def _sanitize_tool_name_part(value: Any, *, fallback: str) -> str:
    raw = str(value or "")
    sanitized = _INVALID_TOOL_NAME_CHARS.sub("_", raw).strip("_")
    return sanitized or fallback


def _mcp_public_tool_name(server_name: Any, tool_name: Any) -> str:
    safe_server = _sanitize_tool_name_part(server_name, fallback="server")
    safe_tool = _sanitize_tool_name_part(tool_name, fallback="tool")
    return f"mcp_{safe_server}_{safe_tool}"


def _ensure_unique_name(candidate: str, taken: set[str], uniqueness_key: str) -> str:
    if candidate not in taken:
        return candidate
    digest = hashlib.sha1(uniqueness_key.encode("utf-8")).hexdigest()[:8]
    with_hash = f"{candidate}_{digest}"
    if with_hash not in taken:
        return with_hash
    suffix = 2
    while f"{with_hash}_{suffix}" in taken:
        suffix += 1
    return f"{with_hash}_{suffix}"


class MCPToolWrapper(Tool):
    """Wraps a single MCP server tool as a nanobot Tool."""

    def __init__(self, session, server_name: str, tool_def, tool_timeout: int = 30):
        self._session = session
        self._original_name = tool_def.name
        self._name = _mcp_public_tool_name(server_name, tool_def.name)
        self._description = tool_def.description or str(tool_def.name)
        self._parameters = tool_def.inputSchema or {"type": "object", "properties": {}}
        self._tool_timeout = tool_timeout

    @property
    def name(self) -> str:
        return self._name

    def set_public_name(self, name: str) -> None:
        self._name = name

    @property
    def description(self) -> str:
        return self._description

    @property
    def parameters(self) -> dict[str, Any]:
        return self._parameters

    async def execute(self, **kwargs: Any) -> str:
        from mcp import types
        try:
            result = await asyncio.wait_for(
                self._session.call_tool(self._original_name, arguments=kwargs),
                timeout=self._tool_timeout,
            )
        except asyncio.TimeoutError:
            logger.warning("MCP tool '{}' timed out after {}s", self._name, self._tool_timeout)
            return f"(MCP tool call timed out after {self._tool_timeout}s)"
        parts = []
        for block in result.content:
            if isinstance(block, types.TextContent):
                parts.append(block.text)
            else:
                parts.append(str(block))
        return "\n".join(parts) or "(no output)"


async def connect_mcp_servers(
    mcp_servers: dict, registry: ToolRegistry, stack: AsyncExitStack
) -> None:
    """Connect to configured MCP servers and register their tools."""
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    taken_names = set(registry.tool_names)

    for name, cfg in mcp_servers.items():
        try:
            if cfg.command:
                params = StdioServerParameters(
                    command=cfg.command, args=cfg.args, env=cfg.env or None
                )
                read, write = await stack.enter_async_context(stdio_client(params))
            elif cfg.url:
                from mcp.client.streamable_http import streamable_http_client
                # Always provide an explicit httpx client so MCP HTTP transport does not
                # inherit httpx's default 5s timeout and preempt the higher-level tool timeout.
                http_client = await stack.enter_async_context(
                    httpx.AsyncClient(
                        headers=cfg.headers or None,
                        follow_redirects=True,
                        timeout=None,
                    )
                )
                read, write, _ = await stack.enter_async_context(
                    streamable_http_client(cfg.url, http_client=http_client)
                )
            else:
                logger.warning("MCP server '{}': no command or url configured, skipping", name)
                continue

            session = await stack.enter_async_context(ClientSession(read, write))
            await session.initialize()

            tools = await session.list_tools()
            for tool_def in tools.tools:
                raw_public_name = f"mcp_{name}_{tool_def.name}"
                wrapper = MCPToolWrapper(session, name, tool_def, tool_timeout=cfg.tool_timeout)
                if wrapper.name != raw_public_name:
                    logger.debug(
                        "MCP: sanitized tool name '{}' to '{}' for provider compatibility",
                        raw_public_name,
                        wrapper.name,
                    )
                unique_name = _ensure_unique_name(
                    wrapper.name,
                    taken_names,
                    f"{name}:{tool_def.name}",
                )
                if unique_name != wrapper.name:
                    logger.warning(
                        "MCP: tool name collision for '{}' on server '{}'; renamed to '{}'",
                        tool_def.name,
                        name,
                        unique_name,
                    )
                    wrapper.set_public_name(unique_name)
                taken_names.add(wrapper.name)
                registry.register(wrapper)
                logger.debug("MCP: registered tool '{}' from server '{}'", wrapper.name, name)

            logger.info("MCP server '{}': connected, {} tools registered", name, len(tools.tools))
        except Exception as e:
            logger.error("MCP server '{}': failed to connect: {}", name, e)
