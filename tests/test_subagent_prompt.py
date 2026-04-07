from unittest.mock import MagicMock

from nanobot.agent.subagent import SubagentManager


def test_subagent_prompt_includes_current_feed_guidance(tmp_path) -> None:
    mgr = SubagentManager(
        provider=MagicMock(),
        workspace=tmp_path,
        bus=MagicMock(),
        max_tool_result_chars=4000,
    )

    prompt = mgr._build_subagent_prompt()

    assert "inbox_board" in prompt
    assert "task_board" in prompt
    assert "task_helper_card" in prompt
    assert "workbench_board" in prompt
    assert "card_board" in prompt
    assert "runtime metadata already includes a card ID" in prompt
