from pathlib import Path

import pytest

from nanobot.integrations.google_calendar import (
    GoogleCalendarConfig,
    build_event_payload,
    complete_manual_authorization,
    event_summary,
    load_oauth_client_config,
    resolve_calendar_id,
    resolve_write_calendar_id,
    start_manual_authorization,
)


def test_build_timed_event_payload_uses_duration_and_timezone() -> None:
    payload = build_event_payload(
        title="Dinner",
        start="2026-04-05T19:00:00",
        duration_minutes=90,
        timezone_name="America/New_York",
        attendees=["a@example.com", " b@example.com "],
    )

    assert payload["summary"] == "Dinner"
    assert payload["start"]["timeZone"] == "America/New_York"
    assert payload["end"]["timeZone"] == "America/New_York"
    assert payload["start"]["dateTime"].startswith("2026-04-05T19:00:00")
    assert payload["end"]["dateTime"].startswith("2026-04-05T20:30:00")
    assert payload["attendees"] == [{"email": "a@example.com"}, {"email": "b@example.com"}]


def test_build_all_day_event_payload_uses_exclusive_end() -> None:
    payload = build_event_payload(
        title="Trip",
        start="2026-04-10",
        all_day=True,
    )

    assert payload["start"] == {"date": "2026-04-10"}
    assert payload["end"] == {"date": "2026-04-11"}


def test_resolve_calendar_id_matches_summary_case_insensitively() -> None:
    calendars = [
        {"id": "abc123", "summary": "Family Calendar", "primary": False},
        {"id": "primary-id", "summary": "Primary", "primary": True},
    ]

    assert resolve_calendar_id(calendars, "family calendar", "primary") == "abc123"
    assert resolve_calendar_id(calendars, "", "primary") == "primary-id"


def test_event_summary_extracts_all_day_flag() -> None:
    event = {
        "id": "evt-1",
        "summary": "Vacation",
        "start": {"date": "2026-04-10"},
        "end": {"date": "2026-04-11"},
        "_calendar_id": "primary",
    }

    summary = event_summary(event)

    assert summary["all_day"] is True
    assert summary["start"] == "2026-04-10"
    assert summary["end"] == "2026-04-11"
    assert summary["calendar_id"] == "primary"


def test_build_event_payload_rejects_backwards_end() -> None:
    with pytest.raises(ValueError, match="after the start"):
        build_event_payload(
            title="Broken",
            start="2026-04-05T19:00:00-04:00",
            end="2026-04-05T18:00:00-04:00",
        )


def test_load_oauth_client_config_prefers_env_credentials(tmp_path) -> None:
    config = GoogleCalendarConfig(
        client_secret_path=tmp_path / "unused.json",
        token_path=tmp_path / "token.json",
        oauth_client_id="client-id",
        oauth_client_secret="client-secret",
    )

    payload, source = load_oauth_client_config(config)

    assert source == "env"
    assert payload["installed"]["client_id"] == "client-id"
    assert payload["installed"]["client_secret"] == "client-secret"


def test_resolve_write_calendar_defaults_to_restricted_target() -> None:
    calendars = [
        {"id": "work-id", "summary": "Work", "primary": False},
        {"id": "primary-id", "summary": "Primary", "primary": True},
    ]
    config = GoogleCalendarConfig(
        client_secret_path=Path("/tmp/client.json"),
        token_path=Path("/tmp/token.json"),
        write_calendar="Work",
    )

    resolved = resolve_write_calendar_id(calendars, "", config)

    assert resolved == "work-id"


def test_resolve_write_calendar_rejects_non_writable_target() -> None:
    calendars = [
        {"id": "work-id", "summary": "Work", "primary": False},
        {"id": "primary-id", "summary": "Primary", "primary": True},
    ]
    config = GoogleCalendarConfig(
        client_secret_path=Path("/tmp/client.json"),
        token_path=Path("/tmp/token.json"),
        write_calendar="Work",
    )

    with pytest.raises(ValueError, match="restricted"):
        resolve_write_calendar_id(calendars, "Primary", config)


def test_start_manual_authorization_uses_loopback_redirect(monkeypatch, tmp_path) -> None:
    captured: dict[str, object] = {}

    class _FakeFlow:
        def __init__(self) -> None:
            self.redirect_uri = ""

        def authorization_url(self, **kwargs):
            captured["kwargs"] = kwargs
            captured["redirect_uri"] = self.redirect_uri
            return "https://example.test/auth", "state-123"

    def _fake_from_client_config(client_config, scopes):
        captured["client_config"] = client_config
        captured["scopes"] = list(scopes)
        return _FakeFlow()

    monkeypatch.setattr(
        "nanobot.integrations.google_calendar.InstalledAppFlow.from_client_config",
        _fake_from_client_config,
    )
    config = GoogleCalendarConfig(
        client_secret_path=tmp_path / "unused.json",
        token_path=tmp_path / "token.json",
        oauth_client_id="client-id",
        oauth_client_secret="client-secret",
    )

    flow, details = start_manual_authorization(config, redirect_port=9)

    assert flow is not None
    assert details["authorization_url"] == "https://example.test/auth"
    assert details["state"] == "state-123"
    assert details["redirect_uri"] == "http://127.0.0.1:9/"
    assert details["oauth_source"] == "env"
    assert captured["redirect_uri"] == "http://127.0.0.1:9/"
    assert captured["kwargs"] == {"access_type": "offline"}


def test_complete_manual_authorization_writes_token(tmp_path) -> None:
    class _Creds:
        valid = True

        def to_json(self) -> str:
            return '{"token":"abc"}'

    class _FakeFlow:
        def __init__(self) -> None:
            self.credentials = _Creds()
            self.authorization_response = None

        def fetch_token(self, *, authorization_response):
            self.authorization_response = authorization_response

    config = GoogleCalendarConfig(
        client_secret_path=tmp_path / "unused.json",
        token_path=tmp_path / "token.json",
        oauth_client_id="client-id",
        oauth_client_secret="client-secret",
    )
    flow = _FakeFlow()

    creds = complete_manual_authorization(
        config,
        flow=flow,
        authorization_response="http://127.0.0.1:9/?code=abc",
    )

    assert creds.valid is True
    assert flow.authorization_response == "https://127.0.0.1:9/?code=abc"
    assert config.token_path.read_text(encoding="utf-8") == '{"token":"abc"}'
