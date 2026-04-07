"""Task board tool for the file-backed Life OS workflow."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

from nanobot.agent.tools.base import Tool


class TaskBoardTool(Tool):
    """Create, query, and organize file-backed tasks and tag context."""

    def __init__(
        self,
        *,
        workspace: Path,
        cards_root: Path | None = None,
        template_key: str = "todo-item-live",
        web_chat_id: str = "web",
    ) -> None:
        self.workspace = workspace
        self.tasks_root = workspace / "tasks"
        self.cards_root = cards_root or (Path.home() / ".nanobot" / "cards")
        self.template_key = template_key
        self.web_chat_id = web_chat_id
        self._task_board_module = None
        self._task_cards_module = None

    @property
    def name(self) -> str:
        return "task_board"

    @property
    def description(self) -> str:
        return (
            "Manage the file-backed kanban task board in the workspace. "
            "Use this to add things the user wants to remember, follow up on, or work on later, "
            "list or query tasks by Obsidian-style hashtag tags such as #japan, move tasks between backlog/committed/in-progress/blocked/done/canceled, "
            "edit an existing task's title or description, "
            "add or remove tags on existing tasks, "
            "create richer context folders for important tags under workspace/tags/<tag>/TAG.md, "
            "and sync active task cards into the web UI. "
            "Prefer this over cron unless the user explicitly asks for a scheduled or recurring reminder."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": [
                        "add",
                        "list",
                        "move",
                        "edit",
                        "sync",
                        "query",
                        "list_tags",
                        "ensure_tag",
                        "add_tag",
                        "remove_tag",
                    ],
                    "description": "Task-board action to perform.",
                },
                "title": {
                    "type": "string",
                    "description": "Task title for add or edit.",
                },
                "task": {
                    "type": "string",
                    "description": "Task file path or filename for move, edit, add_tag, or remove_tag.",
                },
                "lane": {
                    "type": "string",
                    "enum": ["backlog", "committed", "in-progress", "blocked", "done", "canceled"],
                    "description": "Task lane. For add, defaults to backlog. For move, this is the destination lane.",
                },
                "due": {
                    "type": "string",
                    "description": "Optional due date or datetime string for add.",
                },
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional tags for add, list filtering, or query. Prefer hashtag form like #japan.",
                },
                "tag": {
                    "type": "string",
                    "description": "Single tag for ensure_tag. Prefer hashtag form like #japan.",
                },
                "match": {
                    "type": "string",
                    "enum": ["any", "all"],
                    "description": "Whether list/query tag filters should match any or all requested tags. Defaults to any.",
                },
                "body": {
                    "type": "string",
                    "description": "Optional markdown notes for add, edit, or ensure_tag.",
                },
                "description": {
                    "type": "string",
                    "description": "Optional alias for the task markdown body when adding or editing a task.",
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 100,
                    "description": "Optional maximum number of tasks to return for list.",
                },
                "title_display": {
                    "type": "string",
                    "description": "Optional display title when creating a tag folder.",
                },
                "include_done": {
                    "type": "boolean",
                    "description": "Include done and canceled tasks in list or query output. Defaults to false.",
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
    def _task_board(self):
        module = self._load_script_module("task_board_tool_module", "task_board.py")
        module.ensure_board(self.tasks_root)
        return module

    @property
    def _task_cards(self):
        return self._load_script_module("task_cards_tool_module", "task_cards.py")

    async def execute(
        self,
        action: str,
        title: str = "",
        task: str = "",
        lane: str = "",
        due: str = "",
        tags: list[str] | None = None,
        tag: str = "",
        match: str = "any",
        body: str | None = None,
        description: str | None = None,
        limit: int | None = None,
        include_done: bool = False,
        title_display: str = "",
        **kwargs: Any,
    ) -> str:
        body_provided = body is not None or description is not None
        body_value = body if body is not None else description
        body_text = body_value.strip() if isinstance(body_value, str) else ""
        if action == "add":
            return self._add_task(title, lane, due, tags or [], body_text)
        if action == "list":
            return self._list_tasks(lane, tags or [], limit, include_done, match)
        if action == "move":
            return self._move_task(task, lane)
        if action == "edit":
            return self._edit_task(task, title, body_text if body_provided else None)
        if action == "sync":
            return self._sync_cards()
        if action == "query":
            return self._query_tasks(tags or [], lane, limit, include_done, match)
        if action == "list_tags":
            return self._list_tags(include_done)
        if action == "ensure_tag":
            return self._ensure_tag(tag, title_display, body)
        if action == "add_tag":
            return self._add_tags(task, tags or [])
        if action == "remove_tag":
            return self._remove_tags(task, tags or [])
        return json.dumps({"error": f"unknown action: {action}"}, ensure_ascii=False)

    def _add_task(
        self,
        title: str,
        lane: str,
        due: str,
        tags: list[str],
        body: str,
    ) -> str:
        cleaned_title = title.strip()
        if not cleaned_title:
            return json.dumps({"error": "title is required for add"}, ensure_ascii=False)

        target_lane = lane.strip() or "backlog"
        created_path = self._task_board.create_task(
            root=self.tasks_root,
            title=cleaned_title,
            lane=target_lane,
            due=due.strip(),
            tags=self._task_board.normalize_tags([str(tag).strip() for tag in tags]),
            body=body.strip(),
            metadata={"source": "agent"},
        )
        sync_result = self._task_cards.sync_task_cards(
            tasks_root=self.tasks_root,
            instances_dir=self.cards_root / "instances",
            template_key=self.template_key,
            chat_id=self.web_chat_id,
            prune_legacy=True,
        )
        task_payload = self._task_board.parse_task(created_path).to_dict()
        return json.dumps(
            {
                "task": task_payload,
                "sync": sync_result,
            },
            ensure_ascii=False,
            indent=2,
        )

    def _list_tasks(
        self,
        lane: str,
        tags: list[str],
        limit: int | None,
        include_done: bool,
        match: str,
    ) -> str:
        cleaned_lane = lane.strip() or None
        tasks = self._task_board.filter_tasks(
            self._task_board.collect_tasks(self.tasks_root, cleaned_lane),
            tags=tags,
            include_done=include_done,
            match=match,
        )
        if limit is not None:
            tasks = tasks[:limit]
        return json.dumps([task.to_dict() for task in tasks], ensure_ascii=False, indent=2)

    def _query_tasks(
        self,
        tags: list[str],
        lane: str,
        limit: int | None,
        include_done: bool,
        match: str,
    ) -> str:
        normalized_tags = self._task_board.normalize_tags(tags)
        if not normalized_tags:
            return json.dumps({"error": "at least one tag is required for query"}, ensure_ascii=False)
        result = self._task_board.query_tasks(
            self.tasks_root,
            tags=normalized_tags,
            lane=lane.strip() or None,
            include_done=include_done,
            match=match,
            limit=limit,
        )
        return json.dumps(result, ensure_ascii=False, indent=2)

    def _list_tags(self, include_done: bool) -> str:
        result = self._task_board.list_tag_summaries(
            self.tasks_root,
            include_done=include_done,
        )
        return json.dumps(result, ensure_ascii=False, indent=2)

    def _ensure_tag(self, tag: str, title_display: str, body: str) -> str:
        cleaned_tag = tag.strip()
        if not cleaned_tag:
            return json.dumps({"error": "tag is required for ensure_tag"}, ensure_ascii=False)
        tag_note = self._task_board.ensure_tag_folder(
            self.tasks_root,
            cleaned_tag,
            title=title_display.strip(),
            summary=body.strip(),
        )
        context = self._task_board.read_tag_context(self.tasks_root, cleaned_tag)
        return json.dumps(
            {
                "path": str(tag_note),
                "tag": context,
            },
            ensure_ascii=False,
            indent=2,
        )

    def _move_task(self, task: str, lane: str) -> str:
        cleaned_task = task.strip()
        cleaned_lane = lane.strip()
        if not cleaned_task:
            return json.dumps({"error": "task is required for move"}, ensure_ascii=False)
        if not cleaned_lane:
            return json.dumps({"error": "lane is required for move"}, ensure_ascii=False)

        result = self._task_cards.move_task_and_sync(
            cleaned_task,
            cleaned_lane,
            tasks_root=self.tasks_root,
            instances_dir=self.cards_root / "instances",
            template_key=self.template_key,
            chat_id=self.web_chat_id,
            prune_legacy=True,
        )
        return json.dumps(result, ensure_ascii=False, indent=2)

    def _edit_task(self, task: str, title: str, body: str | None) -> str:
        cleaned_task = task.strip()
        cleaned_title = title.strip()
        cleaned_body = body.rstrip() if isinstance(body, str) else None
        if not cleaned_task:
            return json.dumps({"error": "task is required for edit"}, ensure_ascii=False)
        if not cleaned_title and cleaned_body is None:
            return json.dumps(
                {"error": "at least one of title or description is required for edit"},
                ensure_ascii=False,
            )

        updated_path = self._task_board.edit_task(
            self.tasks_root,
            cleaned_task,
            title=cleaned_title if cleaned_title else None,
            body=cleaned_body,
        )
        sync_result = self._task_cards.sync_task_cards(
            tasks_root=self.tasks_root,
            instances_dir=self.cards_root / "instances",
            template_key=self.template_key,
            chat_id=self.web_chat_id,
            prune_legacy=True,
        )
        return json.dumps(
            {
                "task": self._task_board.parse_task(updated_path).to_dict(),
                "sync": sync_result,
            },
            ensure_ascii=False,
            indent=2,
        )

    def _add_tags(self, task: str, tags: list[str]) -> str:
        cleaned_task = task.strip()
        normalized_tags = self._task_board.normalize_tags(tags)
        if not cleaned_task:
            return json.dumps({"error": "task is required for add_tag"}, ensure_ascii=False)
        if not normalized_tags:
            return json.dumps({"error": "at least one tag is required for add_tag"}, ensure_ascii=False)

        updated_path = self._task_board.add_tags_to_task(
            self.tasks_root,
            cleaned_task,
            normalized_tags,
        )
        sync_result = self._task_cards.sync_task_cards(
            tasks_root=self.tasks_root,
            instances_dir=self.cards_root / "instances",
            template_key=self.template_key,
            chat_id=self.web_chat_id,
            prune_legacy=True,
        )
        return json.dumps(
            {
                "task": self._task_board.parse_task(updated_path).to_dict(),
                "sync": sync_result,
            },
            ensure_ascii=False,
            indent=2,
        )

    def _remove_tags(self, task: str, tags: list[str]) -> str:
        cleaned_task = task.strip()
        normalized_tags = self._task_board.normalize_tags(tags)
        if not cleaned_task:
            return json.dumps({"error": "task is required for remove_tag"}, ensure_ascii=False)
        if not normalized_tags:
            return json.dumps(
                {"error": "at least one tag is required for remove_tag"},
                ensure_ascii=False,
            )

        updated_path = self._task_board.remove_tags_from_task(
            self.tasks_root,
            cleaned_task,
            normalized_tags,
        )
        sync_result = self._task_cards.sync_task_cards(
            tasks_root=self.tasks_root,
            instances_dir=self.cards_root / "instances",
            template_key=self.template_key,
            chat_id=self.web_chat_id,
            prune_legacy=True,
        )
        return json.dumps(
            {
                "task": self._task_board.parse_task(updated_path).to_dict(),
                "sync": sync_result,
            },
            ensure_ascii=False,
            indent=2,
        )

    def _sync_cards(self) -> str:
        result = self._task_cards.sync_task_cards(
            tasks_root=self.tasks_root,
            instances_dir=self.cards_root / "instances",
            template_key=self.template_key,
            chat_id=self.web_chat_id,
            prune_legacy=True,
        )
        return json.dumps(result, ensure_ascii=False, indent=2)
