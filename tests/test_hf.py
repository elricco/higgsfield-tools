"""Tests for hf.py – without real API calls.

Instead of sending HTTP, the functions get a FakeSession with
prepared responses. time.sleep is replaced so that nothing waits.
"""

import argparse
import json
from datetime import date
from pathlib import Path

import pytest
import requests
from fakes import FakeResponse, FakeSession

import hf

# --- Fixtures ---------------------------------------------------------------
# A fixture is a function whose return value pytest automatically passes to
# tests that have a parameter with its name.


@pytest.fixture(autouse=True)
def history_file(tmp_path, monkeypatch):
    """Runs automatically for every test: the job log lives in the temporary folder.

    autouse=True means no test can accidentally write to the real .hf-history.json.
    """
    path = tmp_path / "history.json"
    monkeypatch.setattr(hf, "HISTORY_FILE", path)
    monkeypatch.setattr(hf, "GALLERY_FILE", tmp_path / "gallery.html")
    return path


@pytest.fixture(autouse=True)
def browser_calls(monkeypatch):
    """Runs automatically: no test opens a real browser; the URLs are recorded instead."""
    calls = []
    monkeypatch.setattr(hf.webbrowser, "open", lambda url: calls.append(url))
    return calls


@pytest.fixture
def sleeps(monkeypatch):
    """Replaces time.sleep: nothing waits, but the wait times are recorded."""
    recorded = []
    monkeypatch.setattr(hf.time, "sleep", lambda seconds: recorded.append(seconds))
    return recorded


@pytest.fixture
def characters_file(tmp_path, monkeypatch):
    """A characters.json with one trained character."""
    path = tmp_path / "characters.json"
    path.write_text(json.dumps({"Mila": {"id": "ref-123", "model_version": "v2", "status": "completed"}}))
    monkeypatch.setattr(hf, "CHARACTERS_FILE", path)
    return path


@pytest.fixture
def models_file(tmp_path, monkeypatch):
    """A small models.json in the temporary folder instead of the real one."""
    path = tmp_path / "models.json"
    path.write_text(
        json.dumps(
            {
                "test/image": {"type": "text-to-image", "defaults": {"aspect_ratio": "4:3", "resolution": "720p"}},
            }
        )
    )
    monkeypatch.setattr(hf, "MODELS_FILE", path)
    return path


# --- Parameters -------------------------------------------------------------


@pytest.mark.parametrize(
    "text, expected",
    [
        ("duration=5", ("duration", 5)),
        ("cfg_scale=0.5", ("cfg_scale", 0.5)),
        ("enhance_prompt=true", ("enhance_prompt", True)),
        ("aspect_ratio=16:9", ("aspect_ratio", "16:9")),
        ("style_id=abc=def", ("style_id", "abc=def")),
        ("prompt=", ("prompt", "")),
    ],
)
def test_parse_param(text, expected):
    assert hf.parse_param(text) == expected


def test_parse_param_without_equals_sign():
    # pytest.raises checks that the block raises exactly this error.
    with pytest.raises(hf.CliError, match="key=value"):
        hf.parse_param("duration")


def generation_args(**overrides) -> argparse.Namespace:
    """Arguments as from 'submit'/'run'; individual fields can be overridden."""
    values = {
        "model_id": "test/image",
        "prompt": "p",
        "image_url": None,
        "image_urls": None,
        "character": None,
        "param": [],
        "max_usd": None,
        "from_last_frame": None,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def test_build_payload_order(models_file):
    args = generation_args(prompt="a lake", image_url="https://x/a.jpg", param=["resolution=1080p", "seed=7"])
    assert hf.build_payload(args) == {
        "aspect_ratio": "4:3",  # from models.json
        "resolution": "1080p",  # --param overrides the default
        "prompt": "a lake",
        "image_url": "https://x/a.jpg",
        "seed": 7,
    }


def test_build_payload_unknown_model_has_no_defaults(models_file):
    assert hf.build_payload(generation_args(model_id="other/model")) == {"prompt": "p"}


def test_build_payload_image_urls_keeps_order(models_file):
    args = generation_args(model_id="other/model", image_urls=["https://x/1.jpg", "https://x/2.jpg"])
    assert hf.build_payload(args)["image_urls"] == ["https://x/1.jpg", "https://x/2.jpg"]


def test_build_payload_character(models_file, characters_file):
    payload = hf.build_payload(generation_args(model_id="other/model", character="mila"))
    assert payload["custom_reference_id"] == "ref-123"
    assert payload["custom_reference_strength"] == 1.0


def test_build_payload_character_strength_can_be_overridden(models_file, characters_file):
    args = generation_args(model_id="other/model", character="Mila", param=["custom_reference_strength=0.6"])
    assert hf.build_payload(args)["custom_reference_strength"] == 0.6


# --- Configuration ----------------------------------------------------------


def test_load_dotenv(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text(
        '# comment\n\nHF_API_KEY=key-from-file\nexport HF_API_SECRET="secret=with=equals"\nHF_OTHER=from-file\n'
    )
    monkeypatch.delenv("HF_API_KEY", raising=False)
    monkeypatch.delenv("HF_API_SECRET", raising=False)
    monkeypatch.setenv("HF_OTHER", "from-shell")

    hf.load_dotenv(env)

    assert hf.os.environ["HF_API_KEY"] == "key-from-file"
    assert hf.os.environ["HF_API_SECRET"] == "secret=with=equals"
    # Variables that are already set take precedence over the file.
    assert hf.os.environ["HF_OTHER"] == "from-shell"


def test_load_dotenv_missing_file_is_fine(tmp_path):
    hf.load_dotenv(tmp_path / "does-not-exist")


def test_load_credentials_placeholders(monkeypatch):
    monkeypatch.setenv("HF_API_KEY", "your-api-key-id")
    monkeypatch.setenv("HF_API_SECRET", "your-api-key-secret")
    with pytest.raises(hf.CliError, match="placeholders"):
        hf.load_credentials()


def test_load_credentials_missing(monkeypatch):
    monkeypatch.delenv("HF_API_KEY", raising=False)
    monkeypatch.setenv("HF_API_SECRET", "s")
    with pytest.raises(hf.CliError) as info:
        hf.load_credentials()
    assert info.value.exit_code == 2


# --- Evaluating the status --------------------------------------------------


def test_compact_status_completed():
    result = {"request_id": "r1", "status": "completed", "images": [{"url": "u"}], "status_url": "s"}
    assert hf.compact_status(result) == {"request_id": "r1", "status": "completed"}


def test_compact_status_failed_keeps_api_error():
    result = {"request_id": "r1", "status": "failed", "error": "Generation failed"}
    assert hf.compact_status(result)["error"] == "Generation failed"


@pytest.mark.parametrize("status", ["nsfw", "canceled", "failed"])
def test_compact_status_terminal_failure_always_has_error(status):
    assert "error" in hf.compact_status({"request_id": "r1", "status": status})


def test_extract_outputs_images_video_and_audio_deduplicated():
    result = {
        "images": [{"url": "https://cdn/a.png"}, {"url": "https://cdn/b.png"}],
        "video": {"url": "https://cdn/v.mp4"},
        "audio": {"url": "https://cdn/s.mp3"},
        "audios": [{"url": "https://cdn/s.mp3"}],
    }
    assert hf.extract_outputs(result) == [
        ("images", "https://cdn/a.png"),
        ("images", "https://cdn/b.png"),
        ("video", "https://cdn/v.mp4"),
        ("audio", "https://cdn/s.mp3"),
    ]


def test_extract_outputs_empty():
    assert hf.extract_outputs({"status": "completed"}) == []


# --- File names -------------------------------------------------------------


def test_output_filename_single_file():
    name = hf.output_filename(
        "kling-video/v2.5-turbo/standard/image-to-video",
        "r1",
        "video",
        "https://cdn/x/video.mp4?sig=1",
        1,
        1,
        date(2026, 10, 30),
    )
    assert name == "2026-10-30_kling-video-v2.5-turbo-standard-image-to-video_r1.mp4"


def test_output_filename_numbers_multiple_files():
    name = hf.output_filename("a/b", "r1", "images", "https://cdn/x.png", 2, 3, date(2026, 1, 5))
    assert name == "2026-01-05_a-b_r1_2.png"


def test_output_filename_fallback_extension():
    name = hf.output_filename("a/b", "r1", "video", "https://cdn/no-extension", 1, 1, date(2026, 1, 5))
    assert name.endswith("_r1.mp4")


# --- Error messages ---------------------------------------------------------


@pytest.mark.parametrize(
    "body, expected",
    [
        ({"detail": "Invalid credentials"}, "Invalid credentials"),
        ({"detail": ": 'image_url' is a required property"}, "'image_url' is a required property"),
        ({"detail": [{"loc": ["body", "resolution"], "msg": "bad value"}]}, "resolution: bad value"),
        ({"detail": {"code": "account_queue_full", "message": "Too many queued"}}, "Too many queued"),
    ],
)
def test_describe_error(body, expected):
    assert hf.describe_error(FakeResponse(400, body)) == expected


def test_describe_error_without_json():
    response = FakeResponse(502)
    response.text = "Bad Gateway"
    assert hf.describe_error(response) == "Bad Gateway"


# --- api_request: retry behavior --------------------------------------------


def test_api_request_success(sleeps):
    session = FakeSession([FakeResponse(200, {"status": "queued"})])
    assert hf.api_request(session, "GET", "/requests/r1/status") == {"status": "queued"}
    assert session.calls[0]["url"] == "https://api.higgsfield.ai/requests/r1/status"
    assert session.calls[0]["timeout"] == hf.HTTP_TIMEOUT
    assert sleeps == []


def test_api_request_retries_server_errors(sleeps):
    session = FakeSession([FakeResponse(503, {"detail": "busy"}), FakeResponse(500), FakeResponse(200, {"ok": True})])
    assert hf.api_request(session, "GET", "/x") == {"ok": True}
    assert len(session.calls) == 3
    assert len(sleeps) == 2


def test_api_request_does_not_retry_client_errors(sleeps):
    session = FakeSession([FakeResponse(400, {"detail": "bad prompt"})])
    with pytest.raises(hf.CliError, match="HTTP 400: bad prompt"):
        hf.api_request(session, "POST", "/x", json_body={})
    assert len(session.calls) == 1
    assert sleeps == []


def test_api_request_respects_retry_after(sleeps):
    session = FakeSession([FakeResponse(429, {"detail": "full"}, {"Retry-After": "30"}), FakeResponse(200, {})])
    hf.api_request(session, "POST", "/x")
    assert sleeps == [30.0]


def test_api_request_gives_up_after_max_attempts(sleeps):
    session = FakeSession([FakeResponse(503, {"detail": "down"})] * hf.MAX_ATTEMPTS)
    with pytest.raises(hf.CliError, match="HTTP 503: down"):
        hf.api_request(session, "GET", "/x")
    assert len(session.calls) == hf.MAX_ATTEMPTS


def test_api_request_retries_network_errors(sleeps):
    session = FakeSession([requests.ConnectionError("gone"), FakeResponse(200, {"ok": True})])
    assert hf.api_request(session, "GET", "/x") == {"ok": True}


def test_api_request_network_error_message_is_short(sleeps):
    session = FakeSession([requests.Timeout("slow")] * hf.MAX_ATTEMPTS)
    with pytest.raises(hf.CliError, match="Network error after 4 attempts: Timeout"):
        hf.api_request(session, "GET", "/x")


def test_api_request_empty_body(sleeps):
    # e.g. cancel: 202 without content
    assert hf.api_request(FakeSession([FakeResponse(202)]), "POST", "/x") == {}


# --- submit and polling -----------------------------------------------------


ESTIMATE_OK = FakeResponse(200, {"usd": "0.250"})
SUBMIT_OK = FakeResponse(200, {"request_id": "r42", "status": "queued"})


def test_submit_estimates_then_submits_and_logs_job(sleeps, models_file, no_max_usd):
    session = FakeSession([ESTIMATE_OK, SUBMIT_OK])
    result = hf.submit_generation(session, generation_args(prompt="a lake"))

    assert session.calls[0]["url"] == "https://api.higgsfield.ai/estimate/test/image"
    call = session.calls[1]
    assert call["url"] == "https://api.higgsfield.ai/test/image"
    assert len(call["headers"]["Idempotency-Key"]) == 36  # UUID format
    assert result["usd"] == 0.25
    job = hf.load_history()["r42"]
    assert job["model"] == "test/image"
    assert job["prompt"] == "a lake"
    assert job["usd"] == 0.25
    assert job["status"] == "queued"
    assert job["payload"]["aspect_ratio"] == "4:3"


def test_submit_blocked_by_max_usd(sleeps, models_file, no_max_usd):
    session = FakeSession([ESTIMATE_OK])
    with pytest.raises(hf.CliError, match="above the limit of \\$0.200"):
        hf.submit_generation(session, generation_args(max_usd=0.2))
    # Only the estimate was sent, nothing was submitted.
    assert len(session.calls) == 1
    assert hf.load_history() == {}


def test_submit_max_usd_from_environment(sleeps, models_file, monkeypatch):
    monkeypatch.setenv("HF_MAX_USD", "0.10")
    with pytest.raises(hf.CliError, match="limit of \\$0.100"):
        hf.submit_generation(FakeSession([ESTIMATE_OK]), generation_args())


def test_submit_option_beats_environment(sleeps, models_file, monkeypatch):
    monkeypatch.setenv("HF_MAX_USD", "0.10")
    result = hf.submit_generation(FakeSession([ESTIMATE_OK, SUBMIT_OK]), generation_args(max_usd=1.0))
    assert result["request_id"] == "r42"


def test_submit_unknown_price_with_limit_is_blocked(sleeps, models_file, no_max_usd):
    session = FakeSession([FakeResponse(200, {"type": "description", "pricing_description": "$0.2/s"})])
    with pytest.raises(hf.CliError, match="Price unknown \\(\\$0.2/s\\)"):
        hf.submit_generation(session, generation_args(max_usd=1.0))


def test_submit_failing_estimate_without_limit_still_submits(sleeps, models_file, no_max_usd):
    session = FakeSession([FakeResponse(404, {"detail": "Not found"}), SUBMIT_OK])
    result = hf.submit_generation(session, generation_args())
    assert result["usd"] is None
    assert hf.load_history()["r42"]["usd"] is None


def test_load_history_converts_old_entries(history_file):
    history_file.write_text(json.dumps({"old": "a/b", "new": {"model": "c/d", "usd": 0.1}}))
    assert hf.load_history() == {"old": {"model": "a/b"}, "new": {"model": "c/d", "usd": 0.1}}


def test_save_outputs_writes_sidecar_and_logs_files(tmp_path, monkeypatch):
    monkeypatch.setattr(hf, "download_file", lambda url, target: target.write_bytes(b"IMG"))
    job = {
        "model": "a/b",
        "prompt": "a lake",
        "payload": {"prompt": "a lake"},
        "usd": 0.004,
        "submitted": "2026-10-30T10:00:00",
    }
    result = {"request_id": "r1", "images": [{"url": "https://cdn/x.png"}]}

    paths = hf.save_outputs(result, job, tmp_path / "out")

    image = tmp_path / "out" / f"{date.today():%Y-%m-%d}_a-b_r1.png"
    assert paths == [str(image)]
    sidecar = json.loads(image.with_suffix(".json").read_text())
    assert sidecar["prompt"] == "a lake"
    assert sidecar["usd"] == 0.004
    assert sidecar["file"] == image.name
    logged = hf.load_history()["r1"]
    assert logged["status"] == "completed"
    assert logged["files"] == [str(image.resolve())]


def test_spend_adds_up_finished_jobs(history_file, no_env_file, capsys):
    today = date.today().isoformat()
    history_file.write_text(
        json.dumps(
            {
                "old": "a/b",
                "r1": {"model": "img", "usd": 0.004, "status": "completed", "submitted": f"{today}T09:00:00"},
                "r2": {"model": "vid", "usd": 0.4, "status": "completed", "submitted": f"{today}T10:00:00"},
                "r3": {"model": "vid", "usd": 0.4, "status": "failed", "submitted": f"{today}T11:00:00"},
                "r4": {"model": "vid", "usd": 0.4, "status": "in_progress", "submitted": f"{today}T12:00:00"},
                "r5": {"model": "img", "usd": 1.0, "status": "completed", "submitted": "2020-01-01T09:00:00"},
            }
        )
    )
    assert hf.main(["spend", "--days", "1"]) == 0
    assert json.loads(capsys.readouterr().out) == {
        "since": today,
        "estimated_usd": 0.404,
        "by_model": {"vid": 0.4, "img": 0.004},
        "jobs": 2,
        "running": 1,
        "not_charged": 1,
        "price_unknown": 0,
        "untracked": 1,
    }
    hf.main(["spend"])
    assert json.loads(capsys.readouterr().out)["estimated_usd"] == 1.404


def test_status_is_recorded_in_job_log(no_env_file, monkeypatch, capsys):
    monkeypatch.setattr(
        hf,
        "make_session",
        lambda: FakeSession(
            [
                FakeResponse(200, {"request_id": "r1", "status": "nsfw"}),
            ]
        ),
    )
    hf.main(["status", "r1"])
    assert hf.load_history()["r1"]["status"] == "nsfw"


def test_poll_until_done(sleeps):
    session = FakeSession(
        [
            FakeResponse(200, {"request_id": "r1", "status": "queued"}),
            FakeResponse(200, {"request_id": "r1", "status": "in_progress"}),
            FakeResponse(200, {"request_id": "r1", "status": "completed"}),
        ]
    )
    assert hf.poll_until_done(session, "r1", timeout=600)["status"] == "completed"
    # The wait time grows: first ~2 s, then ~3 s (each plus up to 0.5 s jitter).
    assert 2.0 <= sleeps[0] <= 2.5
    assert 3.0 <= sleeps[1] <= 3.5


def test_poll_until_done_timeout(sleeps, monkeypatch):
    # A clock that jumps ahead 100 s every time it is read.
    clock = iter(range(0, 10_000, 100))
    monkeypatch.setattr(hf.time, "monotonic", lambda: next(clock))
    session = FakeSession([FakeResponse(200, {"request_id": "r1", "status": "queued"})] * 10)
    with pytest.raises(hf.CliError, match="Timeout after 150 s"):
        hf.poll_until_done(session, "r1", timeout=150)


# --- Upload and cancel ------------------------------------------------------


UPLOAD_ANSWER = {
    "public_url": "https://cdn/input/x.png",
    "upload_url": "https://storage/presigned",
    "upload_headers": {"Content-Type": "image/png", "x-amz-tagging": "retention=temporary"},
}


@pytest.fixture
def fake_put(monkeypatch):
    """Replaces requests.put and records what was uploaded."""
    calls = []

    def put(url, data=None, headers=None, timeout=None):
        calls.append({"url": url, "data": data, "headers": headers})
        return FakeResponse(200)

    monkeypatch.setattr(hf.requests, "put", put)
    return calls


def test_upload_file(tmp_path, fake_put, sleeps):
    image = tmp_path / "photo.png"
    image.write_bytes(b"PNGDATA")
    session = FakeSession([FakeResponse(200, UPLOAD_ANSWER)])

    assert hf.upload_file(session, image) == "https://cdn/input/x.png"
    assert session.calls[0]["json"] == {"content_type": "image/png"}
    assert fake_put[0]["url"] == "https://storage/presigned"
    assert fake_put[0]["data"] == b"PNGDATA"
    # The given headers are sent along, but never our credentials.
    assert fake_put[0]["headers"]["x-amz-tagging"] == "retention=temporary"
    assert "Authorization" not in fake_put[0]["headers"]


def test_upload_rejects_unsupported_type(tmp_path):
    document = tmp_path / "letter.pdf"
    document.write_bytes(b"%PDF")
    with pytest.raises(hf.CliError, match="not supported"):
        hf.upload_file(FakeSession([]), document)


def test_upload_missing_file(tmp_path):
    with pytest.raises(hf.CliError, match="not found"):
        hf.upload_file(FakeSession([]), tmp_path / "does-not-exist.jpg")


def test_resolve_input_url_keeps_urls():
    # An empty FakeSession would fail on every call – none may happen.
    assert hf.resolve_input_url("https://x/a.jpg", FakeSession([])) == "https://x/a.jpg"


def test_resolve_input_url_uploads_local_files(tmp_path, fake_put, sleeps):
    image = tmp_path / "photo.jpg"
    image.write_bytes(b"JPG")
    session = FakeSession([FakeResponse(200, UPLOAD_ANSWER)])
    assert hf.resolve_input_url(str(image), session) == "https://cdn/input/x.png"
    assert session.calls[0]["json"] == {"content_type": "image/jpeg"}


def test_cancel_success(no_env_file, monkeypatch, capsys):
    session = FakeSession([FakeResponse(202)])
    monkeypatch.setattr(hf, "make_session", lambda: session)
    assert hf.main(["cancel", "r1"]) == 0
    assert json.loads(capsys.readouterr().out) == {"request_id": "r1", "status": "canceled"}
    assert session.calls[0]["url"].endswith("/requests/r1/cancel")


def test_cancel_running_job(no_env_file, monkeypatch, capsys):
    monkeypatch.setattr(hf, "make_session", lambda: FakeSession([FakeResponse(400, {"detail": "started"})]))
    assert hf.main(["cancel", "r1"]) == 1
    assert "already running" in json.loads(capsys.readouterr().out)["error"]


# --- Characters -------------------------------------------------------------


def test_find_character_by_name_or_id(characters_file):
    assert hf.find_character("MILA")["id"] == "ref-123"
    assert hf.find_character("ref-123")["name"] == "Mila"
    with pytest.raises(hf.CliError, match="Unknown character"):
        hf.find_character("Otto")


def test_character_payload():
    assert hf.character_payload("Mila", "v2", ["https://x/1.jpg"]) == {
        "name": "Mila",
        "model_version": "v2",
        "input_images": [{"type": "image_url", "image_url": "https://x/1.jpg"}],
    }


def test_character_create_saves_id(tmp_path, monkeypatch, no_env_file, capsys):
    monkeypatch.setattr(hf, "CHARACTERS_FILE", tmp_path / "characters.json")
    session = FakeSession([FakeResponse(200, {"usd": "2.500"}), FakeResponse(200, {"id": "ref-9", "status": "queued"})])
    monkeypatch.setattr(hf, "make_session", lambda: session)

    assert hf.main(["character", "create", "Otto", "https://x/1.jpg", "https://x/2.jpg"]) == 0
    assert json.loads(capsys.readouterr().out) == {"name": "Otto", "id": "ref-9", "status": "queued", "usd": 2.5}
    assert session.calls[0]["url"].endswith("/estimate/soul-id")
    assert session.calls[1]["url"].endswith("/v1/custom-references")
    assert len(session.calls[1]["json"]["input_images"]) == 2
    assert hf.load_characters()["Otto"]["id"] == "ref-9"
    # The training cost ends up in the job log, so 'hf spend' counts it.
    assert hf.load_history()["ref-9"]["usd"] == 2.5
    assert hf.load_history()["ref-9"]["model"] == "soul-id"


def test_character_create_respects_max_usd(tmp_path, monkeypatch, no_env_file, capsys):
    monkeypatch.setattr(hf, "CHARACTERS_FILE", tmp_path / "characters.json")
    session = FakeSession([FakeResponse(200, {"usd": "2.500"})])
    monkeypatch.setattr(hf, "make_session", lambda: session)
    assert hf.main(["character", "create", "Otto", "https://x/1.jpg", "--max-usd", "1"]) == 1
    assert len(session.calls) == 1
    assert hf.load_characters() == {}


def test_character_create_rejects_duplicate_name(characters_file, no_env_file, capsys):
    assert hf.main(["character", "create", "mila", "https://x/1.jpg"]) == 1
    assert "already exists" in json.loads(capsys.readouterr().out)["error"]


def test_character_add_checks_id_and_saves(tmp_path, monkeypatch, no_env_file, capsys):
    monkeypatch.setattr(hf, "CHARACTERS_FILE", tmp_path / "characters.json")
    session = FakeSession([FakeResponse(200, {"id": "ref-7", "status": "completed"})])
    monkeypatch.setattr(hf, "make_session", lambda: session)

    assert hf.main(["character", "add", "Mila", "ref-7"]) == 0
    assert session.calls[0]["url"].endswith("/v1/custom-references/ref-7")
    assert hf.load_characters()["Mila"]["id"] == "ref-7"
    assert json.loads(capsys.readouterr().out)["status"] == "completed"


def test_character_add_unknown_id(tmp_path, monkeypatch, no_env_file, capsys):
    monkeypatch.setattr(hf, "CHARACTERS_FILE", tmp_path / "characters.json")
    monkeypatch.setattr(hf, "make_session", lambda: FakeSession([FakeResponse(404, {"detail": "Not found"})]))
    assert hf.main(["character", "add", "Mila", "wrong"]) == 1
    assert hf.load_characters() == {}


def test_doctor_reports_missing_credentials(no_env_file, monkeypatch, tmp_path, capsys):
    monkeypatch.delenv("HF_API_KEY", raising=False)
    monkeypatch.delenv("HF_API_SECRET", raising=False)
    monkeypatch.setattr(hf, "check_docs", lambda: "ok")
    monkeypatch.setattr(hf, "CATALOG_FILE", tmp_path / "missing.json")
    assert hf.main(["doctor"]) == 1
    output = json.loads(capsys.readouterr().out)
    assert output["error"] == "Problem with: credentials"
    assert "missing" in output["checks"]["catalog"]


@pytest.mark.parametrize("status_code, expected", [(404, "ok"), (401, "wrong credentials (401)")])
def test_check_api(monkeypatch, status_code, expected):
    monkeypatch.setenv("HF_API_KEY", "k")
    monkeypatch.setenv("HF_API_SECRET", "s")
    monkeypatch.setattr(hf.requests, "get", lambda *args, **kwargs: FakeResponse(status_code))
    assert hf.check_api() == expected


def test_check_api_network_error(monkeypatch):
    monkeypatch.setenv("HF_API_KEY", "k")
    monkeypatch.setenv("HF_API_SECRET", "s")

    def fail(*args, **kwargs):
        raise requests.ConnectTimeout("blocked")

    monkeypatch.setattr(hf.requests, "get", fail)
    assert "firewall" in hf.check_api()


def test_character_status_updates_file(characters_file, no_env_file, monkeypatch, capsys):
    monkeypatch.setattr(
        hf,
        "make_session",
        lambda: FakeSession(
            [
                FakeResponse(200, {"id": "ref-123", "status": "in_progress"}),
            ]
        ),
    )
    assert hf.main(["character", "status", "Mila"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "in_progress"
    assert hf.load_characters()["Mila"]["status"] == "in_progress"


# --- main: output and exit codes --------------------------------------------


@pytest.fixture
def no_max_usd(monkeypatch):
    """Makes sure an HF_MAX_USD from the real environment does not affect the test."""
    monkeypatch.delenv("HF_MAX_USD", raising=False)


@pytest.fixture
def no_env_file(tmp_path, monkeypatch):
    """Prevents main() from loading the project's real .env."""
    monkeypatch.setattr(hf, "ENV_FILE", tmp_path / "no.env")


@pytest.fixture
def catalog_files(tmp_path, monkeypatch, models_file):
    """A small catalog plus prices in the temporary folder."""
    catalog_path = tmp_path / "catalog.json"
    catalog_path.write_text(
        json.dumps(
            {
                "updated": "2026-10-30",
                "models": {
                    "test/image": {
                        "title": "Test Image",
                        "output": "image",
                        "operations": ["text2image"],
                        "listed": True,
                        "documented": True,
                    },
                    "test/video": {
                        "title": "Test Video",
                        "output": "video",
                        "operations": ["image2video"],
                        "listed": True,
                        "documented": False,
                    },
                    "docs/only": {"title": None, "output": None, "operations": [], "listed": False, "documented": True},
                },
            }
        )
    )
    prices_path = tmp_path / "prices.json"
    prices_path.write_text(
        json.dumps(
            {
                "test/image": {"usd": "0.500"},
                "test/video": {"usd": "0.100"},
            }
        )
    )
    monkeypatch.setattr(hf, "CATALOG_FILE", catalog_path)
    monkeypatch.setattr(hf, "PRICES_FILE", prices_path)


def test_main_prints_one_json_line(catalog_files, no_env_file, capsys):
    # capsys captures everything printed to stdout/stderr.
    assert hf.main(["models"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 1
    assert [row["id"] for row in json.loads(lines[0])["models"]] == ["test/image", "test/video"]


def test_models_sorted_by_price_with_filter(catalog_files, no_env_file, capsys):
    hf.main(["models", "--sort", "price"])
    rows = json.loads(capsys.readouterr().out)["models"]
    assert [row["usd"] for row in rows] == ["0.100", "0.500"]
    assert rows[0]["documented"] is False

    hf.main(["models", "--output", "image"])
    assert json.loads(capsys.readouterr().out)["count"] == 1


def test_models_all_includes_docs_only(catalog_files, no_env_file, capsys):
    hf.main(["models", "--all"])
    assert json.loads(capsys.readouterr().out)["count"] == 3


def test_models_without_catalog_gives_hint(no_env_file, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(hf, "CATALOG_FILE", tmp_path / "missing.json")
    assert hf.main(["models"]) == 1
    assert "catalog update" in json.loads(capsys.readouterr().out)["error"]


def test_estimate_with_learning_adds_missing_fields(sleeps):
    session = FakeSession(
        [
            FakeResponse(400, {"detail": ": 'video_urls' is a required property"}),
            FakeResponse(200, {"usd": "1.250"}),
        ]
    )
    entry = hf.estimate_with_learning(session, "m", {"params": {}}, {"prompt": "test"})
    assert entry["usd"] == "1.250"
    assert entry["payload"] == {"prompt": "test", "video_urls": ["https://example.com/input.mp4"]}
    assert session.calls[1]["json"]["video_urls"] == ["https://example.com/input.mp4"]


def test_estimate_with_learning_records_other_errors(sleeps):
    session = FakeSession([FakeResponse(404, {"detail": "Not found"})])
    entry = hf.estimate_with_learning(session, "m", {}, {"prompt": "test"})
    assert entry["usd"] is None
    assert entry["error"] == "HTTP 404: Not found"


def test_estimate_with_learning_keeps_pricing_description(sleeps):
    session = FakeSession([FakeResponse(200, {"type": "description", "pricing_description": "$0.2/s"})])
    entry = hf.estimate_with_learning(session, "m", {}, {"prompt": "test"})
    assert entry["usd"] is None
    assert entry["pricing"] == "$0.2/s"


def test_main_error_has_exit_code_and_no_traceback(no_env_file, monkeypatch, capsys):
    monkeypatch.delenv("HF_API_KEY", raising=False)
    monkeypatch.delenv("HF_API_SECRET", raising=False)
    assert hf.main(["status", "r1"]) == 2
    captured = capsys.readouterr()
    assert json.loads(captured.out) == {"error": "HF_API_KEY and HF_API_SECRET must be set."}
    assert "Traceback" not in captured.err


def test_status_urls(no_env_file, monkeypatch, capsys):
    monkeypatch.setattr(
        hf,
        "make_session",
        lambda: FakeSession(
            [
                FakeResponse(
                    200, {"request_id": "r1", "status": "completed", "images": [{"url": "https://cdn/a.png"}]}
                ),
            ]
        ),
    )
    assert hf.main(["status", "r1", "--urls"]) == 0
    assert json.loads(capsys.readouterr().out) == {
        "request_id": "r1",
        "status": "completed",
        "urls": ["https://cdn/a.png"],
    }


def test_main_failed_job_returns_exit_code_1(no_env_file, monkeypatch, capsys):
    monkeypatch.setattr(
        hf,
        "make_session",
        lambda: FakeSession(
            [
                FakeResponse(200, {"request_id": "r1", "status": "nsfw"}),
            ]
        ),
    )
    assert hf.main(["status", "r1"]) == 1
    assert json.loads(capsys.readouterr().out)["status"] == "nsfw"


# --- Gallery ----------------------------------------------------------------


def test_gallery_items_skip_missing_files_and_sort_newest_first(history_file, tmp_path):
    old_image = tmp_path / "old.png"
    old_image.write_bytes(b"x")
    new_image = tmp_path / "new.png"
    new_image.write_bytes(b"x")
    history_file.write_text(
        json.dumps(
            {
                "r1": {"model": "a/b", "submitted": "2026-10-29T10:00:00", "files": [str(old_image)]},
                "r2": {"model": "a/b", "submitted": "2026-10-30T10:00:00", "files": [str(new_image), "/gone.png"]},
                "r3": {"model": "a/b", "files": ["/gone-too.png"]},
            }
        )
    )
    items, missing = hf.gallery_items()
    assert [entry["request_id"] for entry in items] == ["r2", "r1"]
    assert items[0]["files"] == [str(new_image)]
    assert items[0]["date"] == "2026-10-30 10:00"
    assert missing == 2


def test_scan_adds_old_result_files(history_file, tmp_path):
    folder = tmp_path / "outputs" / "character"
    folder.mkdir(parents=True)
    image = folder / "2026-10-30_a-b_01a18e46-23d4-7f70-b7d6-5e34755ccbc4_1.png"
    image.write_bytes(b"x")
    (folder / "2026-10-30_a-b_01a18e46-23d4-7f70-b7d6-5e34755ccbc4_1.json").write_text("{}")
    (folder / "unrelated.png").write_bytes(b"x")
    history_file.write_text(json.dumps({"01a18e46-23d4-7f70-b7d6-5e34755ccbc4": "a/b"}))

    assert hf.scan_for_results(tmp_path / "outputs") == 1
    # A second scan finds nothing new.
    assert hf.scan_for_results(tmp_path / "outputs") == 0
    assert hf.load_history()["01a18e46-23d4-7f70-b7d6-5e34755ccbc4"]["files"] == [str(image.resolve())]


def test_gallery_command_writes_and_opens_page(history_file, no_env_file, tmp_path, capsys, browser_calls):
    assert hf.main(["gallery"]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["jobs"] == 0
    assert (tmp_path / "gallery.html").exists()
    assert browser_calls == [(tmp_path / "gallery.html").as_uri()]


def test_gallery_no_open(history_file, no_env_file, capsys, browser_calls):
    hf.main(["gallery", "--no-open"])
    assert browser_calls == []


def test_run_rebuilds_gallery(
    sleeps, models_file, no_env_file, no_max_usd, tmp_path, monkeypatch, capsys, browser_calls
):
    monkeypatch.setattr(hf, "download_file", lambda url, target: target.write_bytes(b"IMG"))
    monkeypatch.setattr(
        hf,
        "make_session",
        lambda: FakeSession(
            [
                ESTIMATE_OK,
                SUBMIT_OK,
                FakeResponse(
                    200, {"request_id": "r42", "status": "completed", "images": [{"url": "https://cdn/x.png"}]}
                ),
            ]
        ),
    )
    assert hf.main(["run", "test/image", "--prompt", "a lake", "--out", str(tmp_path / "out"), "--open"]) == 0
    assert json.loads(capsys.readouterr().out)["usd"] == 0.25
    assert "a lake" in (tmp_path / "gallery.html").read_text()
    assert len(browser_calls) == 1


# --- Character views and run_many -------------------------------------------


def many_session(*poll_results, usd="0.040"):
    """Responses for run_many: one estimate per job, then one submit per job, then one poll per job."""
    estimates = [FakeResponse(200, {"usd": usd}) for _ in poll_results]
    submits = [
        FakeResponse(200, {"request_id": f"v{number}", "status": "queued"})
        for number, _ in enumerate(poll_results, start=1)
    ]
    return FakeSession(estimates + submits + [FakeResponse(200, body) for body in poll_results])


def test_character_views_creates_selected_views(sleeps, no_env_file, no_max_usd, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(hf, "MODELS_FILE", tmp_path / "models.json")
    (tmp_path / "models.json").write_text("{}")
    monkeypatch.setattr(hf, "download_file", lambda url, target: target.write_bytes(b"IMG"))
    session = many_session(
        {"request_id": "v1", "status": "completed", "images": [{"url": "https://cdn/1.png"}]},
        {"request_id": "v2", "status": "nsfw"},
    )
    monkeypatch.setattr(hf, "make_session", lambda: session)

    code = hf.main(
        [
            "character",
            "views",
            "https://x/base.png",
            "--views",
            "profile-left",
            "smiling",
            "--details",
            "green eyes",
            "--out",
            str(tmp_path / "out"),
        ]
    )

    output = json.loads(capsys.readouterr().out)
    assert code == 1
    assert output["error"] == "1 of 2 jobs failed."
    assert output["usd"] == 0.08
    assert output["views"][0]["view"] == "profile-left"
    assert len(output["views"][0]["files"]) == 1
    assert output["views"][1]["status"] == "nsfw"
    submitted = [call["json"] for call in session.calls if call["url"].endswith("/alibaba/qwen-image-3/edit")]
    assert submitted[0]["image_urls"] == ["https://x/base.png"]
    assert "LEFT edge" in submitted[0]["prompt"] and "green eyes" in submitted[0]["prompt"]
    assert submitted[0]["aspect_ratio"] == "3:4"


def test_character_views_checks_total_budget(sleeps, no_env_file, no_max_usd, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(hf, "MODELS_FILE", tmp_path / "models.json")
    (tmp_path / "models.json").write_text("{}")
    session = FakeSession([FakeResponse(200, {"usd": "0.040"})] * 5)
    monkeypatch.setattr(hf, "make_session", lambda: session)
    # 5 views x $0.04 = $0.20 > $0.10
    assert hf.main(["character", "views", "https://x/base.png", "--max-usd", "0.1"]) == 1
    assert "$0.200" in json.loads(capsys.readouterr().out)["error"]
    # Only the five free estimates, nothing was submitted.
    assert all("/estimate/" in call["url"] for call in session.calls)


# --- Chaining clips (ffmpeg) ------------------------------------------------


@pytest.fixture
def fake_ffmpeg(monkeypatch):
    """Replaces ffmpeg: records the arguments and creates the output file (the last argument)."""
    calls = []

    def run(arguments):
        calls.append(arguments)
        Path(arguments[-1]).write_bytes(b"FAKE")

    monkeypatch.setattr(hf, "run_ffmpeg", run)
    return calls


def test_find_video_by_path_or_request_id(history_file, tmp_path):
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"x")
    history_file.write_text(json.dumps({"r1": {"files": [str(tmp_path / "x.png"), str(clip)]}, "r2": {"files": []}}))
    assert hf.find_video(str(clip)) == clip
    assert hf.find_video("r1") == clip
    with pytest.raises(hf.CliError, match="hf download r2"):
        hf.find_video("r2")
    with pytest.raises(hf.CliError, match="Video not found"):
        hf.find_video("nothing")


def test_build_payload_from_last_frame(models_file, fake_ffmpeg, tmp_path, monkeypatch):
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"x")
    uploaded = []
    monkeypatch.setattr(
        hf, "resolve_input_url", lambda value, session=None: uploaded.append(value) or "https://cdn/f.png"
    )

    payload = hf.build_payload(generation_args(from_last_frame=str(clip)))

    assert payload["image_url"] == "https://cdn/f.png"
    assert uploaded == [str(tmp_path / "clip_last.png")]
    assert fake_ffmpeg[0][:3] == ["-sseof", "-0.5", "-i"]


def test_build_payload_rejects_image_url_and_last_frame(models_file):
    with pytest.raises(hf.CliError, match="either --image-url or --from-last-frame"):
        hf.build_payload(generation_args(image_url="https://x/a.jpg", from_last_frame="clip.mp4"))


def test_concat_list_quotes_paths():
    assert hf.concat_list([Path("/a/b.mp4"), Path("/a/it's.mp4")]) == "file '/a/b.mp4'\nfile '/a/it'\\''s.mp4'\n"


def test_concat_joins_clips_and_logs_local_job(fake_ffmpeg, no_env_file, tmp_path, capsys):
    clips = []
    for name in ("a.mp4", "b.mp4"):
        clips.append(tmp_path / name)
        clips[-1].write_bytes(b"x")
    target = tmp_path / "scene.mp4"

    assert hf.main(["concat", str(clips[0]), str(clips[1]), "--out-file", str(target)]) == 0

    assert json.loads(capsys.readouterr().out) == {"file": str(target), "clips": 2}
    assert fake_ffmpeg[0][:2] == ["-f", "concat"]
    (job,) = hf.load_history().values()
    assert job["kind"] == "local"
    assert job["files"] == [str(target.resolve())]


def test_spend_ignores_local_jobs(history_file, no_env_file, capsys):
    history_file.write_text(
        json.dumps({"concat-1": {"kind": "local", "submitted": "2026-10-30T10:00:00", "status": "completed"}})
    )
    hf.main(["spend"])
    output = json.loads(capsys.readouterr().out)
    assert output["jobs"] == 0 and output["price_unknown"] == 0


def test_concat_needs_two_clips(no_env_file, tmp_path, capsys):
    clip = tmp_path / "a.mp4"
    clip.write_bytes(b"x")
    assert hf.main(["concat", str(clip)]) == 1


# --- Batch ------------------------------------------------------------------


def write_batch(tmp_path, *lines) -> Path:
    path = tmp_path / "jobs.jsonl"
    path.write_text("\n".join(lines) + "\n")
    return path


def test_read_batch_file(tmp_path, models_file):
    path = write_batch(
        tmp_path,
        "# a comment",
        "",
        '{"prompt": "a lake", "params": {"duration": 5, "aspect_ratio": "16:9", "seed": "7"}}',
        '{"model": "other/model", "prompt": "a cabin"}',
    )
    jobs = hf.read_batch_file(path, "test/image")
    assert [job.model_id for job in jobs] == ["test/image", "other/model"]
    payload = hf.build_payload(jobs[0])
    # Types survive the round trip: number stays number, text stays text (even "7").
    assert payload["duration"] == 5
    assert payload["aspect_ratio"] == "16:9"
    assert payload["seed"] == "7"


@pytest.mark.parametrize(
    "line, message",
    [
        ('{"prompt": "x", "colour": "red"}', "unknown field"),
        ('{"params": {}}', "'prompt' and 'model'"),
        ("not json", "not valid JSON"),
        ('["a list"]', "JSON object"),
    ],
)
def test_read_batch_file_errors(tmp_path, line, message):
    with pytest.raises(hf.CliError, match=message):
        hf.read_batch_file(write_batch(tmp_path, line), "test/image")


def test_batch_dry_run_submits_nothing(no_env_file, models_file, tmp_path, monkeypatch, capsys):
    session = FakeSession([FakeResponse(200, {"usd": "0.004"}), FakeResponse(200, {"usd": "0.400"})])
    monkeypatch.setattr(hf, "make_session", lambda: session)
    path = write_batch(tmp_path, '{"prompt": "a"}', '{"prompt": "b"}')

    assert hf.main(["batch", str(path), "--model", "test/image", "--dry-run"]) == 0

    output = json.loads(capsys.readouterr().out)
    assert output["estimated_usd"] == 0.404
    assert [job["usd"] for job in output["jobs"]] == [0.004, 0.4]
    assert len(session.calls) == 2


def test_batch_total_limit(no_env_file, no_max_usd, models_file, tmp_path, monkeypatch, capsys):
    session = FakeSession([FakeResponse(200, {"usd": "0.300"})] * 2)
    monkeypatch.setattr(hf, "make_session", lambda: session)
    path = write_batch(tmp_path, '{"prompt": "a"}', '{"prompt": "b"}')
    # Each job is below $0.50, but together they cost $0.60.
    assert hf.main(["batch", str(path), "--model", "test/image", "--max-usd", "0.5"]) == 1
    assert "$0.600" in json.loads(capsys.readouterr().out)["error"]


def test_batch_per_job_limit_from_environment(no_env_file, models_file, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HF_MAX_USD", "0.10")
    monkeypatch.setattr(hf, "make_session", lambda: FakeSession([FakeResponse(200, {"usd": "0.300"})]))
    path = write_batch(tmp_path, '{"prompt": "a"}')
    assert hf.main(["batch", str(path), "--model", "test/image", "--max-usd", "5"]) == 1
    assert "limit of $0.100" in json.loads(capsys.readouterr().out)["error"]


def test_batch_runs_all_jobs(sleeps, no_env_file, no_max_usd, models_file, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(hf, "download_file", lambda url, target: target.write_bytes(b"IMG"))
    monkeypatch.setattr(
        hf,
        "make_session",
        lambda: many_session(
            {"request_id": "v1", "status": "completed", "images": [{"url": "https://cdn/1.png"}]},
            {"request_id": "v2", "status": "completed", "images": [{"url": "https://cdn/2.png"}]},
            usd="0.004",
        ),
    )
    path = write_batch(tmp_path, '{"prompt": "a"}', '{"prompt": "b"}')

    assert hf.main(["batch", str(path), "--model", "test/image", "--out", str(tmp_path / "out")]) == 0

    output = json.loads(capsys.readouterr().out)
    assert output["usd"] == 0.008
    assert [len(job["files"]) for job in output["jobs"]] == [1, 1]
    assert hf.load_history()["v2"]["prompt"] == "b"


def test_example_batch_file_is_valid():
    jobs = hf.read_batch_file(hf.PROJECT_DIR / "examples" / "batch.jsonl", None)
    assert len(jobs) == 3


# --- Shared helpers for server and client mode ------------------------------


def test_api_request_error_carries_status_code(sleeps):
    session = FakeSession([FakeResponse(403, {"detail": "nope"})])
    with pytest.raises(hf.CliError, match="HTTP 403: nope") as info:
        hf.api_request(session, "POST", "/x")
    assert info.value.status_code == 403


def test_summarize_spend_matches_cmd_spend():
    today = date.today().isoformat()
    jobs = [
        {"model": "a/b"},
        {"model": "img", "usd": 0.004, "status": "completed", "submitted": f"{today}T09:00:00"},
        {"model": "vid", "usd": 0.4, "status": "completed", "submitted": f"{today}T10:00:00"},
        {"model": "vid", "usd": 0.4, "status": "failed", "submitted": f"{today}T11:00:00"},
        {"model": "vid", "usd": 0.4, "status": "in_progress", "submitted": f"{today}T12:00:00"},
        {"model": "img", "usd": 1.0, "status": "completed", "submitted": "2020-01-01T09:00:00"},
    ]
    assert hf.summarize_spend(jobs, today) == {
        "estimated_usd": 0.404,
        "by_model": {"vid": 0.4, "img": 0.004},
        "jobs": 2,
        "running": 1,
        "not_charged": 1,
        "price_unknown": 0,
        "untracked": 1,
    }


def test_download_outputs_does_not_touch_job_log(tmp_path, monkeypatch):
    monkeypatch.setattr(hf, "download_file", lambda url, target: target.write_bytes(b"IMG"))
    job = {"model": "a/b", "prompt": "a lake", "payload": {"prompt": "a lake"}, "usd": 0.004}
    result = {"request_id": "r1", "images": [{"url": "https://cdn/x.png"}]}

    paths = hf.download_outputs(result, job, tmp_path / "out")

    image = tmp_path / "out" / f"{date.today():%Y-%m-%d}_a-b_r1.png"
    assert paths == [image]
    assert image.with_suffix(".json").exists()
    assert hf.load_history() == {}
