#!/usr/bin/env python3
"""Google Calendar MCP server."""

from __future__ import annotations

import argparse
import socket
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

from nanobot.integrations.google_calendar import (
    DEFAULT_CLIENT_SECRET_PATH,
    DEFAULT_TOKEN_PATH,
    GoogleCalendarConfig,
    build_calendar_service,
    complete_manual_authorization,
    create_event,
    delete_event,
    event_summary,
    list_calendars,
    list_events,
    load_oauth_client_config,
    load_saved_credentials,
    start_manual_authorization,
    update_event,
)

try:
    from mcp.server.fastmcp import FastMCP
except Exception as exc:  # pragma: no cover
    raise RuntimeError(
        "Missing dependency 'mcp'. Install project dependencies before starting the Google Calendar MCP server."
    ) from exc


CONFIG = GoogleCalendarConfig.from_env()
MCP = FastMCP("google-calendar")
_PENDING_MANUAL_AUTH: dict[str, object] | None = None


def _find_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _stop_pending_manual_server() -> None:
    pending = _PENDING_MANUAL_AUTH
    if not pending:
        return
    server = pending.get("server")
    if isinstance(server, ThreadingHTTPServer):
        server.shutdown()
        server.server_close()


def _start_manual_callback_server(port: int) -> ThreadingHTTPServer:
    class _CallbackHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            pending = _PENDING_MANUAL_AUTH
            if pending is not None:
                pending["authorization_response"] = (
                    f"http://127.0.0.1:{self.server.server_port}{self.path}"
                )
            body = (
                "<html><body><h1>Google Calendar authorization received.</h1>"
                "<p>You can return to Nanobot now.</p></body></html>"
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args) -> None:  # noqa: A003
            return

    server = ThreadingHTTPServer(("127.0.0.1", port), _CallbackHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def _sync_cli_into_config(args: argparse.Namespace) -> None:
    global CONFIG
    CONFIG = GoogleCalendarConfig(
        client_secret_path=Path(args.client_secret or CONFIG.client_secret_path).expanduser(),
        token_path=Path(args.token_file or CONFIG.token_path).expanduser(),
        default_calendar=(args.default_calendar or CONFIG.default_calendar).strip() or "primary",
        write_calendar=CONFIG.write_calendar,
        default_timezone=(args.timezone or CONFIG.default_timezone).strip(),
        scopes=CONFIG.scopes,
        oauth_client_json=CONFIG.oauth_client_json,
        oauth_client_id=CONFIG.oauth_client_id,
        oauth_client_secret=CONFIG.oauth_client_secret,
        oauth_auth_uri=CONFIG.oauth_auth_uri,
        oauth_token_uri=CONFIG.oauth_token_uri,
    )


@MCP.tool()
def status() -> dict[str, object]:
    """Show Google Calendar auth/config status."""
    token_exists = CONFIG.token_path.exists()
    client_secret_exists = CONFIG.client_secret_path.exists()
    creds = None
    auth_error = ""
    oauth_source = "unconfigured"
    try:
        creds = load_saved_credentials(CONFIG)
    except Exception as exc:  # pragma: no cover - malformed token files are rare
        auth_error = str(exc)
    try:
        _client_config, oauth_source = load_oauth_client_config(CONFIG)
    except Exception as exc:
        if not auth_error:
            auth_error = str(exc)

    return {
        "authorized": bool(creds and creds.valid),
        "token_file": str(CONFIG.token_path),
        "token_file_exists": token_exists,
        "client_secret_file": str(CONFIG.client_secret_path),
        "client_secret_exists": client_secret_exists,
        "oauth_source": oauth_source,
        "oauth_client_id_configured": bool(CONFIG.oauth_client_id),
        "oauth_client_secret_configured": bool(CONFIG.oauth_client_secret),
        "oauth_client_json_configured": bool(CONFIG.oauth_client_json),
        "default_calendar": CONFIG.default_calendar,
        "write_calendar_restriction": CONFIG.write_calendar,
        "write_restricted": bool(CONFIG.write_calendar),
        "default_timezone": CONFIG.default_timezone,
        "manual_auth_pending": bool(_PENDING_MANUAL_AUTH),
        "manual_auth_callback_received": bool(
            (_PENDING_MANUAL_AUTH or {}).get("authorization_response")
        ),
        "scopes": list(CONFIG.scopes),
        "auth_error": auth_error,
        "authorize_hint": (
            "Set Google OAuth client secrets in the MCP server env or provide the client JSON file, "
            "then run authorize()."
        ),
    }


@MCP.tool()
def authorize(force: bool = False, open_browser: bool = True) -> dict[str, object]:
    """Authorize Google Calendar access via the installed-app OAuth flow."""
    global _PENDING_MANUAL_AUTH
    if not open_browser:
        _stop_pending_manual_server()
        redirect_port = _find_loopback_port()
        server = _start_manual_callback_server(redirect_port)
        flow, details = start_manual_authorization(CONFIG, redirect_port=redirect_port)
        _PENDING_MANUAL_AUTH = {"flow": flow, "server": server, **details}
        return {
            "authorized": False,
            "requires_completion": True,
            "authorization_url": details["authorization_url"],
            "redirect_uri": details["redirect_uri"],
            "state": details["state"],
            "oauth_source": details["oauth_source"],
            "token_file": str(CONFIG.token_path),
        }
    build_calendar_service(
        CONFIG,
        authorize_if_needed=True,
        force_authorize=force,
        open_browser=open_browser,
    )
    return {
        "authorized": True,
        "token_file": str(CONFIG.token_path),
        "client_secret_file": str(CONFIG.client_secret_path),
    }


@MCP.tool(name="authorize_complete")
def authorize_complete_tool(authorization_response: str = "") -> dict[str, object]:
    """Complete a manual Google Calendar OAuth flow from a pasted redirect URL."""
    global _PENDING_MANUAL_AUTH
    pending = _PENDING_MANUAL_AUTH
    if not pending:
        raise RuntimeError("No pending manual Google Calendar authorization flow.")
    flow = pending.get("flow")
    if flow is None:
        raise RuntimeError("Pending manual Google Calendar authorization flow is missing state.")
    response = authorization_response.strip() or str(pending.get("authorization_response") or "").strip()
    if not response:
        raise RuntimeError("Authorization callback has not been received yet.")
    complete_manual_authorization(
        CONFIG,
        flow=flow,
        authorization_response=response,
    )
    _stop_pending_manual_server()
    _PENDING_MANUAL_AUTH = None
    return {
        "authorized": True,
        "token_file": str(CONFIG.token_path),
        "client_secret_file": str(CONFIG.client_secret_path),
    }


@MCP.tool(name="list_calendars")
def list_calendars_tool() -> dict[str, object]:
    """List available Google Calendars for the authorized account."""
    return {"items": list_calendars(CONFIG)}


@MCP.tool(name="list_events")
def list_events_tool(
    calendar: str = "",
    range: str = "upcoming",
    time_min: str = "",
    time_max: str = "",
    query: str = "",
    max_results: int = 20,
    show_deleted: bool = False,
) -> dict[str, object]:
    """List Google Calendar events for a time window or explicit ISO range."""
    return list_events(
        CONFIG,
        calendar=calendar,
        range_name=range,
        time_min=time_min,
        time_max=time_max,
        query=query,
        max_results=max_results,
        show_deleted=show_deleted,
    )


@MCP.tool(name="get_event")
def get_event_tool(event_id: str, calendar: str = "") -> dict[str, object]:
    """Fetch one Google Calendar event by id."""
    service = build_calendar_service(CONFIG)
    calendars = list_calendars(CONFIG)
    from nanobot.integrations.google_calendar import resolve_calendar_id

    calendar_id = resolve_calendar_id(calendars, calendar, CONFIG.default_calendar)
    event = service.events().get(calendarId=calendar_id, eventId=event_id.strip()).execute()
    event["_calendar_id"] = calendar_id
    return {"event": event_summary(event)}


@MCP.tool(name="create_event")
def create_event_tool(
    title: str,
    start: str,
    end: str = "",
    calendar: str = "",
    all_day: bool = False,
    duration_minutes: int = 60,
    description: str = "",
    location: str = "",
    timezone: str = "",
    attendees: list[str] | None = None,
    send_updates: str = "none",
) -> dict[str, object]:
    """Create a Google Calendar event."""
    return {
        "event": create_event(
            CONFIG,
            calendar=calendar,
            title=title,
            start=start,
            end=end,
            all_day=all_day,
            duration_minutes=duration_minutes,
            description=description,
            location=location,
            timezone_name=timezone,
            attendees=attendees,
            send_updates=send_updates,
        )
    }


@MCP.tool(name="update_event")
def update_event_tool(
    event_id: str,
    calendar: str = "",
    title: str = "",
    start: str = "",
    end: str = "",
    all_day: bool | None = None,
    duration_minutes: int = 60,
    description: str | None = None,
    location: str | None = None,
    timezone: str = "",
    attendees: list[str] | None = None,
    send_updates: str = "none",
) -> dict[str, object]:
    """Update an existing Google Calendar event."""
    return {
        "event": update_event(
            CONFIG,
            event_id=event_id,
            calendar=calendar,
            title=title,
            start=start,
            end=end,
            all_day=all_day,
            duration_minutes=duration_minutes,
            description=description,
            location=location,
            timezone_name=timezone,
            attendees=attendees,
            send_updates=send_updates,
        )
    }


@MCP.tool(name="delete_event")
def delete_event_tool(
    event_id: str,
    calendar: str = "",
    send_updates: str = "none",
) -> dict[str, object]:
    """Delete a Google Calendar event."""
    return delete_event(CONFIG, event_id=event_id, calendar=calendar, send_updates=send_updates)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Google Calendar MCP server")
    parser.add_argument(
        "--client-secret",
        default=str(DEFAULT_CLIENT_SECRET_PATH),
        help="Path to the Google OAuth desktop client JSON file.",
    )
    parser.add_argument(
        "--token-file",
        default=str(DEFAULT_TOKEN_PATH),
        help="Path to the persisted Google token JSON file.",
    )
    parser.add_argument(
        "--default-calendar",
        default="primary",
        help="Default calendar id or summary to target.",
    )
    parser.add_argument(
        "--timezone",
        default="",
        help="Default IANA timezone, for example America/New_York.",
    )
    args, _unknown = parser.parse_known_args()
    return args


def main() -> None:
    args = _parse_args()
    _sync_cli_into_config(args)
    MCP.run()


if __name__ == "__main__":
    main()
