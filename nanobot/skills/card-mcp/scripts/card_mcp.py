#!/usr/bin/env python3
"""Card template MCP server.

Exposes a tool that uses ``codex exec`` in single-shot mode to generate
HTML card templates from a description and JSON data payload.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, urlunparse
from urllib.request import Request, urlopen

DEFAULT_MODEL = os.getenv("CARD_TEMPLATE_CODEX_MODEL", "gpt-5")
DEFAULT_TIMEOUT_SECONDS = int(os.getenv("CARD_TEMPLATE_TIMEOUT_SECONDS", "90"))
DEFAULT_DISCOVERY_TIMEOUT_SECONDS = int(os.getenv("CARD_DISCOVERY_TIMEOUT_SECONDS", "10"))
MAX_DESCRIPTION_CHARS = 6000
MAX_DATA_CHARS = 50000
MAX_QUERY_CHARS = 512
MAX_DISCOVERY_MATCHES = 6
MAX_TEMPLATE_HTML_CHARS = 4000
MAX_TEMPLATES_IN_PROMPT = 12
_TOKEN_PATTERN = re.compile(r"[a-z0-9]+")
_INVALID_TEMPLATE_KEY_CHARS = re.compile(r"[^a-z0-9_-]+")
_WORKSPACE = Path(os.getenv("NANOBOT_WORKSPACE", "~/.nanobot")).expanduser()
_CONFIG_PATH = _WORKSPACE / "config.json"
_TEMPLATES_DIR = _WORKSPACE / "cards" / "templates"
_TEMPLATES_CONTEXT_PATH = _WORKSPACE / "CARD_TEMPLATES.md"


def _coerce_data_payload(data: dict[str, Any] | list[Any] | str) -> str:
    if isinstance(data, str):
        raw = data.strip()
        parsed: Any = {} if not raw else json.loads(raw)
    else:
        parsed = data

    if not isinstance(parsed, (dict, list)):
        raise ValueError("data must be a JSON object, array, or JSON-encoded string")

    return json.dumps(parsed, indent=2, ensure_ascii=False)


def _extract_html(raw: str) -> str:
    text = (raw or "").strip()
    if not text:
        return ""

    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].strip().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    first_tag = text.find("<")
    last_tag = text.rfind(">")
    if first_tag >= 0 and last_tag > first_tag:
        candidate = text[first_tag : last_tag + 1].strip()
        if candidate.startswith("<"):
            text = candidate

    return text.strip()


def _extract_json_object(raw: str) -> dict[str, Any]:
    text = (raw or "").strip()
    if not text:
        raise RuntimeError("codex exec returned empty output")

    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].strip().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    candidates = [text]
    first_brace = text.find("{")
    last_brace = text.rfind("}")
    if first_brace >= 0 and last_brace > first_brace:
        candidates.append(text[first_brace : last_brace + 1].strip())

    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed

    raise RuntimeError("codex exec returned invalid JSON output")


def _normalize_template_key(raw: str) -> str:
    key = _INVALID_TEMPLATE_KEY_CHARS.sub("-", str(raw or "").strip().lower()).strip("-")
    return key[:64]


def _load_nanobot_config() -> dict[str, Any]:
    try:
        return json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise RuntimeError(f"nanobot config not found at {_CONFIG_PATH}") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"nanobot config is invalid JSON: {exc}") from exc


def _get_home_assistant_server_config() -> tuple[str, dict[str, str]]:
    config = _load_nanobot_config()
    tools = config.get("tools", {})
    if not isinstance(tools, dict):
        raise RuntimeError("nanobot config missing tools section")

    mcp_servers = tools.get("mcpServers", {})
    if not isinstance(mcp_servers, dict):
        raise RuntimeError("nanobot config missing tools.mcpServers section")

    raw_server = mcp_servers.get("home assistant") or mcp_servers.get("home_assistant")
    if not isinstance(raw_server, dict):
        raise RuntimeError("home assistant MCP server is not configured")

    url = str(raw_server.get("url", "")).strip()
    if not url:
        raise RuntimeError("home assistant MCP server URL is empty")

    raw_headers = raw_server.get("headers", {})
    headers: dict[str, str] = {}
    if isinstance(raw_headers, dict):
        for key, value in raw_headers.items():
            headers[str(key)] = str(value)
    return url, headers


def _home_assistant_origin(mcp_url: str) -> str:
    parsed = urlparse(mcp_url.strip())
    return urlunparse(parsed._replace(path="", params="", query="", fragment="")).rstrip("/")


def _normalize_home_assistant_api_path(target_path: str) -> str:
    normalized = "/" + target_path.lstrip("/")
    if normalized == "/":
        raise ValueError("target path is required")
    if normalized == "/api" or normalized.startswith("/api/"):
        return normalized
    return f"/api{normalized}"


def _fetch_home_assistant_json(target_path: str, *, timeout_seconds: int) -> Any:
    mcp_url, auth_headers = _get_home_assistant_server_config()
    origin = _home_assistant_origin(mcp_url)
    api_path = _normalize_home_assistant_api_path(target_path)
    request = Request(f"{origin}{api_path}", headers=auth_headers, method="GET")
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            payload = response.read().decode("utf-8")
    except OSError as exc:
        raise RuntimeError(f"Home Assistant request failed for {target_path}: {exc}") from exc

    try:
        return json.loads(payload)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Home Assistant returned invalid JSON for {target_path}") from exc


def _tokenize(text: str) -> list[str]:
    return [token for token in _TOKEN_PATTERN.findall(text.lower()) if len(token) >= 2]


def _score_text(query_terms: list[str], *haystacks: str) -> int:
    if not query_terms:
        return 0
    combined = " ".join(haystacks).lower()
    combined_terms = set(_tokenize(combined))
    score = 0
    for term in query_terms:
        if term in combined_terms:
            score += 6
        elif term in combined:
            score += 3
    return score


def _load_template_manifests() -> list[dict[str, str]]:
    manifests: list[dict[str, str]] = []
    if not _TEMPLATES_DIR.exists():
        return manifests

    for template_dir in sorted(_TEMPLATES_DIR.iterdir()):
        manifest_path = template_dir / "manifest.json"
        if not manifest_path.is_file():
            continue
        try:
            raw = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if bool(raw.get("deprecated")):
            continue

        key = str(raw.get("key") or template_dir.name).strip()
        title = str(raw.get("title", "")).strip()
        notes = str(raw.get("notes", "")).strip()
        example_state = raw.get("example_state")
        manifests.append(
            {
                "key": key,
                "title": title,
                "notes": notes,
                "example_state": json.dumps(example_state, ensure_ascii=False, indent=2)
                if isinstance(example_state, dict)
                else "",
                "manifest_path": str(manifest_path),
                "template_path": str(template_dir / "template.html"),
            }
        )
    return manifests


def _read_template_bundle(template_key: str) -> dict[str, Any]:
    safe_key = _normalize_template_key(template_key)
    if not safe_key:
        raise RuntimeError("template_key is required")

    template_dir = _TEMPLATES_DIR / safe_key
    html_path = template_dir / "template.html"
    manifest_path = template_dir / "manifest.json"

    if not html_path.is_file():
        raise RuntimeError(f"template not found: {safe_key}")

    try:
        html_text = html_path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise RuntimeError(f"failed to read template HTML for {safe_key}: {exc}") from exc

    manifest: dict[str, Any] = {}
    if manifest_path.is_file():
        try:
            raw_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"failed to read template manifest for {safe_key}: {exc}") from exc
        if isinstance(raw_manifest, dict):
            manifest = raw_manifest

    example_state = manifest.get("example_state", {})
    if not isinstance(example_state, dict):
        example_state = {}

    return {
        "key": safe_key,
        "title": str(manifest.get("title", "")).strip(),
        "notes": str(manifest.get("notes", "")).strip(),
        "created_at": str(manifest.get("created_at", "")).strip(),
        "updated_at": str(manifest.get("updated_at", "")).strip(),
        "deprecated": bool(manifest.get("deprecated")),
        "html": html_text,
        "example_state": example_state,
        "template_path": str(html_path),
        "manifest_path": str(manifest_path),
    }


def _list_templates_for_context(limit: int | None = None) -> list[dict[str, Any]]:
    templates: list[dict[str, Any]] = []
    if not _TEMPLATES_DIR.exists():
        return templates

    for template_dir in sorted(_TEMPLATES_DIR.iterdir()):
        if not template_dir.is_dir():
            continue
        key = _normalize_template_key(template_dir.name)
        if not key:
            continue
        try:
            bundle = _read_template_bundle(key)
        except RuntimeError:
            continue
        if bool(bundle.get("deprecated")):
            continue
        templates.append(bundle)

    templates.sort(key=lambda item: str(item.get("updated_at", "")), reverse=True)
    if limit is not None:
        return templates[: max(0, limit)]
    return templates


def _render_templates_markdown(rows: list[dict[str, Any]]) -> str:
    lines = [
        "# Card Templates",
        "",
        "These are user-approved template layouts for `mcp_display_render_card` cards.",
        "Each card instance should provide a `template_key` and a `template_state` JSON object.",
        "Use a matching template when the request intent fits.",
        "Do not rewrite the HTML layout when an existing template already fits; fill the template_state instead.",
        "",
    ]

    for row in rows:
        key = str(row.get("key", "")).strip() or "unnamed"
        title = str(row.get("title", "")).strip() or "(untitled)"
        notes = str(row.get("notes", "")).strip() or "(no usage notes)"
        content = str(row.get("html", "")).strip()
        example_state = row.get("example_state", {})
        if len(content) > MAX_TEMPLATE_HTML_CHARS:
            content = content[:MAX_TEMPLATE_HTML_CHARS] + "\n<!-- truncated -->"
        html_lines = [f"    {line}" for line in content.splitlines()] if content else ["    "]
        state_text = (
            json.dumps(example_state, indent=2, ensure_ascii=False)
            if isinstance(example_state, dict)
            else "{}"
        )
        state_lines = [f"    {line}" for line in state_text.splitlines()]
        lines.extend(
            [
                f"## {key}",
                f"- Title: {title}",
                f"- Usage: {notes}",
                "- Example State:",
                *state_lines,
                "- HTML:",
                *html_lines,
                "",
            ]
        )

    return "\n".join(lines).rstrip() + "\n"


def _sync_templates_context_file() -> None:
    rows = _list_templates_for_context(limit=MAX_TEMPLATES_IN_PROMPT)
    _WORKSPACE.mkdir(parents=True, exist_ok=True)
    _TEMPLATES_CONTEXT_PATH.write_text(_render_templates_markdown(rows), encoding="utf-8")


def _discover_template_matches(query: str, *, limit: int) -> list[dict[str, Any]]:
    query_terms = _tokenize(query)
    scored: list[tuple[int, dict[str, Any]]] = []
    for manifest in _load_template_manifests():
        score = _score_text(
            query_terms,
            manifest["key"],
            manifest["title"],
            manifest["notes"],
            manifest["example_state"],
        )
        if score <= 0:
            continue
        scored.append(
            (
                score,
                {
                    **manifest,
                    "score": score,
                    "reuse_instruction": (
                        "Prefer reusing this saved template structure. Do not invent a new layout "
                        "unless the user explicitly asks for a redesign."
                    ),
                },
            )
        )
    scored.sort(key=lambda item: (-item[0], item[1]["key"]))
    return [item[1] for item in scored[:limit]]


def _state_response_shape(domain: str) -> dict[str, Any]:
    if domain == "weather":
        return {
            "endpoint_kind": "state",
            "primary_value_path": "attributes.temperature",
            "primary_unit_path": "attributes.temperature_unit",
            "status_path": "state",
            "common_attribute_paths": [
                "attributes.humidity",
                "attributes.wind_speed",
                "attributes.wind_speed_unit",
                "attributes.pressure",
                "attributes.pressure_unit",
            ],
        }

    return {
        "endpoint_kind": "state",
        "primary_value_path": "state",
        "primary_unit_path": "attributes.unit_of_measurement",
        "common_attribute_paths": [
            "attributes.friendly_name",
            "attributes.device_class",
            "attributes.state_class",
        ],
    }


def _score_state_match(query_terms: list[str], state: dict[str, Any]) -> int:
    entity_id = str(state.get("entity_id", "")).strip()
    if "." not in entity_id:
        return 0

    attrs = state.get("attributes", {})
    if not isinstance(attrs, dict):
        attrs = {}
    domain = entity_id.split(".", 1)[0]
    score = _score_text(
        query_terms,
        entity_id,
        str(attrs.get("friendly_name", "")),
        str(attrs.get("device_class", "")),
        str(attrs.get("unit_of_measurement", "")),
        str(attrs.get("icon", "")),
    )

    if domain == "weather" and "weather" in query_terms:
        score += 10
    if domain in {"sensor", "binary_sensor"} and "sensor" in query_terms:
        score += 4
    if "co2" in query_terms and (
        "co2" in entity_id.lower()
        or "carbon dioxide" in str(attrs.get("friendly_name", "")).lower()
        or str(attrs.get("unit_of_measurement", "")).lower() == "ppm"
    ):
        score += 12
    if "calendar" in query_terms and domain == "calendar":
        score += 10
    return score


def _build_state_match(state: dict[str, Any], score: int) -> dict[str, Any]:
    entity_id = str(state.get("entity_id", "")).strip()
    attrs = state.get("attributes", {})
    if not isinstance(attrs, dict):
        attrs = {}
    domain = entity_id.split(".", 1)[0]
    sample_attributes: dict[str, Any] = {}
    for key in (
        "friendly_name",
        "unit_of_measurement",
        "device_class",
        "state_class",
        "temperature",
        "temperature_unit",
        "humidity",
        "wind_speed",
        "wind_speed_unit",
        "pressure",
        "pressure_unit",
    ):
        if key in attrs:
            sample_attributes[key] = attrs[key]

    return {
        "score": score,
        "entity_id": entity_id,
        "title": str(attrs.get("friendly_name", "")) or entity_id,
        "domain": domain,
        "proxy_path": f"/ha/proxy/states/{entity_id}",
        "response_shape": _state_response_shape(domain),
        "sample": {
            "state": state.get("state"),
            "attributes": sample_attributes,
        },
        "usage_instruction": (
            "Fetch this exact proxy path. Do not change the entity id or remove the /states/ segment."
        ),
    }


def _discover_state_matches(query: str, *, limit: int, timeout_seconds: int) -> list[dict[str, Any]]:
    query_terms = _tokenize(query)
    states = _fetch_home_assistant_json("/states", timeout_seconds=timeout_seconds)
    if not isinstance(states, list):
        raise RuntimeError("Home Assistant /states response was not a JSON array")

    scored: list[tuple[int, dict[str, Any]]] = []
    for state in states:
        if not isinstance(state, dict):
            continue
        score = _score_state_match(query_terms, state)
        if score <= 0:
            continue
        scored.append((score, _build_state_match(state, score)))

    scored.sort(key=lambda item: (-item[0], item[1]["entity_id"]))
    return [item[1] for item in scored[:limit]]


def _score_calendar_match(query_terms: list[str], calendar: dict[str, Any]) -> int:
    entity_id = str(calendar.get("entity_id", "")).strip()
    title = str(calendar.get("name", "")).strip()
    score = _score_text(query_terms, entity_id, title)
    if "calendar" in query_terms:
        score += 10
    if "today" in query_terms:
        score += 2
    if "events" in query_terms:
        score += 2
    return score


def _build_calendar_match(calendar: dict[str, Any], score: int) -> dict[str, Any]:
    entity_id = str(calendar.get("entity_id", "")).strip()
    title = str(calendar.get("name", "")).strip() or entity_id
    return {
        "score": score,
        "entity_id": entity_id,
        "title": title,
        "proxy_list_path": "/ha/proxy/calendars",
        "proxy_events_path_template": f"/ha/proxy/calendars/{entity_id}?start={{iso_start}}&end={{iso_end}}",
        "response_shape": {
            "list_endpoint": "array of calendars with entity_id and name",
            "events_endpoint": "array of events with summary, start, end, location",
        },
        "usage_instruction": (
            "List calendars first, then fetch events for this exact entity id using ISO start/end query params."
        ),
    }


def _discover_calendar_matches(query: str, *, limit: int, timeout_seconds: int) -> list[dict[str, Any]]:
    query_terms = _tokenize(query)
    calendars = _fetch_home_assistant_json("/calendars", timeout_seconds=timeout_seconds)
    if not isinstance(calendars, list):
        raise RuntimeError("Home Assistant /calendars response was not a JSON array")

    scored: list[tuple[int, dict[str, Any]]] = []
    for calendar in calendars:
        if not isinstance(calendar, dict):
            continue
        score = _score_calendar_match(query_terms, calendar)
        if score <= 0:
            continue
        scored.append((score, _build_calendar_match(calendar, score)))

    scored.sort(key=lambda item: (-item[0], item[1]["entity_id"]))
    return [item[1] for item in scored[:limit]]


def _build_prompt(description: str, data_payload: str) -> str:
    return (
        "Generate a reusable HTML card template fragment for the Nanobot web UI.\n"
        "Return a JSON object with exactly two keys: summary and html.\n"
        "The summary should be one or two short sentences describing the layout you created.\n"
        "The html value must be an HTML fragment, not a full document.\n"
        "Do not include markdown fences, comments, or explanations outside the JSON object.\n"
        "The template will be wrapped inside a container with data-nanobot-card-root.\n"
        "The feed already shows the card title outside the template.\n"
        "Do not duplicate the same title as a heading inside the template unless the user explicitly asks for an in-card title.\n"
        "Card state will be injected as a sibling JSON script node:\n"
        '<script type="application/json" data-card-state>{...}</script>\n'
        "Template scripts must read state with:\n"
        "const state = window.__nanobotGetCardState?.(document.currentScript) || {};\n"
        "Use document.currentScript?.closest('[data-nanobot-card-root]') to find the card root.\n"
        "Use only inline CSS and standard HTML elements.\n"
        "Inline JavaScript is allowed when the template needs live refresh behavior.\n"
        "Do not use external stylesheets or external JavaScript.\n"
        "Do not hardcode card-specific titles, units, entity ids, or proxy URLs if they can come from state.\n"
        "The card should be readable on mobile and desktop.\n"
        "Assume the snippet will be shown inside a vertical card feed.\n\n"
        f"Design brief:\n{description.strip()}\n\n"
        f"Example template_state JSON:\n{data_payload}\n\n"
        "Requirements:\n"
        "1. Read values from the state object and render the card from that state.\n"
        "2. Keep structure semantically clear, with a primary heading and concise sections.\n"
        "3. Include sensible fallback text for missing fields when possible.\n"
        "4. The feed already provides the outer card shell, so avoid nested card chrome unless explicitly requested.\n"
        "5. If the brief implies centered or hero-style composition, center the inner content explicitly.\n"
        "5a. Prefer dense layouts with about 12-16px inner padding, minimal decorative borders, no drop shadows, and no oversized empty gaps.\n"
        "5b. Optimize for low-vision readability with strong contrast, large primary values, and concise labels.\n"
        "6. Keep layout compact enough for a card feed.\n"
        "7. Output valid JSON only, and ensure the html field contains valid HTML.\n"
    )


def _build_modify_prompt(
    *,
    template_key: str,
    title: str,
    notes: str,
    current_html: str,
    change_request: str,
    data_payload: str,
    preserve_state_schema: bool,
) -> str:
    state_contract_rule = (
        "Preserve the current template_state schema and field names. Do not rename, remove, or repurpose "
        "existing state fields unless the change request explicitly requires a contract change."
        if preserve_state_schema
        else "You may adjust the template_state schema if the requested redesign requires it, but keep the data contract as small and stable as possible."
    )

    return (
        "Modify an existing reusable HTML card template fragment for the Nanobot web UI.\n"
        "Return a JSON object with exactly two keys: summary and html.\n"
        "The summary should be one or two short sentences describing the changes you made.\n"
        "The html value must be an HTML fragment, not a full document.\n"
        "Do not include markdown fences, comments, or explanations outside the JSON object.\n"
        "The template will be wrapped inside a container with data-nanobot-card-root.\n"
        "The feed already shows the card title outside the template.\n"
        "Do not duplicate the same title as a heading inside the template unless the user explicitly asks for an in-card title.\n"
        "Card state will be injected as a sibling JSON script node:\n"
        '<script type="application/json" data-card-state>{...}</script>\n'
        "Template scripts must read state with:\n"
        "const state = window.__nanobotGetCardState?.(document.currentScript) || {};\n"
        "Use document.currentScript?.closest('[data-nanobot-card-root]') to find the card root.\n"
        "Use only inline CSS and standard HTML elements.\n"
        "Inline JavaScript is allowed when the template needs live refresh behavior.\n"
        "Do not use external stylesheets or external JavaScript.\n"
        "Do not hardcode card-specific titles, units, entity ids, or proxy URLs if they can come from state.\n"
        "Assume the snippet will be shown inside a vertical card feed.\n\n"
        f"Template key:\n{template_key}\n\n"
        f"Current title:\n{title or '(untitled)'}\n\n"
        f"Current usage notes:\n{notes or '(none)'}\n\n"
        "Current template HTML:\n"
        f"{current_html.strip()}\n\n"
        "Current/target example template_state JSON:\n"
        f"{data_payload}\n\n"
        "Requested changes:\n"
        f"{change_request.strip()}\n\n"
        "Requirements:\n"
        "1. Keep this as a reusable template, not a one-off rendered card.\n"
        "2. Read values from the state object and render the card from that state.\n"
        f"3. {state_contract_rule}\n"
        "4. Preserve useful live-refresh behavior unless the change request explicitly removes it.\n"
        "5. Keep structure semantically clear, with a primary heading and concise sections.\n"
        "6. Include sensible fallback text for missing fields when possible.\n"
        "7. The feed already provides the outer card shell, so avoid nested card chrome unless explicitly requested.\n"
        "7a. Prefer dense layouts with about 12-16px inner padding, minimal decorative borders, no drop shadows, and no oversized empty gaps.\n"
        "7b. Optimize for low-vision readability with strong contrast, large primary values, and concise labels.\n"
        "8. Keep layout compact enough for a card feed and readable on mobile and desktop.\n"
        "9. Output valid JSON only, and ensure the html field contains valid HTML.\n"
    )


def _write_template_files(
    *,
    template_key: str,
    title: str,
    html: str,
    notes: str,
    example_state: dict[str, Any],
) -> dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat()
    template_dir = _TEMPLATES_DIR / template_key
    template_dir.mkdir(parents=True, exist_ok=True)

    manifest_path = template_dir / "manifest.json"
    created_at = now
    if manifest_path.exists():
        try:
            existing = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            existing = {}
        if isinstance(existing, dict):
            created_at = str(existing.get("created_at") or created_at)

    (template_dir / "template.html").write_text(html.rstrip() + "\n", encoding="utf-8")
    manifest = {
        "key": template_key,
        "title": title,
        "notes": notes,
        "example_state": example_state,
        "created_at": created_at,
        "updated_at": now,
    }
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    _sync_templates_context_file()
    return {
        "template_key": template_key,
        "title": title,
        "notes": notes,
        "template_path": str(template_dir / "template.html"),
        "manifest_path": str(manifest_path),
    }


def _run_codex_single_shot_output(prompt: str, model: str, timeout_seconds: int) -> str:
    with tempfile.NamedTemporaryFile(prefix="card_mcp_", suffix=".txt", delete=False) as handle:
        output_path = handle.name

    cmd = [
        "codex",
        "exec",
        "--skip-git-repo-check",
        "--sandbox",
        "read-only",
        "--ephemeral",
        "--color",
        "never",
        "--model",
        model,
        "--output-last-message",
        output_path,
        prompt,
    ]

    try:
        completed = subprocess.run(
            cmd,
            text=True,
            capture_output=True,
            timeout=timeout_seconds,
            check=False,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("codex CLI is not installed or not in PATH") from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"codex exec timed out after {timeout_seconds}s") from exc

    if completed.returncode != 0:
        stderr = (completed.stderr or "").strip()
        stdout = (completed.stdout or "").strip()
        details = stderr or stdout or "unknown codex error"
        raise RuntimeError(f"codex exec failed: {details}")

    try:
        with open(output_path, "r", encoding="utf-8") as f:
            result = f.read()
    finally:
        try:
            os.unlink(output_path)
        except OSError:
            pass

    return result


def _run_codex_single_shot(prompt: str, model: str, timeout_seconds: int) -> str:
    result = _run_codex_single_shot_output(prompt=prompt, model=model, timeout_seconds=timeout_seconds)

    html = _extract_html(result)
    if not html:
        raise RuntimeError("codex exec returned no HTML output")
    return html


def _run_codex_single_shot_template_response(
    prompt: str, model: str, timeout_seconds: int
) -> tuple[str, str]:
    result = _run_codex_single_shot_output(
        prompt=prompt,
        model=model,
        timeout_seconds=timeout_seconds,
    )
    payload = _extract_json_object(result)
    summary = str(payload.get("summary", "")).strip()
    html = _extract_html(str(payload.get("html", "")))
    if not html:
        raise RuntimeError("codex exec returned no HTML template")
    if not summary:
        summary = "Template updated successfully."
    return summary, html


def _register_tools(server: Any) -> None:
    @server.tool()
    def discover_live_card_source(
        query: str,
        limit: int = 3,
        timeout_seconds: int = DEFAULT_DISCOVERY_TIMEOUT_SECONDS,
    ) -> dict[str, Any]:
        """Find exact Home Assistant proxy endpoints and matching saved templates for a live card."""
        safe_query = (query or "").strip()
        if not safe_query:
            raise ValueError("query is required")
        if len(safe_query) > MAX_QUERY_CHARS:
            raise ValueError(f"query is too long ({len(safe_query)} > {MAX_QUERY_CHARS})")

        safe_limit = max(1, min(int(limit), MAX_DISCOVERY_MATCHES))
        safe_timeout = max(3, min(int(timeout_seconds), 30))

        result: dict[str, Any] = {
            "query": safe_query,
            "rules": [
                "If a matching saved template exists, reuse it before inventing new HTML.",
                "For live Home Assistant cards, use only the proxy paths returned here.",
                "Do not invent endpoint names or guess response JSON fields.",
                "For /ha/proxy/states/{entity_id}, read data.state and data.attributes from the JSON body.",
            ],
            "template_matches": _discover_template_matches(safe_query, limit=safe_limit),
            "state_matches": [],
            "calendar_matches": [],
        }

        try:
            result["state_matches"] = _discover_state_matches(
                safe_query,
                limit=safe_limit,
                timeout_seconds=safe_timeout,
            )
        except RuntimeError as exc:
            result["state_error"] = str(exc)

        try:
            result["calendar_matches"] = _discover_calendar_matches(
                safe_query,
                limit=safe_limit,
                timeout_seconds=safe_timeout,
            )
        except RuntimeError as exc:
            result["calendar_error"] = str(exc)

        return result

    @server.tool()
    def generate_card_template(
        description: str,
        data: dict[str, Any] | list[Any] | str,
        template_key: str = "",
        title: str = "",
        notes: str = "",
        save: bool = True,
        model: str = DEFAULT_MODEL,
        timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    ) -> dict[str, Any]:
        """Generate a reusable card template from a description and example state JSON."""
        if not description or not description.strip():
            raise ValueError("description is required")
        if len(description) > MAX_DESCRIPTION_CHARS:
            raise ValueError(
                f"description is too long ({len(description)} > {MAX_DESCRIPTION_CHARS})"
            )

        example_state = _coerce_data_payload(data)
        payload = example_state
        if len(payload) > MAX_DATA_CHARS:
            raise ValueError(f"data payload is too large ({len(payload)} > {MAX_DATA_CHARS})")

        safe_model = (model or DEFAULT_MODEL).strip() or DEFAULT_MODEL
        safe_timeout = max(10, min(int(timeout_seconds), 300))
        prompt = _build_prompt(description=description, data_payload=payload)
        summary, html = _run_codex_single_shot_template_response(
            prompt=prompt,
            model=safe_model,
            timeout_seconds=safe_timeout,
        )

        result: dict[str, Any] = {
            "summary": summary,
            "saved": False,
        }

        if not save:
            result["html"] = html
            return result

        safe_key = _normalize_template_key(template_key or title or description)
        if not safe_key:
            raise ValueError("template_key is required when save=true")

        parsed_state = json.loads(example_state)
        if not isinstance(parsed_state, dict):
            raise ValueError("example state must be a JSON object")

        saved = _write_template_files(
            template_key=safe_key,
            title=str(title or safe_key).strip() or safe_key,
            html=html,
            notes=str(notes or "").strip(),
            example_state=parsed_state,
        )
        result.update(saved)
        result["saved"] = True
        return result

    @server.tool()
    def modify_card_template(
        template_key: str,
        change_request: str,
        example_state: dict[str, Any] | str = "",
        target_template_key: str = "",
        title: str = "",
        notes: str = "",
        preserve_state_schema: bool = True,
        save: bool = True,
        model: str = DEFAULT_MODEL,
        timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    ) -> dict[str, Any]:
        """Modify an existing reusable card template with the larger model."""
        safe_change_request = (change_request or "").strip()
        if not safe_change_request:
            raise ValueError("change_request is required")
        if len(safe_change_request) > MAX_DESCRIPTION_CHARS:
            raise ValueError(
                f"change_request is too long ({len(safe_change_request)} > {MAX_DESCRIPTION_CHARS})"
            )

        source = _read_template_bundle(template_key)
        if isinstance(example_state, str) and not example_state.strip():
            parsed_state = source["example_state"]
        else:
            parsed_state = json.loads(_coerce_data_payload(example_state))
            if not isinstance(parsed_state, dict):
                raise ValueError("example_state must be a JSON object")

        payload = json.dumps(parsed_state, indent=2, ensure_ascii=False)
        if len(payload) > MAX_DATA_CHARS:
            raise ValueError(
                f"example_state payload is too large ({len(payload)} > {MAX_DATA_CHARS})"
            )

        safe_model = (model or DEFAULT_MODEL).strip() or DEFAULT_MODEL
        safe_timeout = max(10, min(int(timeout_seconds), 300))
        prompt = _build_modify_prompt(
            template_key=source["key"],
            title=str(title or source["title"]).strip(),
            notes=str(notes or source["notes"]).strip(),
            current_html=source["html"],
            change_request=safe_change_request,
            data_payload=payload,
            preserve_state_schema=bool(preserve_state_schema),
        )
        summary, html = _run_codex_single_shot_template_response(
            prompt=prompt,
            model=safe_model,
            timeout_seconds=safe_timeout,
        )

        result: dict[str, Any] = {
            "summary": summary,
            "saved": False,
            "source_template_key": source["key"],
        }

        if not save:
            result["html"] = html
            result["target_template_key"] = _normalize_template_key(target_template_key or source["key"])
            return result

        safe_target_key = _normalize_template_key(target_template_key or source["key"])
        if not safe_target_key:
            raise ValueError("target_template_key is required when save=true")

        saved = _write_template_files(
            template_key=safe_target_key,
            title=str(title or source["title"] or safe_target_key).strip() or safe_target_key,
            html=html,
            notes=str(notes or source["notes"]).strip(),
            example_state=parsed_state,
        )
        result.update(saved)
        result["target_template_key"] = safe_target_key
        result["saved"] = True
        return result


def main() -> None:
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as exc:
        raise RuntimeError(
            "Missing Python dependency 'mcp'. Install project dependencies first."
        ) from exc

    server = FastMCP("card")
    _register_tools(server)
    server.run()


if __name__ == "__main__":
    main()
