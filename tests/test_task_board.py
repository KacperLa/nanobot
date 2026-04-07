from __future__ import annotations

import importlib.util
import sys
from datetime import datetime
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "task_board.py"
SPEC = importlib.util.spec_from_file_location("task_board", SCRIPT_PATH)
assert SPEC and SPEC.loader
task_board = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = task_board
SPEC.loader.exec_module(task_board)


def test_ensure_board_creates_lane_directories(tmp_path: Path) -> None:
    root = tmp_path / "tasks"

    task_board.ensure_board(root)

    assert (root / "README.md").is_file()
    assert (root / "_template.md").is_file()
    for lane in task_board.LANES:
        assert (root / lane).is_dir()


def test_create_and_list_task(tmp_path: Path) -> None:
    root = tmp_path / "tasks"

    created = task_board.create_task(
        root=root,
        title="Plan Life OS task board",
        lane="backlog",
        due="2026-03-20T12:00:00-04:00",
        tags=["life-os", "planning"],
        body="Capture the first board shape.",
    )

    tasks = task_board.collect_tasks(root)

    assert created.is_file()
    assert len(tasks) == 1
    assert tasks[0].title == "Plan Life OS task board"
    assert tasks[0].lane == "backlog"
    assert tasks[0].due == "2026-03-20T12:00:00-04:00"
    assert tasks[0].tags == ["#life-os", "#planning"]


def test_move_task_updates_lane_and_file_location(tmp_path: Path) -> None:
    root = tmp_path / "tasks"
    created = task_board.create_task(
        root=root,
        title="Build the kanban card",
        lane="backlog",
        due="",
        tags=[],
        body="",
    )

    destination = task_board.move_task(root, str(created), "in-progress")
    moved = task_board.parse_task(destination)

    assert not created.exists()
    assert destination.parent.name == "in-progress"
    assert moved.lane == "in-progress"
    assert moved.updated


def test_create_task_in_committed_sets_committed_for_metadata(tmp_path: Path) -> None:
    root = tmp_path / "tasks"

    created = task_board.create_task(
        root=root,
        title="Commit tomorrow's focus",
        lane="committed",
        due="",
        tags=[],
        body="",
    )
    committed = task_board.parse_task(created)

    assert committed.lane == "committed"
    assert committed.metadata["committed_for"]


def test_move_task_to_committed_sets_committed_for_metadata(tmp_path: Path) -> None:
    root = tmp_path / "tasks"
    created = task_board.create_task(
        root=root,
        title="Block tomorrow morning for trip planning",
        lane="backlog",
        due="",
        tags=[],
        body="",
    )

    destination = task_board.move_task(root, str(created), "committed")
    moved = task_board.parse_task(destination)

    assert destination.parent.name == "committed"
    assert moved.lane == "committed"
    assert moved.metadata["committed_for"]


def test_collect_tasks_promotes_committed_tasks_on_or_after_committed_day(tmp_path: Path) -> None:
    root = tmp_path / "tasks"
    created = task_board.create_task(
        root=root,
        title="Do the committed thing",
        lane="committed",
        due="",
        tags=[],
        body="",
        metadata={"committed_for": "2026-03-24"},
    )

    tasks = task_board.collect_tasks(
        root,
        now=datetime.fromisoformat("2026-03-24T09:00:00-04:00"),
    )

    promoted = next(task for task in tasks if task.title == "Do the committed thing")
    assert not created.exists()
    assert promoted.lane == "in-progress"
    assert promoted.path.parent.name == "in-progress"
    assert "committed_for" not in promoted.metadata


def test_add_and_remove_tags_updates_task_file(tmp_path: Path) -> None:
    root = tmp_path / "tasks"
    created = task_board.create_task(
        root=root,
        title="Reach out to Kevin about RAM",
        lane="backlog",
        due="",
        tags=["framework"],
        body="",
    )

    updated = task_board.add_tags_to_task(root, str(created), ["openhack", "#framework"])
    parsed = task_board.parse_task(updated)
    assert parsed.tags == ["#framework", "#openhack"]

    updated = task_board.remove_tags_from_task(root, str(updated), ["framework"])
    parsed = task_board.parse_task(updated)
    assert parsed.tags == ["#openhack"]


def test_edit_task_updates_title_and_body_without_moving_file(tmp_path: Path) -> None:
    root = tmp_path / "tasks"
    created = task_board.create_task(
        root=root,
        title="Reach out to Kevin about RAM",
        lane="backlog",
        due="",
        tags=["framework"],
        body="Old notes.",
    )

    updated = task_board.edit_task(
        root,
        str(created),
        title="Reach out to Kevin about framework RAM",
        body="New description.",
    )
    parsed = task_board.parse_task(updated)

    assert updated == created
    assert parsed.title == "Reach out to Kevin about framework RAM"
    assert parsed.body == "New description."
    assert parsed.lane == "backlog"
    assert parsed.tags == ["#framework"]


def test_ensure_tag_folder_and_query_related_tasks(tmp_path: Path) -> None:
    root = tmp_path / "tasks"
    task_board.create_task(
        root=root,
        title="Pack for Japan",
        lane="backlog",
        due="",
        tags=["travel", "japan"],
        body="",
    )
    task_board.create_task(
        root=root,
        title="Book flight",
        lane="in-progress",
        due="",
        tags=["#japan"],
        body="",
    )
    tag_note = task_board.ensure_tag_folder(
        root,
        "japan",
        title="Japan Trip",
        summary="Trip planning and prep.",
    )

    result = task_board.query_tasks(root, tags=["#japan"], match="all")

    assert tag_note.is_file()
    assert result["counts"]["total_tasks"] == 2
    assert result["tags"][0]["tag"] == "#japan"
    assert result["tags"][0]["exists"] is True
    assert result["tags"][0]["title"] == "Japan Trip"
    assert "Trip planning and prep." in result["tags"][0]["summary"]


def test_list_tag_summaries_includes_context_only_tags(tmp_path: Path) -> None:
    root = tmp_path / "tasks"
    task_board.create_task(
        root=root,
        title="Reach out to Kevin about RAM",
        lane="backlog",
        due="",
        tags=["framework", "openhack"],
        body="",
    )
    task_board.ensure_tag_folder(root, "#japan", title="Japan")

    tags = task_board.list_tag_summaries(root)

    by_tag = {entry["tag"]: entry for entry in tags}
    assert by_tag["#framework"]["task_count"] == 1
    assert by_tag["#openhack"]["task_count"] == 1
    assert by_tag["#japan"]["task_count"] == 0
    assert by_tag["#japan"]["exists"] is True
