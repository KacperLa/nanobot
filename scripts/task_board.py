#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


LANES = ("backlog", "committed", "in-progress", "blocked", "done", "canceled")
DEFAULT_ROOT = Path.home() / ".nanobot" / "workspace" / "tasks"
TAGS_DIRNAME = "tags"
TAG_TEMPLATE_DIRNAME = "_template"
README_TEXT = """# Tasks

This directory is a file-backed kanban board for your Life OS workflow.

## Lanes

- `backlog`: ideas and tasks that are not active yet
- `committed`: tasks you committed to tackle next and may use for time blocking
- `in-progress`: tasks you are actively working on
- `blocked`: tasks waiting on a dependency or decision
- `done`: completed tasks kept for short-term review
- `canceled`: intentionally dropped tasks

Each task is a Markdown file stored in exactly one lane folder.

## Task Format

Use one file per task. The first line is the title, followed by metadata lines:

```md
# Replace this with the task title
status: backlog
created: 2026-03-16T10:30:00-04:00
updated: 2026-03-16T10:30:00-04:00
due:
tags: #life-os, #planning

---

Write notes, links, subtasks, or context here.
```

## Workflow

- Create new tasks in `backlog/`
- Move tomorrow's committed work into `committed/`
- Move active work into `in-progress/`
- Move stalled work into `blocked/`
- Move completed work into `done/`
- Move abandoned work into `canceled/`
"""
TEMPLATE_TEXT = """# Example task title
status: backlog
created: 2026-03-16T10:30:00-04:00
updated: 2026-03-16T10:30:00-04:00
due:
tags: #life-os

---

## Notes

- Why this matters
- What done looks like
- Any useful links or next steps
"""
TAGS_README_TEXT = """# Tags

This directory holds richer context for important tags used across the Life OS workspace.

## Structure

- `tags/<tag>/TAG.md`: the main context note for a tag
- `tags/<tag>/...`: optional supporting notes, plans, links, or references

Use tags on tasks freely. Only create a tag folder when a tag deserves deeper context.
"""
TAG_TEMPLATE_TEXT = """# {title}
tag: {tag}

## Summary

Briefly describe what this tag covers and why it matters.

## Current Context

Add notes, links, plans, deadlines, or references that help Nanobot reason about this tag.

## Related Notes

- Add supporting files in this folder as needed.
"""


@dataclass
class Task:
    path: Path
    lane: str
    title: str
    created: str
    updated: str
    due: str
    tags: list[str]
    body: str
    metadata: dict[str, str]

    def to_dict(self) -> dict[str, object]:
        return {
            "path": str(self.path),
            "lane": self.lane,
            "title": self.title,
            "created": self.created,
            "updated": self.updated,
            "due": self.due or None,
            "tags": self.tags,
            "body": self.body,
            "metadata": dict(self.metadata),
        }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Manage a file-backed kanban board in the Nanobot workspace."
    )
    parser.add_argument(
        "--root",
        default=str(DEFAULT_ROOT),
        help="Path to the tasks directory.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("init", help="Create the task board folder layout.")

    new_parser = subparsers.add_parser("new", help="Create a new task file.")
    new_parser.add_argument("title", help="Task title.")
    new_parser.add_argument(
        "--lane",
        default="backlog",
        choices=LANES,
        help="Lane to place the task in.",
    )
    new_parser.add_argument(
        "--due",
        default="",
        help="Optional due datetime or free-form due label.",
    )
    new_parser.add_argument(
        "--tags",
        default="",
        help="Comma-separated tags.",
    )
    new_parser.add_argument(
        "--body",
        default="",
        help="Optional body text for the task.",
    )

    list_parser = subparsers.add_parser("list", help="List tasks by lane.")
    list_parser.add_argument(
        "--lane",
        choices=LANES,
        help="Limit output to a single lane.",
    )
    list_parser.add_argument(
        "--tag",
        action="append",
        default=[],
        help="Filter by tag. May be repeated or passed as comma-separated values.",
    )
    list_parser.add_argument(
        "--match",
        choices=("any", "all"),
        default="any",
        help="Whether task tag filtering should match any or all requested tags.",
    )
    list_parser.add_argument(
        "--include-done",
        action="store_true",
        help="Include done and canceled lanes.",
    )
    list_parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON.",
    )

    query_parser = subparsers.add_parser(
        "query", help="Find tasks and tag context related to one or more tags."
    )
    query_parser.add_argument(
        "--tag",
        action="append",
        required=True,
        help="Tag to query. May be repeated or passed as comma-separated values.",
    )
    query_parser.add_argument(
        "--lane",
        choices=LANES,
        help="Limit query to a single lane.",
    )
    query_parser.add_argument(
        "--match",
        choices=("any", "all"),
        default="any",
        help="Whether task tag filtering should match any or all requested tags.",
    )
    query_parser.add_argument(
        "--include-done",
        action="store_true",
        help="Include done and canceled lanes.",
    )
    query_parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON.",
    )

    tags_parser = subparsers.add_parser("list-tags", help="List known tags and counts.")
    tags_parser.add_argument(
        "--include-done",
        action="store_true",
        help="Include done and canceled lanes in tag counts.",
    )
    tags_parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON.",
    )

    ensure_tag_parser = subparsers.add_parser(
        "ensure-tag", help="Create a context folder for a tag if it does not exist."
    )
    ensure_tag_parser.add_argument("tag", help="Tag name.")
    ensure_tag_parser.add_argument(
        "--title",
        default="",
        help="Optional display title for the tag note.",
    )
    ensure_tag_parser.add_argument(
        "--summary",
        default="",
        help="Optional one-line summary to seed the tag note.",
    )

    show_parser = subparsers.add_parser("show", help="Show a single task.")
    show_parser.add_argument("task", help="Task file path.")
    show_parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON.",
    )

    move_parser = subparsers.add_parser("move", help="Move a task to another lane.")
    move_parser.add_argument("task", help="Task file path.")
    move_parser.add_argument(
        "--lane",
        required=True,
        choices=LANES,
        help="Destination lane.",
    )

    edit_parser = subparsers.add_parser(
        "edit", help="Edit a task title and/or description without moving it."
    )
    edit_parser.add_argument("task", help="Task file path.")
    edit_parser.add_argument(
        "--title",
        help="Replacement task title.",
    )
    edit_parser.add_argument(
        "--body",
        "--description",
        dest="body",
        help="Replacement task description/body.",
    )

    add_tag_parser = subparsers.add_parser("add-tag", help="Add one or more tags to a task.")
    add_tag_parser.add_argument("task", help="Task file path.")
    add_tag_parser.add_argument(
        "--tag",
        action="append",
        required=True,
        help="Tag to add. May be repeated or passed as comma-separated values.",
    )

    remove_tag_parser = subparsers.add_parser(
        "remove-tag", help="Remove one or more tags from a task."
    )
    remove_tag_parser.add_argument("task", help="Task file path.")
    remove_tag_parser.add_argument(
        "--tag",
        action="append",
        required=True,
        help="Tag to remove. May be repeated or passed as comma-separated values.",
    )

    return parser.parse_args()


def timestamp_now() -> str:
    return datetime.now().astimezone().replace(microsecond=0).isoformat()


def today_date(*, now: datetime | None = None) -> str:
    reference = now.astimezone() if now is not None else datetime.now().astimezone()
    return reference.date().isoformat()


def tomorrow_date(*, now: datetime | None = None) -> str:
    reference = now.astimezone() if now is not None else datetime.now().astimezone()
    return reference.date().fromordinal(reference.date().toordinal() + 1).isoformat()


def ensure_board(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    for lane in LANES:
        (root / lane).mkdir(parents=True, exist_ok=True)
    readme = root / "README.md"
    if not readme.exists():
        readme.write_text(README_TEXT, encoding="utf-8")
    template = root / "_template.md"
    if not template.exists():
        template.write_text(TEMPLATE_TEXT, encoding="utf-8")
    ensure_tag_space(root)


def tags_root(root: Path) -> Path:
    return root.parent / TAGS_DIRNAME


def ensure_tag_space(root: Path) -> Path:
    tag_root = tags_root(root)
    tag_root.mkdir(parents=True, exist_ok=True)
    readme = tag_root / "README.md"
    if not readme.exists():
        readme.write_text(TAGS_README_TEXT, encoding="utf-8")
    template_dir = tag_root / TAG_TEMPLATE_DIRNAME
    template_dir.mkdir(parents=True, exist_ok=True)
    template_file = template_dir / "TAG.md"
    if not template_file.exists():
        template_file.write_text(
            TAG_TEMPLATE_TEXT.format(title="Example Tag", tag="example"),
            encoding="utf-8",
        )
    return tag_root


def normalize_lane_metadata(
    lane: str,
    metadata: dict[str, str] | None = None,
    *,
    now: datetime | None = None,
) -> dict[str, str]:
    normalized = {str(key): str(value) for key, value in (metadata or {}).items() if str(value).strip()}
    if lane == "committed":
        normalized["committed_for"] = normalized.get("committed_for", "").strip() or tomorrow_date(now=now)
    else:
        normalized.pop("committed_for", None)
    return normalized


def promote_committed_tasks(root: Path, *, now: datetime | None = None) -> list[Path]:
    ensure_board(root)
    committed_dir = root / "committed"
    if not committed_dir.exists():
        return []
    promoted: list[Path] = []
    today = today_date(now=now)
    for path in sorted(committed_dir.glob("*.md")):
        task = parse_task(path)
        committed_for = str(task.metadata.get("committed_for", "")).strip()
        if committed_for and committed_for > today:
            continue
        promoted.append(move_task(root, str(path), "in-progress", now=now))
    return promoted


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "task"


def tag_slug(value: str) -> str:
    raw = str(value).strip().lower()
    if raw.startswith("#"):
        raw = raw[1:]
    return slugify(raw)


def normalize_tag(value: str) -> str:
    return f"#{tag_slug(value)}"


def normalize_tags(values: list[str]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not str(value).strip():
            continue
        for piece in str(value).split(","):
            tag = normalize_tag(piece)
            if not tag or tag in seen:
                continue
            seen.add(tag)
            normalized.append(tag)
    return normalized


def display_title_from_tag(tag: str) -> str:
    return tag.removeprefix("#").replace("-", " ").title()


def tag_note_path(root: Path, tag: str) -> Path:
    return tags_root(root) / tag_slug(tag) / "TAG.md"


def ensure_tag_folder(
    root: Path,
    tag: str,
    *,
    title: str = "",
    summary: str = "",
) -> Path:
    ensure_board(root)
    normalized_tag = normalize_tag(tag)
    tag_dir = tags_root(root) / tag_slug(normalized_tag)
    tag_dir.mkdir(parents=True, exist_ok=True)
    tag_note = tag_dir / "TAG.md"
    if not tag_note.exists():
        note_title = title.strip() or display_title_from_tag(normalized_tag)
        text = TAG_TEMPLATE_TEXT.format(title=note_title, tag=normalized_tag)
        if summary.strip():
            text = text.replace(
                "Briefly describe what this tag covers and why it matters.",
                summary.strip(),
                1,
            )
        tag_note.write_text(text, encoding="utf-8")
    return tag_note


def serialize_task(
    *,
    title: str,
    lane: str,
    created: str,
    updated: str,
    due: str,
    tags: list[str],
    body: str,
    metadata: dict[str, str] | None = None,
) -> str:
    extras = metadata or {}
    metadata_lines = [
        f"# {title}",
        f"status: {lane}",
        f"created: {created}",
        f"updated: {updated}",
        f"due: {due}",
        f"tags: {', '.join(tags)}",
    ]
    for key, value in extras.items():
        if value:
            metadata_lines.append(f"{key}: {value}")
    metadata_lines.extend(
        [
            "",
            "---",
            "",
        ]
    )
    normalized_body = body.rstrip()
    if normalized_body:
        return "\n".join(metadata_lines) + normalized_body + "\n"
    return "\n".join(metadata_lines)


def parse_metadata_line(line: str) -> tuple[str, str] | None:
    if ":" not in line:
        return None
    key, value = line.split(":", 1)
    return key.strip().lower(), value.strip()


def parse_task(path: Path) -> Task:
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    if not lines or not lines[0].startswith("# "):
        raise ValueError(f"Task file {path} is missing a '# Title' line")
    title = lines[0][2:].strip()
    metadata: dict[str, str] = {}
    body_start = len(lines)
    for index, line in enumerate(lines[1:], start=1):
        stripped = line.strip()
        if stripped == "---":
            body_start = index + 1
            break
        if not stripped:
            continue
        entry = parse_metadata_line(line)
        if entry is None:
            body_start = index
            break
        key, value = entry
        metadata[key] = value
    body = "\n".join(lines[body_start:]).strip()
    tags = normalize_tags(metadata.get("tags", "").split(","))
    lane = metadata.get("status") or path.parent.name
    if lane not in LANES:
        raise ValueError(f"Task file {path} has invalid status '{lane}'")
    core_keys = {"status", "created", "updated", "due", "tags"}
    extra_metadata = {
        key: value for key, value in metadata.items() if key not in core_keys and value
    }
    return Task(
        path=path,
        lane=lane,
        title=title,
        created=metadata.get("created", ""),
        updated=metadata.get("updated", ""),
        due=metadata.get("due", ""),
        tags=tags,
        body=body,
        metadata=extra_metadata,
    )


def collect_tasks(root: Path, lane: str | None = None, *, now: datetime | None = None) -> list[Task]:
    promote_committed_tasks(root, now=now)
    selected_lanes = (lane,) if lane else LANES
    tasks: list[Task] = []
    for lane_name in selected_lanes:
        lane_dir = root / lane_name
        if not lane_dir.exists():
            continue
        for path in sorted(lane_dir.glob("*.md")):
            tasks.append(parse_task(path))
    return tasks


def filter_tasks(
    tasks: list[Task],
    *,
    tags: list[str] | None = None,
    include_done: bool = False,
    match: str = "any",
) -> list[Task]:
    filtered = tasks
    if not include_done:
        filtered = [task for task in filtered if task.lane not in {"done", "canceled"}]
    normalized_tags = normalize_tags(tags or [])
    if not normalized_tags:
        return filtered
    wanted = set(normalized_tags)
    if match == "all":
        return [task for task in filtered if wanted.issubset(set(task.tags))]
    return [task for task in filtered if wanted.intersection(task.tags)]


def _tag_note_summary(text: str) -> str:
    paragraphs: list[str] = []
    current: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            if current:
                paragraphs.append(" ".join(current))
                current = []
            continue
        if line.startswith("# ") or line.lower().startswith("tag:"):
            continue
        if line.startswith("## "):
            if current:
                paragraphs.append(" ".join(current))
                current = []
            continue
        current.append(line)
    if current:
        paragraphs.append(" ".join(current))
    return paragraphs[0] if paragraphs else ""


def read_tag_context(root: Path, tag: str) -> dict[str, object]:
    normalized_tag = normalize_tag(tag)
    note_path = tag_note_path(root, normalized_tag)
    tag_dir = note_path.parent
    payload: dict[str, object] = {
        "tag": normalized_tag,
        "title": display_title_from_tag(normalized_tag),
        "path": str(note_path),
        "exists": note_path.exists(),
        "summary": "",
        "body": None,
        "files": [],
    }
    if not note_path.exists():
        return payload
    text = note_path.read_text(encoding="utf-8")
    title = payload["title"]
    for line in text.splitlines():
        if line.startswith("# "):
            title = line[2:].strip() or title
            break
    payload["title"] = title
    payload["summary"] = _tag_note_summary(text)
    payload["body"] = text.strip()
    payload["files"] = sorted(
        str(path.relative_to(tag_dir))
        for path in tag_dir.rglob("*")
        if path.is_file() and path.name != "TAG.md"
    )
    return payload


def list_tag_summaries(root: Path, *, include_done: bool = False) -> list[dict[str, object]]:
    ensure_board(root)
    counts: dict[str, dict[str, int]] = {}
    for task in collect_tasks(root):
        if not include_done and task.lane in {"done", "canceled"}:
            continue
        for tag in task.tags:
            lane_counts = counts.setdefault(
                tag,
                {lane: 0 for lane in LANES},
            )
            lane_counts[task.lane] += 1

    known_tags = set(counts)
    tag_root = tags_root(root)
    if tag_root.exists():
        for path in tag_root.iterdir():
            if not path.is_dir() or path.name.startswith("."):
                continue
            if path.name == TAG_TEMPLATE_DIRNAME:
                continue
            known_tags.add(normalize_tag(path.name))

    summaries: list[dict[str, object]] = []
    for tag in sorted(known_tags):
        lane_counts = counts.get(tag, {lane: 0 for lane in LANES})
        total = sum(lane_counts.values())
        context = read_tag_context(root, tag)
        summaries.append(
            {
                **context,
                "task_count": total,
                "by_lane": dict(lane_counts),
            }
        )
    summaries.sort(key=lambda item: (-int(item["task_count"]), str(item["tag"])))
    return summaries


def query_tasks(
    root: Path,
    *,
    tags: list[str],
    lane: str | None = None,
    include_done: bool = False,
    match: str = "any",
    limit: int | None = None,
) -> dict[str, object]:
    normalized_tags = normalize_tags(tags)
    tasks = filter_tasks(
        collect_tasks(root, lane),
        tags=normalized_tags,
        include_done=include_done,
        match=match,
    )
    if limit is not None:
        tasks = tasks[:limit]
    by_lane = {lane_name: 0 for lane_name in LANES}
    for task in tasks:
        by_lane[task.lane] += 1
    return {
        "tags": [read_tag_context(root, tag) for tag in normalized_tags],
        "tasks": [task.to_dict() for task in tasks],
        "counts": {
            "matched_tags": normalized_tags,
            "total_tasks": len(tasks),
            "by_lane": by_lane,
        },
    }


def resolve_task_path(root: Path, task: str) -> Path:
    raw = Path(task).expanduser()
    if raw.is_absolute():
        candidate = raw
    else:
        candidate = (root / raw).resolve(strict=False)
        if candidate.exists():
            return candidate
        matches = sorted(root.glob(f"*/{raw.name}"))
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise ValueError(f"Task name '{raw.name}' is ambiguous")
    if candidate.exists():
        return candidate
    raise FileNotFoundError(f"Task file not found: {task}")


def create_task(
    root: Path,
    title: str,
    lane: str,
    due: str,
    tags: list[str],
    body: str,
    metadata: dict[str, str] | None = None,
) -> Path:
    ensure_board(root)
    now = datetime.now().astimezone()
    created = now.replace(microsecond=0).isoformat()
    slug = slugify(title)
    filename = f"{created[:10].replace('-', '')}-{slug}.md"
    path = root / lane / filename
    counter = 2
    while path.exists():
        path = root / lane / f"{created[:10].replace('-', '')}-{slug}-{counter}.md"
        counter += 1
    path.write_text(
        serialize_task(
            title=title,
            lane=lane,
            created=created,
            updated=created,
            due=due,
            tags=normalize_tags(tags),
            body=body,
            metadata=normalize_lane_metadata(lane, metadata, now=now),
        ),
        encoding="utf-8",
    )
    return path


def move_task(root: Path, task: str, lane: str, *, now: datetime | None = None) -> Path:
    ensure_board(root)
    source = resolve_task_path(root, task)
    parsed = parse_task(source)
    reference = now.astimezone() if now is not None else datetime.now().astimezone()
    updated = reference.replace(microsecond=0).isoformat()
    destination = root / lane / source.name
    if source.resolve() != destination.resolve():
        shutil.move(str(source), str(destination))
    destination.write_text(
        serialize_task(
            title=parsed.title,
            lane=lane,
            created=parsed.created,
            updated=updated,
            due=parsed.due,
            tags=parsed.tags,
            body=parsed.body,
            metadata=normalize_lane_metadata(lane, parsed.metadata, now=reference),
        ),
        encoding="utf-8",
    )
    return destination


def edit_task(
    root: Path,
    task: str,
    *,
    title: str | None = None,
    body: str | None = None,
) -> Path:
    ensure_board(root)
    task_path = resolve_task_path(root, task)
    parsed = parse_task(task_path)
    new_title = parsed.title if title is None else title.strip()
    if not new_title:
        raise ValueError("Task title cannot be empty")
    new_body = parsed.body if body is None else body.rstrip()
    if title is None and body is None:
        raise ValueError("At least one of title or description must be provided")
    updated = timestamp_now()
    task_path.write_text(
        serialize_task(
            title=new_title,
            lane=parsed.lane,
            created=parsed.created,
            updated=updated,
            due=parsed.due,
            tags=parsed.tags,
            body=new_body,
            metadata=parsed.metadata,
        ),
        encoding="utf-8",
    )
    return task_path


def add_tags_to_task(root: Path, task: str, tags: list[str]) -> Path:
    ensure_board(root)
    task_path = resolve_task_path(root, task)
    parsed = parse_task(task_path)
    updated = timestamp_now()
    task_path.write_text(
        serialize_task(
            title=parsed.title,
            lane=parsed.lane,
            created=parsed.created,
            updated=updated,
            due=parsed.due,
            tags=normalize_tags([*parsed.tags, *tags]),
            body=parsed.body,
            metadata=parsed.metadata,
        ),
        encoding="utf-8",
    )
    return task_path


def remove_tags_from_task(root: Path, task: str, tags: list[str]) -> Path:
    ensure_board(root)
    task_path = resolve_task_path(root, task)
    parsed = parse_task(task_path)
    removed = set(normalize_tags(tags))
    updated = timestamp_now()
    task_path.write_text(
        serialize_task(
            title=parsed.title,
            lane=parsed.lane,
            created=parsed.created,
            updated=updated,
            due=parsed.due,
            tags=[tag for tag in parsed.tags if tag not in removed],
            body=parsed.body,
            metadata=parsed.metadata,
        ),
        encoding="utf-8",
    )
    return task_path


def print_tasks(tasks: list[Task], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps([task.to_dict() for task in tasks], ensure_ascii=False, indent=2))
        return
    if not tasks:
        print("No tasks found.")
        return
    current_lane = None
    for task in tasks:
        if task.lane != current_lane:
            current_lane = task.lane
            print(f"\n[{current_lane}]")
        due_suffix = f" (due {task.due})" if task.due else ""
        tags_suffix = f" [{', '.join(task.tags)}]" if task.tags else ""
        print(f"- {task.title}{due_suffix}{tags_suffix}")


def print_task(task: Task, *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(task.to_dict(), ensure_ascii=False, indent=2))
        return
    print(task.path)
    print(f"Title: {task.title}")
    print(f"Lane: {task.lane}")
    print(f"Created: {task.created or '-'}")
    print(f"Updated: {task.updated or '-'}")
    print(f"Due: {task.due or '-'}")
    print(f"Tags: {', '.join(task.tags) if task.tags else '-'}")
    if task.body:
        print("")
        print(task.body)


def print_query_result(result: dict[str, object], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    tags = result.get("tags") or []
    tasks = result.get("tasks") or []
    counts = result.get("counts") or {}
    if tags:
        print("[tags]")
        for tag in tags:
            if not isinstance(tag, dict):
                continue
            summary = str(tag.get("summary") or "").strip()
            exists = "context" if tag.get("exists") else "no-context"
            suffix = f": {summary}" if summary else ""
            print(f"- {tag.get('tag')} ({exists}){suffix}")
        print("")
    if tasks:
        print("[tasks]")
        for task in tasks:
            if not isinstance(task, dict):
                continue
            due = f" (due {task.get('due')})" if task.get("due") else ""
            tags_suffix = ", ".join(task.get("tags") or [])
            tag_text = f" [{tags_suffix}]" if tags_suffix else ""
            print(f"- {task.get('title')} [{task.get('lane')}]{due}{tag_text}")
    else:
        print("No matching tasks found.")
    if isinstance(counts, dict):
        total = counts.get("total_tasks")
        if total is not None:
            print("")
            print(f"Matched tasks: {total}")


def print_tag_summaries(tags: list[dict[str, object]], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(tags, ensure_ascii=False, indent=2))
        return
    if not tags:
        print("No tags found.")
        return
    for tag in tags:
        summary = str(tag.get("summary") or "").strip()
        summary_suffix = f": {summary}" if summary else ""
        print(f"- {tag['tag']} ({tag['task_count']} tasks){summary_suffix}")


def main() -> None:
    args = parse_args()
    root = Path(args.root).expanduser()

    if args.command == "init":
        ensure_board(root)
        print(root)
        return

    if args.command == "new":
        path = create_task(
            root=root,
            title=args.title,
            lane=args.lane,
            due=args.due.strip(),
            tags=normalize_tags([args.tags]),
            body=args.body.strip(),
        )
        print(path)
        return

    if args.command == "list":
        tasks = filter_tasks(
            collect_tasks(root, args.lane),
            tags=normalize_tags(args.tag),
            include_done=args.include_done,
            match=args.match,
        )
        print_tasks(tasks, as_json=args.json)
        return

    if args.command == "query":
        result = query_tasks(
            root,
            tags=normalize_tags(args.tag),
            lane=args.lane,
            include_done=args.include_done,
            match=args.match,
        )
        print_query_result(result, as_json=args.json)
        return

    if args.command == "list-tags":
        print_tag_summaries(
            list_tag_summaries(root, include_done=args.include_done),
            as_json=args.json,
        )
        return

    if args.command == "ensure-tag":
        print(
            ensure_tag_folder(
                root,
                args.tag,
                title=args.title.strip(),
                summary=args.summary.strip(),
            )
        )
        return

    if args.command == "show":
        print_task(parse_task(resolve_task_path(root, args.task)), as_json=args.json)
        return

    if args.command == "move":
        print(move_task(root, args.task, args.lane))
        return

    if args.command == "edit":
        print(edit_task(root, args.task, title=args.title, body=args.body))
        return

    if args.command == "add-tag":
        print(add_tags_to_task(root, args.task, args.tag))
        return

    if args.command == "remove-tag":
        print(remove_tags_from_task(root, args.task, args.tag))
        return

    raise ValueError(f"Unsupported command: {args.command}")


if __name__ == "__main__":
    main()
