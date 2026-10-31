"""hf serve: runs hf as a small service for agents.

The service holds the Higgsfield credentials, receives completion webhooks
(polling as a safety net), stores results and the job log centrally and
answers the hf CLI in client mode (HF_SERVER). See docs/server.md.
"""

import hmac
import importlib.metadata
import json
import mimetypes
import shutil
import signal
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlsplit

import requests

import hf
import webhooks
from hf import CliError
from store import JobStore

WEBHOOK_PATH = "/webhooks/higgsfield"
# Without webhooks, open jobs are polled this often (seconds).
POLL_INTERVAL = 5
# Longest time one /v1/jobs/<id>?wait=... request is held open (the client simply asks again).
MAX_WAIT = 60
# Failed downloads are retried after 60 s, 120 s, 240 s ... (at most 1 hour apart) until Higgsfield
# deletes the files (expires_at), or MAX_DOWNLOAD_ATTEMPTS times if expires_at is unknown.
DOWNLOAD_RETRY_START = 60
DOWNLOAD_RETRY_MAX = 3600
MAX_DOWNLOAD_ATTEMPTS = 12
# Request bodies are small JSON; anything bigger is refused before it is read (also protects the public webhook).
MAX_BODY = 1_000_000
# Seconds a client may take to send its request (stops very slow or stuck connections from piling up).
SOCKET_TIMEOUT = 30
# How long a request waits for a parallel request with the same Idempotency-Key to finish.
SUBMIT_WAIT = 180
# Higgsfield paths that are passed on unchanged (with the server's credentials).
FORWARD_PREFIXES = ("/requests/", "/estimate/", "/files/", "/v1/custom-references/")


# --- Configuration ----------------------------------------------------------


# @dataclass writes __init__ and friends for us; frozen=True makes the object read-only.
@dataclass(frozen=True)
class ServerConfig:
    api_key: str
    api_secret: str
    token: str
    data_dir: Path
    listen: tuple[str, int]
    webhook_listen: tuple[str, int] | None
    public_url: str | None
    webhook_secret: str | None
    fallback_poll_seconds: int
    max_usd: float | None
    allow_unpriced: frozenset[str]

    # @property: read like an attribute (config.webhooks_enabled), computed on access.
    @property
    def webhooks_enabled(self) -> bool:
        return bool(self.public_url and self.webhook_secret)

    @property
    def webhook_url(self) -> str | None:
        return self.public_url.rstrip("/") + WEBHOOK_PATH if self.webhooks_enabled else None


def config_error(message: str) -> CliError:
    # Exit code 2 like missing credentials in the CLI: a setup problem, not a failed job.
    return CliError(message, exit_code=2)


def parse_address(name: str, value: str) -> tuple[str, int]:
    """'0.0.0.0:8080' -> ('0.0.0.0', 8080)"""
    # rpartition splits at the LAST ':' – so the port is always the part after it.
    host, separator, port = value.rpartition(":")
    if not separator or not host or not port.isdigit():
        raise config_error(f"{name} must look like host:port, e.g. 0.0.0.0:8080 (is '{value}').")
    return host, int(port)


def parse_number(name: str, value: str, kind: type, minimum: float) -> float:
    try:
        number = kind(value)
    except ValueError:
        raise config_error(f"{name} must be a number (is '{value}').") from None
    if number < minimum:
        raise config_error(f"{name} must be at least {minimum} (is '{value}').")
    return number


def load_server_config(environ: Mapping[str, str]) -> ServerConfig:
    """Reads and checks all settings. Refuses to start on anything missing or half configured."""
    for name in ("HF_API_KEY", "HF_API_SECRET", "HF_SERVER_TOKEN"):
        if not environ.get(name):
            raise config_error(f"{name} must be set for hf serve.")
    if environ.get("HF_SERVER"):
        raise config_error("HF_SERVER is set: hf serve must not run in client mode. Unset HF_SERVER.")
    public_url = environ.get("HF_PUBLIC_URL") or None
    webhook_secret = environ.get("HF_WEBHOOK_SECRET") or None
    if bool(public_url) != bool(webhook_secret):
        raise config_error("Set both HF_PUBLIC_URL and HF_WEBHOOK_SECRET for webhooks, or neither for polling.")
    if public_url and not public_url.startswith("https://"):
        raise config_error("HF_PUBLIC_URL must start with https:// (Higgsfield only calls HTTPS webhooks).")
    if webhook_secret:
        try:
            webhooks.signing_key(webhook_secret)
        except webhooks.WebhookError:
            raise config_error(
                "HF_WEBHOOK_SECRET is not a valid whsec_ secret – copy it again from the Console."
            ) from None
    max_usd = environ.get("HF_MAX_USD")
    webhook_listen = environ.get("HF_WEBHOOK_LISTEN")
    return ServerConfig(
        api_key=environ["HF_API_KEY"],
        api_secret=environ["HF_API_SECRET"],
        token=environ["HF_SERVER_TOKEN"],
        data_dir=Path(environ.get("HF_DATA_DIR") or "/data"),
        listen=parse_address("HF_LISTEN", environ.get("HF_LISTEN") or "0.0.0.0:8080"),
        webhook_listen=parse_address("HF_WEBHOOK_LISTEN", webhook_listen) if webhook_listen else None,
        public_url=public_url,
        webhook_secret=webhook_secret,
        fallback_poll_seconds=int(
            parse_number("HF_FALLBACK_POLL_SECONDS", environ.get("HF_FALLBACK_POLL_SECONDS") or "300", int, 1)
        ),
        max_usd=parse_number("HF_MAX_USD", max_usd, float, 0) if max_usd else None,
        allow_unpriced=frozenset(
            item.strip() for item in (environ.get("HF_ALLOW_UNPRICED") or "").split(",") if item.strip()
        ),
    )


# --- Cost policy ------------------------------------------------------------


def budget_refusal(cost: dict, max_usd: float | None, model_id: str, allow_unpriced: frozenset[str]) -> str | None:
    """Why the server refuses a job, or None if it may run."""
    if max_usd is None:
        return None
    if cost["usd"] is None:
        if "*" in allow_unpriced or model_id in allow_unpriced:
            return None
        reason = cost.get("pricing") or cost.get("estimate_error") or "no estimate available"
        return (
            f"Price unknown for {model_id} ({reason}) and this server has a limit of ${max_usd:.3f}. "
            f"Nothing was submitted. Only the owner can allow it via HF_ALLOW_UNPRICED – ask the owner."
        )
    if cost["usd"] > max_usd:
        return f"Estimated cost ${cost['usd']:.3f} is above the limit of ${max_usd:.3f}. Nothing was submitted."
    return None


# --- Routing ----------------------------------------------------------------


def route(method: str, path: str) -> tuple[str, dict]:
    """Which handler answers a request. path is without the query string."""
    if method == "GET" and path == "/healthz":
        return "healthz", {}
    if method == "POST" and path == WEBHOOK_PATH:
        return "webhook", {}
    if path.startswith("/v1/") and not path.startswith("/v1/custom-references"):
        return route_service(method, path.removeprefix("/v1/"))
    if method == "GET" and path.startswith("/requests/") and path.endswith("/status"):
        return "request_status", {"id": path.removeprefix("/requests/").removesuffix("/status")}
    if method == "POST" and path == "/v1/custom-references":
        return "training", {}
    if path.startswith(FORWARD_PREFIXES) or path == "/models":
        return "forward", {}
    # Everything else with at least two path parts, e.g. /higgsfield-ai/soul/v2/standard, is a generation.
    parts = [part for part in path.split("/") if part]
    if method == "POST" and len(parts) >= 2 and not path.startswith(("/v1/", "/webhooks/")):
        return "generation", {"model": "/".join(parts)}
    return "not_found", {}


def route_service(method: str, rest: str) -> tuple[str, dict]:
    """The service's own endpoints under /v1/."""
    if method == "GET":
        if rest == "status":
            return "status_v1", {}
        if rest == "jobs":
            return "jobs", {}
        if rest == "spend":
            return "spend", {}
        if rest == "characters":
            return "characters", {}
        if rest.startswith("jobs/") and rest.count("/") == 1:
            return "job", {"id": rest.removeprefix("jobs/")}
        if rest.startswith("files/"):
            return "file", {"rel": rest.removeprefix("files/")}
    if method == "PUT" and rest.startswith("characters/") and rest.count("/") == 1:
        return "character", {"name": rest.removeprefix("characters/")}
    return "not_found", {}


# --- Logging ----------------------------------------------------------------


def log_event(event: str, **fields) -> None:
    """One JSON line per event on stdout – easy to read with 'docker logs' or 'kubectl logs'."""
    # flush=True writes the line immediately instead of waiting for a full buffer.
    print(json.dumps({"time": datetime.now().isoformat(timespec="seconds"), "event": event, **fields}), flush=True)


def version() -> str:
    try:
        return importlib.metadata.version("higgsfield-tools")
    except importlib.metadata.PackageNotFoundError:
        return "dev"


# --- The service ------------------------------------------------------------


def job_record(job: dict) -> dict:
    """What /v1/jobs/<id> returns: the job log entry without internal fields."""
    fields = ("request_id", "model", "prompt", "payload", "usd", "status", "error", "submitted", "expires_at")
    return {
        **{field: job.get(field) for field in fields},
        "stored": job["stored"],
        "files": job.get("files") or [],
        "download_error": job.get("download_error"),
    }


def is_settled(job: dict | None) -> bool:
    """Nothing more will happen to this job: finished, and its files are stored (or cannot be)."""
    if not job or job["status"] not in hf.TERMINAL_STATUSES:
        return False
    return job["stored"] or job["status"] != "completed" or bool(job["download_error"])


class HfServer:
    """The HTTP server(s) plus the background worker that polls Higgsfield and downloads results."""

    def __init__(
        self,
        config: ServerConfig,
        session_factory: Callable[[], requests.Session] | None = None,
        clock: Callable[[], float] = time.time,
    ):
        self.config = config
        self.session_factory = session_factory or self.default_session
        self.clock = clock
        self.store = JobStore(config.data_dir / "hf.db")
        self.files_dir = config.data_dir / "files"
        self.api_address: tuple[str, int] | None = None
        self.webhook_address: tuple[str, int] | None = None
        # Idempotency-Keys of submissions in progress, so a parallel retry waits instead of paying twice.
        self.submitting: dict[str, threading.Event] = {}
        self.submitting_lock = threading.Lock()
        self._servers: list[ThreadingHTTPServer] = []
        self._threads: list[threading.Thread] = []
        self._stopping = False

    def default_session(self) -> requests.Session:
        session = requests.Session()
        session.headers["Authorization"] = f"Key {self.config.api_key}:{self.config.api_secret}"
        return session

    # --- Start and stop -----------------------------------------------------

    def start(self, worker: bool = True) -> None:
        """Starts listening (and the worker) in background threads and returns immediately."""
        separate = self.config.webhook_listen is not None
        main = ThreadingHTTPServer(
            self.config.listen, make_handler(self, webhook_only=False, allow_webhook=not separate)
        )
        self._servers.append(main)
        self.api_address = main.server_address[:2]
        if separate:
            hook = ThreadingHTTPServer(
                self.config.webhook_listen, make_handler(self, webhook_only=True, allow_webhook=True)
            )
            self._servers.append(hook)
            self.webhook_address = hook.server_address[:2]
        for http_server in self._servers:
            # daemon=True: the thread does not keep the program alive on its own.
            # poll_interval: how often serve_forever checks for shutdown (default 0.5 s makes stopping slow).
            self._threads.append(
                threading.Thread(target=http_server.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True)
            )
        if worker:
            self._threads.append(threading.Thread(target=self.worker_loop, daemon=True))
        for thread in self._threads:
            thread.start()

    def stop(self) -> None:
        self._stopping = True
        with self.store.changed:
            self.store.changed.notify_all()
        for http_server in self._servers:
            http_server.shutdown()
            http_server.server_close()
        for thread in self._threads:
            thread.join(timeout=5)

    # --- Talking to Higgsfield ----------------------------------------------

    def higgsfield(self, method: str, path: str, body: dict | None = None, headers: dict | None = None) -> dict:
        """One call to the real Higgsfield API (always the full URL, never an hf server)."""
        return hf.api_request(self.session_factory(), method, hf.BASE_URL + path, json_body=body, headers=headers)

    def estimate(self, estimate_name: str, body: dict) -> dict:
        try:
            result = self.higgsfield("POST", f"/estimate/{estimate_name}", body)
        except CliError as error:
            return {"usd": None, "pricing": None, "estimate_error": str(error)}
        usd = result.get("usd")
        return {"usd": float(usd) if usd is not None else None, "pricing": result.get("pricing_description")}

    def now_iso(self) -> str:
        return datetime.fromtimestamp(self.clock()).isoformat(timespec="seconds")

    # --- Background worker --------------------------------------------------

    def worker_loop(self) -> None:
        while not self._stopping:
            try:
                self.run_worker_once()
            except Exception as error:  # the worker must never die; log, pause and try again
                log_event("worker_error", error=f"{type(error).__name__}: {error}")
                self.store.wait_until(lambda: self._stopping, 2)
                continue
            # Sleep until a download is due, or 2 seconds at most.
            self.store.wait_until(lambda: self._stopping or bool(self.store.jobs_to_store(self.clock())), 2)

    def run_worker_once(self) -> None:
        """One round: poll jobs that are due, then download finished results."""
        for job in self.store.open_jobs():
            if job["kind"] != "training" and self.poll_due(job):
                self.poll(job)
        for job in self.store.jobs_to_store(self.clock()):
            self.store_files(job)

    def poll_due(self, job: dict) -> bool:
        now = self.clock()
        last = job["last_polled"]
        if not self.config.webhooks_enabled:
            return last is None or now - last >= POLL_INTERVAL
        # With webhooks, only a rare safety poll for jobs that take suspiciously long.
        wait = self.config.fallback_poll_seconds
        submitted = datetime.fromisoformat(job["submitted"]).timestamp() if job["submitted"] else now
        return now - submitted >= wait and (last is None or now - last >= wait)

    def poll(self, job: dict) -> None:
        request_id = job["request_id"]
        try:
            result = self.higgsfield("GET", f"/requests/{request_id}/status")
        except CliError as error:
            self.store.upsert_job(request_id, last_polled=self.clock())
            log_event("poll_failed", request_id=request_id, error=str(error))
            return
        self.store.upsert_job(
            request_id,
            status=result.get("status"),
            error=result.get("error"),
            expires_at=result.get("expires_at"),
            result=result,
            last_polled=self.clock(),
        )
        log_event("polled", request_id=request_id, status=result.get("status"))

    def store_files(self, job: dict) -> None:
        request_id = job["request_id"]
        if not hf.extract_outputs(job["result"] or {}):
            # No file URLs (e.g. a webhook without payload): ask Higgsfield for the full status once.
            self.poll(job)
            job = self.store.get_job(request_id)
        day = (job["submitted"] or self.now_iso())[:10]
        try:
            paths = hf.download_outputs(job["result"] or {"request_id": request_id}, job, self.files_dir / day)
        except (CliError, requests.RequestException, OSError) as error:
            self.download_failed(job, error)
            return
        files = [str(path.relative_to(self.files_dir)) for path in paths]
        self.store.upsert_job(request_id, stored=True, files=files)
        log_event("stored", request_id=request_id, files=files)

    def download_failed(self, job: dict, error: Exception) -> None:
        """Schedules another try with a growing pause – or gives up once Higgsfield has deleted the files."""
        attempts = (job["download_attempts"] or 0) + 1
        now = self.clock()
        delay = min(DOWNLOAD_RETRY_START * 2 ** (attempts - 1), DOWNLOAD_RETRY_MAX)
        expires = parse_time(job["expires_at"])
        give_up = now + delay >= expires if expires is not None else attempts >= MAX_DOWNLOAD_ATTEMPTS
        message = str(error) if isinstance(error, CliError) else f"{type(error).__name__}: {error}"
        if give_up:
            self.store.upsert_job(job["request_id"], download_attempts=attempts, download_error=message)
        else:
            self.store.upsert_job(job["request_id"], download_attempts=attempts, next_download=now + delay)
        log_event("download_failed", request_id=job["request_id"], error=message, attempts=attempts, gave_up=give_up)


def parse_time(value: str | None) -> float | None:
    """'2026-11-07T12:00:00Z' -> seconds since 1970, or None. ("Z" is replaced for Python 3.10.)"""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


# --- HTTP handlers ----------------------------------------------------------


class HttpError(Exception):
    """Ends a request with this status code and {"detail": message}."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def make_handler(app: HfServer, webhook_only: bool, allow_webhook: bool) -> type[BaseHTTPRequestHandler]:
    """Builds the request handler class for one listening port.

    http.server creates a new handler object per request and only knows the class,
    so the class "remembers" app and the port's role through this enclosing function.
    """

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        # Socket timeout for reading a request: slow or stuck clients are disconnected.
        timeout = SOCKET_TIMEOUT

        def log_message(self, format, *args):  # noqa: A002 – name given by http.server
            pass  # silence the default access log; we log our own events

        def do_GET(self):
            self.handle_request("GET")

        def do_POST(self):
            self.handle_request("POST")

        def do_PUT(self):
            self.handle_request("PUT")

        def do_DELETE(self):
            self.handle_request("DELETE")

        # --- Plumbing -------------------------------------------------------

        def handle_request(self, method: str) -> None:
            parts = urlsplit(self.path)
            # unquote turns %2e%2e back into "..", so path tricks are caught by the file check below.
            path = unquote(parts.path)
            self.query = {key: values[-1] for key, values in parse_qs(parts.query).items()}
            name, params = route(method, path)
            if webhook_only and name != "webhook":
                name = "not_found"
            if name == "webhook" and not (allow_webhook and app.config.webhooks_enabled):
                name = "not_found"
            try:
                if name not in ("healthz", "webhook", "not_found"):
                    self.check_token()
                getattr(self, f"on_{name}")(**params)
            except HttpError as error:
                self.send_json(error.status, {"detail": str(error)})
            except CliError as error:
                self.send_json(error.status_code or 502, {"detail": hf_detail(error)})
            except Exception as error:  # anything unexpected: answer instead of dropping the connection
                log_event("request_error", path=path, error=f"{type(error).__name__}: {error}")
                self.close_connection = True
                self.send_json(500, {"detail": f"Internal error ({type(error).__name__}: {error})."})

        def check_token(self) -> None:
            given = self.headers.get("Authorization", "")
            expected = f"Bearer {app.config.token}"
            if not hmac_equal(given, expected):
                raise HttpError(401, "Missing or wrong HF_SERVER_TOKEN.")

        def body_bytes(self) -> bytes:
            text = self.headers.get("Content-Length") or "0"
            if not text.isdigit():
                self.close_connection = True  # the unread body would confuse the next request
                raise HttpError(400, "Invalid Content-Length.")
            length = int(text)
            if length > MAX_BODY:
                self.close_connection = True
                raise HttpError(413, f"Body too large (limit {MAX_BODY} bytes).")
            return self.rfile.read(length) if length else b""

        def body_json(self) -> dict:
            raw = self.body_bytes()
            if not raw:
                return {}
            try:
                data = json.loads(raw)
            except json.JSONDecodeError as error:
                raise HttpError(400, f"Body is not valid JSON: {error.msg}") from error
            if not isinstance(data, dict):
                raise HttpError(400, "Body must be a JSON object.")
            return data

        def send_json(self, status: int, data: dict) -> None:
            body = json.dumps(data, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        # --- Endpoints ------------------------------------------------------

        def on_not_found(self) -> None:
            raise HttpError(404, "Not found.")

        def on_healthz(self) -> None:
            self.send_json(200, {"ok": True})

        def on_status_v1(self) -> None:
            self.send_json(
                200,
                {
                    "version": version(),
                    "webhooks": app.config.webhooks_enabled,
                    "open_jobs": len(app.store.open_jobs()),
                    "data_dir": str(app.config.data_dir),
                },
            )

        def on_generation(self, model: str) -> None:
            # The path was URL-decoded: "%3F" became "?". Such characters never belong to a model ID
            # and would smuggle extra query parameters (e.g. another hf_webhook) to Higgsfield.
            if "?" in model or "#" in model:
                raise HttpError(400, f"Invalid model ID: {model}")
            body = self.body_json()
            key = self.headers.get("Idempotency-Key")
            if not key:
                self.submit_generation(model, body, None)
                return
            with app.submitting_lock:
                existing = app.store.find_by_idempotency_key(key)
                running = app.submitting.get(key)
                if not existing and not running:
                    # This request submits; a parallel retry with the same key will wait for it.
                    app.submitting[key] = threading.Event()
            if not existing and running:
                running.wait(SUBMIT_WAIT)
                existing = app.store.find_by_idempotency_key(key)
                if not existing:
                    raise HttpError(409, "A request with this Idempotency-Key is still being submitted. Try again.")
            if existing:
                # A retry of a job we already submitted: answer from the store, do not pay twice.
                self.send_json(200, {"request_id": existing["request_id"], "status": existing["status"]})
                return
            try:
                self.submit_generation(model, body, key)
            finally:
                with app.submitting_lock:
                    app.submitting.pop(key).set()

        def submit_generation(self, model: str, body: dict, key: str | None) -> None:
            cost = app.estimate(model, body)
            refusal = budget_refusal(cost, app.config.max_usd, model, app.config.allow_unpriced)
            if refusal:
                log_event("refused", model=model, reason=refusal)
                raise HttpError(403, refusal)
            path = f"/{model}"
            if app.config.webhooks_enabled:
                # quote(..., safe="") encodes ":" and "/" too, as a query value needs it.
                path += f"?hf_webhook={quote(app.config.webhook_url, safe='')}"
            result = app.higgsfield("POST", path, body, headers={"Idempotency-Key": key} if key else None)
            app.store.upsert_job(
                result["request_id"],
                model=model,
                prompt=body.get("prompt"),
                payload=body,
                usd=cost["usd"],
                submitted=app.now_iso(),
                status=result.get("status"),
                idempotency_key=key,
            )
            log_event("submitted", request_id=result["request_id"], model=model, usd=cost["usd"])
            self.send_json(200, result)

        def on_training(self) -> None:
            body = self.body_json()
            cost = app.estimate(hf.SOUL_ID_ESTIMATE, body)
            refusal = budget_refusal(cost, app.config.max_usd, hf.SOUL_ID_ESTIMATE, app.config.allow_unpriced)
            if refusal:
                raise HttpError(403, refusal)
            result = app.higgsfield("POST", "/v1/custom-references", body)
            app.store.upsert_job(
                result["id"],
                kind="training",
                model=hf.SOUL_ID_ESTIMATE,
                prompt=f"Soul ID training: {body.get('name')}",
                usd=cost["usd"],
                submitted=app.now_iso(),
                status=result.get("status"),
            )
            log_event("training_started", id=result["id"], usd=cost["usd"])
            self.send_json(200, result)

        def on_forward(self) -> None:
            method = self.command
            body = self.body_json() if method in ("POST", "PUT") else None
            parts = urlsplit(self.path)
            path = parts.path + (f"?{parts.query}" if parts.query else "")
            result = app.higgsfield(method, path, body)
            # Keep the training status in the job log up to date when someone checks it.
            training_id = path.removeprefix("/v1/custom-references/")
            if method == "GET" and training_id != path and app.store.get_job(training_id):
                app.store.upsert_job(training_id, status=result.get("status"))
            self.send_json(200, result)

        def on_request_status(self, id: str) -> None:  # noqa: A002
            job = app.store.get_job(id)
            if job and job["status"] in hf.TERMINAL_STATUSES:
                self.send_json(200, job["result"] or {"request_id": id, "status": job["status"], "error": job["error"]})
                return
            result = app.higgsfield("GET", f"/requests/{id}/status")
            app.store.upsert_job(
                id,
                status=result.get("status"),
                error=result.get("error"),
                expires_at=result.get("expires_at"),
                result=result,
            )
            self.send_json(200, result)

        def on_webhook(self) -> None:
            raw = self.body_bytes()
            try:
                webhooks.verify(app.config.webhook_secret, dict(self.headers.items()), raw, app.clock())
                event = json.loads(raw)
            except (webhooks.WebhookError, json.JSONDecodeError) as error:
                log_event("webhook_rejected", reason=str(error))
                raise HttpError(400, "Webhook rejected.") from error
            # Correctly signed, but not what Higgsfield documents: refuse instead of storing garbage.
            if not isinstance(event, dict) or not event.get("request_id") or not event.get("status"):
                log_event("webhook_rejected", reason="missing request_id or status")
                raise HttpError(400, "Webhook rejected: missing request_id or status.")
            status = webhooks.envelope_to_status(event)
            app.store.upsert_job(
                status["request_id"],
                status=status["status"],
                error=status["error"],
                expires_at=status["expires_at"],
                result=status,
            )
            first_time = app.store.remember_webhook(self.headers["webhook-id"], now=app.clock())
            log_event(
                "webhook_accepted", request_id=status["request_id"], status=status["status"], duplicate=not first_time
            )
            self.send_json(200, {"ok": True} if first_time else {"ok": True, "duplicate": True})

        def on_job(self, id: str) -> None:  # noqa: A002
            try:
                wait = max(0.0, min(float(self.query.get("wait", 0)), MAX_WAIT))
            except ValueError as error:
                raise HttpError(400, "wait must be a number of seconds.") from error
            if app.store.get_job(id) is None:
                raise HttpError(404, f"Unknown job {id}.")
            if wait:
                app.store.wait_until(lambda: is_settled(app.store.get_job(id)), wait)
            self.send_json(200, job_record(app.store.get_job(id)))

        def on_jobs(self) -> None:
            status = self.query.get("status")
            since = self.query.get("since")
            if since:
                try:
                    date.fromisoformat(since)
                except ValueError as error:
                    raise HttpError(400, "since must be a date like 2026-10-31.") from error
            jobs = [
                job_record(job)
                for job in app.store.list_jobs()
                if (not status or job["status"] == status)
                # ISO dates compare correctly as text; jobs without a date are left out when filtering.
                and (not since or (job["submitted"] or "")[:10] >= since)
            ]
            self.send_json(200, {"jobs": jobs})

        def on_file(self, rel: str) -> None:
            base = app.files_dir.resolve()
            target = (base / rel).resolve()
            # is_relative_to: the resolved path must still be inside files/ (no "../" escapes).
            if not target.is_relative_to(base) or not target.is_file():
                raise HttpError(404, "Not found.")
            self.send_response(200)
            self.send_header("Content-Type", mimetypes.guess_type(target.name)[0] or "application/octet-stream")
            self.send_header("Content-Length", str(target.stat().st_size))
            self.end_headers()
            # Copy in 1 MB pieces: a large video never has to fit into memory at once.
            with target.open("rb") as file:
                shutil.copyfileobj(file, self.wfile, 1024 * 1024)

        def on_spend(self) -> None:
            try:
                days = int(self.query["days"]) if self.query.get("days") else None
            except ValueError as error:
                raise HttpError(400, "days must be a whole number.") from error
            since = hf.spend_since(days)
            self.send_json(200, {"since": since or "all", **hf.summarize_spend(app.store.list_jobs(), since)})

        def on_characters(self) -> None:
            self.send_json(200, {"characters": app.store.get_characters()})

        def on_character(self, name: str) -> None:
            self.send_json(200, {"name": name, "character": app.store.put_character(name, self.body_json())})

    return Handler


def hmac_equal(given: str, expected: str) -> bool:
    """Compares two secrets in constant time, so timing reveals nothing about the right token."""
    return hmac.compare_digest(given.encode(), expected.encode())


def hf_detail(error: CliError) -> str:
    """'HTTP 400: started' -> 'started' (the client adds its own 'HTTP 400: ' prefix again)."""
    text = str(error)
    if error.status_code and text.startswith(f"HTTP {error.status_code}: "):
        return text.removeprefix(f"HTTP {error.status_code}: ")
    return text


# --- Entry point ------------------------------------------------------------


def describe_address(address: tuple[str, int] | None) -> str:
    return f"{address[0]}:{address[1]}" if address else "same port"


def serve(config: ServerConfig) -> None:
    """Runs the service until Ctrl+C or SIGTERM (docker stop, Kubernetes)."""
    app = HfServer(config)
    app.start()
    log_event(
        "server_started",
        version=version(),
        api=describe_address(app.api_address),
        webhook=describe_address(app.webhook_address) if config.webhooks_enabled else "off",
        mode="webhooks" if config.webhooks_enabled else "polling",
        data_dir=str(config.data_dir),
    )
    stopped = threading.Event()
    # Docker and Kubernetes stop a container with SIGTERM; we treat it like Ctrl+C.
    signal.signal(signal.SIGTERM, lambda signum, frame: stopped.set())
    try:
        stopped.wait()
    except KeyboardInterrupt:
        pass
    app.stop()
    log_event("server_stopped")
