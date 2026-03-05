"""Display tools — push toast notifications to connected web-UI clients.

These tools let the agent surface text or image content as dismissable
overlay cards in the browser without interrupting the conversation flow.

They require a reference to the running :class:`~nanobot.channels.api.ApiChannel`
so they can call :meth:`~nanobot.channels.api.ApiChannel.push_toast` directly.
The channel reference is injected at construction time (same pattern as
:class:`~nanobot.agent.tools.message.MessageTool`).

Wire messages produced (nanobot → webui client)::

    {"type": "toast",  "kind": "text"|"image",
     "content": "...", "title": "...", "duration_ms": 5000, "chat_id": "web"}

    {"type": "choice", "request_id": "<uuid>", "question": "...",
     "choices": ["Option A", "Option B"], "title": "...", "chat_id": "web"}

Wire message expected back (webui client → nanobot)::

    {"type": "ui-response", "request_id": "<uuid>", "value": "Option A"}
"""

from __future__ import annotations

import uuid
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
            "Display content as a dismissable card in the web UI. "
            "The content is rendered as HTML — you can pass rich HTML including "
            "tables, lists, styled text, images, SVG, or any valid HTML markup. "
            "Markdown is also supported and will be rendered automatically. "
            "Use this to show anything visual: tables, charts, formatted data, "
            "emojis, or content that should not be read aloud via TTS."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": (
                        "HTML or Markdown to render in the card. "
                        "HTML is rendered directly — use it for tables, "
                        "styled text, SVG, etc. Markdown is also accepted."
                    ),
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


class AskUserTool(Tool):
    """Ask the user a multiple-choice question via the web UI and wait for their answer.

    Sends a ``{"type": "choice", ...}`` message to the UI, then suspends until
    the UI returns a ``{"type": "ui-response", "request_id": "...", "value": "..."}``
    message.  The chosen value is returned as the tool result so the agent can
    act on it.
    """

    # Timeout in seconds before giving up waiting for a user response.
    TIMEOUT_S: float = 120.0

    def __init__(self, api_channel: "ApiChannel") -> None:
        self._api = api_channel
        self._chat_id: str = "web"

    def set_chat_id(self, chat_id: str) -> None:
        self._chat_id = chat_id

    @property
    def name(self) -> str:
        return "ask_user"

    @property
    def description(self) -> str:
        return (
            "Ask the user a multiple-choice question via the web UI and wait for "
            "their selection before continuing.  The UI renders a dismissable card "
            "with labelled buttons; the user's choice is returned as a string so "
            "you can branch on it.  Use this when you need explicit user input "
            "before proceeding — e.g. confirming a destructive action, picking "
            "between options, or asking a clarifying question."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "The question to ask the user.",
                },
                "choices": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of option labels to present as buttons (2–6 items).",
                    "minItems": 2,
                    "maxItems": 6,
                },
                "title": {
                    "type": "string",
                    "description": "Optional heading shown above the question.",
                },
            },
            "required": ["question", "choices"],
        }

    async def execute(
        self,
        question: str,
        choices: list[str],
        title: str = "",
        **kwargs: Any,
    ) -> str:
        request_id = str(uuid.uuid4())
        try:
            value = await self._api.push_choice(
                question=question,
                choices=choices,
                title=title,
                request_id=request_id,
                chat_id=self._chat_id,
                timeout=self.TIMEOUT_S,
            )
            return value
        except TimeoutError:
            return "(no response — user did not answer within the timeout)"
        except Exception as exc:
            return f"Error asking user: {exc}"
