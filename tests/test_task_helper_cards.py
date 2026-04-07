from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "task_helper_cards.py"
SPEC = importlib.util.spec_from_file_location("task_helper_cards", SCRIPT_PATH)
assert SPEC and SPEC.loader
task_helper_cards = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = task_helper_cards
SPEC.loader.exec_module(task_helper_cards)


def test_upsert_watch_helper_card_creates_embedded_card(tmp_path: Path) -> None:
    tasks_root = tmp_path / "tasks"
    cards_root = tmp_path / "cards"
    task_path = task_helper_cards.task_board.create_task(
        root=tasks_root,
        title="Find and watch a good ramen video",
        lane="backlog",
        due="",
        tags=["cooking"],
        body="",
        metadata={},
    )

    result = task_helper_cards.upsert_helper_card(
        task_path=str(task_path),
        tasks_root=tasks_root,
        cards_root=cards_root,
        kind="watch",
        primary={
            "title": "How to Make Tokyo-Style Ramen",
            "url": "https://www.youtube.com/watch?v=abc123xyz98",
            "subtitle": "12 minute walkthrough",
            "meta": "YouTube",
        },
    )

    card = result["card"]
    state = json.loads(
        (cards_root / "instances" / card["id"] / "state.json").read_text(encoding="utf-8")
    )

    assert result["helper_kind"] == "watch"
    assert card["title"] == "Watch: Find and watch a good ramen video"
    assert "youtube-nocookie.com/embed/abc123xyz98" in card["content"]
    assert state["helper_kind"] == "watch"
    assert state["primary"]["title"] == "How to Make Tokyo-Style Ramen"


def test_sync_helper_cards_removes_cards_for_done_tasks(tmp_path: Path) -> None:
    tasks_root = tmp_path / "tasks"
    cards_root = tmp_path / "cards"
    task_path = task_helper_cards.task_board.create_task(
        root=tasks_root,
        title="Buy a new travel adapter",
        lane="backlog",
        due="",
        tags=["travel"],
        body="",
        metadata={},
    )

    result = task_helper_cards.upsert_helper_card(
        task_path=str(task_path),
        tasks_root=tasks_root,
        cards_root=cards_root,
        kind="shopping",
    )
    card_id = result["card"]["id"]
    assert (cards_root / "instances" / card_id).is_dir()

    moved_path = task_helper_cards.task_board.move_task(tasks_root, str(task_path), "done")
    assert moved_path.parent.name == "done"

    sync_result = task_helper_cards.sync_helper_cards(
        tasks_root=tasks_root,
        cards_root=cards_root,
    )

    assert card_id in sync_result["removed"]
    assert not (cards_root / "instances" / card_id).exists()


def test_helper_cards_render_only_primary_content(tmp_path: Path) -> None:
    tasks_root = tmp_path / "tasks"
    cards_root = tmp_path / "cards"
    task_path = task_helper_cards.task_board.create_task(
        root=tasks_root,
        title="Buy a new travel adapter",
        lane="backlog",
        due="",
        tags=["travel"],
        body="",
        metadata={},
    )

    result = task_helper_cards.upsert_helper_card(
        task_path=str(task_path),
        tasks_root=tasks_root,
        cards_root=cards_root,
        kind="shopping",
        primary={
            "title": "Search products",
            "url": "https://example.com/shop",
            "subtitle": "Travel adapter",
            "meta": "Primary",
        },
        alternatives=[
            {
                "title": "Alternate option",
                "url": "https://example.com/alternate",
                "subtitle": "Travel adapter",
                "meta": "Alternate",
            }
        ],
        notes="Keep this compact.",
    )

    content = result["card"]["content"]
    assert "Search products" in content
    assert "More Options" not in content
    assert "Keep this compact." not in content
    assert "Travel adapter" not in content


def test_outreach_helper_cards_render_compact_draft(tmp_path: Path) -> None:
    tasks_root = tmp_path / "tasks"
    cards_root = tmp_path / "cards"
    task_path = task_helper_cards.task_board.create_task(
        root=tasks_root,
        title="Get back to Steve",
        lane="in-progress",
        due="",
        tags=[],
        body="",
        metadata={},
    )

    result = task_helper_cards.upsert_helper_card(
        task_path=str(task_path),
        tasks_root=tasks_root,
        cards_root=cards_root,
        kind="outreach",
        primary={
            "title": "Use this draft",
            "url": "sms:+15555555555",
            "subtitle": "Steve",
            "meta": "Text",
        },
        recipient="Steve",
        channel="text",
        subject="Boat ride on April 15",
        draft="Hey Steve, April 15 works for me.",
    )

    content = result["card"]["content"]
    assert "Hey Steve, April 15 works for me." in content
    assert "Use this draft" not in content
    assert "Use draft" not in content
    assert "data-helper-outreach-root" in content
    assert "data-helper-outreach-copy" in content


def test_update_outreach_helper_card_draft_persists_content(tmp_path: Path) -> None:
    tasks_root = tmp_path / "tasks"
    cards_root = tmp_path / "cards"
    task_path = task_helper_cards.task_board.create_task(
        root=tasks_root,
        title="Get back to Steve",
        lane="in-progress",
        due="",
        tags=[],
        body="",
        metadata={},
    )

    result = task_helper_cards.upsert_helper_card(
        task_path=str(task_path),
        tasks_root=tasks_root,
        cards_root=cards_root,
        kind="outreach",
        draft="Old draft",
    )

    updated = task_helper_cards.update_helper_card_draft(
        card_id=result["card"]["id"],
        cards_root=cards_root,
        draft="New draft body",
    )

    content = updated["card"]["content"]
    state = json.loads(
        (cards_root / "instances" / result["card"]["id"] / "state.json").read_text(encoding="utf-8")
    )

    assert "New draft body" in content
    assert state["draft"] == "New draft body"
