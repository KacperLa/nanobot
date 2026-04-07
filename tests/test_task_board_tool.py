from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from nanobot.agent.tools.task_board import TaskBoardTool


def test_task_board_tool_add_and_list(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    cards_root = tmp_path / "cards"
    tool = TaskBoardTool(workspace=workspace, cards_root=cards_root)

    added_payload = json.loads(
        asyncio.run(
            tool.execute(
                action="add",
                title="Capture Life OS tasks quickly",
                tags=["life-os", "capture"],
                body="Use the agent as the intake path.",
            )
        )
    )

    assert added_payload["task"]["title"] == "Capture Life OS tasks quickly"
    assert added_payload["task"]["lane"] == "backlog"
    assert added_payload["task"]["metadata"]["source"] == "agent"
    assert added_payload["task"]["tags"] == ["#life-os", "#capture"]
    created_card_dir = cards_root / "instances" / Path(added_payload["sync"]["created"][0])
    assert created_card_dir.is_dir()

    listed_payload = json.loads(asyncio.run(tool.execute(action="list")))
    assert len(listed_payload) == 1
    assert listed_payload[0]["title"] == "Capture Life OS tasks quickly"


def test_task_board_tool_move_updates_task_and_cards(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    cards_root = tmp_path / "cards"
    tool = TaskBoardTool(workspace=workspace, cards_root=cards_root)

    added_payload = json.loads(
        asyncio.run(
            tool.execute(
                action="add",
                title="Pack for Japan",
            )
        )
    )
    task_path = added_payload["task"]["path"]
    card_id = added_payload["sync"]["created"][0]

    moved_payload = json.loads(
        asyncio.run(
            tool.execute(
                action="move",
                task=task_path,
                lane="in-progress",
            )
        )
    )

    assert moved_payload["lane"] == "in-progress"
    moved_task_path = Path(moved_payload["task_path"])
    assert moved_task_path.parent.name == "in-progress"
    state = json.loads(
        (cards_root / "instances" / card_id / "state.json").read_text(encoding="utf-8")
    )
    assert state["lane"] == "in-progress"


def test_task_board_tool_move_to_committed_updates_task_and_cards(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    cards_root = tmp_path / "cards"
    tool = TaskBoardTool(workspace=workspace, cards_root=cards_root)

    added_payload = json.loads(
        asyncio.run(
            tool.execute(
                action="add",
                title="Commit tomorrow's focus",
            )
        )
    )
    task_path = added_payload["task"]["path"]
    card_id = added_payload["sync"]["created"][0]

    moved_payload = json.loads(
        asyncio.run(
            tool.execute(
                action="move",
                task=task_path,
                lane="committed",
            )
        )
    )

    assert moved_payload["lane"] == "committed"
    moved_task_path = Path(moved_payload["task_path"])
    assert moved_task_path.parent.name == "committed"
    state = json.loads(
        (cards_root / "instances" / card_id / "state.json").read_text(encoding="utf-8")
    )
    assert state["lane"] == "committed"
    assert state["metadata"]["committed_for"]


def test_task_board_tool_query_and_ensure_tag(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    cards_root = tmp_path / "cards"
    tool = TaskBoardTool(workspace=workspace, cards_root=cards_root)

    asyncio.run(
        tool.execute(
            action="add",
            title="Pack for Japan",
            tags=["travel", "japan"],
        )
    )
    asyncio.run(
        tool.execute(
            action="add",
            title="Reach out to Kevin about RAM",
            tags=["framework", "openhack"],
        )
    )

    ensured_payload = json.loads(
        asyncio.run(
            tool.execute(
                action="ensure_tag",
                tag="#japan",
                title_display="Japan Trip",
                body="Trip planning and prep.",
            )
        )
    )
    queried_payload = json.loads(
        asyncio.run(
            tool.execute(
                action="query",
                tags=["japan"],
            )
        )
    )
    listed_tags_payload = json.loads(asyncio.run(tool.execute(action="list_tags")))

    assert Path(ensured_payload["path"]).is_file()
    assert queried_payload["counts"]["total_tasks"] == 1
    assert queried_payload["tasks"][0]["title"] == "Pack for Japan"
    assert queried_payload["tags"][0]["tag"] == "#japan"
    assert any(entry["tag"] == "#japan" for entry in listed_tags_payload)


def test_task_board_tool_add_and_remove_tags(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    cards_root = tmp_path / "cards"
    tool = TaskBoardTool(workspace=workspace, cards_root=cards_root)

    added_payload = json.loads(
        asyncio.run(
            tool.execute(
                action="add",
                title="Reach out to Kevin about RAM",
                tags=["framework"],
            )
        )
    )
    task_path = added_payload["task"]["path"]
    card_id = added_payload["sync"]["created"][0]

    add_tag_payload = json.loads(
        asyncio.run(
            tool.execute(
                action="add_tag",
                task=task_path,
                tags=["openhack"],
            )
        )
    )
    assert add_tag_payload["task"]["tags"] == ["#framework", "#openhack"]
    state_after_add = json.loads(
        (cards_root / "instances" / card_id / "state.json").read_text(encoding="utf-8")
    )
    assert state_after_add["tags"] == ["#framework", "#openhack"]

    remove_tag_payload = json.loads(
        asyncio.run(
            tool.execute(
                action="remove_tag",
                task=task_path,
                tags=["framework"],
            )
        )
    )
    assert remove_tag_payload["task"]["tags"] == ["#openhack"]
    state_after_remove = json.loads(
        (cards_root / "instances" / card_id / "state.json").read_text(encoding="utf-8")
    )
    assert state_after_remove["tags"] == ["#openhack"]


def test_task_board_tool_edit_updates_task_and_cards(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    cards_root = tmp_path / "cards"
    tool = TaskBoardTool(workspace=workspace, cards_root=cards_root)

    added_payload = json.loads(
        asyncio.run(
            tool.execute(
                action="add",
                title="Capture Life OS tasks quickly",
                description="Use the agent as the intake path.",
            )
        )
    )
    task_path = added_payload["task"]["path"]
    card_id = added_payload["sync"]["created"][0]

    edited_payload = json.loads(
        asyncio.run(
            tool.execute(
                action="edit",
                task=task_path,
                title="Capture Life OS tasks faster",
                description="Use the agent and task cards as the intake path.",
            )
        )
    )

    assert edited_payload["task"]["title"] == "Capture Life OS tasks faster"
    assert edited_payload["task"]["body"] == "Use the agent and task cards as the intake path."
    state = json.loads(
        (cards_root / "instances" / card_id / "state.json").read_text(encoding="utf-8")
    )
    assert state["title"] == "Capture Life OS tasks faster"

    cleared_payload = json.loads(
        asyncio.run(
            tool.execute(
                action="edit",
                task=task_path,
                description="",
            )
        )
    )
    assert cleared_payload["task"]["body"] == ""
    cleared_state = json.loads(
        (cards_root / "instances" / card_id / "state.json").read_text(encoding="utf-8")
    )
    assert cleared_state["body"] in ("", None)
