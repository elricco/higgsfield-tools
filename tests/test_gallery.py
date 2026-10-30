"""Tests for gallery.py – pure functions, so no mocks at all."""

import gallery


def test_find_request_id():
    name = "2026-10-30_higgsfield-ai-soul-v2-standard_01a18e46-23d4-7f70-b7d6-5e34755ccbc4_2.png"
    assert gallery.find_request_id(name) == "01a18e46-23d4-7f70-b7d6-5e34755ccbc4"
    assert gallery.find_request_id("turnaround-preview.png") is None


def test_media_kind():
    assert gallery.media_kind("/x/a.PNG") == "image"
    assert gallery.media_kind("/x/a.mp4") == "video"
    assert gallery.media_kind("/x/a.json") is None


def test_repeat_command_quotes_prompt_and_params():
    job = {
        "model": "a/b",
        "prompt": "a lake at dawn",
        "payload": {"prompt": "a lake at dawn", "duration": 5, "aspect_ratio": "16:9"},
    }
    assert gallery.repeat_command(job) == (
        "hf run a/b --prompt 'a lake at dawn' --param duration=5 --param aspect_ratio=16:9"
    )


def test_repeat_command_needs_prompt():
    assert gallery.repeat_command({"model": "a/b"}) is None


def item(**overrides):
    values = {
        "request_id": "r1",
        "model": "a/b",
        "prompt": "a lake",
        "usd": 0.004,
        "files": ["/tmp/x.png"],
        "date": "2026-10-30 10:00",
        "payload": {"prompt": "a lake"},
    }
    values.update(overrides)
    return values


def test_render_gallery_shows_items_filters_and_total():
    page = gallery.render_gallery(
        [item(), item(request_id="r2", model="c/d", usd=0.4, files=["/tmp/v.mp4"])], "2026-10-30 12:00"
    )
    assert "2 jobs · estimated $0.404" in page
    assert '<img src="file:///tmp/x.png"' in page
    assert '<video src="file:///tmp/v.mp4#t=0.1"' in page
    assert '<option value="a/b">' in page and '<option value="c/d">' in page
    assert 'data-kinds="video"' in page


def test_render_gallery_escapes_prompt():
    page = gallery.render_gallery([item(prompt="<script>alert(1)</script>")], "now")
    assert "<script>alert(1)</script>" not in page
    assert "&lt;script&gt;" in page


def test_render_gallery_hides_url_params():
    page = gallery.render_gallery(
        [item(payload={"prompt": "p", "image_url": "https://cdn/x.jpg", "duration": 5})], "now"
    )
    assert "duration: 5" in page
    assert "image_url:" not in page


def test_render_gallery_empty():
    assert "No results yet" in gallery.render_gallery([], "now")
