#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import task_board  # noqa: E402
import task_helper_cards  # noqa: E402


WORKSPACE_DIR = Path.home() / ".nanobot" / "workspace"
TASKS_DIR = WORKSPACE_DIR / "tasks"
CARDS_DIR = Path.home() / ".nanobot" / "cards"
INSTANCES_DIR = CARDS_DIR / "instances"
CONFIG_PATH = Path.home() / ".nanobot" / "config.json"
DEFAULT_TEMPLATE_KEY = "todo-item-live"
ACTIVE_TASK_LANES = {"backlog", "committed", "in-progress", "blocked"}
CARD_LANE_BY_TASK_LANE = {
    "backlog": "work",
    "committed": "attention",
    "in-progress": "attention",
    "blocked": "attention",
}
CARD_PRIORITY_BY_TASK_LANE = {
    "backlog": 72,
    "committed": 80,
    "in-progress": 84,
    "blocked": 90,
}


def _normalize_slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "task"


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _load_home_assistant_config(config_path: Path = CONFIG_PATH) -> tuple[str, dict[str, str]]:
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    servers = payload["tools"]["mcpServers"]
    server = None
    for key in ("home_assistant", "home assistant"):
        candidate = servers.get(key)
        if isinstance(candidate, dict):
            server = candidate
            break
    if server is None:
        raise RuntimeError("Home Assistant MCP server is not configured")
    url = str(server["url"]).strip()
    headers = {str(k): str(v) for k, v in dict(server.get("headers") or {}).items()}
    if not url.endswith("/api/mcp"):
        raise RuntimeError(f"unexpected Home Assistant MCP URL: {url}")
    return url[: -len("/api/mcp")] + "/api", headers


def _request_json(
    method: str,
    path: str,
    *,
    body: dict[str, Any] | None = None,
    config_path: Path = CONFIG_PATH,
) -> Any:
    api_base, headers = _load_home_assistant_config(config_path)
    url = f"{api_base}{path}"
    data = None
    request_headers = dict(headers)
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        request_headers.setdefault("Content-Type", "application/json")
    request = urllib.request.Request(url, data=data, headers=request_headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"Home Assistant {method} {path} failed with HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Home Assistant request failed: {exc}") from exc


def fetch_home_assistant_todos(
    entity_id: str,
    *,
    config_path: Path = CONFIG_PATH,
) -> dict[str, Any]:
    state = _request_json(
        "GET",
        f"/states/{urllib.parse.quote(entity_id, safe='')}",
        config_path=config_path,
    )
    attributes = state.get("attributes") if isinstance(state, dict) else {}
    list_name = str(attributes.get("friendly_name") or entity_id).strip() if isinstance(attributes, dict) else entity_id
    payload = _request_json(
        "POST",
        "/services/todo/get_items?return_response",
        body={"entity_id": entity_id},
        config_path=config_path,
    )
    if not isinstance(payload, dict):
        raise RuntimeError("invalid Home Assistant todo response")
    service_response = payload.get("service_response")
    if not isinstance(service_response, dict):
        raise RuntimeError("todo response missing service_response")
    entity_payload = service_response.get(entity_id)
    if not isinstance(entity_payload, dict):
        raise RuntimeError(f"todo response missing entity {entity_id}")
    items = entity_payload.get("items")
    if not isinstance(items, list):
        raise RuntimeError("todo response missing items list")
    normalized = [item for item in items if isinstance(item, dict)]
    generated_at = datetime.now(timezone.utc).isoformat()
    return {
        "entity_id": entity_id,
        "list_name": list_name,
        "generated_at": generated_at,
        "items": normalized,
    }


def _task_key(task: task_board.Task) -> str:
    source_uid = task.metadata.get("source_uid", "").strip()
    return source_uid or task.path.stem


def _card_id(task: task_board.Task) -> str:
    return f"task-{_normalize_slug(_task_key(task))}"


def _task_body_from_home_assistant(item: dict[str, Any], list_name: str) -> str:
    parts: list[str] = []
    description = item.get("description")
    if isinstance(description, str) and description.strip():
        parts.append(description.strip())
    parts.append("## Imported")
    parts.append("")
    parts.append(f"- Source: Home Assistant")
    parts.append(f"- List: {list_name}")
    uid = str(item.get("uid") or "").strip()
    if uid:
        parts.append(f"- UID: {uid}")
    return "\n".join(parts).strip()


def _existing_imports(tasks_root: Path, entity_id: str) -> set[str]:
    existing: set[str] = set()
    for task in task_board.collect_tasks(tasks_root):
        if task.metadata.get("source") != "home_assistant":
            continue
        if task.metadata.get("source_entity_id") != entity_id:
            continue
        source_uid = task.metadata.get("source_uid", "").strip()
        if source_uid:
            existing.add(source_uid)
    return existing


def import_home_assistant_tasks(
    entity_id: str,
    *,
    tasks_root: Path = TASKS_DIR,
    lane: str = "backlog",
    config_path: Path = CONFIG_PATH,
) -> dict[str, Any]:
    payload = fetch_home_assistant_todos(entity_id, config_path=config_path)
    list_name = str(payload.get("list_name") or entity_id)
    generated_at = str(payload.get("generated_at") or datetime.now(timezone.utc).isoformat())
    existing_uids = _existing_imports(tasks_root, entity_id)
    created_paths: list[str] = []
    skipped_existing: list[str] = []

    for item in payload.get("items", []):
        if not isinstance(item, dict):
            continue
        status = str(item.get("status") or "needs_action").strip()
        if status == "completed":
            continue
        uid = str(item.get("uid") or "").strip()
        if uid and uid in existing_uids:
            skipped_existing.append(uid)
            continue
        summary = str(item.get("summary") or "").strip() or uid or "Imported task"
        due_datetime = item.get("due_datetime")
        due_date = item.get("due")
        due = ""
        if isinstance(due_datetime, str) and due_datetime.strip():
            due = due_datetime.strip()
        elif isinstance(due_date, str) and due_date.strip():
            due = due_date.strip()
        tags = ["home-assistant", "imported"]
        body = _task_body_from_home_assistant(item, list_name)
        metadata = {
            "source": "home_assistant",
            "source_entity_id": entity_id,
            "source_list": list_name,
            "source_uid": uid,
            "imported_at": generated_at,
        }
        created_path = task_board.create_task(
            root=tasks_root,
            title=summary,
            lane=lane,
            due=due,
            tags=tags,
            body=body,
            metadata=metadata,
        )
        created_paths.append(str(created_path))

    return {
        "entity_id": entity_id,
        "list_name": list_name,
        "generated_at": generated_at,
        "created": created_paths,
        "skipped_existing": skipped_existing,
    }


def _state_payload(task: task_board.Task) -> dict[str, Any]:
    return {
        "kind": "file_task",
        "task_path": str(task.path),
        "task_key": _task_key(task),
        "title": task.title,
        "lane": task.lane,
        "created": task.created or None,
        "updated": task.updated or None,
        "due": task.due or None,
        "tags": list(task.tags),
        "body": task.body or None,
        "metadata": dict(task.metadata),
    }


def _card_payload(
    task: task_board.Task,
    *,
    template_key: str,
    chat_id: str,
    created_at: str,
    updated_at: str,
) -> dict[str, Any]:
    return {
        "id": _card_id(task),
        "kind": "text",
        "title": task.title,
        "slot": f"taskboard:{_task_key(task)}",
        "lane": CARD_LANE_BY_TASK_LANE.get(task.lane, "work"),
        "priority": CARD_PRIORITY_BY_TASK_LANE.get(task.lane, 70),
        "state": "active",
        "template_key": template_key,
        "context_summary": f"{task.lane} task: {task.title}",
        "chat_id": chat_id,
        "created_at": created_at,
        "updated_at": updated_at,
    }


def sync_task_cards(
    *,
    tasks_root: Path = TASKS_DIR,
    instances_dir: Path = INSTANCES_DIR,
    template_key: str = DEFAULT_TEMPLATE_KEY,
    chat_id: str = "web",
    prune_legacy: bool = True,
) -> dict[str, Any]:
    cards_root = instances_dir.parent
    tasks = [
        task
        for task in task_board.collect_tasks(tasks_root)
        if task.lane in ACTIVE_TASK_LANES
    ]
    now = datetime.now(timezone.utc).isoformat()
    active_ids: set[str] = set()
    created: list[str] = []
    updated: list[str] = []
    removed: list[str] = []
    pruned_legacy: list[str] = []

    instances_dir.mkdir(parents=True, exist_ok=True)

    for task in tasks:
        instance_id = _card_id(task)
        active_ids.add(instance_id)
        instance_dir = instances_dir / instance_id
        card_path = instance_dir / "card.json"
        state_path = instance_dir / "state.json"

        created_at = now
        if card_path.exists():
            try:
                existing = json.loads(card_path.read_text(encoding="utf-8"))
                created_at = str(existing.get("created_at") or created_at)
            except Exception:
                created_at = now

        instance_dir.mkdir(parents=True, exist_ok=True)
        _write_json(
            card_path,
            _card_payload(
                task,
                template_key=template_key,
                chat_id=chat_id,
                created_at=created_at,
                updated_at=now,
            ),
        )
        _write_json(state_path, _state_payload(task))
        (updated if created_at != now else created).append(instance_id)

    for instance_dir in instances_dir.iterdir():
        if not instance_dir.is_dir():
            continue
        card_path = instance_dir / "card.json"
        if not card_path.exists():
            continue
        try:
            existing = json.loads(card_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        slot = str(existing.get("slot") or "")
        if slot.startswith("taskboard:") and instance_dir.name not in active_ids:
            shutil.rmtree(instance_dir, ignore_errors=True)
            removed.append(instance_dir.name)
            continue
        if prune_legacy and slot.startswith("todo:"):
            shutil.rmtree(instance_dir, ignore_errors=True)
            pruned_legacy.append(instance_dir.name)

    helper_sync = task_helper_cards.sync_helper_cards(
        tasks_root=tasks_root,
        cards_root=cards_root,
        chat_id=chat_id,
    )

    return {
        "count": len(tasks),
        "created": created,
        "updated": updated,
        "removed": removed,
        "pruned_legacy": pruned_legacy,
        "helper_sync": helper_sync,
        "generated_at": now,
    }


def move_task_and_sync(
    task: str,
    lane: str,
    *,
    tasks_root: Path = TASKS_DIR,
    instances_dir: Path = INSTANCES_DIR,
    template_key: str = DEFAULT_TEMPLATE_KEY,
    chat_id: str = "web",
    prune_legacy: bool = True,
) -> dict[str, Any]:
    moved = task_board.move_task(tasks_root, task, lane)
    sync_result = sync_task_cards(
        tasks_root=tasks_root,
        instances_dir=instances_dir,
        template_key=template_key,
        chat_id=chat_id,
        prune_legacy=prune_legacy,
    )
    return {
        "task_path": str(moved),
        "lane": lane,
        "sync": sync_result,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Materialize Nanobot task cards from the file-backed kanban board."
    )
    parser.add_argument("--tasks-root", default=str(TASKS_DIR))
    parser.add_argument("--cards-root", default=str(CARDS_DIR))
    parser.add_argument("--template-key", default=DEFAULT_TEMPLATE_KEY)
    parser.add_argument("--chat-id", default="web")
    parser.add_argument("--no-prune-legacy", action="store_true")

    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("sync", help="Sync active task files into card instances.")

    move_parser = subparsers.add_parser("move", help="Move a task to another lane and sync cards.")
    move_parser.add_argument("--task", required=True, help="Task file path.")
    move_parser.add_argument("--lane", required=True, choices=task_board.LANES)

    import_parser = subparsers.add_parser(
        "import-ha",
        help="Import Home Assistant todo items into the task board and sync cards.",
    )
    import_parser.add_argument("--entity-id", required=True, help="Home Assistant todo entity id.")
    import_parser.add_argument("--lane", default="backlog", choices=task_board.LANES)
    import_parser.add_argument("--config", default=str(CONFIG_PATH))

    return parser.parse_args()


def main() -> int:
    args = parse_args()
    tasks_root = Path(args.tasks_root).expanduser()
    cards_root = Path(args.cards_root).expanduser()
    instances_dir = cards_root / "instances"
    prune_legacy = not args.no_prune_legacy

    if args.command == "sync":
        result = sync_task_cards(
            tasks_root=tasks_root,
            instances_dir=instances_dir,
            template_key=str(args.template_key).strip() or DEFAULT_TEMPLATE_KEY,
            chat_id=str(args.chat_id).strip() or "web",
            prune_legacy=prune_legacy,
        )
    elif args.command == "move":
        result = move_task_and_sync(
            str(args.task),
            str(args.lane),
            tasks_root=tasks_root,
            instances_dir=instances_dir,
            template_key=str(args.template_key).strip() or DEFAULT_TEMPLATE_KEY,
            chat_id=str(args.chat_id).strip() or "web",
            prune_legacy=prune_legacy,
        )
    elif args.command == "import-ha":
        imported = import_home_assistant_tasks(
            str(args.entity_id).strip(),
            tasks_root=tasks_root,
            lane=str(args.lane),
            config_path=Path(args.config).expanduser(),
        )
        result = {
            "imported": imported,
            "sync": sync_task_cards(
                tasks_root=tasks_root,
                instances_dir=instances_dir,
                template_key=str(args.template_key).strip() or DEFAULT_TEMPLATE_KEY,
                chat_id=str(args.chat_id).strip() or "web",
                prune_legacy=prune_legacy,
            ),
        }
    else:
        raise ValueError(f"Unsupported command: {args.command}")

    json.dump(result, sys.stdout, indent=2, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
