"""Google Calendar integration helpers."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

SCOPES = ("https://www.googleapis.com/auth/calendar",)
DEFAULT_CLIENT_SECRET_PATH = (
    Path.home() / ".nanobot" / "secrets" / "google_calendar_client_secret.json"
)
DEFAULT_TOKEN_PATH = Path.home() / ".nanobot" / "secrets" / "google_calendar_token.json"


@dataclass(slots=True)
class GoogleCalendarConfig:
    client_secret_path: Path
    token_path: Path
    default_calendar: str = "primary"
    write_calendar: str = ""
    default_timezone: str = ""
    scopes: tuple[str, ...] = SCOPES
    oauth_client_json: str = ""
    oauth_client_id: str = ""
    oauth_client_secret: str = ""
    oauth_auth_uri: str = "https://accounts.google.com/o/oauth2/auth"
    oauth_token_uri: str = "https://oauth2.googleapis.com/token"

    @classmethod
    def from_env(cls) -> "GoogleCalendarConfig":
        return cls(
            client_secret_path=Path(
                os.getenv("GOOGLE_CALENDAR_CLIENT_SECRET_FILE", str(DEFAULT_CLIENT_SECRET_PATH))
            ).expanduser(),
            token_path=Path(
                os.getenv("GOOGLE_CALENDAR_TOKEN_FILE", str(DEFAULT_TOKEN_PATH))
            ).expanduser(),
            default_calendar=os.getenv("GOOGLE_CALENDAR_DEFAULT_CALENDAR", "primary").strip()
            or "primary",
            write_calendar=os.getenv("GOOGLE_CALENDAR_WRITE_CALENDAR", "").strip(),
            default_timezone=os.getenv("GOOGLE_CALENDAR_TIMEZONE", "").strip(),
            oauth_client_json=os.getenv("GOOGLE_CALENDAR_OAUTH_CLIENT_JSON", "").strip(),
            oauth_client_id=os.getenv("GOOGLE_CALENDAR_CLIENT_ID", "").strip(),
            oauth_client_secret=os.getenv("GOOGLE_CALENDAR_CLIENT_SECRET", "").strip(),
            oauth_auth_uri=os.getenv(
                "GOOGLE_CALENDAR_AUTH_URI", "https://accounts.google.com/o/oauth2/auth"
            ).strip()
            or "https://accounts.google.com/o/oauth2/auth",
            oauth_token_uri=os.getenv(
                "GOOGLE_CALENDAR_TOKEN_URI", "https://oauth2.googleapis.com/token"
            ).strip()
            or "https://oauth2.googleapis.com/token",
        )


def load_oauth_client_config(config: GoogleCalendarConfig) -> tuple[dict[str, Any], str]:
    if config.oauth_client_json:
        try:
            payload = json.loads(config.oauth_client_json)
        except json.JSONDecodeError as exc:
            raise RuntimeError("GOOGLE_CALENDAR_OAUTH_CLIENT_JSON is not valid JSON.") from exc
        if not isinstance(payload, dict):
            raise RuntimeError("GOOGLE_CALENDAR_OAUTH_CLIENT_JSON must decode to an object.")
        return payload, "env_json"

    if config.oauth_client_id and config.oauth_client_secret:
        return (
            {
                "installed": {
                    "client_id": config.oauth_client_id,
                    "client_secret": config.oauth_client_secret,
                    "auth_uri": config.oauth_auth_uri,
                    "token_uri": config.oauth_token_uri,
                    "redirect_uris": ["http://localhost"],
                }
            },
            "env",
        )

    if not config.client_secret_path.exists():
        raise RuntimeError(
            "Google Calendar OAuth client config is missing. Set GOOGLE_CALENDAR_CLIENT_ID and "
            "GOOGLE_CALENDAR_CLIENT_SECRET in the MCP server env, set "
            "GOOGLE_CALENDAR_OAUTH_CLIENT_JSON, or provide the client JSON file at "
            f"{config.client_secret_path}."
        )

    try:
        payload = json.loads(config.client_secret_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"Google Calendar OAuth client file is not valid JSON: {config.client_secret_path}"
        ) from exc
    if not isinstance(payload, dict):
        raise RuntimeError(
            f"Google Calendar OAuth client file must contain a JSON object: {config.client_secret_path}"
        )
    return payload, "file"


def _normalize_zoneinfo(timezone_name: str | None) -> ZoneInfo | None:
    if not timezone_name:
        return None
    try:
        return ZoneInfo(timezone_name.strip())
    except ZoneInfoNotFoundError as exc:  # pragma: no cover - depends on system tz db
        raise ValueError(f"Unknown timezone '{timezone_name}'.") from exc


def _parse_date(value: str) -> date:
    text = str(value or "").strip()
    if not text:
        raise ValueError("Date value is required.")
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"Invalid date '{value}'. Use YYYY-MM-DD.") from exc


def _parse_datetime(value: str, timezone_name: str | None = None) -> datetime:
    text = str(value or "").strip()
    if not text:
        raise ValueError("Datetime value is required.")
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(
            f"Invalid datetime '{value}'. Use an ISO timestamp like 2026-04-05T18:30:00-04:00."
        ) from exc
    if parsed.tzinfo is not None:
        return parsed
    tz = _normalize_zoneinfo(timezone_name) or datetime.now().astimezone().tzinfo
    if tz is None:  # pragma: no cover - local tz should always exist
        raise ValueError("Could not determine a local timezone for a naive datetime.")
    return parsed.replace(tzinfo=tz)


def _time_window(
    range_name: str, timezone_name: str | None = None
) -> tuple[datetime, datetime | None]:
    now = datetime.now(_normalize_zoneinfo(timezone_name)).astimezone()
    normalized = range_name.strip().lower()
    if normalized in {"", "upcoming"}:
        return now, None
    if normalized == "today":
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        return start, start + timedelta(days=1)
    if normalized == "tomorrow":
        start = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        return start, start + timedelta(days=1)
    if normalized in {"week", "next_7_days"}:
        return now, now + timedelta(days=7)
    if normalized in {"month", "next_30_days"}:
        return now, now + timedelta(days=30)
    raise ValueError(
        "range must be one of: upcoming, today, tomorrow, week, next_7_days, month, next_30_days"
    )


def build_event_payload(
    *,
    title: str,
    start: str,
    end: str = "",
    all_day: bool = False,
    duration_minutes: int = 60,
    description: str = "",
    location: str = "",
    timezone_name: str = "",
    attendees: list[str] | None = None,
) -> dict[str, Any]:
    title_text = title.strip()
    if not title_text:
        raise ValueError("title is required")

    body: dict[str, Any] = {"summary": title_text}
    if description.strip():
        body["description"] = description.strip()
    if location.strip():
        body["location"] = location.strip()
    cleaned_attendees = [email.strip() for email in (attendees or []) if email.strip()]
    if cleaned_attendees:
        body["attendees"] = [{"email": email} for email in cleaned_attendees]

    if all_day:
        start_date = _parse_date(start)
        end_date = _parse_date(end) if end.strip() else start_date + timedelta(days=1)
        if end_date <= start_date:
            raise ValueError("All-day event end date must be after the start date.")
        body["start"] = {"date": start_date.isoformat()}
        body["end"] = {"date": end_date.isoformat()}
        return body

    if duration_minutes <= 0:
        raise ValueError("duration_minutes must be > 0.")
    start_dt = _parse_datetime(start, timezone_name or None)
    end_dt = (
        _parse_datetime(end, timezone_name or None)
        if end.strip()
        else start_dt + timedelta(minutes=duration_minutes)
    )
    if end_dt <= start_dt:
        raise ValueError("Timed event end must be after the start.")

    start_payload: dict[str, str] = {"dateTime": start_dt.isoformat()}
    end_payload: dict[str, str] = {"dateTime": end_dt.isoformat()}
    tz_name = timezone_name.strip() or getattr(start_dt.tzinfo, "key", "")
    if tz_name:
        start_payload["timeZone"] = tz_name
        end_payload["timeZone"] = tz_name
    body["start"] = start_payload
    body["end"] = end_payload
    return body


def event_summary(event: dict[str, Any]) -> dict[str, Any]:
    start = event.get("start") or {}
    end = event.get("end") or {}
    all_day = "date" in start
    attendees = event.get("attendees") or []
    return {
        "id": event.get("id"),
        "summary": event.get("summary") or "(untitled)",
        "description": event.get("description") or "",
        "location": event.get("location") or "",
        "status": event.get("status") or "",
        "html_link": event.get("htmlLink") or "",
        "all_day": all_day,
        "start": start.get("dateTime") or start.get("date") or "",
        "end": end.get("dateTime") or end.get("date") or "",
        "calendar_id": event.get("_calendar_id") or "",
        "attendees": [item.get("email", "") for item in attendees if item.get("email")],
    }


def load_saved_credentials(config: GoogleCalendarConfig) -> Credentials | None:
    if not config.token_path.exists():
        return None
    return Credentials.from_authorized_user_file(str(config.token_path), list(config.scopes))


def ensure_credentials(
    config: GoogleCalendarConfig,
    *,
    authorize_if_needed: bool = False,
    force_authorize: bool = False,
    open_browser: bool = True,
) -> Credentials:
    config.token_path.parent.mkdir(parents=True, exist_ok=True)
    creds = None if force_authorize else load_saved_credentials(config)

    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        config.token_path.write_text(creds.to_json(), encoding="utf-8")

    if creds and creds.valid:
        return creds

    if not authorize_if_needed:
        raise RuntimeError(
            "Google Calendar is not authorized. Configure OAuth client secrets in the Google "
            "Calendar MCP env or provide the client JSON file and then run authorize."
        )

    client_config, _source = load_oauth_client_config(config)
    flow = InstalledAppFlow.from_client_config(client_config, list(config.scopes))
    creds = flow.run_local_server(host="127.0.0.1", port=0, open_browser=open_browser)
    config.token_path.write_text(creds.to_json(), encoding="utf-8")
    return creds


def start_manual_authorization(
    config: GoogleCalendarConfig,
    *,
    redirect_host: str = "127.0.0.1",
    redirect_port: int = 1,
) -> tuple[InstalledAppFlow, dict[str, Any]]:
    client_config, source = load_oauth_client_config(config)
    flow = InstalledAppFlow.from_client_config(client_config, list(config.scopes))
    flow.redirect_uri = f"http://{redirect_host}:{redirect_port}/"
    authorization_url, state = flow.authorization_url(access_type="offline")
    return flow, {
        "authorization_url": authorization_url,
        "state": state,
        "redirect_uri": flow.redirect_uri,
        "oauth_source": source,
    }


def complete_manual_authorization(
    config: GoogleCalendarConfig,
    *,
    flow: InstalledAppFlow,
    authorization_response: str,
) -> Credentials:
    config.token_path.parent.mkdir(parents=True, exist_ok=True)
    response = str(authorization_response or "").strip()
    if not response:
        raise RuntimeError("authorization_response is required.")
    normalized = response.replace("http://", "https://", 1)
    flow.fetch_token(authorization_response=normalized)
    creds = flow.credentials
    config.token_path.write_text(creds.to_json(), encoding="utf-8")
    return creds


def build_calendar_service(
    config: GoogleCalendarConfig,
    *,
    authorize_if_needed: bool = False,
    force_authorize: bool = False,
    open_browser: bool = True,
):
    creds = ensure_credentials(
        config,
        authorize_if_needed=authorize_if_needed,
        force_authorize=force_authorize,
        open_browser=open_browser,
    )
    return build("calendar", "v3", credentials=creds, cache_discovery=False)


def list_calendars(config: GoogleCalendarConfig) -> list[dict[str, Any]]:
    service = build_calendar_service(config)
    response = service.calendarList().list().execute()
    items = response.get("items") or []
    return [
        {
            "id": item.get("id", ""),
            "summary": item.get("summary", ""),
            "primary": bool(item.get("primary")),
            "access_role": item.get("accessRole", ""),
            "time_zone": item.get("timeZone", ""),
        }
        for item in items
    ]


def resolve_calendar_id(
    calendars: list[dict[str, Any]],
    requested: str,
    default_calendar: str = "primary",
) -> str:
    needle = requested.strip() or default_calendar
    if needle == "primary":
        primary = next((item for item in calendars if item.get("primary")), None)
        if primary:
            return str(primary.get("id") or "primary")
        return "primary"

    exact_id = next((item for item in calendars if str(item.get("id")) == needle), None)
    if exact_id:
        return str(exact_id["id"])

    exact_summary = next(
        (
            item
            for item in calendars
            if str(item.get("summary", "")).strip().lower() == needle.lower()
        ),
        None,
    )
    if exact_summary:
        return str(exact_summary["id"])

    raise ValueError(
        f"Unknown calendar '{needle}'. Available: "
        + ", ".join(str(item.get("summary") or item.get("id")) for item in calendars)
    )


def resolve_write_calendar_id(
    calendars: list[dict[str, Any]],
    requested: str,
    config: GoogleCalendarConfig,
) -> str:
    write_target = config.write_calendar.strip()
    if not write_target:
        return resolve_calendar_id(calendars, requested, config.default_calendar)

    allowed_id = resolve_calendar_id(calendars, write_target, config.default_calendar)
    if not requested.strip():
        return allowed_id

    requested_id = resolve_calendar_id(calendars, requested, config.default_calendar)
    if requested_id != allowed_id:
        raise ValueError(
            "Google Calendar writes are restricted to "
            f"'{write_target}'. Requested calendar '{requested.strip()}' is not writable."
        )
    return requested_id


def list_events(
    config: GoogleCalendarConfig,
    *,
    calendar: str = "",
    range_name: str = "upcoming",
    time_min: str = "",
    time_max: str = "",
    query: str = "",
    max_results: int = 20,
    show_deleted: bool = False,
) -> dict[str, Any]:
    if max_results < 1 or max_results > 100:
        raise ValueError("max_results must be between 1 and 100.")
    service = build_calendar_service(config)
    calendars = list_calendars(config)
    calendar_id = resolve_calendar_id(calendars, calendar, config.default_calendar)

    if time_min.strip():
        start_dt = _parse_datetime(time_min, config.default_timezone or None)
        end_dt = (
            _parse_datetime(time_max, config.default_timezone or None) if time_max.strip() else None
        )
    else:
        start_dt, end_dt = _time_window(range_name, config.default_timezone or None)

    response = (
        service.events()
        .list(
            calendarId=calendar_id,
            timeMin=start_dt.isoformat(),
            timeMax=end_dt.isoformat() if end_dt else None,
            maxResults=max_results,
            singleEvents=True,
            orderBy="startTime",
            q=query.strip() or None,
            showDeleted=show_deleted,
        )
        .execute()
    )
    items = response.get("items") or []
    normalized = []
    for item in items:
        enriched = dict(item)
        enriched["_calendar_id"] = calendar_id
        normalized.append(event_summary(enriched))
    return {
        "calendar_id": calendar_id,
        "range": range_name,
        "items": normalized,
    }


def create_event(
    config: GoogleCalendarConfig,
    *,
    calendar: str = "",
    title: str,
    start: str,
    end: str = "",
    all_day: bool = False,
    duration_minutes: int = 60,
    description: str = "",
    location: str = "",
    timezone_name: str = "",
    attendees: list[str] | None = None,
    send_updates: str = "none",
) -> dict[str, Any]:
    if send_updates not in {"all", "externalOnly", "none"}:
        raise ValueError("send_updates must be one of: all, externalOnly, none.")
    service = build_calendar_service(config)
    calendars = list_calendars(config)
    calendar_id = resolve_write_calendar_id(calendars, calendar, config)
    body = build_event_payload(
        title=title,
        start=start,
        end=end,
        all_day=all_day,
        duration_minutes=duration_minutes,
        description=description,
        location=location,
        timezone_name=timezone_name or config.default_timezone,
        attendees=attendees,
    )
    created = (
        service.events()
        .insert(
            calendarId=calendar_id,
            body=body,
            sendUpdates=send_updates,
        )
        .execute()
    )
    created["_calendar_id"] = calendar_id
    return event_summary(created)


def update_event(
    config: GoogleCalendarConfig,
    *,
    event_id: str,
    calendar: str = "",
    title: str = "",
    start: str = "",
    end: str = "",
    all_day: bool | None = None,
    duration_minutes: int = 60,
    description: str | None = None,
    location: str | None = None,
    timezone_name: str = "",
    attendees: list[str] | None = None,
    send_updates: str = "none",
) -> dict[str, Any]:
    if not event_id.strip():
        raise ValueError("event_id is required.")
    if send_updates not in {"all", "externalOnly", "none"}:
        raise ValueError("send_updates must be one of: all, externalOnly, none.")
    service = build_calendar_service(config)
    calendars = list_calendars(config)
    calendar_id = resolve_write_calendar_id(calendars, calendar, config)
    existing = service.events().get(calendarId=calendar_id, eventId=event_id.strip()).execute()

    body = dict(existing)
    if title.strip():
        body["summary"] = title.strip()
    if description is not None:
        body["description"] = description.strip()
    if location is not None:
        body["location"] = location.strip()
    if attendees is not None:
        body["attendees"] = [{"email": email.strip()} for email in attendees if email.strip()]

    if start.strip() or end.strip() or all_day is not None:
        next_all_day = (
            bool(all_day) if all_day is not None else ("date" in (body.get("start") or {}))
        )
        start_value = start.strip() or str(
            (body.get("start") or {}).get("dateTime") or (body.get("start") or {}).get("date") or ""
        )
        end_value = end.strip() or str(
            (body.get("end") or {}).get("dateTime") or (body.get("end") or {}).get("date") or ""
        )
        time_payload = build_event_payload(
            title=str(body.get("summary") or title or "(untitled)"),
            start=start_value,
            end=end_value,
            all_day=next_all_day,
            duration_minutes=duration_minutes,
            description=str(body.get("description") or ""),
            location=str(body.get("location") or ""),
            timezone_name=timezone_name or config.default_timezone,
            attendees=[
                item.get("email", "") for item in body.get("attendees") or [] if item.get("email")
            ],
        )
        body["start"] = time_payload["start"]
        body["end"] = time_payload["end"]

    updated = (
        service.events()
        .update(
            calendarId=calendar_id,
            eventId=event_id.strip(),
            body=body,
            sendUpdates=send_updates,
        )
        .execute()
    )
    updated["_calendar_id"] = calendar_id
    return event_summary(updated)


def delete_event(
    config: GoogleCalendarConfig,
    *,
    event_id: str,
    calendar: str = "",
    send_updates: str = "none",
) -> dict[str, Any]:
    if not event_id.strip():
        raise ValueError("event_id is required.")
    if send_updates not in {"all", "externalOnly", "none"}:
        raise ValueError("send_updates must be one of: all, externalOnly, none.")
    calendars = list_calendars(config)
    calendar_id = resolve_write_calendar_id(calendars, calendar, config)
    service = build_calendar_service(config)
    service.events().delete(
        calendarId=calendar_id,
        eventId=event_id.strip(),
        sendUpdates=send_updates,
    ).execute()
    return {"deleted": True, "event_id": event_id.strip(), "calendar_id": calendar_id}
