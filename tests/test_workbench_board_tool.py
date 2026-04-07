from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from nanobot.agent.tools.workbench_board import WorkbenchBoardTool


def test_workbench_board_tool_upsert_and_list(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    tool = WorkbenchBoardTool(workspace=workspace)

    upsert_payload = json.loads(
        asyncio.run(
            tool.execute(
                action="upsert",
                chat_id="web",
                title="Comparison",
                content="<div>Compare options</div>",
                slot="compare:1",
            )
        )
    )

    item_id = upsert_payload["item"]["id"]
    listed = json.loads(asyncio.run(tool.execute(action="list", chat_id="web")))

    assert listed["items"][0]["id"] == item_id
    assert listed["items"][0]["title"] == "Comparison"


def test_workbench_board_tool_remove(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    tool = WorkbenchBoardTool(workspace=workspace)

    upsert_payload = json.loads(
        asyncio.run(
            tool.execute(
                action="upsert",
                chat_id="web",
                title="Temporary map",
                content="<div>Map</div>",
            )
        )
    )
    item_id = upsert_payload["item"]["id"]

    removed = json.loads(
        asyncio.run(tool.execute(action="remove", chat_id="web", item_id=item_id))
    )
    listed = json.loads(asyncio.run(tool.execute(action="list", chat_id="web")))

    assert removed["removed"] is True
    assert listed["items"] == []
