"""Tests für hf.py – ohne echte API-Aufrufe.

Statt HTTP zu senden, bekommen die Funktionen eine FakeSession mit
vorbereiteten Antworten. time.sleep wird ersetzt, damit nichts wartet.
"""

import argparse
import json
from datetime import date

import pytest
import requests

import hf


# --- Hilfsklassen -----------------------------------------------------------


class FakeResponse:
    """Ahmt die Teile von requests.Response nach, die hf.py benutzt."""

    def __init__(self, status_code: int, body=None, headers: dict | None = None):
        self.status_code = status_code
        self._body = body
        self.headers = headers or {}
        self.text = json.dumps(body) if body is not None else ""
        self.content = self.text.encode()
        self.reason = "Fake"

    def json(self):
        if self._body is None:
            raise ValueError("kein JSON")
        return self._body


class FakeSession:
    """Gibt nacheinander die vorbereiteten Antworten zurück und merkt sich jeden Aufruf.

    Ein Eintrag kann auch eine Exception sein – dann wird sie geworfen.
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
# Eine Fixture ist eine Funktion, deren Rückgabewert pytest automatisch an Tests
# übergibt, die einen Parameter mit ihrem Namen haben.


@pytest.fixture
def sleeps(monkeypatch):
    """Ersetzt time.sleep: nichts wartet, aber die Wartezeiten werden notiert."""
    recorded = []
    monkeypatch.setattr(hf.time, "sleep", lambda seconds: recorded.append(seconds))
    return recorded


@pytest.fixture
def models_file(tmp_path, monkeypatch):
    """Eine kleine models.json im temporären Ordner statt der echten."""
    path = tmp_path / "models.json"
    path.write_text(json.dumps({
        "test/image": {"type": "text-to-image", "defaults": {"aspect_ratio": "4:3", "resolution": "720p"}},
    }))
    monkeypatch.setattr(hf, "MODELS_FILE", path)
    return path


# --- Parameter --------------------------------------------------------------


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
    # pytest.raises prüft, dass der Block genau diesen Fehler wirft.
    with pytest.raises(hf.CliError, match="key=value"):
        hf.parse_param("duration")


def test_build_payload_order(models_file):
    args = argparse.Namespace(
        model_id="test/image", prompt="ein See", image_url="https://x/a.jpg",
        param=["resolution=1080p", "seed=7"],
    )
    assert hf.build_payload(args) == {
        "aspect_ratio": "4:3",          # aus models.json
        "resolution": "1080p",          # --param überschreibt den Default
        "prompt": "ein See",
        "image_url": "https://x/a.jpg",
        "seed": 7,
    }


def test_build_payload_unknown_model_has_no_defaults(models_file):
    args = argparse.Namespace(model_id="other/model", prompt="p", image_url=None, param=[])
    assert hf.build_payload(args) == {"prompt": "p"}


# --- Konfiguration ----------------------------------------------------------


def test_load_dotenv(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text(
        "# Kommentar\n"
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
    # Bereits gesetzte Variablen haben Vorrang vor der Datei.
    assert hf.os.environ["HF_OTHER"] == "from-shell"


def test_load_dotenv_missing_file_is_fine(tmp_path):
    hf.load_dotenv(tmp_path / "gibt-es-nicht")


def test_load_credentials_missing(monkeypatch):
    monkeypatch.delenv("HF_API_KEY", raising=False)
    monkeypatch.setenv("HF_API_SECRET", "s")
    with pytest.raises(hf.CliError) as info:
        hf.load_credentials()
    assert info.value.exit_code == 2


# --- Statusauswertung -------------------------------------------------------


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


# --- Dateinamen -------------------------------------------------------------


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


# --- Fehlermeldungen --------------------------------------------------------


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


# --- api_request: Retry-Verhalten -------------------------------------------


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
    session = FakeSession([requests.ConnectionError("weg"), FakeResponse(200, {"ok": True})])
    assert hf.api_request(session, "GET", "/x") == {"ok": True}


def test_api_request_network_error_message_is_short(sleeps):
    session = FakeSession([requests.Timeout("langsam")] * hf.MAX_ATTEMPTS)
    with pytest.raises(hf.CliError, match="Netzwerkfehler nach 4 Versuchen: Timeout"):
        hf.api_request(session, "GET", "/x")


def test_api_request_empty_body(sleeps):
    # z. B. cancel: 202 ohne Inhalt
    assert hf.api_request(FakeSession([FakeResponse(202)]), "POST", "/x") == {}


# --- submit und Polling -----------------------------------------------------


def test_submit_sends_idempotency_key_and_remembers_model(sleeps, models_file, tmp_path, monkeypatch):
    monkeypatch.setattr(hf, "HISTORY_FILE", tmp_path / "history.json")
    session = FakeSession([FakeResponse(200, {"request_id": "r42", "status": "queued"})])
    args = argparse.Namespace(model_id="test/image", prompt="p", image_url=None, param=[])

    hf.submit_generation(session, args)

    call = session.calls[0]
    assert call["url"] == "https://api.higgsfield.ai/test/image"
    assert len(call["headers"]["Idempotency-Key"]) == 36  # UUID-Format
    assert hf.load_history() == {"r42": "test/image"}


def test_poll_until_done(sleeps):
    session = FakeSession([
        FakeResponse(200, {"request_id": "r1", "status": "queued"}),
        FakeResponse(200, {"request_id": "r1", "status": "in_progress"}),
        FakeResponse(200, {"request_id": "r1", "status": "completed"}),
    ])
    assert hf.poll_until_done(session, "r1", timeout=600)["status"] == "completed"
    # Wartezeit wächst: erst ~2 s, dann ~3 s (jeweils plus bis zu 0,5 s Jitter).
    assert 2.0 <= sleeps[0] <= 2.5
    assert 3.0 <= sleeps[1] <= 3.5


def test_poll_until_done_timeout(sleeps, monkeypatch):
    # Eine Uhr, die bei jedem Ablesen 100 s weiterspringt.
    clock = iter(range(0, 10_000, 100))
    monkeypatch.setattr(hf.time, "monotonic", lambda: next(clock))
    session = FakeSession([FakeResponse(200, {"request_id": "r1", "status": "queued"})] * 10)
    with pytest.raises(hf.CliError, match="Timeout nach 150 s"):
        hf.poll_until_done(session, "r1", timeout=150)


# --- main: Ausgabe und Exit-Codes -------------------------------------------


@pytest.fixture
def no_env_file(tmp_path, monkeypatch):
    """Verhindert, dass main() die echte .env des Projekts lädt."""
    monkeypatch.setattr(hf, "ENV_FILE", tmp_path / "no.env")


@pytest.fixture
def catalog_files(tmp_path, monkeypatch, models_file):
    """Ein kleiner Katalog plus Preise im temporären Ordner."""
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
    # capsys fängt alles ab, was auf stdout/stderr ausgegeben wird.
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
    assert json.loads(captured.out) == {"error": "HF_API_KEY und HF_API_SECRET müssen gesetzt sein."}
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
