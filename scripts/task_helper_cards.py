#!/usr/bin/env python3
from __future__ import annotations

import argparse
import html
import json
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, urlparse


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import card_board  # noqa: E402
import task_board  # noqa: E402


HELPER_KINDS = ("watch", "read", "travel", "shopping", "outreach")
ACTIVE_TASK_LANES = {"backlog", "committed", "in-progress", "blocked"}
DEFAULT_TASKS_ROOT = Path.home() / ".nanobot" / "workspace" / "tasks"
DEFAULT_CARDS_ROOT = Path.home() / ".nanobot" / "cards"
CARD_LANE_BY_TASK_LANE = {
    "backlog": "context",
    "committed": "attention",
    "in-progress": "attention",
    "blocked": "attention",
}
CARD_PRIORITY_BY_TASK_LANE = {
    "backlog": 74,
    "committed": 82,
    "in-progress": 86,
    "blocked": 84,
}
HELPER_LABELS = {
    "watch": "Watch",
    "read": "Read",
    "travel": "Travel",
    "shopping": "Shop",
    "outreach": "Draft",
}
HELPER_SUMMARIES = {
    "watch": "Start with this instead of searching from scratch.",
    "read": "Reference material gathered for this task.",
    "travel": "Directions and destination context gathered for this task.",
    "shopping": "Shopping links gathered for this task.",
    "outreach": "A first-pass draft is ready for this task.",
}
TASK_KIND_PATTERNS: list[tuple[str, tuple[str, ...]]] = [
    (
        "watch",
        (
            "watch",
            "youtube",
            "video",
            "tutorial",
            "learn from",
            "lecture",
            "course",
        ),
    ),
    (
        "read",
        (
            "read",
            "research",
            "article",
            "paper",
            "docs",
            "documentation",
            "reference",
            "study",
        ),
    ),
    (
        "travel",
        (
            "go to",
            "drive to",
            "travel to",
            "directions",
            "map",
            "maps",
            "airport",
            "hotel",
            "visit",
            "drop by",
        ),
    ),
    (
        "shopping",
        (
            "buy",
            "order",
            "purchase",
            "shop",
            "find price",
            "pick up",
        ),
    ),
    (
        "outreach",
        (
            "call",
            "email",
            "reach out",
            "text",
            "message",
            "contact",
            "follow up",
        ),
    ),
]


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_kind(value: str) -> str:
    cleaned = str(value or "").strip().lower().replace(" ", "-").replace("_", "-")
    if cleaned not in HELPER_KINDS:
        raise ValueError(f"invalid helper kind: {value}")
    return cleaned


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", str(value or "").lower()).strip("-")
    return slug or "helper"


def _task_key(task: task_board.Task) -> str:
    source_uid = task.metadata.get("source_uid", "").strip()
    return source_uid or task.path.stem


def _helper_card_id(task: task_board.Task, kind: str) -> str:
    return f"task-helper-{kind}-{_slugify(_task_key(task))}"


def _helper_slot(task: task_board.Task, kind: str) -> str:
    return f"taskhelper:{kind}:{_task_key(task)}"


def _infer_kind(task: task_board.Task) -> str | None:
    text = " ".join(part for part in [task.title, task.body] if part).lower()
    for kind, patterns in TASK_KIND_PATTERNS:
        if any(pattern in text for pattern in patterns):
            return kind
    return None


def _escape_text(value: str) -> str:
    return html.escape(str(value or ""), quote=True)


def _sanitize_url(value: str) -> str:
    text = str(value or "").strip()
    return text if text.startswith(("http://", "https://", "mailto:", "tel:", "sms:")) else ""


def _normalize_resource(raw: dict[str, Any] | None) -> dict[str, str]:
    payload = raw if isinstance(raw, dict) else {}
    return {
        "title": str(payload.get("title", "") or "").strip(),
        "url": _sanitize_url(str(payload.get("url", "") or "")),
        "subtitle": str(payload.get("subtitle", "") or "").strip(),
        "meta": str(payload.get("meta", "") or "").strip(),
    }


def _normalize_resources(raw: list[dict[str, Any]] | None) -> list[dict[str, str]]:
    if not isinstance(raw, list):
        return []
    resources: list[dict[str, str]] = []
    for entry in raw:
        item = _normalize_resource(entry)
        if item["title"] or item["url"] or item["subtitle"] or item["meta"]:
            resources.append(item)
    return resources


def _youtube_embed_url(url: str) -> str:
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    video_id = ""
    if "youtu.be" in host:
        video_id = parsed.path.strip("/").split("/")[0]
    elif "youtube.com" in host:
        if parsed.path == "/watch":
            video_id = parse_qs(parsed.query).get("v", [""])[0]
        elif parsed.path.startswith("/embed/"):
            video_id = parsed.path.split("/", 2)[2]
        elif parsed.path.startswith("/shorts/"):
            video_id = parsed.path.split("/", 2)[2]
    if not video_id:
        return ""
    return f"https://www.youtube-nocookie.com/embed/{quote(video_id)}"


def _fallback_query(task: task_board.Task, query: str) -> str:
    explicit = str(query or "").strip()
    return explicit or task.title.strip()


def _mailto_url(recipient: str, subject: str, draft: str) -> str:
    to = recipient.strip()
    if not to or "@" not in to:
        return ""
    params = []
    if subject.strip():
        params.append(f"subject={quote(subject.strip())}")
    if draft.strip():
        params.append(f"body={quote(draft.strip())}")
    suffix = f"?{'&'.join(params)}" if params else ""
    return f"mailto:{quote(to)}{suffix}"


def _outreach_phone_url(recipient: str, channel: str, draft: str) -> str:
    digits = re.sub(r"[^0-9+]+", "", recipient)
    if not digits:
        return ""
    lowered = channel.strip().lower()
    if lowered == "text":
        suffix = f"?body={quote(draft.strip())}" if draft.strip() else ""
        return f"sms:{digits}{suffix}"
    if lowered == "call":
        return f"tel:{digits}"
    return ""


def _fallback_primary_resource(
    *,
    task: task_board.Task,
    kind: str,
    query: str,
    recipient: str,
    channel: str,
    subject: str,
    draft: str,
) -> dict[str, str]:
    search_query = quote(query)
    if kind == "watch":
        return {
            "title": "Search YouTube",
            "url": f"https://www.youtube.com/results?search_query={search_query}",
            "subtitle": query,
            "meta": "Fallback search",
        }
    if kind == "read":
        return {
            "title": "Search the web",
            "url": f"https://www.google.com/search?q={search_query}",
            "subtitle": query,
            "meta": "Fallback research",
        }
    if kind == "travel":
        return {
            "title": "Open in Google Maps",
            "url": f"https://www.google.com/maps/search/?api=1&query={search_query}",
            "subtitle": query,
            "meta": "Directions",
        }
    if kind == "shopping":
        return {
            "title": "Search products",
            "url": f"https://www.google.com/search?q={quote(f'buy {query}')}",
            "subtitle": query,
            "meta": "Fallback shopping search",
        }
    primary_url = _mailto_url(recipient, subject, draft)
    if not primary_url:
        primary_url = _outreach_phone_url(recipient, channel, draft)
    return {
        "title": "Use this draft",
        "url": primary_url,
        "subtitle": recipient or query or task.title,
        "meta": channel.title() if channel.strip() else "Outreach",
    }


def _content_resource_card(resource: dict[str, str], *, action_label: str) -> str:
    title = _escape_text(resource.get("title", "") or action_label)
    url = _escape_text(resource.get("url", ""))
    action_html = (
        f'<a href="{url}" target="_blank" rel="noreferrer" '
        'style="display:inline-block; margin-top:8px; padding:6px 10px; '
        'background:#7e583d; color:#fff8ef; text-decoration:none; font-size:0.72rem; '
        'line-height:1; font-weight:700;">'
        f"{_escape_text(action_label)}</a>"
        if url
        else ""
    )
    return (
        '<div style="margin-top:10px; padding:10px; border:1px solid rgba(146, 104, 73, 0.18); '
        'background:rgba(255, 249, 241, 0.92);">'
        f'<div style="font-size:0.9rem; line-height:1.02; color:#22150d; font-weight:700;">{title}</div>'
        f"{action_html}"
        "</div>"
    )


def _json_script_text(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")


def _render_outreach_content(card_id: str, helper_title: str, summary: str, draft: str) -> str:
    safe_card_id = _escape_text(card_id)
    safe_title = _escape_text(helper_title)
    safe_summary = _escape_text(summary.strip())
    safe_draft = _escape_text(draft)
    state_json = _json_script_text({"card_id": card_id, "draft": draft})
    summary_html = (
        f'<div style="margin-top:6px; color:#6b4d38; font-size:0.78rem; line-height:1.28;">{safe_summary}</div>'
        if safe_summary
        else ""
    )
    empty_hint = "Tap to add a draft."
    return (
        f'<div data-helper-outreach-root data-card-id="{safe_card_id}" '
        'style="font-family:\'IBM Plex Sans Condensed\', \'Arial Narrow\', sans-serif;">'
        f'<div style="font-size:1rem; line-height:1.02; color:#20130b; font-weight:700;">{safe_title}</div>'
        f"{summary_html}"
        '<div data-helper-outreach-view tabindex="0" '
        'style="margin-top:12px; padding:10px; border:1px solid rgba(146, 104, 73, 0.18); '
        'background:rgba(255, 249, 241, 0.92); white-space:pre-wrap; color:#352116; '
        'font-size:0.78rem; line-height:1.32; cursor:text;">'
        f"{safe_draft or _escape_text(empty_hint)}"
        "</div>"
        '<textarea data-helper-outreach-editor '
        'style="display:none; width:100%; margin-top:12px; padding:10px; border:1px solid rgba(146, 104, 73, 0.28); '
        'background:rgba(255, 249, 241, 0.92); color:#352116; font:400 0.78rem/1.32 \'IBM Plex Sans Condensed\', '
        '\'Arial Narrow\', sans-serif; resize:none; box-sizing:border-box; outline:none;" '
        'rows="1"></textarea>'
        '<div style="display:flex; justify-content:flex-end; margin-top:8px;">'
        '<button type="button" data-helper-outreach-copy '
        'style="padding:6px 10px; border:1px solid rgba(126, 88, 61, 0.22); background:rgba(255, 249, 241, 0.92); '
        'color:#5c2f17; font:700 0.72rem/1 \'M-1m Code\', ui-monospace, monospace; cursor:pointer;">Copy</button>'
        "</div>"
        '<div data-helper-outreach-status '
        'style="margin-top:6px; min-height:0.72rem; color:#8b6447; font-size:0.68rem; line-height:1;"></div>'
        f'<script type="application/json" data-helper-outreach-state>{state_json}</script>'
        "<script>(function(){\n"
        "  const script = document.currentScript;\n"
        "  const root = script?.closest('[data-helper-outreach-root]');\n"
        "  if (!(root instanceof HTMLElement)) return;\n"
        "  const stateEl = root.querySelector('script[data-helper-outreach-state]');\n"
        "  const viewEl = root.querySelector('[data-helper-outreach-view]');\n"
        "  const editorEl = root.querySelector('[data-helper-outreach-editor]');\n"
        "  const copyBtn = root.querySelector('[data-helper-outreach-copy]');\n"
        "  const statusEl = root.querySelector('[data-helper-outreach-status]');\n"
        "  if (!(stateEl instanceof HTMLScriptElement) || !(viewEl instanceof HTMLElement) || !(editorEl instanceof HTMLTextAreaElement) || !(copyBtn instanceof HTMLButtonElement) || !(statusEl instanceof HTMLElement)) return;\n"
        "  let state = {};\n"
        "  try { state = JSON.parse(stateEl.textContent || '{}'); } catch { state = {}; }\n"
        "  const cardId = typeof state.card_id === 'string' ? state.card_id.trim() : '';\n"
        f"  const emptyHint = {json.dumps(empty_hint)};\n"
        "  let currentDraft = typeof state.draft === 'string' ? state.draft : '';\n"
        "  let editing = false;\n"
        "  let saving = false;\n"
        "  const autosize = () => { editorEl.style.height = '0px'; editorEl.style.height = `${Math.max(editorEl.scrollHeight, 24)}px`; };\n"
        "  const setStatus = (text, tone) => { statusEl.textContent = text || ''; statusEl.style.color = tone || '#8b6447'; };\n"
        "  const render = () => {\n"
        "    viewEl.textContent = currentDraft || emptyHint;\n"
        "    editorEl.value = currentDraft;\n"
        "    autosize();\n"
        "    copyBtn.disabled = saving;\n"
        "  };\n"
        "  const enterEdit = () => {\n"
        "    if (saving || editing) return;\n"
        "    editing = true;\n"
        "    viewEl.style.display = 'none';\n"
        "    editorEl.style.display = 'block';\n"
        "    editorEl.value = currentDraft;\n"
        "    autosize();\n"
        "    editorEl.focus();\n"
        "    const end = editorEl.value.length;\n"
        "    editorEl.setSelectionRange(end, end);\n"
        "    setStatus('', '#8b6447');\n"
        "  };\n"
        "  const exitEdit = () => {\n"
        "    editing = false;\n"
        "    editorEl.style.display = 'none';\n"
        "    viewEl.style.display = 'block';\n"
        "  };\n"
        "  const saveDraft = async () => {\n"
        "    const nextDraft = editorEl.value;\n"
        "    if (nextDraft === currentDraft) { exitEdit(); return; }\n"
        "    if (!cardId || typeof window.__nanobotCallTool !== 'function') { currentDraft = nextDraft; exitEdit(); render(); return; }\n"
        "    saving = true;\n"
        "    copyBtn.disabled = true;\n"
        "    setStatus('Saving', '#8b6447');\n"
        "    try {\n"
        "      const result = await window.__nanobotCallTool('task_helper_card', { action: 'update_draft', card_id: cardId, draft: nextDraft });\n"
        "      const payload = result?.parsed && typeof result.parsed === 'object' ? result.parsed : null;\n"
        "      const card = payload && typeof payload.card === 'object' ? payload.card : null;\n"
        "      const templateState = card && typeof card.template_state === 'object' ? card.template_state : null;\n"
        "      currentDraft = templateState && typeof templateState.draft === 'string' ? templateState.draft : nextDraft;\n"
        "      state.draft = currentDraft;\n"
        "      stateEl.textContent = JSON.stringify(state).replace(/<\\//g, '<\\\\/');\n"
        "      render();\n"
        "      exitEdit();\n"
        "      setStatus('Saved', '#4f7862');\n"
        "      window.setTimeout(() => { if (!editing && !saving) setStatus('', '#8b6447'); }, 1200);\n"
        "    } catch (error) {\n"
        "      console.error('Outreach helper save failed', error);\n"
        "      setStatus(error instanceof Error ? error.message : String(error), '#b45309');\n"
        "      editorEl.focus();\n"
        "      return;\n"
        "    } finally {\n"
        "      saving = false;\n"
        "      copyBtn.disabled = false;\n"
        "    }\n"
        "  };\n"
        "  const copyDraft = async () => {\n"
        "    const text = editing ? editorEl.value : currentDraft;\n"
        "    try {\n"
        "      if (navigator.clipboard && typeof navigator.clipboard.writeText === 'function') {\n"
        "        await navigator.clipboard.writeText(text);\n"
        "      } else {\n"
        "        editorEl.style.display = 'block';\n"
        "        editorEl.value = text;\n"
        "        editorEl.select();\n"
        "        document.execCommand('copy');\n"
        "        if (!editing) editorEl.style.display = 'none';\n"
        "      }\n"
        "      setStatus('Copied', '#4f7862');\n"
        "      window.setTimeout(() => { if (!editing && !saving) setStatus('', '#8b6447'); }, 1200);\n"
        "    } catch (error) {\n"
        "      setStatus(error instanceof Error ? error.message : String(error), '#b45309');\n"
        "    }\n"
        "  };\n"
        "  viewEl.addEventListener('click', enterEdit);\n"
        "  viewEl.addEventListener('keydown', (event) => {\n"
        "    if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); enterEdit(); }\n"
        "  });\n"
        "  editorEl.addEventListener('input', autosize);\n"
        "  editorEl.addEventListener('blur', () => { if (!saving) void saveDraft(); });\n"
        "  editorEl.addEventListener('keydown', (event) => {\n"
        "    if (event.key === 'Escape') {\n"
        "      event.preventDefault();\n"
        "      editorEl.value = currentDraft;\n"
        "      autosize();\n"
        "      exitEdit();\n"
        "      setStatus('', '#8b6447');\n"
        "      return;\n"
        "    }\n"
        "    if ((event.metaKey || event.ctrlKey) && event.key === 'Enter') {\n"
        "      event.preventDefault();\n"
        "      editorEl.blur();\n"
        "    }\n"
        "  });\n"
        "  copyBtn.addEventListener('click', () => { void copyDraft(); });\n"
        "  render();\n"
        "})();</script>"
        "</div>"
    )


def _render_helper_content(state: dict[str, Any], *, card_id: str) -> str:
    kind = str(state.get("helper_kind", "") or "")
    task_title = str(state.get("task_title", "") or "Untitled task")
    helper_title = str(state.get("helper_title", "") or f"{HELPER_LABELS.get(kind, 'Helper')}: {task_title}")
    summary = str(state.get("summary", "") or "")
    primary = _normalize_resource(state.get("primary"))
    draft = str(state.get("draft", "") or "")

    if kind == "outreach":
        return _render_outreach_content(card_id, helper_title, summary, draft)

    body_parts = [
        f'<div style="font-size:1rem; line-height:1.02; color:#20130b; font-weight:700;">{_escape_text(helper_title)}</div>',
    ]
    if summary:
        body_parts.append(
            f'<div style="margin-top:6px; color:#6b4d38; font-size:0.78rem; line-height:1.28;">{_escape_text(summary)}</div>'
        )

    embed_url = str(state.get("embed_url", "") or "")
    if kind == "watch" and embed_url:
        escaped_embed_url = _escape_text(embed_url)
        body_parts.append(
            '<div style="position:relative; margin-top:10px; padding-top:56.25%; '
            'background:#e5d0bd; overflow:hidden;">'
            f'<iframe src="{escaped_embed_url}" title="{_escape_text(helper_title)}" '
            'allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share" '
            'allowfullscreen style="position:absolute; inset:0; width:100%; height:100%; border:0;"></iframe>'
            '</div>'
        )

    action_label = {
        "watch": "Watch",
        "read": "Open",
        "travel": "Directions",
        "shopping": "View",
        "outreach": "Use draft",
    }.get(kind, "Open")
    if primary["title"] or primary["url"]:
        body_parts.append(_content_resource_card(primary, action_label=action_label))

    return (
        '<div style="font-family:\'IBM Plex Sans Condensed\', \'Arial Narrow\', sans-serif;">'
        + "".join(part for part in body_parts if part)
        + "</div>"
    )


def _build_card_state(
    *,
    task: task_board.Task,
    kind: str,
    helper_title: str,
    summary: str,
    query: str,
    primary: dict[str, str],
    alternatives: list[dict[str, str]],
    notes: str,
    recipient: str,
    channel: str,
    subject: str,
    draft: str,
) -> dict[str, Any]:
    embed_url = _youtube_embed_url(primary.get("url", "")) if kind == "watch" else ""
    return {
        "helper_kind": kind,
        "task_path": str(task.path),
        "task_key": _task_key(task),
        "task_title": task.title,
        "task_lane": task.lane,
        "task_due": task.due or None,
        "task_tags": list(task.tags),
        "helper_title": helper_title,
        "summary": summary,
        "query": query,
        "primary": primary,
        "alternatives": alternatives,
        "notes": notes,
        "recipient": recipient,
        "channel": channel,
        "subject": subject,
        "draft": draft,
        "embed_url": embed_url,
    }


def upsert_helper_card(
    *,
    task_path: str,
    tasks_root: Path = DEFAULT_TASKS_ROOT,
    cards_root: Path = DEFAULT_CARDS_ROOT,
    kind: str = "",
    title: str = "",
    summary: str = "",
    query: str = "",
    primary: dict[str, Any] | None = None,
    alternatives: list[dict[str, Any]] | None = None,
    notes: str = "",
    recipient: str = "",
    channel: str = "",
    subject: str = "",
    draft: str = "",
    chat_id: str = "web",
) -> dict[str, Any]:
    task_board.ensure_board(tasks_root)
    card_board.ensure_cards_root(cards_root)
    task = task_board.parse_task(task_board.resolve_task_path(tasks_root, task_path))
    helper_kind = _normalize_kind(kind) if kind.strip() else (_infer_kind(task) or "")
    if not helper_kind:
        raise ValueError("could not infer a helper kind for this task; provide kind explicitly")

    normalized_primary = _normalize_resource(primary)
    normalized_alternatives = _normalize_resources(alternatives)
    effective_query = _fallback_query(task, query)
    effective_summary = summary.strip() or HELPER_SUMMARIES.get(helper_kind, "")
    effective_title = title.strip() or f"{HELPER_LABELS.get(helper_kind, 'Helper')}: {task.title}"
    effective_notes = notes.strip()
    effective_recipient = recipient.strip()
    effective_channel = channel.strip()
    effective_subject = subject.strip()
    effective_draft = draft.strip()

    if not normalized_primary["url"] and helper_kind == "outreach" and task.body.strip() and not effective_draft:
        effective_draft = task.body.strip()

    if not normalized_primary["title"] and not normalized_primary["url"]:
        normalized_primary = _fallback_primary_resource(
            task=task,
            kind=helper_kind,
            query=effective_query,
            recipient=effective_recipient,
            channel=effective_channel,
            subject=effective_subject,
            draft=effective_draft,
        )

    state = _build_card_state(
        task=task,
        kind=helper_kind,
        helper_title=effective_title,
        summary=effective_summary,
        query=effective_query,
        primary=normalized_primary,
        alternatives=normalized_alternatives,
        notes=effective_notes,
        recipient=effective_recipient,
        channel=effective_channel,
        subject=effective_subject,
        draft=effective_draft,
    )
    card_id = _helper_card_id(task, helper_kind)
    content = _render_helper_content(state, card_id=card_id)
    card_payload = {
        "id": card_id,
        "kind": "text",
        "title": effective_title,
        "content": content,
        "slot": _helper_slot(task, helper_kind),
        "lane": CARD_LANE_BY_TASK_LANE.get(task.lane, "context"),
        "priority": CARD_PRIORITY_BY_TASK_LANE.get(task.lane, 74),
        "state": "active",
        "template_key": "",
        "template_state": state,
        "context_summary": f"{HELPER_LABELS.get(helper_kind, 'Helper')} card for task: {task.title}",
        "chat_id": chat_id,
    }
    persisted = card_board.write_card(cards_root, card_payload)
    return {
        "card": persisted,
        "task": task.to_dict(),
        "helper_kind": helper_kind,
    }


def update_helper_card_draft(
    *,
    card_id: str,
    cards_root: Path = DEFAULT_CARDS_ROOT,
    draft: str,
) -> dict[str, Any]:
    card_board.ensure_cards_root(cards_root)
    target_id = card_id.strip()
    if not target_id:
        raise ValueError("card_id is required")
    card = card_board.load_card(cards_root, target_id)
    state = dict(card.get("template_state", {}))
    if str(state.get("helper_kind", "")).strip() != "outreach":
        raise ValueError("only outreach helper cards support draft editing")
    state["draft"] = draft
    card["template_state"] = state
    card["content"] = _render_helper_content(state, card_id=target_id)
    card["updated_at"] = _utc_now_iso()
    persisted = card_board.write_card(cards_root, card)
    return {"card": persisted, "helper_kind": "outreach"}


def remove_helper_card(
    *,
    task_path: str,
    tasks_root: Path = DEFAULT_TASKS_ROOT,
    cards_root: Path = DEFAULT_CARDS_ROOT,
    kind: str,
) -> dict[str, Any]:
    task_board.ensure_board(tasks_root)
    card_board.ensure_cards_root(cards_root)
    task = task_board.parse_task(task_board.resolve_task_path(tasks_root, task_path))
    helper_kind = _normalize_kind(kind)
    card_id = _helper_card_id(task, helper_kind)
    instance_dir = cards_root / "instances" / card_id
    existed = instance_dir.exists()
    shutil.rmtree(instance_dir, ignore_errors=True)
    return {"card_id": card_id, "removed": existed}


def _is_helper_card(card: dict[str, Any]) -> bool:
    slot = str(card.get("slot", "") or "")
    template_state = card.get("template_state", {})
    helper_kind = ""
    if isinstance(template_state, dict):
        helper_kind = str(template_state.get("helper_kind", "") or "")
    return slot.startswith("taskhelper:") and helper_kind in HELPER_KINDS


def sync_helper_cards(
    *,
    tasks_root: Path = DEFAULT_TASKS_ROOT,
    cards_root: Path = DEFAULT_CARDS_ROOT,
    chat_id: str = "web",
) -> dict[str, Any]:
    task_board.ensure_board(tasks_root)
    card_board.ensure_cards_root(cards_root)
    cards = card_board.collect_cards(cards_root, chat_id=chat_id)
    created_or_updated: list[str] = []
    removed: list[str] = []
    for card in cards:
        if not _is_helper_card(card):
            continue
        state = card.get("template_state", {})
        if not isinstance(state, dict):
            continue
        task_path = str(state.get("task_path", "") or "").strip()
        helper_kind = str(state.get("helper_kind", "") or "").strip()
        if not task_path or helper_kind not in HELPER_KINDS:
            continue
        try:
            task = task_board.parse_task(task_board.resolve_task_path(tasks_root, task_path))
        except Exception:
            shutil.rmtree(cards_root / "instances" / str(card.get("id", "")), ignore_errors=True)
            removed.append(str(card.get("id", "")))
            continue
        if task.lane not in ACTIVE_TASK_LANES:
            shutil.rmtree(cards_root / "instances" / str(card.get("id", "")), ignore_errors=True)
            removed.append(str(card.get("id", "")))
            continue

        refreshed = upsert_helper_card(
            task_path=str(task.path),
            tasks_root=tasks_root,
            cards_root=cards_root,
            kind=helper_kind,
            title=str(state.get("helper_title", "") or ""),
            summary=str(state.get("summary", "") or ""),
            query=str(state.get("query", "") or ""),
            primary=state.get("primary") if isinstance(state.get("primary"), dict) else None,
            alternatives=state.get("alternatives") if isinstance(state.get("alternatives"), list) else None,
            notes=str(state.get("notes", "") or ""),
            recipient=str(state.get("recipient", "") or ""),
            channel=str(state.get("channel", "") or ""),
            subject=str(state.get("subject", "") or ""),
            draft=str(state.get("draft", "") or ""),
            chat_id=chat_id,
        )
        created_or_updated.append(str(refreshed["card"]["id"]))
    return {
        "updated": created_or_updated,
        "removed": removed,
        "count": len(created_or_updated),
        "generated_at": _utc_now_iso(),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create and sync linked helper cards for tasks.")
    parser.add_argument("--tasks-root", default=str(DEFAULT_TASKS_ROOT))
    parser.add_argument("--cards-root", default=str(DEFAULT_CARDS_ROOT))
    parser.add_argument("--chat-id", default="web")
    subparsers = parser.add_subparsers(dest="command", required=True)

    augment_parser = subparsers.add_parser("augment")
    augment_parser.add_argument("--task", required=True)
    augment_parser.add_argument("--kind", default="")
    augment_parser.add_argument("--title", default="")
    augment_parser.add_argument("--summary", default="")
    augment_parser.add_argument("--query", default="")
    augment_parser.add_argument("--primary-title", default="")
    augment_parser.add_argument("--primary-url", default="")
    augment_parser.add_argument("--primary-subtitle", default="")
    augment_parser.add_argument("--primary-meta", default="")
    augment_parser.add_argument("--notes", default="")
    augment_parser.add_argument("--recipient", default="")
    augment_parser.add_argument("--channel", default="")
    augment_parser.add_argument("--subject", default="")
    augment_parser.add_argument("--draft", default="")
    augment_parser.add_argument(
        "--alternatives-json",
        default="[]",
        help="JSON array of alternative resources.",
    )

    remove_parser = subparsers.add_parser("remove")
    remove_parser.add_argument("--task", required=True)
    remove_parser.add_argument("--kind", required=True, choices=HELPER_KINDS)

    update_draft_parser = subparsers.add_parser("update-draft")
    update_draft_parser.add_argument("--card-id", required=True)
    update_draft_parser.add_argument("--draft", default="")

    subparsers.add_parser("sync")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    tasks_root = Path(args.tasks_root).expanduser()
    cards_root = Path(args.cards_root).expanduser()
    chat_id = str(args.chat_id).strip() or "web"

    if args.command == "augment":
        try:
            alternatives = json.loads(args.alternatives_json)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid alternatives JSON: {exc}") from exc
        result = upsert_helper_card(
            task_path=str(args.task),
            tasks_root=tasks_root,
            cards_root=cards_root,
            kind=str(args.kind),
            title=str(args.title),
            summary=str(args.summary),
            query=str(args.query),
            primary={
                "title": str(args.primary_title),
                "url": str(args.primary_url),
                "subtitle": str(args.primary_subtitle),
                "meta": str(args.primary_meta),
            },
            alternatives=alternatives if isinstance(alternatives, list) else [],
            notes=str(args.notes),
            recipient=str(args.recipient),
            channel=str(args.channel),
            subject=str(args.subject),
            draft=str(args.draft),
            chat_id=chat_id,
        )
    elif args.command == "remove":
        result = remove_helper_card(
            task_path=str(args.task),
            tasks_root=tasks_root,
            cards_root=cards_root,
            kind=str(args.kind),
        )
    elif args.command == "update-draft":
        result = update_helper_card_draft(
            card_id=str(args.card_id),
            cards_root=cards_root,
            draft=str(args.draft),
        )
    elif args.command == "sync":
        result = sync_helper_cards(tasks_root=tasks_root, cards_root=cards_root, chat_id=chat_id)
    else:
        raise ValueError(f"unsupported command: {args.command}")

    json.dump(result, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
