# Google Calendar MCP

Nanobot can use Google Calendar through a local MCP server.

## What This Adds

When configured, Nanobot gets these tools:

- `mcp_google_calendar_status`
- `mcp_google_calendar_authorize`
- `mcp_google_calendar_list_calendars`
- `mcp_google_calendar_list_events`
- `mcp_google_calendar_get_event`
- `mcp_google_calendar_create_event`
- `mcp_google_calendar_update_event`
- `mcp_google_calendar_delete_event`

Use them for real calendar commitments. Do not fake calendar events as tasks or reminders.

## One-Time Setup

1. In Google Cloud Console, create or choose a project.
2. Enable the Google Calendar API.
3. Create an OAuth client for a desktop application.
4. Copy the OAuth client values into the `google_calendar` MCP server env in `~/.nanobot/config.json`:

- `GOOGLE_CALENDAR_CLIENT_ID`
- `GOOGLE_CALENDAR_CLIENT_SECRET`

Optional:
- `GOOGLE_CALENDAR_AUTH_URI`
- `GOOGLE_CALENDAR_TOKEN_URI`
- `GOOGLE_CALENDAR_WRITE_CALENDAR` to restrict create/update/delete to exactly one calendar id or summary while leaving reads unrestricted

The file path method still works as a fallback, but MCP env is now the preferred source.

## Runtime Files

- Preferred secret source: `~/.nanobot/config.json` -> `tools.mcpServers.google_calendar.env`
- Fallback OAuth client JSON: `~/.nanobot/secrets/google_calendar_client_secret.json`
- Saved token: `~/.nanobot/secrets/google_calendar_token.json`

The token file is created automatically after authorization.

## Authorization

After the MCP env is filled in, restart Nanobot so the MCP server loads.
Then ask Nanobot to authorize Google Calendar, or call the tool directly:

```text
mcp_google_calendar_authorize(force=false)
```

The OAuth flow opens a browser on the machine running the Nanobot gateway.

## Example Requests

- "Add dinner with Steve to my calendar tomorrow at 7 PM."
- "Create an all-day event on April 15 called Japan travel day."
- "What is on my Google Calendar this week?"
- "Move my dentist appointment to 3 PM."

## Notes

- The default calendar is `primary`.
- If `GOOGLE_CALENDAR_WRITE_CALENDAR` is set, writes default to that calendar and attempts to write anywhere else are rejected.
- The configured default timezone is `America/New_York`.
- Timed events default to 60 minutes when no end time is given.
- `mcp_google_calendar_status` reports whether OAuth secrets are coming from env, JSON, or are still unconfigured.
