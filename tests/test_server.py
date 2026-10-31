"""Tests for hf serve: a real HTTP server on 127.0.0.1, Higgsfield replaced by a FakeSession."""

import json
import threading
import time
from pathlib import Path

import pytest
import requests
from fakes import FakeResponse, FakeSession

import hf
import server
import webhooks

TOKEN = "test-token"
SECRET = "whsec_dGVzdC1rZXktMTIzNDU2Nzg5MA=="
AUTH = {"Authorization": f"Bearer {TOKEN}"}
IMAGE_RESULT = {"request_id": "r1", "status": "completed", "images": [{"url": "https://cdn/x.png"}]}


@pytest.fixture(autouse=True)
def fake_downloads(monkeypatch):
    """Result downloads write a small file instead of fetching from the CDN."""
    monkeypatch.setattr(hf, "download_file", lambda url, target: target.write_bytes(b"IMG"))
    monkeypatch.setattr(hf.time, "sleep", lambda seconds: None)


@pytest.fixture
def start_server(tmp_path):
    """Starts an HfServer; returns (server, fake Higgsfield session). Stops all servers afterwards."""
    started = []

    def start(responses=(), worker=False, **env):
        environ = {
            "HF_API_KEY": "k",
            "HF_API_SECRET": "s",
            "HF_SERVER_TOKEN": TOKEN,
            "HF_DATA_DIR": str(tmp_path / "data"),
            "HF_LISTEN": "127.0.0.1:0",
            **env,
        }
        fake = FakeSession(list(responses))
        app = server.HfServer(server.load_server_config(environ), session_factory=lambda: fake)
        app.start(worker=worker)
        started.append(app)
        return app, fake

    yield start
    for app in started:
        app.stop()


WEBHOOKS_ON = {"HF_PUBLIC_URL": "https://hf.example.com", "HF_WEBHOOK_SECRET": SECRET}


def url(app, path, port=None):
    host, api_port = app.api_address
    return f"http://{host}:{port or api_port}{path}"


def send_webhook(app, event: dict, webhook_id="msg_1", port=None, secret=SECRET):
    body = json.dumps(event).encode()
    timestamp = str(int(time.time()))
    headers = {
        "webhook-id": webhook_id,
        "webhook-timestamp": timestamp,
        "webhook-signature": webhooks.sign(secret, webhook_id, timestamp, body),
        "Content-Type": "application/json",
    }
    return requests.post(url(app, "/webhooks/higgsfield", port), data=body, headers=headers, timeout=5)


def submit(app, model="higgsfield-ai/soul/v2/standard", body=None, key="key-1"):
    headers = {**AUTH, "Idempotency-Key": key}
    return requests.post(url(app, f"/{model}"), json=body or {"prompt": "a lake"}, headers=headers, timeout=5)


def completed_event(request_id="r1"):
    return {
        "request_id": request_id,
        "status": "completed",
        "error": None,
        "expires_at": "2026-11-07T12:00:00Z",
        "payload": {"images": [{"url": "https://cdn/x.png"}]},
    }


# --- Access -----------------------------------------------------------------


def test_healthz_without_token(start_server):
    app, _ = start_server()
    response = requests.get(url(app, "/healthz"), timeout=5)
    assert response.status_code == 200
    assert response.json() == {"ok": True}


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer wrong"}, {"Authorization": TOKEN}])
def test_api_requires_token(start_server, headers):
    app, _ = start_server()
    response = requests.get(url(app, "/v1/status"), headers=headers, timeout=5)
    assert response.status_code == 401
    assert "detail" in response.json()


def test_status_v1(start_server):
    app, _ = start_server(**WEBHOOKS_ON)
    data = requests.get(url(app, "/v1/status"), headers=AUTH, timeout=5).json()
    assert data["webhooks"] is True
    assert data["open_jobs"] == 0
    assert data["version"]


# --- Submitting -------------------------------------------------------------


def test_generation_adds_webhook_url_and_records_job(start_server):
    app, fake = start_server(
        [FakeResponse(200, {"usd": "0.004"}), FakeResponse(200, {"request_id": "r1", "status": "queued"})],
        **WEBHOOKS_ON,
    )
    response = submit(app, body={"prompt": "a lake", "aspect_ratio": "16:9"})

    assert response.status_code == 200
    assert response.json()["request_id"] == "r1"
    assert fake.calls[0]["url"] == "https://api.higgsfield.ai/estimate/higgsfield-ai/soul/v2/standard"
    assert fake.calls[1]["url"] == (
        "https://api.higgsfield.ai/higgsfield-ai/soul/v2/standard"
        "?hf_webhook=https%3A%2F%2Fhf.example.com%2Fwebhooks%2Fhiggsfield"
    )
    assert fake.calls[1]["headers"]["Idempotency-Key"] == "key-1"
    job = app.store.get_job("r1")
    assert job["model"] == "higgsfield-ai/soul/v2/standard"
    assert job["prompt"] == "a lake"
    assert job["payload"]["aspect_ratio"] == "16:9"
    assert job["usd"] == 0.004
    assert job["status"] == "queued"


def test_generation_without_webhooks_has_no_query(start_server):
    app, fake = start_server(
        [FakeResponse(200, {"usd": "0.004"}), FakeResponse(200, {"request_id": "r1", "status": "queued"})]
    )
    submit(app)
    assert fake.calls[1]["url"] == "https://api.higgsfield.ai/higgsfield-ai/soul/v2/standard"


@pytest.mark.parametrize(
    "estimate, message",
    [
        ({"usd": "0.600"}, "above the limit"),
        ({"type": "description", "pricing_description": "$0.2/s"}, "Price unknown"),
    ],
)
def test_generation_refused_over_cap_and_unknown_price(start_server, estimate, message):
    app, fake = start_server([FakeResponse(200, estimate)], HF_MAX_USD="0.5")
    response = submit(app)
    assert response.status_code == 403
    assert message in response.json()["detail"]
    assert len(fake.calls) == 1  # only the free estimate
    assert app.store.list_jobs() == []


def test_unpriced_model_allowed_by_owner(start_server):
    app, fake = start_server(
        [
            FakeResponse(200, {"type": "description", "pricing_description": "$0.2/s"}),
            FakeResponse(200, {"request_id": "r1", "status": "queued"}),
        ],
        HF_MAX_USD="0.5",
        HF_ALLOW_UNPRICED="higgsfield-ai/soul/v2/standard",
    )
    assert submit(app).status_code == 200


def test_same_idempotency_key_submits_once(start_server):
    app, fake = start_server(
        [FakeResponse(200, {"usd": "0.004"}), FakeResponse(200, {"request_id": "r1", "status": "queued"})]
    )
    first = submit(app, key="same")
    second = submit(app, key="same")
    assert first.json()["request_id"] == second.json()["request_id"] == "r1"
    assert len(fake.calls) == 2  # one estimate, one submit – the retry did not reach Higgsfield


def test_training_is_capped_and_recorded(start_server):
    app, fake = start_server(
        [FakeResponse(200, {"usd": "2.500"}), FakeResponse(200, {"id": "ref-1", "status": "not_ready"})],
        HF_MAX_USD="3",
    )
    response = requests.post(url(app, "/v1/custom-references"), json={"name": "Mila"}, headers=AUTH, timeout=5)
    assert response.status_code == 200
    assert fake.calls[0]["url"] == "https://api.higgsfield.ai/estimate/soul-id"
    job = app.store.get_job("ref-1")
    assert job["kind"] == "training"
    assert job["usd"] == 2.5

    capped, _ = start_server(
        [FakeResponse(200, {"usd": "2.500"})], HF_MAX_USD="1", HF_DATA_DIR=str(Path(app.config.data_dir) / "x")
    )
    refused = requests.post(url(capped, "/v1/custom-references"), json={"name": "Otto"}, headers=AUTH, timeout=5)
    assert refused.status_code == 403


def test_training_status_updates_on_forwarded_check(start_server):
    app, _ = start_server(
        [
            FakeResponse(200, {"usd": "2.500"}),
            FakeResponse(200, {"id": "ref-1", "status": "not_ready"}),
            FakeResponse(200, {"id": "ref-1", "status": "completed"}),
        ]
    )
    requests.post(url(app, "/v1/custom-references"), json={"name": "Mila"}, headers=AUTH, timeout=5)
    requests.get(url(app, "/v1/custom-references/ref-1"), headers=AUTH, timeout=5)
    assert app.store.get_job("ref-1")["status"] == "completed"


def test_forward_passes_status_code(start_server):
    app, fake = start_server([FakeResponse(400, {"detail": "started"})])
    response = requests.post(url(app, "/requests/r1/cancel"), headers=AUTH, timeout=5)
    assert response.status_code == 400
    assert response.json()["detail"] == "started"
    assert fake.calls[0]["url"] == "https://api.higgsfield.ai/requests/r1/cancel"


def test_unknown_path_is_404(start_server):
    app, _ = start_server()
    assert requests.post(url(app, "/single"), headers=AUTH, timeout=5).status_code == 404


# --- Webhooks ---------------------------------------------------------------


def test_webhook_flow_stores_files_and_wait_returns(start_server):
    app, _ = start_server(
        [FakeResponse(200, {"usd": "0.004"}), FakeResponse(200, {"request_id": "r1", "status": "queued"})],
        **WEBHOOKS_ON,
    )
    submit(app)
    assert send_webhook(app, completed_event()).status_code == 200
    app.run_worker_once()

    record = requests.get(url(app, "/v1/jobs/r1?wait=5"), headers=AUTH, timeout=10).json()
    assert record["status"] == "completed"
    assert record["stored"] is True
    (rel,) = record["files"]
    stored = app.config.data_dir / "files" / rel
    assert stored.read_bytes() == b"IMG"
    assert json.loads(stored.with_suffix(".json").read_text())["prompt"] == "a lake"


def test_wait_returns_when_worker_stores_later(start_server):
    app, _ = start_server(**WEBHOOKS_ON)
    send_webhook(app, completed_event())
    started = time.monotonic()
    # The worker thread is off; run it once shortly after the wait began.
    threading.Timer(0.2, app.run_worker_once).start()
    record = requests.get(url(app, "/v1/jobs/r1?wait=10"), headers=AUTH, timeout=15).json()
    assert record["stored"] is True
    assert time.monotonic() - started < 5


def test_webhook_before_submit_recorded(start_server):
    app, _ = start_server(
        [FakeResponse(200, {"usd": "0.004"}), FakeResponse(200, {"request_id": "r1", "status": "queued"})],
        **WEBHOOKS_ON,
    )
    send_webhook(app, completed_event())
    submit(app)
    assert app.store.get_job("r1")["status"] == "completed"
    assert app.store.get_job("r1")["model"] == "higgsfield-ai/soul/v2/standard"


def test_duplicate_webhook_is_ignored(start_server):
    app, _ = start_server(**WEBHOOKS_ON)
    assert send_webhook(app, completed_event()).json() == {"ok": True}
    assert send_webhook(app, completed_event()).json() == {"ok": True, "duplicate": True}


def test_bad_signature_rejected(start_server):
    app, _ = start_server(**WEBHOOKS_ON)
    response = send_webhook(app, completed_event(), secret="whsec_b3RoZXIta2V5")
    assert response.status_code == 400
    assert app.store.get_job("r1") is None


def test_webhook_404_when_disabled(start_server):
    app, _ = start_server()
    assert send_webhook(app, completed_event()).status_code == 404


def test_separate_webhook_port(start_server):
    app, _ = start_server(HF_WEBHOOK_LISTEN="127.0.0.1:0", **WEBHOOKS_ON)
    assert send_webhook(app, completed_event()).status_code == 404
    assert send_webhook(app, completed_event(), port=app.webhook_address[1]).status_code == 200
    # The webhook port answers nothing else.
    assert requests.get(url(app, "/v1/status", app.webhook_address[1]), headers=AUTH, timeout=5).status_code == 404


# --- Polling and restart ----------------------------------------------------


def test_fallback_poll_completes_job_without_webhooks(start_server):
    app, fake = start_server(
        [
            FakeResponse(200, {"usd": "0.004"}),
            FakeResponse(200, {"request_id": "r1", "status": "queued"}),
            FakeResponse(200, IMAGE_RESULT),
        ]
    )
    submit(app)
    app.run_worker_once()
    assert fake.calls[2]["url"] == "https://api.higgsfield.ai/requests/r1/status"
    assert app.store.get_job("r1")["stored"] is True


def test_webhook_mode_polls_only_old_jobs(start_server):
    app, fake = start_server(
        [FakeResponse(200, {"usd": "0.004"}), FakeResponse(200, {"request_id": "r1", "status": "queued"})],
        HF_FALLBACK_POLL_SECONDS="300",
        **WEBHOOKS_ON,
    )
    submit(app)
    app.run_worker_once()
    assert len(fake.calls) == 2  # just submitted: no poll yet, the webhook is expected
    fake.responses.append(FakeResponse(200, IMAGE_RESULT))
    app.clock = lambda: time.time() + 301
    app.run_worker_once()
    assert app.store.get_job("r1")["stored"] is True


def test_restart_resumes_open_jobs(start_server):
    first, _ = start_server(
        [FakeResponse(200, {"usd": "0.004"}), FakeResponse(200, {"request_id": "r1", "status": "queued"})]
    )
    submit(first)
    first.stop()
    second, _ = start_server([FakeResponse(200, IMAGE_RESULT)])
    second.run_worker_once()
    assert second.store.get_job("r1")["stored"] is True


def test_failed_download_is_retried_with_backoff_then_given_up(start_server, monkeypatch):
    attempts = []

    def broken(url, target):
        attempts.append(url)
        raise hf.CliError("Download failed: HTTP 503")

    monkeypatch.setattr(hf, "download_file", broken)
    app, _ = start_server(**WEBHOOKS_ON)
    now = time.time()
    app.clock = lambda: now
    event = {**completed_event(), "expires_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now + 3600))}
    send_webhook(app, event)

    app.run_worker_once()
    job = app.store.get_job("r1")
    assert (job["download_attempts"], job["download_error"], job["stored"]) == (1, None, False)
    app.run_worker_once()  # not due yet: no new attempt
    assert len(attempts) == 1

    app.clock = lambda: now + 120
    app.run_worker_once()
    assert len(attempts) == 2

    app.clock = lambda: now + 4000  # after expires_at: Higgsfield has deleted the files
    app.run_worker_once()
    assert app.store.get_job("r1")["download_error"] == "Download failed: HTTP 503"
    assert app.store.jobs_to_store(now + 99999) == []


def test_retry_succeeds_after_a_temporary_failure(start_server, monkeypatch):
    calls = []

    def flaky(url, target):
        calls.append(url)
        if len(calls) == 1:
            raise requests.exceptions.ChunkedEncodingError("connection reset")
        target.write_bytes(b"IMG")

    monkeypatch.setattr(hf, "download_file", flaky)
    app, _ = start_server(**WEBHOOKS_ON)
    now = time.time()
    app.clock = lambda: now
    send_webhook(app, completed_event())
    app.run_worker_once()
    app.clock = lambda: now + 120
    app.run_worker_once()
    assert app.store.get_job("r1")["stored"] is True


def test_missing_result_files_trigger_a_fresh_poll(start_server):
    app, fake = start_server([FakeResponse(200, IMAGE_RESULT)], **WEBHOOKS_ON)
    # A completed webhook without payload (e.g. an unexpected format): ask Higgsfield before giving up.
    send_webhook(app, {"request_id": "r1", "status": "completed", "error": None, "payload": None})
    app.run_worker_once()
    assert fake.calls[0]["url"] == "https://api.higgsfield.ai/requests/r1/status"
    assert app.store.get_job("r1")["stored"] is True


def test_worker_does_not_spin_on_unexpected_download_errors(start_server, monkeypatch):
    attempts = []

    def reset(url, target):
        attempts.append(url)
        raise requests.exceptions.ChunkedEncodingError("connection reset mid-stream")

    monkeypatch.setattr(hf, "download_file", reset)
    monkeypatch.setattr(server, "log_event", lambda event, **fields: None)
    app, _ = start_server(worker=True, **WEBHOOKS_ON)
    send_webhook(app, completed_event())
    # Event().wait instead of time.sleep: the fake_downloads fixture switches time.sleep off.
    threading.Event().wait(1)
    assert 1 <= len(attempts) <= 2


def test_concurrent_retries_with_same_key_submit_once(start_server):
    class SlowHiggsfield:
        """Answers by URL: estimates at once, submissions slowly and with a new ID each time."""

        def __init__(self):
            self.submits = 0

        def request(self, method, url, json=None, headers=None, timeout=None):
            if "/estimate/" in url:
                return FakeResponse(200, {"usd": "0.004"})
            threading.Event().wait(0.5)  # Higgsfield takes a while (time.sleep is switched off in these tests)
            self.submits += 1
            return FakeResponse(200, {"request_id": f"r{self.submits}", "status": "queued"})

    slow = SlowHiggsfield()
    app, _ = start_server()
    app.session_factory = lambda: slow
    results = []
    threads = [threading.Thread(target=lambda: results.append(submit(app, key="same").json())) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert slow.submits == 1
    assert [result["request_id"] for result in results] == ["r1", "r1"]


def test_unexpected_higgsfield_answer_gives_json_error(start_server, monkeypatch):
    monkeypatch.setattr(server, "log_event", lambda event, **fields: None)
    app, _ = start_server([FakeResponse(200, {"usd": "0.004"}), FakeResponse(200, {"unexpected": True})])
    response = submit(app)
    assert response.status_code == 500
    assert "KeyError" in response.json()["detail"]


def raw_request(app, method, path, headers, body=b"", port=None):
    import http.client

    host, api_port = app.api_address
    connection = http.client.HTTPConnection(host, port or api_port, timeout=5)
    connection.putrequest(method, path)
    for name, value in headers.items():
        connection.putheader(name, value)
    connection.endheaders()
    if body:
        connection.send(body)
    response = connection.getresponse()
    status = response.status
    connection.close()
    return status


def test_bad_content_length_is_400(start_server):
    app, _ = start_server(**WEBHOOKS_ON)
    assert raw_request(app, "POST", "/webhooks/higgsfield", {"Content-Length": "abc"}) == 400
    assert raw_request(app, "POST", "/webhooks/higgsfield", {"Content-Length": "-5"}) == 400


def test_huge_body_is_refused_without_reading_it(start_server):
    app, _ = start_server(**WEBHOOKS_ON)
    assert raw_request(app, "POST", "/webhooks/higgsfield", {"Content-Length": str(50_000_000)}) == 413


# --- Other endpoints --------------------------------------------------------


def test_request_status_from_store(start_server):
    app, fake = start_server(**WEBHOOKS_ON)
    send_webhook(app, completed_event())
    data = requests.get(url(app, "/requests/r1/status"), headers=AUTH, timeout=5).json()
    assert data["status"] == "completed"
    assert data["images"] == [{"url": "https://cdn/x.png"}]
    assert fake.calls == []


def test_request_status_unknown_job_asks_higgsfield(start_server):
    app, fake = start_server([FakeResponse(200, {"request_id": "r9", "status": "in_progress"})])
    data = requests.get(url(app, "/requests/r9/status"), headers=AUTH, timeout=5).json()
    assert data["status"] == "in_progress"
    assert app.store.get_job("r9")["status"] == "in_progress"


def test_spend_and_characters_endpoints(start_server):
    app, _ = start_server()
    app.store.upsert_job("r1", model="img", usd=0.004, status="completed", submitted="2026-10-31T10:00:00")
    spend = requests.get(url(app, "/v1/spend"), headers=AUTH, timeout=5).json()
    assert spend["since"] == "all"
    assert spend["estimated_usd"] == 0.004

    put = requests.put(url(app, "/v1/characters/Mila"), json={"id": "ref-1"}, headers=AUTH, timeout=5)
    assert put.status_code == 200
    data = requests.get(url(app, "/v1/characters"), headers=AUTH, timeout=5).json()
    assert data == {"characters": {"Mila": {"id": "ref-1"}}}


def test_jobs_list(start_server):
    app, _ = start_server()
    app.store.upsert_job("r1", status="queued")
    data = requests.get(url(app, "/v1/jobs"), headers=AUTH, timeout=5).json()
    assert [job["request_id"] for job in data["jobs"]] == ["r1"]


def test_job_404(start_server):
    app, _ = start_server()
    assert requests.get(url(app, "/v1/jobs/nope"), headers=AUTH, timeout=5).status_code == 404


# --- hf serve and file safety -----------------------------------------------


def test_serve_refuses_half_webhook_config(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(hf, "ENV_FILE", tmp_path / "no.env")
    for name, value in {"HF_API_KEY": "k", "HF_API_SECRET": "s", "HF_SERVER_TOKEN": "t"}.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("HF_PUBLIC_URL", "https://hf.example.com")
    monkeypatch.delenv("HF_WEBHOOK_SECRET", raising=False)
    monkeypatch.delenv("HF_SERVER", raising=False)
    assert hf.main(["serve"]) == 2
    error = json.loads(capsys.readouterr().out)["error"]
    assert "HF_PUBLIC_URL" in error and "HF_WEBHOOK_SECRET" in error


@pytest.mark.parametrize("path", ["/v1/files/../hf.db", "/v1/files/%2e%2e/hf.db", "/v1/files/..%2fhf.db"])
def test_file_path_traversal_is_404(start_server, path):
    app, _ = start_server()
    assert (app.config.data_dir / "hf.db").exists()
    # A raw request: requests would clean up "../" itself and hide the attack.
    import http.client

    host, port = app.api_address
    connection = http.client.HTTPConnection(host, port, timeout=5)
    connection.request("GET", path, headers=AUTH)
    response = connection.getresponse()
    assert response.status == 404
    assert b"SQLite" not in response.read()
    connection.close()
