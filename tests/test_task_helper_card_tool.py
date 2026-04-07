from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from nanobot.agent.tools.task_helper_card import TaskHelperCardTool
from nanobot.agent.tools.task_board import TaskBoardTool


def test_task_helper_card_tool_augment_infers_watch_and_creates_fallback(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    cards_root = tmp_path / "cards"
    task_tool = TaskBoardTool(workspace=workspace, cards_root=cards_root)
    helper_tool = TaskHelperCardTool(workspace=workspace, cards_root=cards_root)

    task_payload = json.loads(
        asyncio.run(
            task_tool.execute(
                action="add",
                title="Find and watch a good video about sourdough starter",
            )
        )
    )
    task_path = task_payload["task"]["path"]

    helper_payload = json.loads(
        asyncio.run(
            helper_tool.execute(
                action="augment",
                task=task_path,
            )
        )
    )

    card = helper_payload["card"]
    state = json.loads(
        (cards_root / "instances" / card["id"] / "state.json").read_text(encoding="utf-8")
    )

    assert helper_payload["helper_kind"] == "watch"
    assert card["title"] == "Watch: Find and watch a good video about sourdough starter"
    assert "youtube.com/results?search_query=" in card["content"]
    assert state["primary"]["title"] == "Search YouTube"


def test_task_helper_card_tool_sync_removes_helper_for_completed_task(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    cards_root = tmp_path / "cards"
    task_tool = TaskBoardTool(workspace=workspace, cards_root=cards_root)
    helper_tool = TaskHelperCardTool(workspace=workspace, cards_root=cards_root)

    task_payload = json.loads(
        asyncio.run(
            task_tool.execute(
                action="add",
                title="Call Kevin about the RAM shipment",
            )
        )
    )
    task_path = task_payload["task"]["path"]

    helper_payload = json.loads(
        asyncio.run(
            helper_tool.execute(
                action="augment",
                task=task_path,
            )
        )
    )
    card_id = helper_payload["card"]["id"]
    assert (cards_root / "instances" / card_id).is_dir()

    asyncio.run(
        task_tool.execute(
            action="move",
            task=task_path,
            lane="done",
        )
    )

    assert not (cards_root / "instances" / card_id).exists()


def test_task_helper_card_tool_updates_outreach_draft(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    cards_root = tmp_path / "cards"
    task_tool = TaskBoardTool(workspace=workspace, cards_root=cards_root)
    helper_tool = TaskHelperCardTool(workspace=workspace, cards_root=cards_root)

    task_payload = json.loads(
        asyncio.run(
            task_tool.execute(
                action="add",
                title="Get back to Steve",
                description="Reply about the boat ride.",
            )
        )
    )
    task_path = task_payload["task"]["path"]

    helper_payload = json.loads(
        asyncio.run(
            helper_tool.execute(
                action="augment",
                task=task_path,
                kind="outreach",
                draft="Old draft",
            )
        )
    )

    card_id = helper_payload["card"]["id"]
    updated = json.loads(
        asyncio.run(
            helper_tool.execute(
                action="update_draft",
                card_id=card_id,
                draft="New draft",
            )
        )
    )

    assert updated["card"]["id"] == card_id
    assert updated["card"]["template_state"]["draft"] == "New draft"
    assert "New draft" in updated["card"]["content"]
