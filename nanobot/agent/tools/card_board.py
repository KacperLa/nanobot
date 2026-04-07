"""Card board tool for file-backed web UI cards."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

from nanobot.agent.tools.base import Tool


class CardBoardTool(Tool):
    """Inspect and update file-backed web UI cards by card ID."""

    def __init__(self, *, cards_root: Path | None = None) -> None:
        self.cards_root = cards_root or (Path.home() / ".nanobot" / "cards")
        self._card_board_module = None

    @property
    def name(self) -> str:
        return "card_board"

    @property
    def description(self) -> str:
        return (
            "Inspect and update file-backed web UI cards by card_id. "
            "Use this when runtime metadata already includes a card_id from the UI. "
            "Prefer this over reading card template files or curling localhost. "
            "Useful for showing an attached card, clearing editable card content such as the calorie tracker, "
            "updating template_state, or marking a card active/stale/resolved/superseded/archived."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["show", "list", "set_state", "update_template_state", "replace_template_state", "clear_content"],
                    "description": "Card-board action to perform.",
                },
                "card_id": {
                    "type": "string",
                    "description": "Card ID from attached runtime metadata.",
                },
                "chat_id": {
                    "type": "string",
                    "description": "Optional chat ID filter for list.",
                },
                "state": {
                    "type": "string",
                    "enum": ["active", "stale", "resolved", "superseded", "archived"],
                    "description": "Card lifecycle state for set_state.",
                },
                "template_state": {
                    "type": "object",
                    "description": "Template state object for update or replace actions.",
                },
            },
            "required": ["action"],
        }

    def _load_script_module(self):
        if self._card_board_module is not None:
            return self._card_board_module

        repo_root = Path(__file__).resolve().parents[3]
        module_path = repo_root / "scripts" / "card_board.py"
        if not module_path.exists():
            raise RuntimeError(f"missing helper script: {module_path}")
        spec = importlib.util.spec_from_file_location("card_board_tool_module", module_path)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"failed to load helper script: {module_path}")
        module = importlib.util.module_from_spec(spec)
        sys.modules.setdefault("card_board_tool_module", module)
        spec.loader.exec_module(module)
        self._card_board_module = module
        return module

    @property
    def _card_board(self):
        module = self._load_script_module()
        module.ensure_cards_root(self.cards_root)
        return module

    async def execute(
        self,
        action: str,
        card_id: str = "",
        chat_id: str = "",
        state: str = "",
        template_state: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> str:
        if action == "list":
            cards = self._card_board.collect_cards(self.cards_root, chat_id=chat_id.strip())
            return json.dumps(cards, ensure_ascii=False, indent=2)
        if action == "show":
            return self._show(card_id)
        if action == "set_state":
            return self._set_state(card_id, state)
        if action == "update_template_state":
            return self._update_template_state(card_id, template_state or {})
        if action == "replace_template_state":
            return self._replace_template_state(card_id, template_state or {})
        if action == "clear_content":
            return self._clear_content(card_id)
        return json.dumps({"error": f"unknown action: {action}"}, ensure_ascii=False)

    def _show(self, card_id: str) -> str:
        card = self._card_board.load_card(self.cards_root, card_id.strip())
        return json.dumps({"card": card}, ensure_ascii=False, indent=2)

    def _set_state(self, card_id: str, state: str) -> str:
        target_state = state.strip().lower()
        if target_state not in {"active", "stale", "resolved", "superseded", "archived"}:
            return json.dumps({"error": "state is required and must be a valid card state"}, ensure_ascii=False)
        card = self._card_board.load_card(self.cards_root, card_id.strip())
        card["state"] = target_state
        card["updated_at"] = self._card_board._utc_now_iso()
        persisted = self._card_board.write_card(self.cards_root, card)
        return json.dumps({"card": persisted}, ensure_ascii=False, indent=2)

    def _update_template_state(self, card_id: str, patch: dict[str, Any]) -> str:
        card = self._card_board.load_card(self.cards_root, card_id.strip())
        if str(card.get("kind", "")) != "text":
            return json.dumps({"error": "only text cards support template_state"}, ensure_ascii=False)
        card = self._card_board.merge_template_state(card, patch)
        persisted = self._card_board.write_card(self.cards_root, card)
        return json.dumps({"card": persisted}, ensure_ascii=False, indent=2)

    def _replace_template_state(self, card_id: str, template_state: dict[str, Any]) -> str:
        card = self._card_board.load_card(self.cards_root, card_id.strip())
        if str(card.get("kind", "")) != "text":
            return json.dumps({"error": "only text cards support template_state"}, ensure_ascii=False)
        card = self._card_board.replace_template_state(card, template_state)
        persisted = self._card_board.write_card(self.cards_root, card)
        return json.dumps({"card": persisted}, ensure_ascii=False, indent=2)

    def _clear_content(self, card_id: str) -> str:
        card = self._card_board.load_card(self.cards_root, card_id.strip())
        if str(card.get("kind", "")) != "text":
            return json.dumps({"error": "only text cards can be cleared"}, ensure_ascii=False)
        card = self._card_board.clear_card_content(card)
        persisted = self._card_board.write_card(self.cards_root, card)
        return json.dumps({"card": persisted}, ensure_ascii=False, indent=2)
