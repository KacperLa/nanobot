from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "inbox_board.py"
SPEC = importlib.util.spec_from_file_location("inbox_board", SCRIPT_PATH)
assert SPEC and SPEC.loader
inbox_board = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = inbox_board
SPEC.loader.exec_module(inbox_board)


def test_ensure_inbox_creates_layout(tmp_path: Path) -> None:
    root = tmp_path / "workspace" / "inbox"

    inbox_board.ensure_inbox(root)

    assert (root / "README.md").is_file()
    assert (root / "_template.md").is_file()


def test_capture_and_list_open_items(tmp_path: Path) -> None:
    root = tmp_path / "workspace" / "inbox"

    created = inbox_board.create_item(
        root,
        title="Remember travel adapter",
        kind="task",
        source="agent",
        confidence=0.9,
        suggested_due="2026-04-10",
        tags=["japan", "travel"],
        body="Buy the adapter before the trip.",
    )

    items = inbox_board.collect_items(root)

    assert created.is_file()
    assert len(items) == 1
    assert items[0].title == "Remember travel adapter"
    assert items[0].kind == "task"
    assert items[0].status == "new"
    assert items[0].tags == ["#japan", "#travel"]


def test_capture_without_title_uses_raw_text(tmp_path: Path) -> None:
    root = tmp_path / "workspace" / "inbox"

    created = inbox_board.create_item(
        root,
        title="",
        raw_text="Need to follow up with Kevin about framework RAM this week.",
    )
    parsed = inbox_board.parse_item(created)

    assert parsed.title.startswith("Need to follow up with Kevin")
    assert "## Raw Capture" in parsed.body


def test_update_item_changes_status_and_due(tmp_path: Path) -> None:
    root = tmp_path / "workspace" / "inbox"
    created = inbox_board.create_item(
        root,
        title="Pack chargers",
        tags=["japan"],
    )

    updated = inbox_board.update_item(
        root,
        str(created),
        status="triaged",
        suggested_due="2026-04-12",
        tags=["japan", "travel"],
    )
    parsed = inbox_board.parse_item(updated)

    assert parsed.status == "triaged"
    assert parsed.suggested_due == "2026-04-12"
    assert parsed.tags == ["#japan", "#travel"]


def test_accept_item_as_task_marks_item_accepted_and_creates_task(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    inbox_root = workspace / "inbox"
    tasks_root = workspace / "tasks"

    created = inbox_board.create_item(
        inbox_root,
        title="Pack chargers",
        kind="task",
        tags=["japan", "travel"],
        body="Bring all charging cables.",
        suggested_due="2026-04-12",
    )

    item_path, task_path = inbox_board.accept_item_as_task(
        inbox_root,
        str(created),
        tasks_root=tasks_root,
        lane="backlog",
    )

    accepted = inbox_board.parse_item(item_path)
    task = inbox_board.task_board.parse_task(task_path)

    assert accepted.status == "accepted"
    assert accepted.metadata["accepted_task_path"] == str(task_path)
    assert task.title == "Pack chargers"
    assert task.due == "2026-04-12"
    assert task.tags == ["#japan", "#travel"]


def test_dismiss_item_marks_it_closed(tmp_path: Path) -> None:
    root = tmp_path / "workspace" / "inbox"
    created = inbox_board.create_item(root, title="Random idea")

    dismissed = inbox_board.dismiss_item(root, str(created))
    parsed = inbox_board.parse_item(dismissed)

    assert parsed.status == "dismissed"
    assert parsed.metadata["dismissed_at"]
