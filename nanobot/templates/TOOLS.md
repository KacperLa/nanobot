# Tool Usage Notes

Tool signatures are provided automatically via function calling.
This file documents non-obvious constraints and usage patterns.

## exec — Safety Limits

- Commands have a configurable timeout (default 60s)
- Dangerous commands are blocked (rm -rf, format, dd, shutdown, etc.)
- Output is truncated at 10,000 characters
- `restrictToWorkspace` config can limit file access to the workspace

## glob — File Discovery

- Use `glob` to find files by pattern before falling back to shell commands
- Simple patterns like `*.py` match recursively by filename
- Use `entry_type="dirs"` when you need matching directories instead of files
- Use `head_limit` and `offset` to page through large result sets
- Prefer this over `exec` when you only need file paths

## grep — Content Search

- Use `grep` to search file contents inside the workspace
- Default behavior returns only matching file paths (`output_mode="files_with_matches"`)
- Supports optional `glob` filtering plus `context_before` / `context_after`
- Supports `type="py"`, `type="ts"`, `type="md"` and similar shorthand filters
- Use `fixed_strings=true` for literal keywords containing regex characters
- Use `output_mode="files_with_matches"` to get only matching file paths
- Use `output_mode="count"` to size a search before reading full matches
- Use `head_limit` and `offset` to page across results
- Prefer this over `exec` for code and history searches
- Binary or oversized files may be skipped to keep results readable

## cron — Scheduled Reminders

- Use `cron` only for explicit scheduled or recurring reminders.
- If the user wants to remember something but does not give a schedule, prefer `inbox_board` instead.
- Never invent a time or recurrence just to use cron.
- Please refer to cron skill for usage.

## inbox_board — Inbox Capture Layer

- Use `inbox_board` to capture low-friction items in `workspace/inbox`.
- Prefer it for vague reminders, ideas, passive listening distilled captures, and things that should be organized later.
- Accept inbox items into the task board only once they are clearly actionable.

## task_board — File-Backed Tasks and Tags

- Use `task_board` to add, list, query, move, and sync markdown tasks in `workspace/tasks`.
- Prefer `task_board` when the user explicitly wants a task created immediately.
- Task lanes are `backlog`, `committed`, `in-progress`, `blocked`, `done`, and `canceled`.
- Tags are the single grouping system across tasks and richer context, and should use hashtag form like `#japan`.
- If a tag needs deeper notes or planning context, create or update `workspace/tags/<tag>/TAG.md`.
- Prefer tag queries when the user asks for everything related to a topic or project-like area.

## task_helper_card — Linked Helper Cards

- Use `task_helper_card` to reduce friction on actionable tasks by preparing a linked helper artifact.
- Typical patterns:
  - watch/learn -> watch helper card
  - read/research -> reading/reference helper card
  - go somewhere -> travel/map helper card
  - buy/order -> shopping/product helper card
  - call/email/reach out -> outreach draft helper card
- `action=augment` can create a useful fallback helper card from the task alone when external results are still weak.
- `action=update_draft` persists edits to an outreach draft helper card.

## workbench_board — Temporary Session Workbench

- Use `workbench_board` for temporary session-scoped visual artifacts that belong on the chat-side workbench instead of the durable feed.
- Prefer it for scratch drafts, shortlists, comparisons, temporary maps, research canvases, ad hoc visualizations, and interactive snippets.
- Prefer persistent feed cards only when the artifact should remain part of the user's durable attention system beyond the current session.

## card_board — File-Backed Web UI Cards

- Use `card_board` when a request is about an attached UI card and runtime metadata already includes a `card_id`.
- Prefer it over reading card template files or curling localhost card endpoints.
- Use it to inspect an attached card, clear editable card content, update `template_state`, or mark a card active/stale/resolved/superseded/archived.
- Do not ask the user to re-provide the card ID if it is already present in runtime metadata.

## Calendar MCP Tools

- If Google Calendar or other calendar MCP tools are available, use them for real calendar events.
- Prefer calendar tools over `task_board` or `cron` when the user explicitly wants something added to their calendar.
