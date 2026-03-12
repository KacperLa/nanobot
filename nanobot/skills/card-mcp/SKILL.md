---
name: card-mcp
description: Discover live card data sources and create reusable state-driven card templates with a larger model.
metadata: {"nanobot":{"emoji":"🃏","requires":{"bins":["python3","codex"]}}}
---

# Card MCP

Use this skill when you need to discover exact live data endpoints for a card, create a new reusable card template, or modify an existing saved template.
Use it when no saved template fits the request, when the user explicitly wants a new card design, or when the user wants to tweak the structure/style/behavior of an existing card template.

## Setup

Register this MCP server in `~/.nanobot/config.json`:

```json
{
  "tools": {
    "mcpServers": {
      "card": {
        "command": "python3",
        "args": ["/home/kacper/nanobot/nanobot/skills/card-mcp/scripts/card_mcp.py"],
        "env": {
          "CARD_TEMPLATE_CODEX_MODEL": "gpt-5"
        },
        "tool_timeout": 120
      }
    }
  }
}
```

## MCP Tools

When configured with server key `card`, the tool is:

- `mcp_card_discover_live_card_source(query, limit=3, timeout_seconds=10)`
- `mcp_card_generate_card_template(description, data, template_key="", title="", notes="", save=true, model="gpt-5", timeout_seconds=90)`
- `mcp_card_modify_card_template(template_key, change_request, example_state="", target_template_key="", title="", notes="", preserve_state_schema=true, save=true, model="gpt-5", timeout_seconds=90)`

`mcp_card_generate_card_template` creates a reusable template that reads `template_state` from the standard Nanobot card wrapper. When `save=true`, it writes the template and its example state manifest into `~/.nanobot/cards/templates/<template_key>/`.
`mcp_card_discover_live_card_source` returns exact Home Assistant proxy paths, payload-shape guidance, and matching saved templates for live cards.
`mcp_card_modify_card_template` loads an existing saved template, applies a requested design change with the larger model, and saves the updated template back to disk. By default it preserves the existing `template_state` contract.
When `save=true`, the template-generation tools return a short summary of what changed plus the saved template metadata, not raw HTML.

## Live Card Rules

Before filling `template_state` for a live Home Assistant card, call `mcp_card_discover_live_card_source` when you need help finding the exact proxy path.

For script-backed live cards:
- keep the script under `~/.nanobot/workspace/`
- make it print valid JSON to stdout
- use exact `/script/proxy/<script>.py?arg=...` paths in `template_state`
- validate the state with `mcp_display_validate_card_state` before rendering if you are unsure

For live Home Assistant values, use backend proxy endpoints from card JavaScript:

- Proxy pattern: `/ha/proxy/{path}`
- Example state endpoint: `/ha/proxy/states/sensor.bedroom_co2`
- Script proxy pattern: `/script/proxy/<script>.py?arg=--flag&arg=value`

Important:

- Use only the exact proxy paths returned by `mcp_card_discover_live_card_source`.
- Do not invent entity ids, endpoint names, or response field names.
- If the tool returns a matching saved template, reuse that template structure unless the user asks for a redesign.
- Never embed API keys or bearer tokens in card HTML.
- Never call Home Assistant host directly from card HTML.
- Always call backend proxy paths (`/ha/proxy/...`) so auth stays server-side.
- For workspace scripts, call the script proxy path (`/script/proxy/...`) so the card stays template-state driven.

## Template Contract

Saved templates are reusable layout/behavior shells. A card instance provides the `template_state` JSON.

The web UI injects state like this inside the card wrapper:

```html
<div data-nanobot-card-root>
  <script type="application/json" data-card-state>{"title":"Bedroom CO2","source_url":"/ha/proxy/states/sensor.co2"}</script>
  <!-- template.html content -->
</div>
```

Inside template scripts, read state with:

```js
const root = document.currentScript?.closest("[data-nanobot-card-root]");
const state = window.__nanobotGetCardState?.(document.currentScript) || {};
```

## Recommended Workflow

1. Gather the data requirements from the user.
2. Check `CARD_TEMPLATES.md` for an existing saved template.
3. If the card needs live Home Assistant data, call `mcp_card_discover_live_card_source` only to find exact proxy paths.
4. Fill a `template_state` JSON object with the live/static data the template expects.
5. Call `mcp_display_validate_card_state` if you want to validate state URLs before rendering.
6. Call `mcp_display_render_card` with `template_key` and `template_state`.
7. Only call `mcp_card_generate_card_template` when no saved template fits or the user asks for a new design.
8. When the user asks to tweak an existing card design, call `mcp_card_modify_card_template` with that card's `template_key` instead of generating a brand new template.
9. After a successful saved template create/modify call, trust the tool result. Do not manually inspect or edit template files unless the tool failed.

## Template Modification Rules

When the user wants to change the appearance or structure of an existing card:

1. Reuse the existing `template_key` unless the user explicitly wants a separate variant.
2. Call `mcp_card_modify_card_template` with a focused `change_request`.
3. Leave `preserve_state_schema=true` unless the user explicitly wants the card's data contract changed.
4. If you need a new variant instead of overwriting the old template, set `target_template_key` to a new key.
5. After modifying the template, keep rendering cards through `mcp_display_render_card` with `template_key + template_state`.
6. Do not pass raw HTML into `mcp_display_render_card`. That tool renders saved templates from state.
7. Do not read or edit template files directly after a successful `mcp_card_modify_card_template` call. That MCP call is the authoritative editing step.
