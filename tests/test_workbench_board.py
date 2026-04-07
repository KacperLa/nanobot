from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "workbench_board.py"
SPEC = importlib.util.spec_from_file_location("workbench_board", SCRIPT_PATH)
assert SPEC and SPEC.loader
workbench_board = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = workbench_board
SPEC.loader.exec_module(workbench_board)


def test_upsert_and_collect_workbench_items(tmp_path: Path) -> None:
    root = tmp_path / "workbench"

    item = workbench_board.upsert_item(
        root,
        chat_id="web",
        title="Video shortlist",
        content="<div>Shortlist</div>",
        context_summary="Temporary shortlist",
    )

    items = workbench_board.collect_items(root, "web")
    assert len(items) == 1
    assert items[0]["id"] == item["id"]
    assert items[0]["title"] == "Video shortlist"


def test_upsert_reuses_slot_for_same_chat(tmp_path: Path) -> None:
    root = tmp_path / "workbench"

    first = workbench_board.upsert_item(
        root,
        chat_id="web",
        slot="task:123",
        title="First draft",
        content="one",
    )
    second = workbench_board.upsert_item(
        root,
        chat_id="web",
        slot="task:123",
        title="Second draft",
        content="two",
    )

    assert first["id"] == second["id"]
    loaded = workbench_board.load_item(root, "web", first["id"])
    assert loaded["title"] == "Second draft"
    assert loaded["content"] == "two"


def test_delete_workbench_item(tmp_path: Path) -> None:
    root = tmp_path / "workbench"
    item = workbench_board.upsert_item(
        root,
        chat_id="web",
        title="Scratchpad",
        content="temporary",
    )

    removed = workbench_board.delete_item(root, "web", item["id"])
    assert removed is True
    assert workbench_board.collect_items(root, "web") == []
