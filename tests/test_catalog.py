"""Tests for catalog.py – pure functions, so no mocks at all."""

import pytest

import catalog

# An excerpt in the format of llms-full.txt: one normal page, two model pages.
SAMPLE_DOCS = """# Authentication
Source: https://docs.higgsfield.ai/docs/authentication

Send both values in the Authorization header.

# Kling 2.5 Turbo — Standard image to video API
Source: https://docs.higgsfield.ai/docs/models/kling-2-5-turbo/standard-image-to-video

**Endpoint ID:** `kling-video/v2.5-turbo/standard/image-to-video`

## Usage notes

* duration accepts 5 or 10 seconds; 5 is the default.
* This endpoint does not expose a sound or aspect\\_ratio field.

## Input schema

> **Complete JSON schema**
>
> ```json
> {
>   "type": "object",
>   "required": ["prompt", "image_url"],
>   "properties": {
>     "prompt": {"type": "string", "title": "Prompt"},
>     "duration": {"enum": [5, 10], "type": "integer", "default": 5},
>     "image_url": {"type": "string", "format": "uri"},
>     "seed": {"anyOf": [{"type": "integer"}, {"type": "null"}]}
>   }
> }
> ```

## Response

# Z-Image — Turbo API
Source: https://docs.higgsfield.ai/docs/models/z-image/turbo

**Endpoint ID:** `z-image/turbo`

No schema on this page.
"""


def test_parse_docs_finds_model_pages_only():
    docs = catalog.parse_docs(SAMPLE_DOCS)
    assert sorted(docs) == ["kling-video/v2.5-turbo/standard/image-to-video", "z-image/turbo"]


def test_parse_docs_reads_schema_and_notes():
    kling = catalog.parse_docs(SAMPLE_DOCS)["kling-video/v2.5-turbo/standard/image-to-video"]
    assert kling["docs_url"].endswith("/kling-2-5-turbo/standard-image-to-video")
    assert kling["required"] == ["prompt", "image_url"]
    assert kling["params"]["duration"] == {"type": "integer", "default": 5, "enum": [5, 10]}
    assert kling["params"]["seed"] == {"type": ["integer", "null"]}
    # "\_" from the Markdown becomes "_".
    assert kling["notes"][1] == "This endpoint does not expose a sound or aspect_ratio field."


def test_parse_docs_page_without_schema():
    z_image = catalog.parse_docs(SAMPLE_DOCS)["z-image/turbo"]
    assert z_image["params"] == {}
    assert z_image["required"] == []


def test_build_catalog_merges_api_and_docs():
    api_items = [
        {"slug": "z-image/turbo", "title": "Z-Image", "output_type": "image", "operation_type": ["text2image"]},
        {"slug": "kling-video/v4.0/image-to-video", "title": "Kling 4.0", "output_type": "video",
         "operation_type": ["image2video"]},
    ]
    docs = {"z-image/turbo": {"required": ["prompt"]}, "old/model": {"required": []}}
    models = catalog.build_catalog(api_items, docs)

    assert models["z-image/turbo"]["documented"] is True
    assert models["z-image/turbo"]["required"] == ["prompt"]
    assert models["kling-video/v4.0/image-to-video"]["documented"] is False
    assert models["old/model"]["listed"] is False


@pytest.mark.parametrize("filters, expected", [
    ({}, True),
    ({"output": "video"}, True),
    ({"output": "image"}, False),
    ({"operation": "image2video"}, True),
    ({"operation": "text2video"}, False),
    ({"search": "kling 4.0"}, True),
    ({"search": "KLING"}, True),
    ({"search": "kling 3.0"}, False),
])
def test_matches(filters, expected):
    model = {"title": "Kling 4.0", "output": "video", "operations": ["image2video"]}
    assert catalog.matches("kling-video/v4.0/image-to-video", model, **filters) is expected


def test_placeholder_payload_uses_required_fields():
    model = {
        "required": ["prompt", "image_url", "duration", "reference_video_urls"],
        "params": {"duration": {"enum": [5, 10]}, "reference_video_urls": {"type": "array"}},
    }
    assert catalog.placeholder_payload(model) == {
        "prompt": "test",
        "image_url": "https://example.com/input.jpg",
        "duration": 5,
        "reference_video_urls": ["https://example.com/input.mp4"],
    }


def test_placeholder_payload_guesses_without_docs():
    model = {"operations": ["image2video", "video2video"]}
    assert catalog.placeholder_payload(model) == {
        "prompt": "test",
        "image_url": "https://example.com/input.jpg",
        "video_url": "https://example.com/input.mp4",
    }


def test_missing_field():
    assert catalog.missing_field("HTTP 400: 'audio_urls' is a required property") == "audio_urls"
    assert catalog.missing_field("HTTP 400: resolution: bad value") is None


def test_price_sort_key():
    prices = [{"usd": "0.5"}, None, {"usd": None, "pricing": "..."}, {"usd": "0.004"}]
    assert sorted(prices, key=catalog.price_sort_key)[:2] == [{"usd": "0.004"}, {"usd": "0.5"}]
