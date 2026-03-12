"""Validation helpers for rendered web UI cards."""

from __future__ import annotations

from dataclasses import dataclass
from html.parser import HTMLParser
import re
from typing import Any

_HTML_OPEN_RE = re.compile(r"<(?:!doctype|[a-zA-Z][^>]*)>", re.IGNORECASE)
_PROXY_REF_RE = re.compile(r"/ha/proxy/[^\s\"'`<>,;)]*")
_SCRIPT_PROXY_REF_RE = re.compile(r"/script/proxy/[^\s\"'`<>,;)]*")
_DIRECT_API_RE = re.compile(r"https?://[^\s\"'`<>,;)]*/api/[^\s\"'`<>,;)]*", re.IGNORECASE)
_VOID_TAGS = {
    "area",
    "base",
    "br",
    "col",
    "embed",
    "hr",
    "img",
    "input",
    "link",
    "meta",
    "param",
    "source",
    "track",
    "wbr",
}
_AUTO_CLOSE_TAGS = {"li", "p"}


@dataclass(frozen=True)
class ProxyReference:
    """A proxy path found in card content."""

    raw: str
    probe_path: str
    probe_mode: str
    detail: str
    source_kind: str = "home_assistant"


class _CardHTMLValidator(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.errors: list[str] = []
        self._stack: list[tuple[str, tuple[int, int]]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        normalized = tag.lower()
        if normalized in _AUTO_CLOSE_TAGS and self._stack and self._stack[-1][0] == normalized:
            self._stack.pop()
        if normalized not in _VOID_TAGS:
            self._stack.append((normalized, self.getpos()))

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        return

    def handle_endtag(self, tag: str) -> None:
        normalized = tag.lower()
        if normalized in _VOID_TAGS:
            self.errors.append(f"void tag </{normalized}> should not have a closing tag")
            return
        if not self._stack:
            self.errors.append(f"unexpected closing tag </{normalized}>")
            return
        if self._stack[-1][0] == normalized:
            self._stack.pop()
            return

        for idx in range(len(self._stack) - 1, -1, -1):
            if self._stack[idx][0] == normalized:
                expected = self._stack[-1][0]
                self.errors.append(
                    f"mismatched closing tag </{normalized}>; expected </{expected}> first"
                )
                del self._stack[idx:]
                return

        self.errors.append(f"unexpected closing tag </{normalized}>")

    def close(self) -> None:
        super().close()
        while self._stack:
            tag, _pos = self._stack.pop()
            self.errors.append(f"unclosed tag <{tag}>")


def looks_like_html(content: str) -> bool:
    return bool(_HTML_OPEN_RE.search(content or ""))


def validate_html_fragment(content: str) -> list[str]:
    if not looks_like_html(content):
        return []

    parser = _CardHTMLValidator()
    parser.feed(content)
    parser.close()
    return parser.errors


def find_direct_api_urls(content: str) -> list[str]:
    urls: list[str] = []
    seen: set[str] = set()
    for match in _DIRECT_API_RE.finditer(content or ""):
        url = match.group(0).rstrip(".")
        if url and url not in seen:
            seen.add(url)
            urls.append(url)
    return urls


def find_proxy_references(content: str) -> list[ProxyReference]:
    refs, errors = inspect_proxy_references(content)
    if errors:
        raise ValueError("; ".join(errors))
    return refs


def inspect_proxy_references(content: str) -> tuple[list[ProxyReference], list[str]]:
    refs: list[ProxyReference] = []
    errors: list[str] = []
    seen: set[str] = set()

    for pattern in (_PROXY_REF_RE, _SCRIPT_PROXY_REF_RE):
        for match in pattern.finditer(content or ""):
            raw = match.group(0).rstrip(".")
            if not raw or raw in seen:
                continue
            seen.add(raw)
            try:
                refs.append(_normalize_proxy_reference(raw))
            except ValueError as exc:
                errors.append(str(exc))
    return refs, errors


def find_script_proxy_references(content: str) -> tuple[list[ProxyReference], list[str]]:
    refs: list[ProxyReference] = []
    errors: list[str] = []
    seen: set[str] = set()

    for match in _SCRIPT_PROXY_REF_RE.finditer(content or ""):
        raw = match.group(0).rstrip(".")
        if not raw or raw in seen:
            continue
        seen.add(raw)
        try:
            refs.append(_normalize_proxy_reference(raw))
        except ValueError as exc:
            errors.append(str(exc))
    return refs, errors


def find_direct_api_urls_in_state(state: Any) -> list[str]:
    urls: list[str] = []
    seen: set[str] = set()

    for value in _iter_state_strings(state):
        candidate = value.strip()
        if not candidate:
            continue
        if not _DIRECT_API_RE.fullmatch(candidate):
            continue
        if candidate in seen:
            continue
        seen.add(candidate)
        urls.append(candidate)

    return urls


def inspect_state_proxy_references(state: Any) -> tuple[list[ProxyReference], list[str]]:
    refs: list[ProxyReference] = []
    errors: list[str] = []
    seen: set[str] = set()

    for value in _iter_state_strings(state):
        candidate = value.strip()
        if not candidate.startswith("/ha/proxy/") and not candidate.startswith("/script/proxy/"):
            continue
        if candidate in seen:
            continue
        seen.add(candidate)
        try:
            refs.append(_normalize_proxy_reference(candidate))
        except ValueError as exc:
            errors.append(str(exc))

    return refs, errors


def _iter_state_strings(state: Any) -> list[str]:
    values: list[str] = []

    def visit(node: Any) -> None:
        if isinstance(node, str):
            values.append(node)
            return
        if isinstance(node, dict):
            for item in node.values():
                visit(item)
            return
        if isinstance(node, (list, tuple, set)):
            for item in node:
                visit(item)

    visit(state)
    return values


def _normalize_proxy_reference(raw: str) -> ProxyReference:
    if raw.startswith("/script/proxy/"):
        tail = raw[len("/script/proxy") :]
        if not tail.startswith("/") or tail == "/":
            raise ValueError(f"script proxy reference is missing a target path: {raw}")
        if "${" in raw:
            raise ValueError(
                f"dynamic script endpoint is not allowed: {raw}. Use an exact workspace script path."
            )
        return ProxyReference(
            raw=raw,
            probe_path=tail,
            probe_mode="exact",
            detail="workspace script endpoint",
            source_kind="script",
        )

    if not raw.startswith("/ha/proxy/"):
        raise ValueError(f"invalid proxy reference: {raw}")

    tail = raw[len("/ha/proxy") :]
    if not tail.startswith("/") or tail == "/":
        raise ValueError(f"proxy reference is missing a target path: {raw}")

    path_part = tail.split("?", 1)[0]
    if "${" not in path_part:
        return ProxyReference(
            raw=raw,
            probe_path=path_part,
            probe_mode="exact",
            detail="exact path",
            source_kind="home_assistant",
        )

    static_prefix = path_part.split("${", 1)[0]
    if static_prefix == "/states/":
        raise ValueError(
            f"dynamic state endpoint is not allowed: {raw}. Use an exact entity id from discovery."
        )
    if static_prefix == "/calendars/":
        return ProxyReference(
            raw=raw,
            probe_path="/calendars",
            probe_mode="exact",
            detail="calendar collection for dynamic calendar event lookup",
            source_kind="home_assistant",
        )
    if not static_prefix:
        raise ValueError(f"proxy reference is too dynamic to validate: {raw}")

    return ProxyReference(
        raw=raw,
        probe_path=static_prefix.rstrip("/") or static_prefix,
        probe_mode="prefix",
        detail="dynamic path prefix",
        source_kind="home_assistant",
    )
