#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ITEM_KINDS = ("text", "question")
DEFAULT_ROOT = Path.home() / ".nanobot" / "workspace" / "workbench"
_VALID_ID_CHARS = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-")


def ensure_workbench(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_id(raw: str) -> str:
    value = raw.strip()
    if not value or len(value) > 128:
        return ""
    if any(ch not in _VALID_ID_CHARS for ch in value):
        return ""
    return value


def _normalize_chat_id(raw: str) -> str:
    return _normalize_id(raw)


def _chat_dir(root: Path, chat_id: str) -> Path:
    return root / chat_id


def _item_path(root: Path, chat_id: str, item_id: str) -> Path:
    return _chat_dir(root, chat_id) / f"{item_id}.json"


def _coerce_item(raw: dict[str, Any]) -> dict[str, Any]:
    item_id = _normalize_id(str(raw.get("id", "")))
    if not item_id:
        raise ValueError("invalid workbench item id")

    chat_id = _normalize_chat_id(str(raw.get("chat_id", "")))
    if not chat_id:
        raise ValueError("invalid chat id")

    kind = str(raw.get("kind", "text") or "text").strip().lower()
    if kind not in ITEM_KINDS:
        kind = "text"

    template_state = raw.get("template_state", {})
    if not isinstance(template_state, dict):
        template_state = {}

    raw_choices = raw.get("choices", [])
    choices = [str(choice) for choice in raw_choices] if isinstance(raw_choices, list) else []

    return {
        "id": item_id,
        "chat_id": chat_id,
        "kind": kind,
        "title": str(raw.get("title", "")),
        "content": str(raw.get("content", "")),
        "question": str(raw.get("question", "")),
        "choices": choices,
        "response_value": str(raw.get("response_value", "")),
        "slot": str(raw.get("slot", "")),
        "template_key": str(raw.get("template_key", "")),
        "template_state": template_state,
        "context_summary": str(raw.get("context_summary", "")),
        "promotable": bool(raw.get("promotable", True)),
        "source_card_id": str(raw.get("source_card_id", "")),
        "created_at": str(raw.get("created_at", "")),
        "updated_at": str(raw.get("updated_at", "")),
    }


def load_item(root: Path, chat_id: str, item_id: str) -> dict[str, Any]:
    ensure_workbench(root)
    normalized_chat_id = _normalize_chat_id(chat_id)
    normalized_item_id = _normalize_id(item_id)
    if not normalized_chat_id:
        raise ValueError("invalid chat id")
    if not normalized_item_id:
        raise ValueError("invalid workbench item id")
    path = _item_path(root, normalized_chat_id, normalized_item_id)
    if not path.exists():
        raise FileNotFoundError(f"workbench item not found: {normalized_item_id}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("invalid workbench item payload")
    return _coerce_item(payload)


def write_item(root: Path, item: dict[str, Any]) -> dict[str, Any]:
    ensure_workbench(root)
    normalized = _coerce_item(item)
    now = _utc_now_iso()
    try:
        existing = load_item(root, normalized["chat_id"], normalized["id"])
    except FileNotFoundError:
        existing = None
    normalized["created_at"] = normalized.get("created_at") or (
        existing.get("created_at") if existing else now
    )
    normalized["updated_at"] = normalized.get("updated_at") or now

    chat_dir = _chat_dir(root, normalized["chat_id"])
    chat_dir.mkdir(parents=True, exist_ok=True)
    path = _item_path(root, normalized["chat_id"], normalized["id"])
    path.write_text(json.dumps(normalized, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return load_item(root, normalized["chat_id"], normalized["id"])


def collect_items(root: Path, chat_id: str) -> list[dict[str, Any]]:
    ensure_workbench(root)
    normalized_chat_id = _normalize_chat_id(chat_id)
    if not normalized_chat_id:
        raise ValueError("invalid chat id")
    chat_dir = _chat_dir(root, normalized_chat_id)
    if not chat_dir.exists():
        return []
    items: list[dict[str, Any]] = []
    for path in sorted(chat_dir.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                continue
            items.append(_coerce_item(payload))
        except Exception:
            continue
    items.sort(key=lambda item: str(item.get("updated_at", "")), reverse=True)
    return items


def _find_item_by_slot(root: Path, chat_id: str, slot: str) -> dict[str, Any] | None:
    target_slot = slot.strip()
    if not target_slot:
        return None
    for item in collect_items(root, chat_id):
        if str(item.get("slot", "")).strip() == target_slot:
            return item
    return None


def upsert_item(
    root: Path,
    *,
    chat_id: str,
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
) -> dict[str, Any]:
    normalized_chat_id = _normalize_chat_id(chat_id)
    if not normalized_chat_id:
        raise ValueError("invalid chat id")

    next_id = _normalize_id(item_id)
    if not next_id and slot.strip():
        existing = _find_item_by_slot(root, normalized_chat_id, slot)
        if existing is not None:
            next_id = str(existing.get("id", ""))
    if not next_id:
        next_id = f"wb-{uuid.uuid4().hex[:10]}"

    payload = {
        "id": next_id,
        "chat_id": normalized_chat_id,
        "kind": kind,
        "title": title,
        "content": content,
        "question": question,
        "choices": list(choices or []),
        "response_value": response_value,
        "slot": slot,
        "template_key": template_key,
        "template_state": template_state or {},
        "context_summary": context_summary,
        "promotable": promotable,
        "source_card_id": source_card_id,
    }
    return write_item(root, payload)


def delete_item(root: Path, chat_id: str, item_id: str) -> bool:
    ensure_workbench(root)
    normalized_chat_id = _normalize_chat_id(chat_id)
    normalized_item_id = _normalize_id(item_id)
    if not normalized_chat_id:
        raise ValueError("invalid chat id")
    if not normalized_item_id:
        raise ValueError("invalid workbench item id")
    path = _item_path(root, normalized_chat_id, normalized_item_id)
    if not path.exists():
        return False
    path.unlink()
    return True


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Manage session-scoped workbench items.")
    parser.add_argument("--root", default=str(DEFAULT_ROOT))
    subparsers = parser.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser("list")
    list_parser.add_argument("--chat-id", required=True)

    show_parser = subparsers.add_parser("show")
    show_parser.add_argument("--chat-id", required=True)
    show_parser.add_argument("--item-id", required=True)

    upsert_parser = subparsers.add_parser("upsert")
    upsert_parser.add_argument("--chat-id", required=True)
    upsert_parser.add_argument("--item-id", default="")
    upsert_parser.add_argument("--kind", default="text", choices=ITEM_KINDS)
    upsert_parser.add_argument("--title", default="")
    upsert_parser.add_argument("--content", default="")
    upsert_parser.add_argument("--question", default="")
    upsert_parser.add_argument("--choices-json", default="[]")
    upsert_parser.add_argument("--response-value", default="")
    upsert_parser.add_argument("--slot", default="")
    upsert_parser.add_argument("--template-key", default="")
    upsert_parser.add_argument("--template-state-json", default="{}")
    upsert_parser.add_argument("--context-summary", default="")
    upsert_parser.add_argument("--promotable", action="store_true")
    upsert_parser.add_argument("--source-card-id", default="")

    remove_parser = subparsers.add_parser("remove")
    remove_parser.add_argument("--chat-id", required=True)
    remove_parser.add_argument("--item-id", required=True)

    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = Path(args.root).expanduser()
    if args.command == "list":
        result: dict[str, Any] = {"items": collect_items(root, args.chat_id)}
    elif args.command == "show":
        result = {"item": load_item(root, args.chat_id, args.item_id)}
    elif args.command == "remove":
        result = {"removed": delete_item(root, args.chat_id, args.item_id)}
    elif args.command == "upsert":
        choices = json.loads(args.choices_json)
        template_state = json.loads(args.template_state_json)
        if not isinstance(choices, list):
            raise ValueError("choices-json must decode to a list")
        if not isinstance(template_state, dict):
            raise ValueError("template-state-json must decode to an object")
        result = {
            "item": upsert_item(
                root,
                chat_id=args.chat_id,
                item_id=args.item_id,
                kind=args.kind,
                title=args.title,
                content=args.content,
                question=args.question,
                choices=[str(choice) for choice in choices],
                response_value=args.response_value,
                slot=args.slot,
                template_key=args.template_key,
                template_state=template_state,
                context_summary=args.context_summary,
                promotable=args.promotable,
                source_card_id=args.source_card_id,
            )
        }
    else:
        raise ValueError(f"unsupported command: {args.command}")

    json.dump(result, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
