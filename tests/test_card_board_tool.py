from __future__ import annotations

import asyncio
import json
from pathlib import Path

from nanobot.agent.tools.card_board import CardBoardTool


def _write_card_fixture(cards_root: Path, card_id: str = "live-calorie-tracker") -> Path:
    instance_dir = cards_root / "instances" / card_id
    instance_dir.mkdir(parents=True, exist_ok=True)
    (instance_dir / "card.json").write_text(
        json.dumps(
            {
                "id": card_id,
                "kind": "text",
                "title": "Calories",
                "lane": "context",
                "priority": 88,
                "state": "active",
                "template_key": "list-total-live",
                "chat_id": "web",
                "created_at": "2026-04-01T00:00:00+00:00",
                "updated_at": "2026-04-01T00:00:00+00:00",
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (instance_dir / "state.json").write_text(
        json.dumps(
            {
                "left_label": "Cal",
                "right_label": "Food",
                "rows": [
                    {"value": "200", "name": "Chicken"},
                    {"value": "150", "name": "Avocado"},
                ],
                "score": 88,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return instance_dir


def test_card_board_tool_show_and_clear_content(tmp_path: Path) -> None:
    cards_root = tmp_path / "cards"
    _write_card_fixture(cards_root)
    tool = CardBoardTool(cards_root=cards_root)

    shown = json.loads(asyncio.run(tool.execute(action="show", card_id="live-calorie-tracker")))
    assert shown["card"]["id"] == "live-calorie-tracker"
    assert shown["card"]["template_state"]["rows"][0]["name"] == "Chicken"

    cleared = json.loads(
        asyncio.run(tool.execute(action="clear_content", card_id="live-calorie-tracker"))
    )
    assert cleared["card"]["template_state"]["rows"] == []
    assert cleared["card"]["template_state"]["score"] == 88


def test_card_board_tool_set_state_and_update_template_state(tmp_path: Path) -> None:
    cards_root = tmp_path / "cards"
    _write_card_fixture(cards_root, card_id="live-calorie-tracker-2")
    tool = CardBoardTool(cards_root=cards_root)

    updated = json.loads(
        asyncio.run(
            tool.execute(
                action="update_template_state",
                card_id="live-calorie-tracker-2",
                template_state={"total_suffix": "kcal"},
            )
        )
    )
    assert updated["card"]["template_state"]["total_suffix"] == "kcal"
    assert updated["card"]["template_state"]["rows"][0]["name"] == "Chicken"

    state_changed = json.loads(
        asyncio.run(
            tool.execute(
                action="set_state",
                card_id="live-calorie-tracker-2",
                state="stale",
            )
        )
    )
    assert state_changed["card"]["state"] == "stale"
