from __future__ import annotations

import asyncio
import json
from pathlib import Path

from nanobot.agent.tools.inbox_board import InboxBoardTool


def test_inbox_board_tool_capture_and_list(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    cards_root = tmp_path / "cards"
    tool = InboxBoardTool(workspace=workspace, cards_root=cards_root)

    captured_payload = json.loads(
        asyncio.run(
            tool.execute(
                action="capture",
                title="Remember travel adapter",
                kind="task",
                tags=["japan", "travel"],
                description="Buy this before the trip.",
                confidence=0.9,
            )
        )
    )

    assert captured_payload["item"]["title"] == "Remember travel adapter"
    assert captured_payload["item"]["status"] == "new"
    assert captured_payload["item"]["tags"] == ["#japan", "#travel"]

    listed_payload = json.loads(asyncio.run(tool.execute(action="list")))
    assert len(listed_payload) == 1
    assert listed_payload[0]["title"] == "Remember travel adapter"


def test_inbox_board_tool_update_and_dismiss(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    cards_root = tmp_path / "cards"
    tool = InboxBoardTool(workspace=workspace, cards_root=cards_root)

    captured_payload = json.loads(
        asyncio.run(
            tool.execute(
                action="capture",
                title="Pack chargers",
                tags=["japan"],
            )
        )
    )
    item_path = captured_payload["item"]["path"]

    updated_payload = json.loads(
        asyncio.run(
            tool.execute(
                action="update",
                item=item_path,
                status="triaged",
                due="2026-04-12",
                tags=["japan", "travel"],
            )
        )
    )
    assert updated_payload["item"]["status"] == "triaged"
    assert updated_payload["item"]["suggested_due"] == "2026-04-12"
    assert updated_payload["item"]["tags"] == ["#japan", "#travel"]

    dismissed_payload = json.loads(
        asyncio.run(
            tool.execute(
                action="dismiss",
                item=item_path,
            )
        )
    )
    assert dismissed_payload["item"]["status"] == "dismissed"


def test_inbox_board_tool_accept_task_creates_task_and_syncs_cards(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    cards_root = tmp_path / "cards"
    tool = InboxBoardTool(workspace=workspace, cards_root=cards_root)

    captured_payload = json.loads(
        asyncio.run(
            tool.execute(
                action="capture",
                title="Renew passport",
                kind="task",
                tags=["japan", "travel"],
                description="Fill out the renewal paperwork.",
                due="2026-04-15",
            )
        )
    )
    item_path = captured_payload["item"]["path"]

    accepted_payload = json.loads(
        asyncio.run(
            tool.execute(
                action="accept_task",
                item=item_path,
                lane="backlog",
            )
        )
    )

    assert accepted_payload["item"]["status"] == "accepted"
    assert accepted_payload["task"]["title"] == "Renew passport"
    assert accepted_payload["task"]["due"] == "2026-04-15"
    created_card_dir = cards_root / "instances" / Path(accepted_payload["sync"]["created"][0])
    assert created_card_dir.is_dir()
