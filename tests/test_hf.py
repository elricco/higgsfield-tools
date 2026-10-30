"""Tests for hf.py – without real API calls.

Instead of sending HTTP, the functions get a FakeSession with
prepared responses. time.sleep is replaced so that nothing waits.
"""

import argparse
import json
from datetime import date

import pytest
import requests

import hf


# --- Helper classes ---------------------------------------------------------


class FakeResponse:
    """Imitates the parts of requests.Response that hf.py uses."""

    def __init__(self, status_code: int, body=None, headers: dict | None = None):
        self.status_code = status_code
        self._body = body
        self.headers = headers or {}
        self.text = json.dumps(body) if body is not None else ""
        self.content = self.text.encode()
        self.reason = "Fake"

    def json(self):
        if self._body is None:
            raise ValueError("no JSON")
        return self._body


class FakeSession:
    """Returns the prepared responses one after another and records every call.

    An entry can also be an exception – then it is raised.
    """

    def __init__(self, responses: list):
        self.responses = list(responses)
        self.calls = []

    def request(self, method, url, json=None, headers=None, timeout=None):
        self.calls.append({"method": method, "url": url, "json": json, "headers": headers, "timeout": timeout})
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


# --- Fixtures ---------------------------------------------------------------
# A fixture is a function whose return value pytest automatically passes to
# tests that have a parameter with its name.


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
    path.write_text(json.dumps({
        "test/image": {"type": "text-to-image", "defaults": {"aspect_ratio": "4:3", "resolution": "720p"}},
    }))
    monkeypatch.setattr(hf, "MODELS_FILE", path)
    return path


# --- Parameters -------------------------------------------------------------


@pytest.mark.parametrize("text, expected", [
    ("duration=5", ("duration", 5)),
    ("cfg_scale=0.5", ("cfg_scale", 0.5)),
    ("enhance_prompt=true", ("enhance_prompt", True)),
    ("aspect_ratio=16:9", ("aspect_ratio", "16:9")),
    ("style_id=abc=def", ("style_id", "abc=def")),
    ("prompt=", ("prompt", "")),
])
def test_parse_param(text, expected):
    assert hf.parse_param(text) == expected


def test_parse_param_without_equals_sign():
    # pytest.raises checks that the block raises exactly this error.
    with pytest.raises(hf.CliError, match="key=value"):
        hf.parse_param("duration")


def generation_args(**overrides) -> argparse.Namespace:
    """Arguments as from 'submit'/'run'; individual fields can be overridden."""
    values = {"model_id": "test/image", "prompt": "p", "image_url": None,
              "image_urls": None, "character": None, "param": []}
    values.update(overrides)
    return argparse.Namespace(**values)


def test_build_payload_order(models_file):
    args = generation_args(prompt="a lake", image_url="https://x/a.jpg",
                           param=["resolution=1080p", "seed=7"])
    assert hf.build_payload(args) == {
        "aspect_ratio": "4:3",          # from models.json
        "resolution": "1080p",          # --param overrides the default
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
        "# comment\n"
        "\n"
        "HF_API_KEY=key-from-file\n"
        'export HF_API_SECRET="secret=with=equals"\n'
        "HF_OTHER=from-file\n"
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
    name = hf.output_filename("kling-video/v2.5-turbo/standard/image-to-video", "r1", "video",
                              "https://cdn/x/video.mp4?sig=1", 1, 1, date(2026, 10, 30))
    assert name == "2026-10-30_kling-video-v2.5-turbo-standard-image-to-video_r1.mp4"


def test_output_filename_numbers_multiple_files():
    name = hf.output_filename("a/b", "r1", "images", "https://cdn/x.png", 2, 3, date(2026, 1, 5))
    assert name == "2026-01-05_a-b_r1_2.png"


def test_output_filename_fallback_extension():
    name = hf.output_filename("a/b", "r1", "video", "https://cdn/no-extension", 1, 1, date(2026, 1, 5))
    assert name.endswith("_r1.mp4")


# --- Error messages ---------------------------------------------------------


@pytest.mark.parametrize("body, expected", [
    ({"detail": "Invalid credentials"}, "Invalid credentials"),
    ({"detail": ": 'image_url' is a required property"}, "'image_url' is a required property"),
    ({"detail": [{"loc": ["body", "resolution"], "msg": "bad value"}]}, "resolution: bad value"),
    ({"detail": {"code": "account_queue_full", "message": "Too many queued"}}, "Too many queued"),
])
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


def test_submit_sends_idempotency_key_and_remembers_model(sleeps, models_file, tmp_path, monkeypatch):
    monkeypatch.setattr(hf, "HISTORY_FILE", tmp_path / "history.json")
    session = FakeSession([FakeResponse(200, {"request_id": "r42", "status": "queued"})])
    hf.submit_generation(session, generation_args())

    call = session.calls[0]
    assert call["url"] == "https://api.higgsfield.ai/test/image"
    assert len(call["headers"]["Idempotency-Key"]) == 36  # UUID format
    assert hf.load_history() == {"r42": "test/image"}


def test_poll_until_done(sleeps):
    session = FakeSession([
        FakeResponse(200, {"request_id": "r1", "status": "queued"}),
        FakeResponse(200, {"request_id": "r1", "status": "in_progress"}),
        FakeResponse(200, {"request_id": "r1", "status": "completed"}),
    ])
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
    session = FakeSession([FakeResponse(200, {"id": "ref-9", "status": "queued"})])
    monkeypatch.setattr(hf, "make_session", lambda: session)

    assert hf.main(["character", "create", "Otto", "https://x/1.jpg", "https://x/2.jpg"]) == 0
    assert json.loads(capsys.readouterr().out) == {"name": "Otto", "id": "ref-9", "status": "queued"}
    assert session.calls[0]["url"].endswith("/v1/custom-references")
    assert len(session.calls[0]["json"]["input_images"]) == 2
    assert hf.load_characters()["Otto"]["id"] == "ref-9"


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
    monkeypatch.setattr(hf, "make_session", lambda: FakeSession([
        FakeResponse(200, {"id": "ref-123", "status": "in_progress"}),
    ]))
    assert hf.main(["character", "status", "Mila"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "in_progress"
    assert hf.load_characters()["Mila"]["status"] == "in_progress"


# --- main: output and exit codes --------------------------------------------


@pytest.fixture
def no_env_file(tmp_path, monkeypatch):
    """Prevents main() from loading the project's real .env."""
    monkeypatch.setattr(hf, "ENV_FILE", tmp_path / "no.env")


@pytest.fixture
def catalog_files(tmp_path, monkeypatch, models_file):
    """A small catalog plus prices in the temporary folder."""
    catalog_path = tmp_path / "catalog.json"
    catalog_path.write_text(json.dumps({"updated": "2026-10-30", "models": {
        "test/image": {"title": "Test Image", "output": "image", "operations": ["text2image"],
                       "listed": True, "documented": True},
        "test/video": {"title": "Test Video", "output": "video", "operations": ["image2video"],
                       "listed": True, "documented": False},
        "docs/only": {"title": None, "output": None, "operations": [], "listed": False, "documented": True},
    }}))
    prices_path = tmp_path / "prices.json"
    prices_path.write_text(json.dumps({
        "test/image": {"usd": "0.500"},
        "test/video": {"usd": "0.100"},
    }))
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
    session = FakeSession([
        FakeResponse(400, {"detail": ": 'video_urls' is a required property"}),
        FakeResponse(200, {"usd": "1.250"}),
    ])
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
    monkeypatch.setattr(hf, "make_session", lambda: FakeSession([
        FakeResponse(200, {"request_id": "r1", "status": "completed", "images": [{"url": "https://cdn/a.png"}]}),
    ]))
    assert hf.main(["status", "r1", "--urls"]) == 0
    assert json.loads(capsys.readouterr().out) == {
        "request_id": "r1", "status": "completed", "urls": ["https://cdn/a.png"],
    }


def test_main_failed_job_returns_exit_code_1(no_env_file, monkeypatch, capsys):
    monkeypatch.setattr(hf, "make_session", lambda: FakeSession([
        FakeResponse(200, {"request_id": "r1", "status": "nsfw"}),
    ]))
    assert hf.main(["status", "r1"]) == 1
    assert json.loads(capsys.readouterr().out)["status"] == "nsfw"
