"""Create session-scoped workbench items for temporary visual artifacts."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

from nanobot.agent.tools.base import Tool


class WorkbenchBoardTool(Tool):
    """Manage temporary session-scoped workbench items."""

    def __init__(self, *, workspace: Path) -> None:
        self.workspace = workspace
        self.workbench_root = workspace / "workbench"
        self._module = None

    @property
    def name(self) -> str:
        return "workbench_board"

    @property
    def description(self) -> str:
        return (
            "Create and manage session-scoped workbench items for temporary visual artifacts. "
            "Use this for scratch drafts, comparisons, shortlists, temporary maps, research canvases, "
            "and ad hoc visualizations that should appear in the chat-side workbench instead of the main feed. "
            "Promotable workbench items can later be added to the main feed by the user."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["upsert", "list", "show", "remove"],
                    "description": "Workbench action to perform.",
                },
                "chat_id": {
                    "type": "string",
                    "description": "Session chat_id for the workbench item.",
                },
                "item_id": {
                    "type": "string",
                    "description": "Optional item id for updates, show, or remove.",
                },
                "kind": {
                    "type": "string",
                    "enum": ["text", "question"],
                    "description": "Workbench item kind for upsert.",
                },
                "title": {
                    "type": "string",
                    "description": "Workbench item title.",
                },
                "content": {
                    "type": "string",
                    "description": "Workbench item body HTML or markdown.",
                },
                "question": {
                    "type": "string",
                    "description": "Optional question text for question items.",
                },
                "choices": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional choices for question items.",
                },
                "response_value": {
                    "type": "string",
                    "description": "Optional selected response value.",
                },
                "slot": {
                    "type": "string",
                    "description": "Optional stable slot for updating an existing workbench item.",
                },
                "template_key": {
                    "type": "string",
                    "description": "Optional template key for richer rendered content.",
                },
                "template_state": {
                    "type": "object",
                    "description": "Optional template state object.",
                },
                "context_summary": {
                    "type": "string",
                    "description": "Optional short summary used by the UI.",
                },
                "promotable": {
                    "type": "boolean",
                    "description": "Whether the UI should offer promotion to the main feed.",
                },
                "source_card_id": {
                    "type": "string",
                    "description": "Optional source card id if this workbench item came from a feed card.",
                },
            },
            "required": ["action"],
        }

    def _load_module(self):
        if self._module is not None:
            return self._module
        repo_root = Path(__file__).resolve().parents[3]
        module_path = repo_root / "scripts" / "workbench_board.py"
        if not module_path.exists():
            raise RuntimeError(f"missing helper script: {module_path}")
        spec = importlib.util.spec_from_file_location("workbench_board_tool_module", module_path)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"failed to load helper script: {module_path}")
        module = importlib.util.module_from_spec(spec)
        sys.modules.setdefault("workbench_board_tool_module", module)
        spec.loader.exec_module(module)
        self._module = module
        return module

    @property
    def _workbench_board(self):
        module = self._load_module()
        module.ensure_workbench(self.workbench_root)
        return module

    async def execute(
        self,
        action: str,
        chat_id: str = "",
        item_id: str = "",
        kind: str = "text",
        title: str = "",
        content: str = "",
        question: str = "",
        choices: list[str] | None = None,
        response_value: str = "",
        slot: str = "",
        template_key: str = "",
        template_state: dict[str, Any] | None = None,
        context_summary: str = "",
        promotable: bool = True,
        source_card_id: str = "",
        **kwargs: Any,
    ) -> str:
        if action == "list":
            if not chat_id.strip():
                return json.dumps({"error": "chat_id is required for list"}, ensure_ascii=False)
            return json.dumps(
                {"items": self._workbench_board.collect_items(self.workbench_root, chat_id.strip())},
                ensure_ascii=False,
                indent=2,
            )
        if action == "show":
            if not chat_id.strip():
                return json.dumps({"error": "chat_id is required for show"}, ensure_ascii=False)
            if not item_id.strip():
                return json.dumps({"error": "item_id is required for show"}, ensure_ascii=False)
            return json.dumps(
                {"item": self._workbench_board.load_item(self.workbench_root, chat_id.strip(), item_id.strip())},
                ensure_ascii=False,
                indent=2,
            )
        if action == "remove":
            if not chat_id.strip():
                return json.dumps({"error": "chat_id is required for remove"}, ensure_ascii=False)
            if not item_id.strip():
                return json.dumps({"error": "item_id is required for remove"}, ensure_ascii=False)
            return json.dumps(
                {"removed": self._workbench_board.delete_item(self.workbench_root, chat_id.strip(), item_id.strip())},
                ensure_ascii=False,
                indent=2,
            )
        if action == "upsert":
            if not chat_id.strip():
                return json.dumps({"error": "chat_id is required for upsert"}, ensure_ascii=False)
            item = self._workbench_board.upsert_item(
                self.workbench_root,
                chat_id=chat_id.strip(),
                item_id=item_id.strip(),
                kind=kind,
                title=title,
                content=content,
                question=question,
                choices=[str(choice) for choice in (choices or [])],
                response_value=response_value,
                slot=slot,
                template_key=template_key,
                template_state=template_state or {},
                context_summary=context_summary,
                promotable=promotable,
                source_card_id=source_card_id,
            )
            return json.dumps({"item": item}, ensure_ascii=False, indent=2)
        return json.dumps({"error": f"unknown action: {action}"}, ensure_ascii=False)
