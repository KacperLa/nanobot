"""Create linked helper cards that reduce friction on actionable tasks."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

from nanobot.agent.tools.base import Tool


class TaskHelperCardTool(Tool):
    """Create linked helper cards for watch/read/travel/shopping/outreach tasks."""

    def __init__(
        self,
        *,
        workspace: Path,
        cards_root: Path | None = None,
        web_chat_id: str = "web",
    ) -> None:
        self.workspace = workspace
        self.tasks_root = workspace / "tasks"
        self.cards_root = cards_root or (Path.home() / ".nanobot" / "cards")
        self.web_chat_id = web_chat_id
        self._task_helper_cards_module = None

    @property
    def name(self) -> str:
        return "task_helper_card"

    @property
    def description(self) -> str:
        return (
            "Create or sync linked helper cards for actionable tasks. "
            "Use this when a task can be made easier immediately with a prepared artifact. "
            "Typical patterns are watch/learn -> watch card, read/research -> reading card, "
            "go somewhere -> travel card, buy/order -> shopping card, and call/email/reach out -> outreach draft card. "
            "If you only know the task path, action=augment can infer the helper kind and create a useful fallback card. "
            "If you have better search results, pass a primary resource and alternatives to enrich it. "
            "Use action=update_draft to persist an edited outreach draft by card_id."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["augment", "sync", "remove", "update_draft"],
                    "description": "Helper-card action to perform.",
                },
                "task": {
                    "type": "string",
                    "description": "Task file path for augment or remove.",
                },
                "card_id": {
                    "type": "string",
                    "description": "Helper card ID for update_draft.",
                },
                "kind": {
                    "type": "string",
                    "enum": ["watch", "read", "travel", "shopping", "outreach"],
                    "description": "Optional helper kind. If omitted for augment, it will be inferred from the task.",
                },
                "title": {
                    "type": "string",
                    "description": "Optional helper-card title override.",
                },
                "summary": {
                    "type": "string",
                    "description": "Optional short summary shown near the top of the helper card.",
                },
                "query": {
                    "type": "string",
                    "description": "Optional search or destination query to preserve on the card.",
                },
                "primary_title": {
                    "type": "string",
                    "description": "Optional main resource title.",
                },
                "primary_url": {
                    "type": "string",
                    "description": "Optional main resource URL. A YouTube URL will be embedded automatically on watch cards.",
                },
                "primary_subtitle": {
                    "type": "string",
                    "description": "Optional main resource subtitle.",
                },
                "primary_meta": {
                    "type": "string",
                    "description": "Optional main resource meta line, such as channel, duration, price, or route note.",
                },
                "alternatives": {
                    "type": "array",
                    "description": "Optional alternative resources to show below the main one.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "title": {"type": "string"},
                            "url": {"type": "string"},
                            "subtitle": {"type": "string"},
                            "meta": {"type": "string"},
                        },
                    },
                },
                "notes": {
                    "type": "string",
                    "description": "Optional notes block for read/watch/travel/shopping helper cards.",
                },
                "recipient": {
                    "type": "string",
                    "description": "Optional recipient for outreach cards.",
                },
                "channel": {
                    "type": "string",
                    "description": "Optional outreach channel, such as email, text, or call.",
                },
                "subject": {
                    "type": "string",
                    "description": "Optional outreach subject line.",
                },
                "draft": {
                    "type": "string",
                    "description": "Optional outreach draft body.",
                },
            },
            "required": ["action"],
        }

    def _load_script_module(self):
        if self._task_helper_cards_module is not None:
            return self._task_helper_cards_module

        repo_root = Path(__file__).resolve().parents[3]
        module_path = repo_root / "scripts" / "task_helper_cards.py"
        if not module_path.exists():
            raise RuntimeError(f"missing helper script: {module_path}")
        spec = importlib.util.spec_from_file_location("task_helper_card_tool_module", module_path)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"failed to load helper script: {module_path}")
        module = importlib.util.module_from_spec(spec)
        sys.modules.setdefault("task_helper_card_tool_module", module)
        spec.loader.exec_module(module)
        self._task_helper_cards_module = module
        return module

    @property
    def _task_helper_cards(self):
        module = self._load_script_module()
        module.task_board.ensure_board(self.tasks_root)
        module.card_board.ensure_cards_root(self.cards_root)
        return module

    async def execute(
        self,
        action: str,
        task: str = "",
        card_id: str = "",
        kind: str = "",
        title: str = "",
        summary: str = "",
        query: str = "",
        primary_title: str = "",
        primary_url: str = "",
        primary_subtitle: str = "",
        primary_meta: str = "",
        alternatives: list[dict[str, Any]] | None = None,
        notes: str = "",
        recipient: str = "",
        channel: str = "",
        subject: str = "",
        draft: str = "",
        **kwargs: Any,
    ) -> str:
        if action == "sync":
            result = self._task_helper_cards.sync_helper_cards(
                tasks_root=self.tasks_root,
                cards_root=self.cards_root,
                chat_id=self.web_chat_id,
            )
            return json.dumps(result, ensure_ascii=False, indent=2)

        if action == "remove":
            if not task.strip():
                return json.dumps({"error": "task is required for remove"}, ensure_ascii=False)
            result = self._task_helper_cards.remove_helper_card(
                task_path=task.strip(),
                tasks_root=self.tasks_root,
                cards_root=self.cards_root,
                kind=kind.strip(),
            )
            return json.dumps(result, ensure_ascii=False, indent=2)

        if action == "update_draft":
            if not card_id.strip():
                return json.dumps({"error": "card_id is required for update_draft"}, ensure_ascii=False)
            result = self._task_helper_cards.update_helper_card_draft(
                card_id=card_id.strip(),
                cards_root=self.cards_root,
                draft=draft,
            )
            return json.dumps(result, ensure_ascii=False, indent=2)

        if action == "augment":
            if not task.strip():
                return json.dumps({"error": "task is required for augment"}, ensure_ascii=False)
            result = self._task_helper_cards.upsert_helper_card(
                task_path=task.strip(),
                tasks_root=self.tasks_root,
                cards_root=self.cards_root,
                kind=kind.strip(),
                title=title,
                summary=summary,
                query=query,
                primary={
                    "title": primary_title,
                    "url": primary_url,
                    "subtitle": primary_subtitle,
                    "meta": primary_meta,
                },
                alternatives=alternatives or [],
                notes=notes,
                recipient=recipient,
                channel=channel,
                subject=subject,
                draft=draft,
                chat_id=self.web_chat_id,
            )
            return json.dumps(result, ensure_ascii=False, indent=2)

        return json.dumps({"error": f"unknown action: {action}"}, ensure_ascii=False)
