#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


DEFAULT_CONFIG_PATH = Path.home() / ".nanobot" / "config.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fetch hourly weather forecast payloads for upcoming-event cards."
    )
    parser.add_argument(
        "--config",
        default=str(DEFAULT_CONFIG_PATH),
        help="Path to the Nanobot config.json file.",
    )
    parser.add_argument(
        "--nws-entity",
        default="weather.korh",
        help="Home Assistant weather entity used for condition, temperature, wind, and precipitation.",
    )
    parser.add_argument(
        "--uv-entity",
        default="weather.openweathermap_2",
        help="Home Assistant weather entity used for UV forecast values.",
    )
    parser.add_argument(
        "--forecast-type",
        default="hourly",
        choices=["hourly", "daily", "twice_daily"],
        help="Forecast granularity to request from Home Assistant.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=48,
        help="Maximum number of forecast rows to return for each source.",
    )
    return parser.parse_args()


def load_home_assistant_config(config_path: str) -> tuple[str, dict[str, str]]:
    config = json.loads(Path(config_path).read_text(encoding="utf-8"))
    home_assistant = config["tools"]["mcpServers"]["home_assistant"]
    mcp_url = str(home_assistant["url"])
    base_url = mcp_url.removesuffix("/api/mcp")
    headers = dict(home_assistant.get("headers") or {})
    return base_url, headers


def api_request(
    base_url: str,
    headers: dict[str, str],
    method: str,
    path: str,
    payload: dict[str, object] | None = None,
) -> object:
    body = None
    request_headers = dict(headers)
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        request_headers["Content-Type"] = "application/json"
    request = urllib.request.Request(
        f"{base_url}{path}",
        data=body,
        headers=request_headers,
        method=method,
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        return json.load(response)


def get_state(
    base_url: str,
    headers: dict[str, str],
    entity_id: str,
) -> dict[str, object]:
    state = api_request(base_url, headers, "GET", f"/api/states/{entity_id}")
    if not isinstance(state, dict):
        raise TypeError(f"Unexpected state response for {entity_id}")
    return state


def get_forecast(
    base_url: str,
    headers: dict[str, str],
    entity_id: str,
    forecast_type: str,
    limit: int,
) -> list[dict[str, object]]:
    response = api_request(
        base_url,
        headers,
        "POST",
        "/api/services/weather/get_forecasts?return_response",
        {
            "entity_id": entity_id,
            "type": forecast_type,
        },
    )
    if not isinstance(response, dict):
        raise TypeError(f"Unexpected forecast response for {entity_id}")
    service_response = response.get("service_response")
    if not isinstance(service_response, dict):
        raise TypeError(f"Missing service_response for {entity_id}")
    entity_response = service_response.get(entity_id)
    if not isinstance(entity_response, dict):
        raise KeyError(f"Missing forecast response for {entity_id}")
    forecast = entity_response.get("forecast")
    if not isinstance(forecast, list):
        raise TypeError(f"Missing forecast list for {entity_id}")
    normalized_forecast = [item for item in forecast if isinstance(item, dict)]
    if limit > 0:
        return normalized_forecast[:limit]
    return normalized_forecast


def summarize_weather_entity(
    state: dict[str, object],
    forecast: list[dict[str, object]] | None,
) -> dict[str, object]:
    attributes = state.get("attributes")
    if not isinstance(attributes, dict):
        attributes = {}
    return {
        "entity_id": state.get("entity_id"),
        "state": state.get("state"),
        "friendly_name": attributes.get("friendly_name"),
        "temperature_unit": attributes.get("temperature_unit"),
        "wind_speed_unit": attributes.get("wind_speed_unit"),
        "precipitation_unit": attributes.get("precipitation_unit"),
        "forecast": forecast,
    }


def capture_weather_source(
    base_url: str,
    headers: dict[str, str],
    entity_id: str,
    forecast_type: str,
    limit: int,
) -> tuple[dict[str, object] | None, str | None]:
    try:
        state = get_state(base_url, headers, entity_id)
        forecast = get_forecast(base_url, headers, entity_id, forecast_type, limit)
        return summarize_weather_entity(state, forecast), None
    except (KeyError, TypeError, urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as error:
        return None, str(error)


def main() -> None:
    args = parse_args()
    result: dict[str, object] = {
        "success": True,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "forecast_type": args.forecast_type,
        "nws": None,
        "uv": None,
        "errors": {},
    }

    try:
        base_url, headers = load_home_assistant_config(args.config)
    except Exception as error:  # noqa: BLE001
        result["success"] = False
        result["errors"] = {"config": str(error)}
        print(json.dumps(result, ensure_ascii=False))
        return

    nws, nws_error = capture_weather_source(
        base_url=base_url,
        headers=headers,
        entity_id=args.nws_entity,
        forecast_type=args.forecast_type,
        limit=args.limit,
    )
    uv, uv_error = capture_weather_source(
        base_url=base_url,
        headers=headers,
        entity_id=args.uv_entity,
        forecast_type=args.forecast_type,
        limit=args.limit,
    )

    result["nws"] = nws
    result["uv"] = uv

    errors = result["errors"]
    if isinstance(errors, dict):
        if nws_error:
            errors["nws"] = nws_error
        if uv_error:
            errors["uv"] = uv_error
        result["success"] = not errors

    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
