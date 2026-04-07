from unittest.mock import MagicMock

from nanobot.agent.subagent import SubagentManager


def test_subagent_prompt_includes_current_feed_guidance(tmp_path) -> None:
    mgr = SubagentManager(
        provider=MagicMock(),
        workspace=tmp_path,
        bus=MagicMock(),
    )

    prompt = mgr._build_subagent_prompt()

    assert "inbox_board" in prompt
    assert "task_board" in prompt
    assert "task_helper_card" in prompt
    assert "workbench_board" in prompt
    assert "card_board" in prompt
    assert "do not ask the user to find the card ID again" in prompt
