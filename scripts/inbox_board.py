#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import task_board  # noqa: E402


STATUSES = ("new", "triaged", "accepted", "dismissed", "merged")
OPEN_STATUSES = {"new", "triaged"}
KINDS = ("task", "reminder", "note", "idea", "event_prep", "unknown")
DEFAULT_ROOT = Path.home() / ".nanobot" / "workspace" / "inbox"
README_TEXT = """# Inbox

This directory is the low-friction capture layer for the Life OS workflow.

Use inbox items when something should be captured first and organized later.
Good inbox candidates include:

- vague reminders
- things to follow up on later
- passive listening distilled captures
- ideas, notes, and possible tasks

Inbox items can later be accepted into the task board, dismissed, or merged.
"""
TEMPLATE_TEXT = """# Follow up on travel packing
kind: task
status: new
source: agent
confidence: 0.85
captured: 2026-04-01T08:30:00-04:00
updated: 2026-04-01T08:30:00-04:00
suggested_due:
tags: #japan, #travel

---

Write the distilled capture here.

## Raw Capture

Original wording or transcript snippet.
"""


@dataclass
class InboxItem:
    path: Path
    title: str
    kind: str
    status: str
    source: str
    confidence: float | None
    captured: str
    updated: str
    suggested_due: str
    tags: list[str]
    body: str
    metadata: dict[str, str]

    def to_dict(self) -> dict[str, object]:
        return {
            "path": str(self.path),
            "title": self.title,
            "kind": self.kind,
            "status": self.status,
            "source": self.source,
            "confidence": self.confidence,
            "captured": self.captured,
            "updated": self.updated,
            "suggested_due": self.suggested_due or None,
            "tags": list(self.tags),
            "body": self.body,
            "metadata": dict(self.metadata),
        }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Manage the file-backed Nanobot inbox capture layer."
    )
    parser.add_argument(
        "--root",
        default=str(DEFAULT_ROOT),
        help="Path to the inbox directory.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("init", help="Create the inbox folder layout.")

    capture_parser = subparsers.add_parser("capture", help="Create a new inbox item.")
    capture_parser.add_argument("title", nargs="?", default="", help="Inbox item title.")
    capture_parser.add_argument(
        "--kind",
        default="unknown",
        choices=KINDS,
        help="Inbox item kind.",
    )
    capture_parser.add_argument(
        "--source",
        default="agent",
        help="Capture source label, e.g. agent or passive-listen.",
    )
    capture_parser.add_argument(
        "--confidence",
        type=float,
        help="Optional capture confidence between 0 and 1.",
    )
    capture_parser.add_argument(
        "--due",
        default="",
        help="Optional suggested due date or datetime.",
    )
    capture_parser.add_argument(
        "--tags",
        default="",
        help="Comma-separated tags.",
    )
    capture_parser.add_argument(
        "--body",
        default="",
        help="Optional distilled notes/body.",
    )
    capture_parser.add_argument(
        "--raw-text",
        default="",
        help="Optional raw capture text or transcript snippet.",
    )
    capture_parser.add_argument(
        "--session",
        default="",
        help="Optional linked chat/session id.",
    )

    list_parser = subparsers.add_parser("list", help="List inbox items.")
    list_parser.add_argument(
        "--status",
        choices=STATUSES,
        help="Filter by a single inbox status.",
    )
    list_parser.add_argument(
        "--kind",
        choices=KINDS,
        help="Filter by a single inbox kind.",
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
        help="Whether tag filters should match any or all requested tags.",
    )
    list_parser.add_argument(
        "--include-closed",
        action="store_true",
        help="Include accepted, dismissed, and merged items.",
    )
    list_parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON.",
    )

    show_parser = subparsers.add_parser("show", help="Show a single inbox item.")
    show_parser.add_argument("item", help="Inbox item file path.")
    show_parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON.",
    )

    update_parser = subparsers.add_parser("update", help="Update an inbox item.")
    update_parser.add_argument("item", help="Inbox item file path.")
    update_parser.add_argument("--title", help="Replacement title.")
    update_parser.add_argument("--body", "--description", dest="body", help="Replacement body.")
    update_parser.add_argument(
        "--kind",
        choices=KINDS,
        help="Replacement kind.",
    )
    update_parser.add_argument(
        "--status",
        choices=STATUSES,
        help="Replacement status.",
    )
    update_parser.add_argument(
        "--source",
        help="Replacement source label.",
    )
    update_parser.add_argument(
        "--confidence",
        type=float,
        help="Replacement confidence between 0 and 1.",
    )
    update_parser.add_argument(
        "--due",
        help="Replacement suggested due date or datetime.",
    )
    update_parser.add_argument(
        "--tags",
        default=None,
        help="Replacement comma-separated tags. Pass an empty string to clear tags.",
    )

    accept_parser = subparsers.add_parser(
        "accept-task", help="Accept an inbox item into the task board."
    )
    accept_parser.add_argument("item", help="Inbox item file path.")
    accept_parser.add_argument(
        "--tasks-root",
        default="",
        help="Optional path to the task board root. Defaults to ../tasks.",
    )
    accept_parser.add_argument(
        "--lane",
        default="backlog",
        choices=task_board.LANES,
        help="Destination task lane.",
    )
    accept_parser.add_argument("--title", default="", help="Optional task title override.")
    accept_parser.add_argument(
        "--body",
        "--description",
        dest="body",
        default="",
        help="Optional task body override.",
    )
    accept_parser.add_argument("--due", default="", help="Optional due override.")
    accept_parser.add_argument(
        "--tags",
        default=None,
        help="Optional replacement comma-separated task tags.",
    )

    dismiss_parser = subparsers.add_parser("dismiss", help="Dismiss an inbox item.")
    dismiss_parser.add_argument("item", help="Inbox item file path.")

    return parser.parse_args()


def timestamp_now() -> str:
    return datetime.now().astimezone().replace(microsecond=0).isoformat()


def ensure_inbox(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    readme = root / "README.md"
    if not readme.exists():
        readme.write_text(README_TEXT, encoding="utf-8")
    template = root / "_template.md"
    if not template.exists():
        template.write_text(TEMPLATE_TEXT, encoding="utf-8")


def normalize_kind(value: str) -> str:
    cleaned = str(value).strip().lower().replace(" ", "_")
    if cleaned not in KINDS:
        raise ValueError(f"invalid inbox kind: {value}")
    return cleaned


def normalize_status(value: str) -> str:
    cleaned = str(value).strip().lower()
    if cleaned not in STATUSES:
        raise ValueError(f"invalid inbox status: {value}")
    return cleaned


def normalize_source(value: str) -> str:
    cleaned = re.sub(r"[^a-z0-9_-]+", "-", str(value).strip().lower()).strip("-")
    return cleaned or "agent"


def normalize_confidence(value: float | str | None) -> float | None:
    if value is None or value == "":
        return None
    confidence = float(value)
    if confidence < 0 or confidence > 1:
        raise ValueError("confidence must be between 0 and 1")
    return round(confidence, 4)


def derive_title(title: str, raw_text: str) -> str:
    cleaned_title = title.strip()
    if cleaned_title:
        return cleaned_title
    compact = re.sub(r"\s+", " ", raw_text).strip()
    if not compact:
        raise ValueError("title or raw_text is required for inbox capture")
    if len(compact) <= 96:
        return compact
    return compact[:93].rstrip() + "..."


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "capture"


def serialize_item(
    *,
    title: str,
    kind: str,
    status: str,
    source: str,
    confidence: float | None,
    captured: str,
    updated: str,
    suggested_due: str,
    tags: list[str],
    body: str,
    metadata: dict[str, str] | None = None,
) -> str:
    extras = metadata or {}
    confidence_text = "" if confidence is None else format(confidence, ".4f").rstrip("0").rstrip(".")
    lines = [
        f"# {title}",
        f"kind: {kind}",
        f"status: {status}",
        f"source: {source}",
        f"confidence: {confidence_text}",
        f"captured: {captured}",
        f"updated: {updated}",
        f"suggested_due: {suggested_due}",
        f"tags: {', '.join(tags)}",
    ]
    for key, value in extras.items():
        if value:
            lines.append(f"{key}: {value}")
    lines.extend(["", "---", ""])
    normalized_body = body.rstrip()
    if normalized_body:
        return "\n".join(lines) + normalized_body + "\n"
    return "\n".join(lines)


def parse_metadata_line(line: str) -> tuple[str, str] | None:
    if ":" not in line:
        return None
    key, value = line.split(":", 1)
    return key.strip().lower(), value.strip()


def parse_item(path: Path) -> InboxItem:
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    if not lines or not lines[0].startswith("# "):
        raise ValueError(f"Inbox file {path} is missing a '# Title' line")
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
    core_keys = {
        "kind",
        "status",
        "source",
        "confidence",
        "captured",
        "updated",
        "suggested_due",
        "tags",
    }
    confidence_raw = metadata.get("confidence", "").strip()
    confidence = float(confidence_raw) if confidence_raw else None
    kind = metadata.get("kind", "unknown") or "unknown"
    status = metadata.get("status", "new") or "new"
    extra_metadata = {
        key: value for key, value in metadata.items() if key not in core_keys and value
    }
    return InboxItem(
        path=path,
        title=title,
        kind=normalize_kind(kind),
        status=normalize_status(status),
        source=normalize_source(metadata.get("source", "agent")),
        confidence=confidence,
        captured=metadata.get("captured", ""),
        updated=metadata.get("updated", ""),
        suggested_due=metadata.get("suggested_due", ""),
        tags=task_board.normalize_tags(metadata.get("tags", "").split(",")),
        body=body,
        metadata=extra_metadata,
    )


def collect_items(root: Path) -> list[InboxItem]:
    ensure_inbox(root)
    items: list[InboxItem] = []
    for path in sorted(root.glob("*.md")):
        if path.name.startswith("_") or path.name == "README.md":
            continue
        items.append(parse_item(path))
    items.sort(key=lambda item: (item.updated or item.captured or "", item.title.lower()), reverse=True)
    return items


def filter_items(
    items: list[InboxItem],
    *,
    status: str | None = None,
    kind: str | None = None,
    tags: list[str] | None = None,
    include_closed: bool = False,
    match: str = "any",
) -> list[InboxItem]:
    filtered = items
    if not include_closed:
        filtered = [item for item in filtered if item.status in OPEN_STATUSES]
    if status:
        filtered = [item for item in filtered if item.status == normalize_status(status)]
    if kind:
        filtered = [item for item in filtered if item.kind == normalize_kind(kind)]
    normalized_tags = task_board.normalize_tags(tags or [])
    if not normalized_tags:
        return filtered
    wanted = set(normalized_tags)
    if match == "all":
        return [item for item in filtered if wanted.issubset(set(item.tags))]
    return [item for item in filtered if wanted.intersection(item.tags)]


def resolve_item_path(root: Path, item: str) -> Path:
    raw = Path(item).expanduser()
    if raw.is_absolute():
        candidate = raw
    else:
        candidate = (root / raw).resolve(strict=False)
        if candidate.exists():
            return candidate
        matches = sorted(root.glob(raw.name))
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise ValueError(f"Inbox item name '{raw.name}' is ambiguous")
    if candidate.exists():
        return candidate
    raise FileNotFoundError(f"Inbox item not found: {item}")


def create_item(
    root: Path,
    *,
    title: str,
    kind: str = "unknown",
    source: str = "agent",
    confidence: float | None = None,
    suggested_due: str = "",
    tags: list[str] | None = None,
    body: str = "",
    raw_text: str = "",
    metadata: dict[str, str] | None = None,
) -> Path:
    ensure_inbox(root)
    now = timestamp_now()
    final_title = derive_title(title, raw_text)
    combined_body = body.strip()
    raw_capture = raw_text.strip()
    if raw_capture:
        raw_block = f"## Raw Capture\n\n{raw_capture}"
        combined_body = f"{combined_body}\n\n{raw_block}".strip() if combined_body else raw_block
    slug = slugify(final_title)
    filename = f"{now[:10].replace('-', '')}-{slug}.md"
    path = root / filename
    counter = 2
    while path.exists():
        path = root / f"{now[:10].replace('-', '')}-{slug}-{counter}.md"
        counter += 1
    path.write_text(
        serialize_item(
            title=final_title,
            kind=normalize_kind(kind),
            status="new",
            source=normalize_source(source),
            confidence=normalize_confidence(confidence),
            captured=now,
            updated=now,
            suggested_due=suggested_due.strip(),
            tags=task_board.normalize_tags(tags or []),
            body=combined_body,
            metadata={str(key): str(value) for key, value in (metadata or {}).items() if str(value).strip()},
        ),
        encoding="utf-8",
    )
    return path


def update_item(
    root: Path,
    item: str,
    *,
    title: str | None = None,
    body: str | None = None,
    kind: str | None = None,
    status: str | None = None,
    source: str | None = None,
    confidence: float | None = None,
    confidence_provided: bool = False,
    suggested_due: str | None = None,
    tags: list[str] | None = None,
    metadata_updates: dict[str, str] | None = None,
) -> Path:
    ensure_inbox(root)
    item_path = resolve_item_path(root, item)
    parsed = parse_item(item_path)
    next_title = parsed.title if title is None else title.strip()
    if not next_title:
        raise ValueError("Inbox item title cannot be empty")
    next_body = parsed.body if body is None else body.rstrip()
    next_kind = parsed.kind if kind is None else normalize_kind(kind)
    next_status = parsed.status if status is None else normalize_status(status)
    next_source = parsed.source if source is None else normalize_source(source)
    next_confidence = (
        parsed.confidence
        if not confidence_provided
        else normalize_confidence(confidence)
    )
    next_due = parsed.suggested_due if suggested_due is None else suggested_due.strip()
    next_tags = parsed.tags if tags is None else task_board.normalize_tags(tags)
    merged_metadata = dict(parsed.metadata)
    if metadata_updates:
        for key, value in metadata_updates.items():
            if str(value).strip():
                merged_metadata[str(key)] = str(value).strip()
            else:
                merged_metadata.pop(str(key), None)
    item_path.write_text(
        serialize_item(
            title=next_title,
            kind=next_kind,
            status=next_status,
            source=next_source,
            confidence=next_confidence,
            captured=parsed.captured,
            updated=timestamp_now(),
            suggested_due=next_due,
            tags=next_tags,
            body=next_body,
            metadata=merged_metadata,
        ),
        encoding="utf-8",
    )
    return item_path


def accept_item_as_task(
    inbox_root: Path,
    item: str,
    *,
    tasks_root: Path,
    lane: str = "backlog",
    title: str = "",
    body: str | None = None,
    due: str = "",
    tags: list[str] | None = None,
) -> tuple[Path, Path]:
    ensure_inbox(inbox_root)
    task_board.ensure_board(tasks_root)
    item_path = resolve_item_path(inbox_root, item)
    parsed = parse_item(item_path)
    if parsed.status not in OPEN_STATUSES:
        raise ValueError(f"inbox item is already {parsed.status}")
    task_path = task_board.create_task(
        root=tasks_root,
        title=title.strip() or parsed.title,
        lane=lane,
        due=due.strip() or parsed.suggested_due,
        tags=tags if tags is not None else parsed.tags,
        body=parsed.body if body is None else body.rstrip(),
        metadata={
            "source": "inbox",
            "inbox_item_path": str(item_path),
            "inbox_source": parsed.source,
        },
    )
    update_item(
        inbox_root,
        str(item_path),
        status="accepted",
        metadata_updates={
            "accepted_task_path": str(task_path),
            "accepted_task_lane": lane,
            "accepted_at": timestamp_now(),
        },
    )
    return item_path, task_path


def dismiss_item(root: Path, item: str) -> Path:
    return update_item(
        root,
        item,
        status="dismissed",
        metadata_updates={"dismissed_at": timestamp_now()},
    )


def print_items(items: list[InboxItem], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps([item.to_dict() for item in items], ensure_ascii=False, indent=2))
        return
    if not items:
        print("No inbox items found.")
        return
    for item in items:
        due_suffix = f" (due {item.suggested_due})" if item.suggested_due else ""
        tags_suffix = f" [{', '.join(item.tags)}]" if item.tags else ""
        print(f"- {item.title} [{item.status}/{item.kind}]{due_suffix}{tags_suffix}")


def print_item(item: InboxItem, *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(item.to_dict(), ensure_ascii=False, indent=2))
        return
    print(item.path)
    print(f"Title: {item.title}")
    print(f"Kind: {item.kind}")
    print(f"Status: {item.status}")
    print(f"Source: {item.source}")
    print(f"Confidence: {item.confidence if item.confidence is not None else '-'}")
    print(f"Captured: {item.captured or '-'}")
    print(f"Updated: {item.updated or '-'}")
    print(f"Suggested Due: {item.suggested_due or '-'}")
    print(f"Tags: {', '.join(item.tags) if item.tags else '-'}")
    if item.body:
        print("")
        print(item.body)


def main() -> None:
    args = parse_args()
    root = Path(args.root).expanduser()

    if args.command == "init":
        ensure_inbox(root)
        print(root)
        return

    if args.command == "capture":
        path = create_item(
            root,
            title=args.title,
            kind=args.kind,
            source=args.source,
            confidence=args.confidence,
            suggested_due=args.due,
            tags=task_board.normalize_tags([args.tags]),
            body=args.body,
            raw_text=args.raw_text,
            metadata={"source_session": args.session.strip()} if args.session.strip() else None,
        )
        print(path)
        return

    if args.command == "list":
        items = filter_items(
            collect_items(root),
            status=args.status,
            kind=args.kind,
            tags=task_board.normalize_tags(args.tag),
            include_closed=args.include_closed,
            match=args.match,
        )
        print_items(items, as_json=args.json)
        return

    if args.command == "show":
        print_item(parse_item(resolve_item_path(root, args.item)), as_json=args.json)
        return

    if args.command == "update":
        tags = None if args.tags is None else task_board.normalize_tags([args.tags])
        updated = update_item(
            root,
            args.item,
            title=args.title,
            body=args.body,
            kind=args.kind,
            status=args.status,
            source=args.source,
            confidence=args.confidence,
            confidence_provided=args.confidence is not None,
            suggested_due=args.due,
            tags=tags,
        )
        print(updated)
        return

    if args.command == "accept-task":
        tasks_root = (
            Path(args.tasks_root).expanduser()
            if args.tasks_root
            else root.parent / "tasks"
        )
        item_path, task_path = accept_item_as_task(
            root,
            args.item,
            tasks_root=tasks_root,
            lane=args.lane,
            title=args.title,
            body=args.body if args.body else None,
            due=args.due,
            tags=None if args.tags is None else task_board.normalize_tags([args.tags]),
        )
        print(json.dumps({"item": str(item_path), "task": str(task_path)}, ensure_ascii=False, indent=2))
        return

    if args.command == "dismiss":
        print(dismiss_item(root, args.item))
        return


if __name__ == "__main__":
    main()
