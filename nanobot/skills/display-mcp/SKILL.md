---
name: display-mcp
description: UI display tools over MCP for upserting HTML cards and asking question cards in the web UI.
metadata: {"nanobot":{"emoji":"🖼️","requires":{"bins":["python3"]}}}
---

# Display MCP

Use this skill when the agent needs to upsert a saved template card in the web UI or ask the user a multiple-choice question as a card.

## Setup

Register this MCP server in `~/.nanobot/config.json`:

```json
{
  "tools": {
    "mcpServers": {
      "display": {
        "command": "python3",
        "args": ["/home/kacper/nanobot/nanobot/skills/display-mcp/scripts/display_mcp.py"],
        "env": {
          "NANOBOT_API_SOCKET": "/home/kacper/.nanobot/api.sock",
          "DISPLAY_MCP_CHAT_ID": "web",
          "DISPLAY_MCP_TIMEOUT_SECONDS": "120"
        },
        "tool_timeout": 180
      }
    }
  }
}
```

## MCP Tools

When configured with server key `display`, the tools are:

- `mcp_display_render_card(template_key, template_state, title="", chat_id="web", slot="", lane="context", priority=50, context_summary="")`
- `mcp_display_validate_card_state(template_key, template_state)`
- `mcp_display_ask_user(question, choices, title="", chat_id="web", slot="", lane="attention", priority=90, template_key="", context_summary="", timeout_seconds=120)`

## Notes

- Cards are sent through the same API channel used by the web UI.
- Text cards are template-based. The card instance payload is `template_key + template_state`; the backend materializes the HTML from the saved template.
- `mcp_display_render_card` validates that the template exists and that any `/ha/proxy/...` or `/script/proxy/...` source URLs inside `template_state` are valid before sending the card.
- Direct Home Assistant API URLs such as `http://host:8123/api/...` are rejected; use `/ha/proxy/...` instead.
- Workspace script URLs must use exact `/script/proxy/<script>.py?arg=...` paths. Scripts must live under `~/.nanobot/workspace/` and print valid JSON.
- Use `mcp_display_validate_card_state` when you want to check URLs/state before rendering.
- `slot` lets the UI replace/update a card instead of creating duplicates.
- `lane` and `priority` control feed ordering.
- `mcp_display_ask_user` blocks until the user responds (or times out).
- Keep `chat_id` as `"web"` for the default web interface.
