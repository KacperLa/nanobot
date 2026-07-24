"""Tests for LLM image preparation helpers."""

from __future__ import annotations

import base64
from io import BytesIO

from PIL import Image

from nanobot.utils.helpers import build_image_content_blocks, prepare_image_for_llm


def _jpeg(width: int, height: int, *, quality: int = 95) -> bytes:
    image = Image.new("RGB", (width, height), "#d8c6a3")
    buffer = BytesIO()
    image.save(buffer, format="JPEG", quality=quality)
    return buffer.getvalue()


def _noisy_jpeg(width: int, height: int, *, quality: int = 100) -> bytes:
    image = Image.effect_noise((width, height), 80).convert("RGB")
    buffer = BytesIO()
    image.save(buffer, format="JPEG", quality=quality)
    return buffer.getvalue()


def test_prepare_image_for_llm_downscales_large_images() -> None:
    raw = _jpeg(400, 300)

    prepared = prepare_image_for_llm(
        raw,
        "image/jpeg",
        max_edge=100,
        max_pixels=100 * 100,
    )

    assert prepared.resized is True
    assert prepared.mime == "image/jpeg"
    assert prepared.width <= 100
    assert prepared.height <= 100
    assert len(prepared.raw) < len(raw)


def test_prepare_image_for_llm_recompresses_to_base64_budget() -> None:
    raw = _noisy_jpeg(300, 300, quality=100)

    prepared = prepare_image_for_llm(
        raw,
        "image/jpeg",
        max_base64_bytes=12_000,
        max_edge=300,
        max_pixels=300 * 300,
    )

    assert prepared.resized is True
    assert prepared.base64_size_bytes <= 12_000


def test_build_image_content_blocks_uses_prepared_image_metadata() -> None:
    raw = _jpeg(300, 200)

    blocks = build_image_content_blocks(
        raw,
        "image/jpeg",
        "/tmp/receipt.jpg",
        "(Image file: /tmp/receipt.jpg)",
    )

    image_block = blocks[0]
    assert image_block["type"] == "image_url"
    assert image_block["_meta"]["path"] == "/tmp/receipt.jpg"
    encoded = image_block["image_url"]["url"].split(",", 1)[1]
    assert base64.b64decode(encoded)
