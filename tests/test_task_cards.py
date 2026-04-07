from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "task_cards.py"
SPEC = importlib.util.spec_from_file_location("task_cards", SCRIPT_PATH)
assert SPEC and SPEC.loader
task_cards = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = task_cards
SPEC.loader.exec_module(task_cards)


def test_sync_task_cards_creates_active_cards_and_prunes_legacy(tmp_path: Path) -> None:
    tasks_root = tmp_path / "tasks"
    cards_root = tmp_path / "cards"
    instances_dir = cards_root / "instances"

    task_cards.task_board.create_task(
        root=tasks_root,
        title="Plan migration",
        lane="backlog",
        due="",
        tags=["life-os"],
        body="",
        metadata={"source": "manual"},
    )
    task_cards.task_board.create_task(
        root=tasks_root,
        title="Already done",
        lane="done",
        due="",
        tags=[],
        body="",
        metadata={},
    )
    legacy_dir = instances_dir / "legacy-todo"
    legacy_dir.mkdir(parents=True, exist_ok=True)
    (legacy_dir / "card.json").write_text(
        json.dumps({"slot": "todo:todo.kacpers_to_do:abc"}, indent=2),
        encoding="utf-8",
    )

    result = task_cards.sync_task_cards(
        tasks_root=tasks_root,
        instances_dir=instances_dir,
        template_key="todo-item-live",
        chat_id="web",
        prune_legacy=True,
    )

    assert result["count"] == 1
    assert len(result["created"]) == 1
    created_dir = instances_dir / result["created"][0]
    state = json.loads((created_dir / "state.json").read_text(encoding="utf-8"))
    assert state["lane"] == "backlog"
    assert state["metadata"]["source"] == "manual"
    assert not legacy_dir.exists()


def test_move_task_and_sync_updates_lane(tmp_path: Path) -> None:
    tasks_root = tmp_path / "tasks"
    instances_dir = tmp_path / "cards" / "instances"
    task_path = task_cards.task_board.create_task(
        root=tasks_root,
        title="Ship task board",
        lane="backlog",
        due="",
        tags=[],
        body="",
        metadata={"source_uid": "abc-123"},
    )
    task_cards.sync_task_cards(tasks_root=tasks_root, instances_dir=instances_dir)

    result = task_cards.move_task_and_sync(
        str(task_path),
        "in-progress",
        tasks_root=tasks_root,
        instances_dir=instances_dir,
    )

    moved_path = Path(result["task_path"])
    assert moved_path.parent.name == "in-progress"
    task_state = json.loads(
        (instances_dir / "task-abc-123" / "state.json").read_text(encoding="utf-8")
    )
    assert task_state["lane"] == "in-progress"


def test_sync_task_cards_keeps_committed_tasks_active(tmp_path: Path) -> None:
    tasks_root = tmp_path / "tasks"
    instances_dir = tmp_path / "cards" / "instances"

    task_cards.task_board.create_task(
        root=tasks_root,
        title="Commit tomorrow's focus",
        lane="committed",
        due="",
        tags=["life-os"],
        body="",
        metadata={"source": "manual"},
    )

    result = task_cards.sync_task_cards(
        tasks_root=tasks_root,
        instances_dir=instances_dir,
        template_key="todo-item-live",
        chat_id="web",
    )

    assert result["count"] == 1
    created_dir = instances_dir / result["created"][0]
    card = json.loads((created_dir / "card.json").read_text(encoding="utf-8"))
    state = json.loads((created_dir / "state.json").read_text(encoding="utf-8"))
    assert card["lane"] == "attention"
    assert card["priority"] == 80
    assert state["lane"] == "committed"


def test_import_home_assistant_tasks_skips_existing_source_uids(tmp_path: Path, monkeypatch) -> None:
    tasks_root = tmp_path / "tasks"
    task_cards.task_board.create_task(
        root=tasks_root,
        title="Pack for Japan",
        lane="backlog",
        due="",
        tags=["home-assistant"],
        body="",
        metadata={
            "source": "home_assistant",
            "source_entity_id": "todo.kacpers_to_do",
            "source_uid": "uid-1",
        },
    )

    def fake_fetch(entity_id: str, *, config_path: Path) -> dict[str, object]:
        assert entity_id == "todo.kacpers_to_do"
        return {
            "entity_id": entity_id,
            "list_name": "Kacper's To-Do",
            "generated_at": "2026-03-16T12:00:00+00:00",
            "items": [
                {"uid": "uid-1", "summary": "Pack for Japan", "status": "needs_action"},
                {"uid": "uid-2", "summary": "Do the laundry", "status": "needs_action"},
            ],
        }

    monkeypatch.setattr(task_cards, "fetch_home_assistant_todos", fake_fetch)

    result = task_cards.import_home_assistant_tasks(
        "todo.kacpers_to_do",
        tasks_root=tasks_root,
        lane="backlog",
    )

    assert result["skipped_existing"] == ["uid-1"]
    assert len(result["created"]) == 1
