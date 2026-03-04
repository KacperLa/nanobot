"""Display tools — push toast notifications to connected web-UI clients.

These tools let the agent surface text or image content as dismissable
overlay cards in the browser without interrupting the conversation flow.

They require a reference to the running :class:`~nanobot.channels.api.ApiChannel`
so they can call :meth:`~nanobot.channels.api.ApiChannel.push_toast` directly.
The channel reference is injected at construction time (same pattern as
:class:`~nanobot.agent.tools.message.MessageTool`).

Wire message produced (nanobot → webui client)::

    {"type": "toast", "kind": "text"|"image",
     "content": "...", "title": "...", "duration_ms": 5000, "chat_id": "web"}
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from nanobot.agent.tools.base import Tool

if TYPE_CHECKING:
    from nanobot.channels.api import ApiChannel


class ShowTextTool(Tool):
    """Push a text toast notification to the web UI."""

    def __init__(self, api_channel: "ApiChannel") -> None:
        self._api = api_channel
        self._chat_id: str = "web"

    def set_chat_id(self, chat_id: str) -> None:
        """Update routing chat_id for the next call."""
        self._chat_id = chat_id

    @property
    def name(self) -> str:
        return "show_text"

    @property
    def description(self) -> str:
        return (
            "Display a short text message as a dismissable toast notification "
            "in the web UI. Use this to surface summaries, alerts, or any "
            "text you want the user to notice immediately."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": "The text body to display in the toast.",
                },
                "title": {
                    "type": "string",
                    "description": "Optional heading shown above the content.",
                },
                "duration_ms": {
                    "type": "integer",
                    "description": (
                        "How long (milliseconds) the toast stays on screen "
                        "before auto-dismissing.  Use 0 for sticky.  "
                        "Default: 6000."
                    ),
                },
            },
            "required": ["content"],
        }

    async def execute(
        self,
        content: str,
        title: str = "",
        duration_ms: int = 6000,
        **kwargs: Any,
    ) -> str:
        try:
            await self._api.push_toast(
                kind="text",
                content=content,
                title=title,
                duration_ms=duration_ms,
                chat_id=self._chat_id,
            )
            return f"Toast displayed: {content[:60]}{'…' if len(content) > 60 else ''}"
        except Exception as exc:
            return f"Error showing toast: {exc}"


class ShowImageTool(Tool):
    """Push an image toast notification to the web UI."""

    def __init__(self, api_channel: "ApiChannel") -> None:
        self._api = api_channel
        self._chat_id: str = "web"

    def set_chat_id(self, chat_id: str) -> None:
        """Update routing chat_id for the next call."""
        self._chat_id = chat_id

    @property
    def name(self) -> str:
        return "show_image"

    @property
    def description(self) -> str:
        return (
            "Display an image as a dismissable toast notification in the web UI. "
            "Pass either a public URL (https://…) or a base-64 data-URI "
            "(data:image/png;base64,…).  Use this to show charts, screenshots, "
            "or any visual content."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": (
                        "Image source: a public URL (https://…) or a "
                        "base-64 data-URI (data:image/…;base64,…)."
                    ),
                },
                "title": {
                    "type": "string",
                    "description": "Optional caption shown below the image.",
                },
                "duration_ms": {
                    "type": "integer",
                    "description": (
                        "How long (milliseconds) the toast stays on screen "
                        "before auto-dismissing.  Use 0 for sticky.  "
                        "Default: 10000."
                    ),
                },
            },
            "required": ["content"],
        }

    async def execute(
        self,
        content: str,
        title: str = "",
        duration_ms: int = 10000,
        **kwargs: Any,
    ) -> str:
        try:
            await self._api.push_toast(
                kind="image",
                content=content,
                title=title,
                duration_ms=duration_ms,
                chat_id=self._chat_id,
            )
            snippet = content[:80] + ("…" if len(content) > 80 else "")
            return f"Image toast displayed: {snippet}"
        except Exception as exc:
            return f"Error showing image toast: {exc}"
