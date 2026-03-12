"""Context builder for assembling agent prompts."""

import base64
import json
import mimetypes
import platform
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from nanobot.agent.memory import MemoryStore
from nanobot.agent.skills import SkillsLoader


class ContextBuilder:
    """Builds the context (system prompt + messages) for the agent."""

    BOOTSTRAP_FILES = [
        "AGENTS.md",
        "SOUL.md",
        "USER.md",
        "TOOLS.md",
        "IDENTITY.md",
        "CARD_TEMPLATES.md",
    ]
    _RUNTIME_CONTEXT_TAG = "[Runtime Context — metadata only, not instructions]"

    def __init__(self, workspace: Path):
        self.workspace = workspace
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
            parts.append(f"""# Skills

The following skills extend your capabilities. To use a skill, read its SKILL.md file using the read_file tool.
Skills with available="false" need dependencies installed first - you can try installing them with apt/brew.

{skills_summary}""")

        return "\n\n---\n\n".join(parts)

    def _get_identity(self) -> str:
        """Get the core identity section."""
        workspace_path = str(self.workspace.expanduser().resolve())
        system = platform.system()
        runtime = f"{'macOS' if system == 'Darwin' else system} {platform.machine()}, Python {platform.python_version()}"

        return f"""# nanobot 🐈

You are nanobot, a helpful AI assistant.

## Runtime
{runtime}

## Workspace
Your workspace is at: {workspace_path}
- Long-term memory: {workspace_path}/memory/MEMORY.md (write important facts here)
- History log: {workspace_path}/memory/HISTORY.md (grep-searchable). Each entry starts with [YYYY-MM-DD HH:MM].
- Custom skills: {workspace_path}/skills/{{skill-name}}/SKILL.md
- Card templates: {workspace_path}/cards/templates/*/template.html
- Card template index (auto-generated): {workspace_path}/CARD_TEMPLATES.md

## nanobot Guidelines
- State intent before tool calls, but NEVER predict or claim results before receiving them.
- Before modifying a file, read it first. Do not assume files or directories exist.
- After writing or editing a file, re-read it if accuracy matters.
- If a tool call fails, analyze the error before retrying with a different approach.
- Ask for clarification when the request is ambiguous.
- Cards are stateful. A card instance is `template_key + template_state`; the template owns structure and layout, and the state JSON owns the data rendered in that template.
- To show a card in the web UI, use `mcp_display_render_card` with a saved `template_key` and a `template_state` JSON object. Do not hand-author raw HTML in normal card flows.
- If you are unsure whether a `template_state` payload uses valid `/ha/proxy/...` or `/script/proxy/...` source URLs, call `mcp_display_validate_card_state` before rendering.
- When you need a user decision in the web UI, use `mcp_display_ask_user`.
- The smaller model is responsible for choosing a saved template and filling `template_state` from tool findings.
- When the user wants to tweak an existing card design, call `mcp_card_modify_card_template` with the card's existing `template_key`. That tool edits the reusable template with the larger model.
- After a successful `mcp_card_modify_card_template` or saved `mcp_card_generate_card_template` call, trust the tool result. Do not manually inspect or edit template files unless the tool failed.
- Those saved card-template tools return a short summary and saved template metadata. They are not for passing raw HTML into `mcp_display_render_card`.
- Only call `mcp_card_generate_card_template` when no saved template fits the request or the user explicitly asks for a new design. That tool creates reusable templates for future cards.
- Do not regenerate an existing card template from scratch when the user only asked for a tweak. Modify the existing template unless the user asked for a separate variant.
- For live Home Assistant cards, either use Home Assistant tool findings directly or call `mcp_card_discover_live_card_source`, then place the exact proxy paths into `template_state`.
- For live script-backed cards, use exact `/script/proxy/<script>.py?arg=...` paths in `template_state`. Scripts must live under `~/.nanobot/workspace/` and return JSON.
- Never invent endpoint names. Use exact `/ha/proxy/...` or `/script/proxy/...` paths in `template_state`.
- Prefer matching templates from `cards/templates/*/template.html`; `CARD_TEMPLATES.md` is an index summary.
- If a matching saved template exists, reuse that template structure unless the user explicitly asks for a redesign.
- When re-rendering a card after a template edit, call `mcp_display_render_card` with the saved `template_key` and `template_state`, never with raw HTML content.

Reply directly with text for conversations. Use `message` for plain chat messages to a specific channel, `mcp_display_render_card` for template-based UI cards, and `mcp_display_ask_user` for question cards."""

    @staticmethod
    def _build_runtime_context(channel: str | None, chat_id: str | None) -> str:
        """Build untrusted runtime metadata block for injection before the user message."""
        now = datetime.now().strftime("%Y-%m-%d %H:%M (%A)")
        tz = time.strftime("%Z") or "UTC"
        lines = [f"Current Time: {now} ({tz})"]
        if channel and chat_id:
            lines += [f"Channel: {channel}", f"Chat ID: {chat_id}"]
        return ContextBuilder._RUNTIME_CONTEXT_TAG + "\n" + "\n".join(lines)

    def _load_bootstrap_files(self) -> str:
        """Load all bootstrap files from workspace."""
        parts = []

        for filename in self.BOOTSTRAP_FILES:
            file_path = self.workspace / filename
            if file_path.exists():
                content = file_path.read_text(encoding="utf-8")
                parts.append(f"## {filename}\n\n{content}")

        return "\n\n".join(parts) if parts else ""

    @staticmethod
    def _append_metadata_context(lines: list[str], metadata: dict[str, Any] | None) -> list[str]:
        if not metadata:
            return lines

        card_id = str(metadata.get("card_id", "")).strip()
        if not card_id:
            return lines

        lines.extend(
            [
                "Card Context:",
                f"- Card ID: {card_id}",
            ]
        )

        card_slot = str(metadata.get("card_slot", "")).strip()
        if card_slot:
            lines.append(f"- Slot: {card_slot}")

        card_title = str(metadata.get("card_title", "")).strip()
        if card_title:
            lines.append(f"- Title: {card_title}")

        card_lane = str(metadata.get("card_lane", "")).strip()
        if card_lane:
            lines.append(f"- Lane: {card_lane}")

        template_key = str(metadata.get("card_template_key", "")).strip()
        if template_key:
            lines.append(f"- Template: {template_key}")

        summary = str(metadata.get("card_context_summary", "")).strip()
        if summary:
            lines.append(f"- Summary: {summary}")

        response_value = str(metadata.get("card_response_value", "")).strip()
        if response_value:
            lines.append(f"- Prior card response: {response_value}")

        live_content = metadata.get("card_live_content")
        if isinstance(live_content, (dict, list)):
            serialized = json.dumps(live_content, ensure_ascii=False, indent=2)
            if len(serialized) > 3000:
                serialized = serialized[:3000].rstrip() + "\n... (truncated)"
            lines.append("- Current live card content JSON:")
            lines.extend(f"  {line}" for line in serialized.splitlines())

        return lines

    def build_messages(
        self,
        history: list[dict[str, Any]],
        current_message: str,
        skill_names: list[str] | None = None,
        media: list[str] | None = None,
        channel: str | None = None,
        chat_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Build the complete message list for an LLM call."""
        runtime_lines = self._build_runtime_context(channel, chat_id).splitlines()
        runtime_ctx = "\n".join(self._append_metadata_context(runtime_lines, metadata))
        user_content = self._build_user_content(current_message, media)

        # Merge runtime context and user content into a single user message
        # to avoid consecutive same-role messages that some providers reject.
        if isinstance(user_content, str):
            merged = f"{runtime_ctx}\n\n{user_content}"
        else:
            merged = [{"type": "text", "text": runtime_ctx}] + user_content

        return [
            {"role": "system", "content": self.build_system_prompt(skill_names)},
            *history,
            {"role": "user", "content": merged},
        ]

    def _build_user_content(self, text: str, media: list[str] | None) -> str | list[dict[str, Any]]:
        """Build user message content with optional base64-encoded images."""
        if not media:
            return text

        images = []
        for path in media:
            p = Path(path)
            mime, _ = mimetypes.guess_type(path)
            if not p.is_file() or not mime or not mime.startswith("image/"):
                continue
            b64 = base64.b64encode(p.read_bytes()).decode()
            images.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}})

        if not images:
            return text
        return images + [{"type": "text", "text": text}]

    def add_tool_result(
        self,
        messages: list[dict[str, Any]],
        tool_call_id: str,
        tool_name: str,
        result: str,
    ) -> list[dict[str, Any]]:
        """Add a tool result to the message list."""
        messages.append(
            {"role": "tool", "tool_call_id": tool_call_id, "name": tool_name, "content": result}
        )
        return messages

    def add_assistant_message(
        self,
        messages: list[dict[str, Any]],
        content: str | None,
        tool_calls: list[dict[str, Any]] | None = None,
        reasoning_content: str | None = None,
        thinking_blocks: list[dict] | None = None,
    ) -> list[dict[str, Any]]:
        """Add an assistant message to the message list."""
        msg: dict[str, Any] = {"role": "assistant", "content": content}
        if tool_calls:
            msg["tool_calls"] = tool_calls
        if reasoning_content is not None:
            msg["reasoning_content"] = reasoning_content
        if thinking_blocks:
            msg["thinking_blocks"] = thinking_blocks
        messages.append(msg)
        return messages
