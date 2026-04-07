"""Inbox board tool for low-friction Life OS capture."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

from nanobot.agent.tools.base import Tool


class InboxBoardTool(Tool):
    """Capture ambiguous or passive items before promoting them to tasks."""

    def __init__(
        self,
        *,
        workspace: Path,
        cards_root: Path | None = None,
        task_template_key: str = "todo-item-live",
        web_chat_id: str = "web",
    ) -> None:
        self.workspace = workspace
        self.inbox_root = workspace / "inbox"
        self.tasks_root = workspace / "tasks"
        self.cards_root = cards_root or (Path.home() / ".nanobot" / "cards")
        self.task_template_key = task_template_key
        self.web_chat_id = web_chat_id
        self._inbox_board_module = None
        self._task_cards_module = None
        self._task_board_module = None

    @property
    def name(self) -> str:
        return "inbox_board"

    @property
    def description(self) -> str:
        return (
            "Manage the low-friction inbox capture layer in workspace/inbox. "
            "Use this for vague reminders, things to remember later, passive listening distilled items, "
            "ideas, notes, and possible tasks that should be captured first and organized later. "
            "List or show inbox items, update them, dismiss them, or accept them into the task board "
            "once they are clearly actionable."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["capture", "list", "show", "update", "accept_task", "dismiss"],
                    "description": "Inbox action to perform.",
                },
                "title": {
                    "type": "string",
                    "description": "Inbox title or distilled summary for capture or update.",
                },
                "item": {
                    "type": "string",
                    "description": "Inbox item path or filename for show, update, accept_task, or dismiss.",
                },
                "kind": {
                    "type": "string",
                    "enum": ["task", "reminder", "note", "idea", "event_prep", "unknown"],
                    "description": "Inbox item kind.",
                },
                "status": {
                    "type": "string",
                    "enum": ["new", "triaged", "accepted", "dismissed", "merged"],
                    "description": "Replacement inbox status for update.",
                },
                "source": {
                    "type": "string",
                    "description": "Capture source such as agent, user, or passive-listen.",
                },
                "confidence": {
                    "type": "number",
                    "minimum": 0,
                    "maximum": 1,
                    "description": "Optional confidence score between 0 and 1.",
                },
                "due": {
                    "type": "string",
                    "description": "Optional suggested due date for capture/update or due override for accept_task.",
                },
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional hashtag tags such as #japan.",
                },
                "body": {
                    "type": "string",
                    "description": "Optional distilled notes/body for capture, update, or accept_task.",
                },
                "description": {
                    "type": "string",
                    "description": "Optional alias for body.",
                },
                "raw_text": {
                    "type": "string",
                    "description": "Optional raw capture text or transcript snippet to preserve under the inbox item.",
                },
                "session_id": {
                    "type": "string",
                    "description": "Optional linked chat/session id for traceability.",
                },
                "lane": {
                    "type": "string",
                    "enum": ["backlog", "committed", "in-progress", "blocked", "done", "canceled"],
                    "description": "Destination task lane for accept_task. Defaults to backlog.",
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 100,
                    "description": "Optional maximum number of inbox items to return for list.",
                },
                "include_closed": {
                    "type": "boolean",
                    "description": "Include accepted, dismissed, and merged items in list output.",
                },
            },
            "required": ["action"],
        }

    def _load_script_module(self, module_name: str, relative_path: str):
        cached = getattr(self, f"_{module_name}_module", None)
        if cached is not None:
            return cached

        repo_root = Path(__file__).resolve().parents[3]
        module_path = repo_root / "scripts" / relative_path
        if not module_path.exists():
            raise RuntimeError(f"missing helper script: {module_path}")
        spec = importlib.util.spec_from_file_location(module_name, module_path)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"failed to load helper script: {module_path}")
        module = importlib.util.module_from_spec(spec)
        sys.modules.setdefault(module_name, module)
        spec.loader.exec_module(module)
        setattr(self, f"_{module_name}_module", module)
        return module

    @property
    def _inbox_board(self):
        module = self._load_script_module("inbox_board_tool_module", "inbox_board.py")
        module.ensure_inbox(self.inbox_root)
        return module

    @property
    def _task_board(self):
        module = self._load_script_module("task_board_from_inbox_tool_module", "task_board.py")
        module.ensure_board(self.tasks_root)
        return module

    @property
    def _task_cards(self):
        return self._load_script_module("task_cards_from_inbox_tool_module", "task_cards.py")

    async def execute(
        self,
        action: str,
        title: str = "",
        item: str = "",
        kind: str = "",
        status: str = "",
        source: str = "",
        confidence: float | None = None,
        due: str | None = None,
        tags: list[str] | None = None,
        body: str | None = None,
        description: str | None = None,
        raw_text: str = "",
        session_id: str = "",
        lane: str = "",
        limit: int | None = None,
        include_closed: bool = False,
        **kwargs: Any,
    ) -> str:
        body_value = body if body is not None else description
        body_text = body_value if isinstance(body_value, str) else ""
        if action == "capture":
            return self._capture_item(
                title,
                kind or "unknown",
                source or "agent",
                confidence,
                due,
                tags or [],
                body_text,
                raw_text,
                session_id,
            )
        if action == "list":
            return self._list_items(status, kind, tags or [], limit, include_closed)
        if action == "show":
            return self._show_item(item)
        if action == "update":
            return self._update_item(item, title, body_text if body_value is not None else None, kind, status, source, confidence, due, tags)
        if action == "accept_task":
            return self._accept_task(item, lane, title, body_text if body_value is not None else None, due, tags)
        if action == "dismiss":
            return self._dismiss_item(item)
        return json.dumps({"error": f"unknown action: {action}"}, ensure_ascii=False)

    def _capture_item(
        self,
        title: str,
        kind: str,
        source: str,
        confidence: float | None,
        due: str | None,
        tags: list[str],
        body: str,
        raw_text: str,
        session_id: str,
    ) -> str:
        path = self._inbox_board.create_item(
            self.inbox_root,
            title=title.strip(),
            kind=kind,
            source=source.strip() or "agent",
            confidence=confidence,
            suggested_due=(due or "").strip(),
            tags=self._task_board.normalize_tags([str(tag).strip() for tag in tags]),
            body=body.strip(),
            raw_text=raw_text.strip(),
            metadata={"source_session": session_id.strip()} if session_id.strip() else None,
        )
        return json.dumps(
            {"item": self._inbox_board.parse_item(path).to_dict()},
            ensure_ascii=False,
            indent=2,
        )

    def _list_items(
        self,
        status: str,
        kind: str,
        tags: list[str],
        limit: int | None,
        include_closed: bool,
    ) -> str:
        items = self._inbox_board.filter_items(
            self._inbox_board.collect_items(self.inbox_root),
            status=status.strip() or None,
            kind=kind.strip() or None,
            tags=self._task_board.normalize_tags(tags),
            include_closed=include_closed,
        )
        if limit is not None:
            items = items[:limit]
        return json.dumps([item.to_dict() for item in items], ensure_ascii=False, indent=2)

    def _show_item(self, item: str) -> str:
        cleaned_item = item.strip()
        if not cleaned_item:
            return json.dumps({"error": "item is required for show"}, ensure_ascii=False)
        parsed = self._inbox_board.parse_item(
            self._inbox_board.resolve_item_path(self.inbox_root, cleaned_item)
        )
        return json.dumps(parsed.to_dict(), ensure_ascii=False, indent=2)

    def _update_item(
        self,
        item: str,
        title: str,
        body: str | None,
        kind: str,
        status: str,
        source: str,
        confidence: float | None,
        due: str,
        tags: list[str] | None,
    ) -> str:
        cleaned_item = item.strip()
        if not cleaned_item:
            return json.dumps({"error": "item is required for update"}, ensure_ascii=False)
        cleaned_due = None if due is None else due.strip()
        updated = self._inbox_board.update_item(
            self.inbox_root,
            cleaned_item,
            title=title.strip() if title.strip() else None,
            body=body.rstrip() if isinstance(body, str) else None,
            kind=kind.strip() or None,
            status=status.strip() or None,
            source=source.strip() or None,
            confidence=confidence,
            confidence_provided=confidence is not None,
            suggested_due=cleaned_due,
            tags=None if tags is None else self._task_board.normalize_tags(tags),
        )
        return json.dumps(
            {"item": self._inbox_board.parse_item(updated).to_dict()},
            ensure_ascii=False,
            indent=2,
        )

    def _accept_task(
        self,
        item: str,
        lane: str,
        title: str,
        body: str | None,
        due: str | None,
        tags: list[str] | None,
    ) -> str:
        cleaned_item = item.strip()
        if not cleaned_item:
            return json.dumps({"error": "item is required for accept_task"}, ensure_ascii=False)
        item_path, task_path = self._inbox_board.accept_item_as_task(
            self.inbox_root,
            cleaned_item,
            tasks_root=self.tasks_root,
            lane=lane.strip() or "backlog",
            title=title.strip(),
            body=None if body is None else body.rstrip(),
            due=(due or "").strip(),
            tags=None if tags is None else self._task_board.normalize_tags(tags),
        )
        sync_result = self._task_cards.sync_task_cards(
            tasks_root=self.tasks_root,
            instances_dir=self.cards_root / "instances",
            template_key=self.task_template_key,
            chat_id=self.web_chat_id,
            prune_legacy=True,
        )
        return json.dumps(
            {
                "item": self._inbox_board.parse_item(item_path).to_dict(),
                "task": self._task_board.parse_task(task_path).to_dict(),
                "sync": sync_result,
            },
            ensure_ascii=False,
            indent=2,
        )

    def _dismiss_item(self, item: str) -> str:
        cleaned_item = item.strip()
        if not cleaned_item:
            return json.dumps({"error": "item is required for dismiss"}, ensure_ascii=False)
        updated = self._inbox_board.dismiss_item(self.inbox_root, cleaned_item)
        return json.dumps(
            {"item": self._inbox_board.parse_item(updated).to_dict()},
            ensure_ascii=False,
            indent=2,
        )
