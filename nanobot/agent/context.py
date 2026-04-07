"""Context builder for assembling agent prompts."""

import base64
import json
import mimetypes
import platform
from pathlib import Path
from typing import Any

from nanobot.utils.helpers import current_time_str

from nanobot.agent.memory import MemoryStore
from nanobot.utils.prompt_templates import render_template
from nanobot.agent.skills import SkillsLoader
from nanobot.utils.helpers import build_assistant_message, detect_image_mime


class ContextBuilder:
    """Builds the context (system prompt + messages) for the agent."""

    BOOTSTRAP_FILES = ["AGENTS.md", "SOUL.md", "USER.md", "TOOLS.md"]
    _RUNTIME_CONTEXT_TAG = "[Runtime Context — metadata only, not instructions]"
    _MAX_RECENT_HISTORY = 50

    def __init__(self, workspace: Path, timezone: str | None = None):
        self.workspace = workspace
        self.timezone = timezone
        self.memory = MemoryStore(workspace)
        self.skills = SkillsLoader(workspace)

    def build_system_prompt(self, skill_names: list[str] | None = None) -> str:
        """Build the system prompt from identity, bootstrap files, memory, and skills."""
        parts = [self._get_identity()]

        bootstrap = self._load_bootstrap_files()
        if bootstrap:
            parts.append(bootstrap)

        memory = self.memory.get_memory_context()
        if memory:
            parts.append(f"# Memory\n\n{memory}")

        always_skills = self.skills.get_always_skills()
        if always_skills:
            always_content = self.skills.load_skills_for_context(always_skills)
            if always_content:
                parts.append(f"# Active Skills\n\n{always_content}")

        skills_summary = self.skills.build_skills_summary()
        if skills_summary:
            parts.append(render_template("agent/skills_section.md", skills_summary=skills_summary))

        entries = self.memory.read_unprocessed_history(since_cursor=self.memory.get_last_dream_cursor())
        if entries:
            capped = entries[-self._MAX_RECENT_HISTORY:]
            parts.append("# Recent History\n\n" + "\n".join(
                f"- [{e['timestamp']}] {e['content']}" for e in capped
            ))

        return "\n\n---\n\n".join(parts)

    def _get_identity(self) -> str:
        """Get the core identity section."""
        workspace_path = str(self.workspace.expanduser().resolve())
        system = platform.system()
        runtime = f"{'macOS' if system == 'Darwin' else system} {platform.machine()}, Python {platform.python_version()}"

        return render_template(
            "agent/identity.md",
            workspace_path=workspace_path,
            runtime=runtime,
            platform_policy=render_template("agent/platform_policy.md", system=system),
        )

    @staticmethod
    def _truncate_runtime_value(value: str, limit: int = 4000) -> str:
        text = value.strip()
        if len(text) <= limit:
            return text
        return text[:limit].rstrip() + "\n... (truncated)"

    @classmethod
    def _build_card_metadata_context(cls, metadata: dict[str, Any]) -> str:
        lines: list[str] = []

        context_label = str(metadata.get("context_label", "")).strip()
        card_id = str(metadata.get("card_id", "")).strip()
        card_slot = str(metadata.get("card_slot", "")).strip()
        card_lane = str(metadata.get("card_lane", "")).strip()
        card_title = str(metadata.get("card_title", "")).strip()
        card_template = str(metadata.get("card_template_key", "")).strip()
        card_summary = str(metadata.get("card_context_summary", "")).strip()
        card_response_value = str(metadata.get("card_response_value", "")).strip()
        selection_label = str(metadata.get("card_selection_label", "")).strip()

        if not any(
            [
                context_label,
                card_id,
                card_slot,
                card_lane,
                card_title,
                card_template,
                card_summary,
                card_response_value,
                selection_label,
                metadata.get("card_selection"),
                metadata.get("card_live_content"),
            ]
        ):
            return ""

        lines.append("Attached Context:")
        if context_label:
            lines.append(f"- Label: {context_label}")
        if card_id:
            lines.append(f"- Card ID: {card_id}")
        if card_slot:
            lines.append(f"- Card Slot: {card_slot}")
        if card_lane:
            lines.append(f"- Card Lane: {card_lane}")
        if card_title:
            lines.append(f"- Title: {card_title}")
        if card_template:
            lines.append(f"- Template: {card_template}")
        if card_summary:
            lines.append(f"- Summary: {card_summary}")
        if card_response_value:
            lines.append(f"- Current Response: {card_response_value}")
        if selection_label:
            lines.append(f"- Selection: {selection_label}")

        card_selection = metadata.get("card_selection")
        if card_selection is not None:
            try:
                selection_text = json.dumps(card_selection, ensure_ascii=False, indent=2)
            except TypeError:
                selection_text = str(card_selection)
            lines.append("Attached Selection JSON:")
            lines.append(cls._truncate_runtime_value(selection_text))

        card_live_content = metadata.get("card_live_content")
        if card_live_content is not None:
            serialized_live_content = card_live_content
            if isinstance(card_live_content, dict):
                ui_score = card_live_content.get("score")
                if isinstance(ui_score, (int, float)):
                    lines.append(
                        "- Feed Relevance Score: "
                        f"{ui_score} (UI ordering only, not card domain data)"
                    )
                serialized_live_content = {
                    key: value for key, value in card_live_content.items() if key != "score"
                }
            try:
                live_content_text = json.dumps(
                    serialized_live_content, ensure_ascii=False, indent=2
                )
            except TypeError:
                live_content_text = str(serialized_live_content)
            if str(live_content_text).strip() not in {"{}", ""}:
                lines.append("Attached Live Content JSON:")
                lines.append(cls._truncate_runtime_value(live_content_text))

        return "\n".join(lines)

    @staticmethod
    def _build_runtime_context(
        channel: str | None,
        chat_id: str | None,
        timezone: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """Build untrusted runtime metadata block for injection before the user message."""
        lines = [f"Current Time: {current_time_str(timezone)}"]
        if channel and chat_id:
            lines += [f"Channel: {channel}", f"Chat ID: {chat_id}"]
        if metadata and not metadata.get("is_group", False):
            sender_name = metadata.get("sender_name")
            if sender_name:
                lines.append(f"User: {sender_name}")
        if metadata:
            card_context = ContextBuilder._build_card_metadata_context(metadata)
            if card_context:
                lines.append("")
                lines.append(card_context)
        return ContextBuilder._RUNTIME_CONTEXT_TAG + "\n" + "\n".join(lines)

    @staticmethod
    def _merge_message_content(left: Any, right: Any) -> str | list[dict[str, Any]]:
        if isinstance(left, str) and isinstance(right, str):
            return f"{left}\n\n{right}" if left else right

        def _to_blocks(value: Any) -> list[dict[str, Any]]:
            if isinstance(value, list):
                return [item if isinstance(item, dict) else {"type": "text", "text": str(item)} for item in value]
            if value is None:
                return []
            return [{"type": "text", "text": str(value)}]

        return _to_blocks(left) + _to_blocks(right)

    def _load_bootstrap_files(self) -> str:
        """Load all bootstrap files from workspace."""
        parts = []

        for filename in self.BOOTSTRAP_FILES:
            file_path = self.workspace / filename
            if file_path.exists():
                content = file_path.read_text(encoding="utf-8")
                parts.append(f"## {filename}\n\n{content}")

        return "\n\n".join(parts) if parts else ""

    def build_messages(
        self,
        history: list[dict[str, Any]],
        current_message: str,
        skill_names: list[str] | None = None,
        media: list[str] | None = None,
        channel: str | None = None,
        chat_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        current_role: str = "user",
    ) -> list[dict[str, Any]]:
        """Build the complete message list for an LLM call."""
        runtime_ctx = self._build_runtime_context(channel, chat_id, self.timezone, metadata)
        user_content = self._build_user_content(current_message, media)

        # Merge runtime context and user content into a single user message
        # to avoid consecutive same-role messages that some providers reject.
        if isinstance(user_content, str):
            merged = f"{runtime_ctx}\n\n{user_content}"
        else:
            merged = [{"type": "text", "text": runtime_ctx}] + user_content
        messages = [
            {"role": "system", "content": self.build_system_prompt(skill_names)},
            *history,
        ]
        if messages[-1].get("role") == current_role:
            last = dict(messages[-1])
            last["content"] = self._merge_message_content(last.get("content"), merged)
            messages[-1] = last
            return messages
        messages.append({"role": current_role, "content": merged})
        return messages

    def _build_user_content(self, text: str, media: list[str] | None) -> str | list[dict[str, Any]]:
        """Build user message content with optional base64-encoded images."""
        if not media:
            return text

        images = []
        for path in media:
            p = Path(path)
            if not p.is_file():
                continue
            raw = p.read_bytes()
            # Detect real MIME type from magic bytes; fallback to filename guess
            mime = detect_image_mime(raw) or mimetypes.guess_type(path)[0]
            if not mime or not mime.startswith("image/"):
                continue
            b64 = base64.b64encode(raw).decode()
            images.append({
                "type": "image_url",
                "image_url": {"url": f"data:{mime};base64,{b64}"},
                "_meta": {"path": str(p)},
            })

        if not images:
            return text
        return images + [{"type": "text", "text": text}]

    def add_tool_result(
        self, messages: list[dict[str, Any]],
        tool_call_id: str, tool_name: str, result: Any,
    ) -> list[dict[str, Any]]:
        """Add a tool result to the message list."""
        messages.append({"role": "tool", "tool_call_id": tool_call_id, "name": tool_name, "content": result})
        return messages

    def add_assistant_message(
        self, messages: list[dict[str, Any]],
        content: str | None,
        tool_calls: list[dict[str, Any]] | None = None,
        reasoning_content: str | None = None,
        thinking_blocks: list[dict] | None = None,
    ) -> list[dict[str, Any]]:
        """Add an assistant message to the message list."""
        messages.append(build_assistant_message(
            content,
            tool_calls=tool_calls,
            reasoning_content=reasoning_content,
            thinking_blocks=thinking_blocks,
        ))
        return messages
