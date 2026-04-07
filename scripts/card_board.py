#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


CARD_STATES = ("active", "stale", "resolved", "superseded", "archived")
DEFAULT_ROOT = Path.home() / ".nanobot" / "cards"
CARD_ID_CHARS = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-")


def ensure_cards_root(root: Path) -> None:
    (root / "instances").mkdir(parents=True, exist_ok=True)
    (root / "templates").mkdir(parents=True, exist_ok=True)


def _normalize_card_id(raw: str) -> str:
    card_id = raw.strip()
    if not card_id:
        return ""
    if any(ch not in CARD_ID_CHARS for ch in card_id):
        return ""
    if len(card_id) > 128:
        return ""
    return card_id


def _card_instance_dir(root: Path, card_id: str) -> Path:
    return root / "instances" / card_id


def _card_meta_path(root: Path, card_id: str) -> Path:
    return _card_instance_dir(root, card_id) / "card.json"


def _card_state_path(root: Path, card_id: str) -> Path:
    return _card_instance_dir(root, card_id) / "state.json"


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected object JSON in {path}")
    return payload


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _coerce_card_record(raw: dict[str, Any]) -> dict[str, Any]:
    card_id = _normalize_card_id(str(raw.get("id", "")))
    if not card_id:
        raise ValueError("invalid card id")

    kind = str(raw.get("kind", "text") or "text").strip().lower()
    if kind not in {"text", "question"}:
        kind = "text"

    lane = str(raw.get("lane", "context") or "context").strip().lower()
    if lane not in {"attention", "work", "context", "history"}:
        lane = "context"

    state = str(raw.get("state", "active") or "active").strip().lower()
    if state not in CARD_STATES:
        state = "active"

    try:
        priority = int(raw.get("priority", 50))
    except (TypeError, ValueError):
        priority = 50
    priority = max(0, min(priority, 100))

    template_state = raw.get("template_state", {})
    if not isinstance(template_state, dict):
        template_state = {}

    return {
        "id": card_id,
        "kind": kind,
        "title": str(raw.get("title", "")),
        "content": str(raw.get("content", "")),
        "question": str(raw.get("question", "")),
        "choices": raw.get("choices", []) if isinstance(raw.get("choices"), list) else [],
        "response_value": str(raw.get("response_value", "")),
        "slot": str(raw.get("slot", "")),
        "lane": lane,
        "priority": priority,
        "state": state,
        "template_key": str(raw.get("template_key", "")),
        "template_state": template_state,
        "context_summary": str(raw.get("context_summary", "")),
        "chat_id": str(raw.get("chat_id", "web") or "web"),
        "snooze_until": str(raw.get("snooze_until", "") or ""),
        "created_at": str(raw.get("created_at", "") or ""),
        "updated_at": str(raw.get("updated_at", "") or ""),
    }


def load_card(root: Path, card_id: str) -> dict[str, Any]:
    ensure_cards_root(root)
    normalized_id = _normalize_card_id(card_id)
    if not normalized_id:
        raise ValueError("invalid card id")
    meta_path = _card_meta_path(root, normalized_id)
    if not meta_path.exists():
        raise FileNotFoundError(f"card not found: {normalized_id}")

    card = _coerce_card_record(_load_json(meta_path))
    state_path = _card_state_path(root, normalized_id)
    if state_path.exists():
        raw_state = _load_json(state_path)
        card["template_state"] = raw_state
    return card


def write_card(root: Path, card: dict[str, Any]) -> dict[str, Any]:
    ensure_cards_root(root)
    normalized = _coerce_card_record(card)
    now = _utc_now_iso()
    existing: dict[str, Any] | None
    try:
        existing = load_card(root, normalized["id"])
    except FileNotFoundError:
        existing = None

    normalized["created_at"] = normalized.get("created_at") or (
        existing.get("created_at") if existing else now
    )
    normalized["updated_at"] = normalized.get("updated_at") or now

    instance_dir = _card_instance_dir(root, normalized["id"])
    instance_dir.mkdir(parents=True, exist_ok=True)
    meta_path = _card_meta_path(root, normalized["id"])
    state_path = _card_state_path(root, normalized["id"])

    template_state = normalized.pop("template_state", {})
    meta_path.write_text(
        json.dumps(normalized, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    if normalized["kind"] == "text":
        state_path.write_text(
            json.dumps(template_state if isinstance(template_state, dict) else {}, indent=2, ensure_ascii=False)
            + "\n",
            encoding="utf-8",
        )
    elif state_path.exists():
        state_path.unlink()
    return load_card(root, normalized["id"])


def collect_cards(root: Path, chat_id: str = "") -> list[dict[str, Any]]:
    ensure_cards_root(root)
    cards: list[dict[str, Any]] = []
    for instance_dir in sorted((root / "instances").iterdir()):
        if not instance_dir.is_dir():
            continue
        try:
            card = load_card(root, instance_dir.name)
        except Exception:
            continue
        if chat_id and str(card.get("chat_id", "web") or "web") != chat_id:
            continue
        cards.append(card)
    cards.sort(key=lambda item: str(item.get("updated_at", "")), reverse=True)
    return cards


def merge_template_state(card: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    next_state = dict(card.get("template_state", {}))
    next_state.update(patch)
    card["template_state"] = next_state
    card["updated_at"] = _utc_now_iso()
    return card


def replace_template_state(card: dict[str, Any], template_state: dict[str, Any]) -> dict[str, Any]:
    card["template_state"] = dict(template_state)
    card["updated_at"] = _utc_now_iso()
    return card


def clear_card_content(card: dict[str, Any]) -> dict[str, Any]:
    state = dict(card.get("template_state", {}))
    template_key = str(card.get("template_key", "")).strip()

    if template_key == "list-total-live" or isinstance(state.get("rows"), list):
        state["rows"] = []
        card["template_state"] = state
        card["updated_at"] = _utc_now_iso()
        return card

    if isinstance(state.get("items"), list):
        state["items"] = []
        card["template_state"] = state
        card["updated_at"] = _utc_now_iso()
        return card

    raise ValueError(
        "card content cannot be cleared generically for this template; inspect and update template_state"
    )
