"""Client mode: the CLI talks to a real hf server (in this process); only Higgsfield is fake."""

import json
import threading
from pathlib import Path

import pytest
from fakes import FakeResponse, FakeSession

import hf
import server

TOKEN = "client-test-token"
IMAGE_RESULT = {"request_id": "r1", "status": "completed", "images": [{"url": "https://cdn/x.png"}]}


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """No real .env, no real job log or characters file, no sleeping, no CDN downloads."""
    monkeypatch.setattr(hf, "ENV_FILE", tmp_path / "no.env")
    monkeypatch.setattr(hf, "HISTORY_FILE", tmp_path / "history.json")
    monkeypatch.setattr(hf, "CHARACTERS_FILE", tmp_path / "characters.json")
    monkeypatch.setattr(hf, "GALLERY_FILE", tmp_path / "gallery.html")
    monkeypatch.setattr(hf, "MODELS_FILE", tmp_path / "models.json")
    (tmp_path / "models.json").write_text("{}")
    monkeypatch.setattr(hf, "download_file", lambda url, target: target.write_bytes(b"IMG"))
    monkeypatch.setattr(hf.time, "sleep", lambda seconds: None)
    # Server and CLI share this process (and stdout) in tests; keep the server's log lines apart.
    monkeypatch.setattr(server, "log_event", lambda event, **fields: None)
    for name in ("HF_API_KEY", "HF_API_SECRET", "HF_MAX_USD"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def connect(tmp_path, monkeypatch):
    """Starts a server with prepared Higgsfield responses and points the CLI at it."""
    started = []

    def start(responses=(), **env):
        environ = {
            "HF_API_KEY": "k",
            "HF_API_SECRET": "s",
            "HF_SERVER_TOKEN": TOKEN,
            "HF_DATA_DIR": str(tmp_path / "server-data"),
            "HF_LISTEN": "127.0.0.1:0",
            **env,
        }
        fake = FakeSession(list(responses))
        app = server.HfServer(server.load_server_config(environ), session_factory=lambda: fake)
        app.start(worker=False)
        started.append(app)
        host, port = app.api_address
        monkeypatch.setenv("HF_SERVER", f"http://{host}:{port}/")
        monkeypatch.setenv("HF_SERVER_TOKEN", TOKEN)
        return app, fake

    yield start
    for app in started:
        app.stop()


def run_cli(capsys, *argv) -> tuple[int, dict]:
    code = hf.main(list(argv))
    return code, json.loads(capsys.readouterr().out)


def submit_responses():
    # The client estimates (forwarded), then the server estimates again and submits.
    return [
        FakeResponse(200, {"usd": "0.004"}),
        FakeResponse(200, {"usd": "0.004"}),
        FakeResponse(200, {"request_id": "r1", "status": "queued"}),
    ]


def test_server_without_token_is_refused(monkeypatch, capsys):
    monkeypatch.setenv("HF_SERVER", "http://127.0.0.1:9")
    monkeypatch.delenv("HF_SERVER_TOKEN", raising=False)
    code, output = run_cli(capsys, "status", "r1")
    assert code == 2
    assert "HF_SERVER_TOKEN" in output["error"]


def test_run_against_server(connect, tmp_path, capsys):
    app, fake = connect([*submit_responses(), FakeResponse(200, IMAGE_RESULT)])
    # The worker thread is off in tests: let it run once shortly after the client starts waiting.
    threading.Timer(0.3, app.run_worker_once).start()

    code, output = run_cli(capsys, "run", "test/image", "--prompt", "a lake", "--out", str(tmp_path / "out"))

    assert code == 0, output
    assert output["status"] == "completed"
    (path,) = [Path(file) for file in output["files"]]
    assert path.parent == tmp_path / "out"
    assert path.read_bytes() == b"IMG"
    assert json.loads(path.with_suffix(".json").read_text())["prompt"] == "a lake"
    # The central log is on the server; nothing was written locally.
    assert not hf.HISTORY_FILE.exists()
    assert app.store.get_job("r1")["stored"] is True
    # Higgsfield was only ever called by the server, with the server's credentials and full URLs.
    assert all(call["url"].startswith("https://api.higgsfield.ai/") for call in fake.calls)


def test_wait_and_download_after_client_timeout(connect, tmp_path, capsys):
    app, fake = connect(submit_responses())

    code, output = run_cli(capsys, "run", "test/image", "--prompt", "a lake", "--timeout", "1")
    assert code == 1
    assert "r1" in output["error"] and "wait" in output["error"]

    fake.responses.append(FakeResponse(200, IMAGE_RESULT))
    app.run_worker_once()

    code, output = run_cli(capsys, "wait", "r1", "--timeout", "5")
    assert (code, output["status"]) == (0, "completed")
    code, output = run_cli(capsys, "download", "r1", "--out", str(tmp_path / "later"))
    assert code == 0, output
    assert len(output["files"]) == 1


def test_spend_from_server(connect, capsys):
    app, _ = connect()
    app.store.upsert_job("r1", model="img", usd=0.004, status="completed", submitted="2026-10-31T10:00:00")
    code, output = run_cli(capsys, "spend")
    assert code == 0
    assert output["estimated_usd"] == 0.004


def test_characters_are_central(connect, capsys):
    app, _ = connect([FakeResponse(200, {"id": "ref-1", "status": "completed"})])
    code, _ = run_cli(capsys, "character", "add", "Mila", "ref-1")
    assert code == 0
    assert not hf.CHARACTERS_FILE.exists()
    code, output = run_cli(capsys, "character", "list")
    assert output["characters"] == [{"name": "Mila", "id": "ref-1", "model_version": "v2", "status": "completed"}]
    assert app.store.get_characters()["Mila"]["id"] == "ref-1"


def test_doctor_client_mode(connect, capsys):
    connect()
    code, output = run_cli(capsys, "doctor")
    assert code == 0, output
    assert output["checks"]["mode"] == "client"
    assert output["checks"]["server"] == "ok"
    assert output["checks"]["webhooks"] == "off (polling)"


def test_doctor_client_mode_unreachable(monkeypatch, capsys):
    monkeypatch.setenv("HF_SERVER", "http://127.0.0.1:9")
    monkeypatch.setenv("HF_SERVER_TOKEN", TOKEN)
    code, output = run_cli(capsys, "doctor")
    assert code == 1
    assert output["checks"]["server"].startswith("unreachable")


def test_server_cap_message_reaches_client(connect, capsys):
    connect([FakeResponse(200, {"usd": "0.004"}), FakeResponse(200, {"usd": "0.004"})], HF_MAX_USD="0.001")
    code, output = run_cli(capsys, "run", "test/image", "--prompt", "a lake")
    assert code == 1
    assert "HTTP 403" in output["error"] and "above the limit of $0.001" in output["error"]


def test_gallery_not_available_in_client_mode(connect, capsys):
    connect()
    code, output = run_cli(capsys, "gallery", "--no-open")
    assert code == 1
    assert "client mode" in output["error"]
