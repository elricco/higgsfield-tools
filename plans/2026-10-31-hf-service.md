# hf as a service – Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a server mode (`hf serve`) that receives Higgsfield webhooks (polling as fallback), stores results and the job log centrally, and a client mode in which the existing CLI talks to that server.

**Architecture:** Three new modules next to `hf.py`: `webhooks.py` (pure signature verification), `store.py` (SQLite job store with a change condition), `server.py` (config, routing, HTTP handlers on `http.server.ThreadingHTTPServer`, background worker). The server mirrors the Higgsfield paths the CLI already calls and reuses `hf.api_request` to forward them, so client mode in `hf.py` is mostly "other base URL + bearer token" plus two seams: `wait_for_job` and `fetch_outputs`.

**Tech Stack:** Python ≥ 3.10, standard library (`http.server`, `sqlite3`, `hmac`, `threading`) + `requests`; pytest; ruff; Docker; GitHub Actions; Kubernetes YAML.

**Spec:** `planning/specs/2026-10-31-hf-service-design.md` (branch `planning`, mounted at `./planning`). Read it before starting.

## Global Constraints

- Work on branch `feature/hf-service` in the main checkout (`/Users/timmmy/projects/playground/higgsfield-tools`), not on `planning`. Commit after every task; never push (the owner pushes).
- Python ≥ 3.10 (CI runs 3.10 and newest); standard library + `requests` only. No web framework.
- `ruff check .` and `ruff format --check .` must pass after every task; line length 120.
- Local mode (no `HF_SERVER`) must behave exactly as before: all existing tests stay green unchanged except where a task says otherwise.
- Every CLI command still prints exactly one JSON line; errors are `CliError`, no stack traces without `--verbose`.
- Never print or log `HF_API_KEY`, `HF_API_SECRET`, `HF_SERVER_TOKEN`, `HF_WEBHOOK_SECRET`.
- Code style: English, simple, comment a Python concept where it appears first in a file (learning project).
- Tests never use the network except `127.0.0.1`; Higgsfield is always the existing `FakeSession`/`FakeResponse` approach (`tests/test_hf.py`). Move those two classes to `tests/fakes.py` in Task 1 so all test files share them.
- No paid API calls during implementation. The live test is a separate, owner-confirmed session.
- Webhook facts (copy exactly): headers `webhook-id`, `webhook-timestamp`, `webhook-signature` (space-separated `v1,<base64>`); secret prefix `whsec_`; signed content `{id}.{timestamp}.{raw body}`, HMAC-SHA256, base64; tolerance 300 s; submit with query `hf_webhook=<url-encoded URL>`; envelope `{request_id, status, error, expires_at, payload}`.
- Env vars (names exact): `HF_API_KEY`, `HF_API_SECRET`, `HF_SERVER_TOKEN`, `HF_SERVER`, `HF_DATA_DIR` (default `/data`), `HF_LISTEN` (default `0.0.0.0:8080`), `HF_WEBHOOK_LISTEN` (optional), `HF_PUBLIC_URL`, `HF_WEBHOOK_SECRET`, `HF_FALLBACK_POLL_SECONDS` (default 300), `HF_MAX_USD`, `HF_ALLOW_UNPRICED` (comma list or `*`).
- Webhook path: `/webhooks/higgsfield`. Health: `/healthz`. Service API prefix: `/v1/`.

## Review Focus

1. **Webhook arrives before the submit response is recorded** (fast job) → the job must still end up `completed` and stored, not overwritten back to `queued`. Test in Task 5.
2. **Same webhook delivered twice / Higgsfield retries** → processed once, second answer still 2xx. Test in Task 5.
3. **Server restarts with open jobs and undownloaded results** → a new server on the same data dir resumes polling and downloading. Test in Task 5.
4. **Client `run` times out while the job is still running** → clear `CliError` naming the request ID; a later `hf wait <id>` / `hf download <id>` against the server succeeds. Test in Task 7.
5. **`/v1/files/../../hf.db` or other path tricks** → 404, never a file outside `files/`. Test in Task 6.

---

### Task 1: Shared building blocks in `hf.py`

Small refactors the server and client mode both need. No behavior change in local mode.

**Files:**
- Modify: `hf.py` (`CliError`, `api_request`, `save_outputs`, `cmd_spend`)
- Create: `tests/fakes.py` (move `FakeResponse`, `FakeSession` from `tests/test_hf.py`, import them there)
- Test: `tests/test_hf.py`

**Interfaces:**
- Produces:
  - `CliError(message: str, exit_code: int = 1, status_code: int | None = None)` – `api_request` sets `status_code` to the HTTP status on `>= 400` responses.
  - `api_base_url() -> str` – returns `BASE_URL` (Task 7 extends it); `api_request` uses it instead of `BASE_URL`.
  - `download_outputs(result: dict, job: dict, out_dir: Path) -> list[Path]` – download every output + write each sidecar; **no** job-log write. `save_outputs` = `download_outputs` + `update_job(...)` as today.
  - `summarize_spend(jobs: Iterable[dict], since: str | None) -> dict` – exactly today's `cmd_spend` output minus `since`; `cmd_spend` returns `{"since": since or "all", **summarize_spend(load_history().values(), since)}`.
  - `tests/fakes.py`: `FakeResponse`, `FakeSession` (unchanged code).

- [ ] **Step 1: Create branch** `git switch -c feature/hf-service`
- [ ] **Step 2: Move the fakes** into `tests/fakes.py`; `tests/test_hf.py` does `from fakes import FakeResponse, FakeSession` (add `tests/conftest.py` with `sys.path.insert(0, str(Path(__file__).parent))` if needed). Run `pytest -q` → 128 passed.
- [ ] **Step 3: Write failing tests**
  - `test_api_request_error_carries_status_code`: `FakeResponse(403, {"detail": "nope"})` → `CliError` with `.status_code == 403` and message `"HTTP 403: nope"`.
  - `test_summarize_spend_matches_cmd_spend`: reuse the history from `test_spend_adds_up_finished_jobs`; `summarize_spend(history.values(), today)` equals the old expected dict without `"since"`.
  - `test_download_outputs_does_not_touch_job_log`: like `test_save_outputs_writes_sidecar_and_logs_files` but asserts `hf.load_history() == {}` and returns `[Path]`.
- [ ] **Step 4: Run** `pytest tests/test_hf.py -q` → the three new tests FAIL.
- [ ] **Step 5: Implement** the four interface changes above.
- [ ] **Step 6: Run** `pytest -q && ruff check . && ruff format --check .` → all pass.
- [ ] **Step 7: Commit** `git commit -am "Prepare shared helpers for server and client mode"` (add `tests/fakes.py`, `tests/conftest.py`).

---

### Task 2: `webhooks.py` – signature verification

**Files:**
- Create: `webhooks.py`, `tests/test_webhooks.py`
- Modify: `pyproject.toml` (`py-modules` += `"webhooks"`)

**Interfaces:**
- Produces:
  - `class WebhookError(Exception)` – message says why (missing header, bad timestamp, too old, no matching signature).
  - `TOLERANCE_SECONDS = 300`
  - `sign(secret: str, webhook_id: str, timestamp: str, body: bytes) -> str` – returns `"v1,<base64>"`.
  - `verify(secret: str, headers: Mapping[str, str], body: bytes, now: float) -> None` – header names case-insensitive; raises `WebhookError`; uses `hmac.compare_digest`.
  - `envelope_to_status(event: dict) -> dict` – `{"request_id", "status", "error", "expires_at", **(event["payload"] or {})}` (same shape as `GET /requests/{id}/status`, so `hf.extract_outputs` works on it).

- [ ] **Step 1: Write failing tests** (secret `"whsec_" + base64(b"test-key-1234567890")`, body `b'{"request_id":"r1","status":"completed"}'`, timestamp `"1767225600"`, now `1767225600.0`):
  - `test_valid_signature_passes`
  - `test_any_of_two_signatures_passes` (header `"v1,<wrong> v1,<right>"`, rotation case)
  - `test_modified_body_fails`
  - `test_old_timestamp_fails` (now = ts + 301) and `test_future_timestamp_fails` (now = ts − 301)
  - `test_missing_headers_fail`
  - `test_headers_are_case_insensitive` (`"Webhook-Id"` etc.)
  - `test_secret_without_prefix_works` (raw base64 secret accepted too)
  - `test_envelope_to_status_flattens_payload` (payload `{"images": [{"url": "u"}]}` → `result["images"] == [{"url": "u"}]`; `payload: None` → no extra keys)
- [ ] **Step 2: Run** `pytest tests/test_webhooks.py -q` → FAIL (module missing).
- [ ] **Step 3: Implement** per the Global Constraints webhook facts (strip `whsec_`, base64-decode key).
- [ ] **Step 4: Run** `pytest -q && ruff check . && ruff format --check .` → pass.
- [ ] **Step 5: Commit** "Add Standard Webhooks signature verification".

---

### Task 3: `store.py` – SQLite job store

**Files:**
- Create: `store.py`, `tests/test_store.py`
- Modify: `pyproject.toml` (`py-modules` += `"store"`)

**Interfaces:**
- Produces `class JobStore`:
  - `__init__(self, path: Path)` – creates parent dir, tables, `PRAGMA journal_mode=WAL`; one connection with `check_same_thread=False` guarded by a `threading.Lock`; `self.changed = threading.Condition(self._lock)`.
  - `upsert_job(self, request_id: str, **fields) -> dict` – merge like `hf.update_job`; JSON-encode `payload`, `result`, `files`; `notify_all()` on `changed`. **Never downgrade a terminal status**: if the stored status is in `hf.TERMINAL_STATUSES` and the new one is not, keep the stored one.
  - `get_job(self, request_id: str) -> dict | None`
  - `find_by_idempotency_key(self, key: str) -> dict | None`
  - `list_jobs(self) -> list[dict]`
  - `open_jobs(self) -> list[dict]` – status not terminal (including `None`)
  - `jobs_to_store(self) -> list[dict]` – status `completed`, `stored` false, `download_error` null
  - `remember_webhook(self, webhook_id: str) -> bool` – `True` if new, `False` if already seen
  - `get_characters(self) -> dict[str, dict]`, `put_character(self, name: str, data: dict) -> dict` (merge, case-insensitive name match keeps the existing spelling)
  - `wait_until(self, predicate: Callable[[], bool], timeout: float) -> bool` – waits on `changed`.
- Job columns: `request_id` (PK), `model`, `prompt`, `payload`, `usd`, `kind`, `status`, `error`, `result`, `expires_at`, `submitted`, `last_polled` (REAL), `stored` (INTEGER 0/1), `files` (JSON list of paths relative to `files/`), `download_error`, `idempotency_key` (UNIQUE, nullable).

- [ ] **Step 1: Write failing tests** with `tmp_path / "hf.db"`:
  - `test_upsert_merges_fields_and_decodes_json`
  - `test_terminal_status_is_not_downgraded` (completed, then upsert status `queued` → still `completed`)
  - `test_open_jobs_and_jobs_to_store`
  - `test_remember_webhook_dedupes`
  - `test_characters_case_insensitive` (put `Mila`, put `mila` → one entry named `Mila`)
  - `test_data_survives_reopen` (new `JobStore` on same file sees the job)
  - `test_wait_until_wakes_on_change` (thread upserts after 0.1 s; `wait_until(..., 2)` returns `True` well before 2 s)
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Run** full suite + ruff → pass.
- [ ] **Step 5: Commit** "Add SQLite job store".

---

### Task 4: Server configuration and policies (pure functions in `server.py`)

**Files:**
- Create: `server.py`, `tests/test_server_config.py`
- Modify: `pyproject.toml` (`py-modules` += `"server"`)

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) class ServerConfig`: `api_key: str`, `api_secret: str`, `token: str`, `data_dir: Path`, `listen: tuple[str, int]`, `webhook_listen: tuple[str, int] | None`, `public_url: str | None`, `webhook_secret: str | None`, `fallback_poll_seconds: int`, `max_usd: float | None`, `allow_unpriced: frozenset[str]`; property `webhooks_enabled -> bool` (both `public_url` and `webhook_secret`); property `webhook_url -> str | None` (`public_url.rstrip("/") + "/webhooks/higgsfield"`).
  - `load_server_config(environ: Mapping[str, str]) -> ServerConfig` – raises `hf.CliError(..., exit_code=2)` when key/secret/token missing, when exactly one of `HF_PUBLIC_URL`/`HF_WEBHOOK_SECRET` is set ("set both or neither"), when `HF_PUBLIC_URL` is not `https://`, when `HF_SERVER` is set (server must not run in client mode), or on bad numbers / `host:port`.
  - `budget_refusal(cost: dict, max_usd: float | None, model_id: str, allow_unpriced: frozenset[str]) -> str | None` – `None` = allowed. Unknown price + cap + model not allowed → message that contains the model ID, `HF_ALLOW_UNPRICED` and "ask the owner". Price above cap → message like `hf.check_budget`.
  - `route(method: str, path: str) -> tuple[str, dict]` – handler name + params, path without query. Names: `healthz`, `webhook`, `status_v1`, `job` (`id`), `jobs`, `file` (`rel`), `spend`, `characters`, `character` (`name`), `request_status` (`id`), `generation` (`model`), `training`, `forward`, `not_found`. Rules: `GET /healthz`; `POST /webhooks/higgsfield`; `GET /v1/status`; `GET /v1/jobs/<id>`; `GET /v1/jobs`; `GET /v1/files/<rel>`; `GET /v1/spend`; `GET /v1/characters`; `PUT /v1/characters/<name>`; `GET /requests/<id>/status` → `request_status`; `POST /v1/custom-references` → `training`; `POST /requests/…`, `POST /estimate/…`, `POST /files/…`, `GET /models`, `GET /v1/custom-references/…` → `forward`; any other `POST /<a>/<b>…` → `generation`; everything else `not_found`.

- [ ] **Step 1: Write failing tests**: config happy path with all variables; each refusal case above; defaults (`/data`, `0.0.0.0:8080`, 300, empty allow set); `HF_ALLOW_UNPRICED="*"` and `"a/b, c/d"`; `budget_refusal` table (no cap → None; 0.4 ≤ 0.5 → None; 0.6 > 0.5 → message; unknown + cap → message; unknown + cap + model in allow set → None; unknown + cap + `*` → None); `route` table covering every rule, including `POST /higgsfield-ai/soul/v2/standard` → `("generation", {"model": "higgsfield-ai/soul/v2/standard"})` and `GET /v1/files/2026-10-31/x.png` → `("file", {"rel": "2026-10-31/x.png"})`.
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Run** suite + ruff → pass.
- [ ] **Step 5: Commit** "Add server configuration, budget policy and routing".

---

### Task 5: Server core – HTTP handlers, webhook, worker

**Files:**
- Modify: `server.py`
- Create: `tests/test_server.py`

**Interfaces:**
- Consumes: `ServerConfig`, `route`, `budget_refusal` (Task 4); `JobStore` (Task 3); `webhooks.verify`, `webhooks.envelope_to_status` (Task 2); `hf.api_request`, `hf.estimate_cost`, `hf.extract_outputs`, `hf.download_outputs`, `hf.summarize_spend`, `hf.SOUL_ID_ESTIMATE`, `CliError.status_code` (Task 1).
- Produces:
  - `class HfServer`: `__init__(self, config: ServerConfig, session_factory: Callable[[], requests.Session], clock: Callable[[], float] = time.time)`; `start(self) -> None` (HTTP server(s) + worker thread, non-blocking); `stop(self) -> None`; attributes `api_address: tuple[str, int]` and `webhook_address: tuple[str, int] | None` (real bound ports, so tests can use port 0); `run_worker_once(self) -> None` (one poll + download pass, used by tests and by the worker loop).
  - `log_event(event: str, **fields) -> None` – one JSON line to stdout; never secrets.
  - Job record returned by `/v1/jobs/<id>`: `{"request_id", "model", "prompt", "payload", "usd", "status", "error", "submitted", "expires_at", "stored": bool, "files": [rel, ...], "download_error"}`.
- Behavior (decisions the implementer cannot infer):
  - Auth: `Authorization: Bearer <token>` with `hmac.compare_digest`; missing/wrong → 401 `{"detail": "..."}`. No auth for `healthz` and `webhook`.
  - Errors are always JSON `{"detail": "..."}` so `hf.describe_error` shows them.
  - `generation`: read JSON body; estimate with `hf.estimate_cost` (failure → unknown price); `budget_refusal` → 403 with the message; same `Idempotency-Key` header already in store → return the stored submit response (200) without calling Higgsfield; else forward via `hf.api_request(session, "POST", f"/{model}" + (f"?hf_webhook={quote(webhook_url, safe='')}" if enabled), json_body=body, headers={"Idempotency-Key": key})`; record `model`, `prompt`, `payload`, `usd`, `submitted`, `status`, `idempotency_key`; return Higgsfield's JSON.
  - `training`: same budget logic with `hf.SOUL_ID_ESTIMATE`; record with `kind="training"`, `model="soul-id"`, `request_id` = returned `id`.
  - `forward`: `hf.api_request` with the server session; on `CliError` reply with `error.status_code or 502` and the detail.
  - `request_status`: known job with terminal status → stored `result` (or minimal status dict); otherwise forward to Higgsfield and upsert.
  - `webhook` (only if `webhooks_enabled`, else 404): read exactly `Content-Length` bytes; `verify` failure → 400 + `log_event("webhook_rejected", reason=...)`; `remember_webhook` false → 200 `{"duplicate": true}`; else upsert `status`, `error`, `expires_at`, `result=envelope_to_status(event)` → 200. No downloading inside the handler.
  - `job`: query `wait=<s>` (clamp 0–60) → `store.wait_until(job is terminal and (stored or status != "completed" or download_error), wait)`.
  - `file`: resolve `data_dir / "files" / rel`; must stay inside `files/` (`Path.resolve()` + `is_relative_to`) and exist, else 404; stream with correct `Content-Type` (`mimetypes`).
  - `spend`: `days` query → same `since` logic as `hf.cmd_spend`; `{"since": ..., **hf.summarize_spend(store.list_jobs(), since)}`.
  - `characters` / `character`: `get_characters` / `put_character` with the JSON body.
  - `status_v1`: `{"version", "webhooks": bool, "open_jobs": int, "data_dir": str}`; version from `importlib.metadata.version("higgsfield-tools")`, fallback `"dev"`.
  - Worker (`run_worker_once`): (a) for each `jobs_to_store()`: `hf.download_outputs(job["result"], job, data_dir / "files" / submitted-date)`, upsert `stored=1`, `files` (relative); on `CliError` upsert `download_error`. (b) polling: without webhooks poll every open job whose `last_polled` is older than 5 s; with webhooks only jobs submitted more than `fallback_poll_seconds` ago and last polled more than `fallback_poll_seconds` ago. Poll = `GET /requests/<id>/status` via `hf.api_request`, upsert `status`, `error`, `expires_at`, `result`, `last_polled`. The worker loop calls `run_worker_once` then `store.wait_until(lambda: False, 2)` until stopped.
  - With `webhook_listen` set, the webhook route is served only on that second server; the main server answers 404 for it.

- [ ] **Step 1: Write failing tests** (fixture starts `HfServer` on `127.0.0.1:0` in `tmp_path`, `session_factory` returns a `FakeSession`, `hf.download_file` monkeypatched to write bytes; requests via `requests` to `http://127.0.0.1:<port>`):
  - `test_healthz_without_token`, `test_api_requires_token` (401 without/with wrong token)
  - `test_generation_adds_webhook_url_and_records_job` (FakeSession sees URL ending `?hf_webhook=https%3A%2F%2Fhf.example.com%2Fwebhooks%2Fhiggsfield`)
  - `test_generation_without_webhooks_has_no_query`
  - `test_generation_refused_over_cap_and_unknown_price` (403, Higgsfield never called for submit)
  - `test_same_idempotency_key_submits_once`
  - `test_training_is_capped_and_recorded`
  - `test_forward_passes_status_code` (Higgsfield 400 → client sees 400 + detail)
  - `test_webhook_flow_stores_files_and_wait_returns` (submit → signed webhook → `run_worker_once` → `GET /v1/jobs/<id>?wait=5` has `stored: true`, file exists with sidecar)
  - `test_webhook_before_submit_recorded` (Review Focus 1)
  - `test_duplicate_webhook_is_ignored` (Review Focus 2)
  - `test_bad_signature_rejected`
  - `test_webhook_404_when_disabled`
  - `test_fallback_poll_completes_job_without_webhooks`
  - `test_restart_resumes_open_jobs` (Review Focus 3: stop, new `HfServer` on same dir, `run_worker_once` polls + stores)
  - `test_separate_webhook_port` (main port 404, webhook port 200)
  - `test_spend_and_characters_endpoints`
- [ ] **Step 2: Run** `pytest tests/test_server.py -q` → FAIL.
- [ ] **Step 3: Implement** `HfServer` and handlers (one `BaseHTTPRequestHandler` subclass created per server via a small factory function holding `self`).
- [ ] **Step 4: Run** suite + ruff → pass.
- [ ] **Step 5: Commit** "Add server: API, webhook receiver and background worker".

---

### Task 6: `hf serve` command and file safety

**Files:**
- Modify: `hf.py` (parser + `cmd_serve`), `server.py` (`serve(config) -> None` blocking: `HfServer(...).start()`, log `server_started` with mode, wait for `KeyboardInterrupt`/SIGTERM, `stop()`)
- Test: `tests/test_server.py`

**Interfaces:**
- Produces: `hf serve` (no options; everything from the environment). On config errors prints the usual one-line `{"error": ...}` with exit code 2. Startup log line: `{"event": "server_started", "api": "0.0.0.0:8080", "webhook": "0.0.0.0:8081" | "same port" | "off", "mode": "webhooks" | "polling"}`.
- SIGTERM handling via `signal.signal` so `docker stop` / Kubernetes shut down cleanly.

- [ ] **Step 1: Failing tests**: `test_serve_refuses_half_webhook_config` (`hf.main(["serve"])` with only `HF_PUBLIC_URL` → exit 2, error mentions both variable names); `test_file_path_traversal_is_404` (Review Focus 5: `GET /v1/files/../hf.db`, `GET /v1/files/%2e%2e/hf.db` → 404).
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Run** suite + ruff → pass.
- [ ] **Step 5: Manual check** (free, no Higgsfield call): `HF_API_KEY=x HF_API_SECRET=y HF_SERVER_TOKEN=t HF_DATA_DIR=$(mktemp -d) HF_LISTEN=127.0.0.1:8099 hf serve` in background; `curl -s 127.0.0.1:8099/healthz` → `{"ok": true}`; `curl -s -o /dev/null -w "%{http_code}" 127.0.0.1:8099/v1/status` → `401`; stop it.
- [ ] **Step 6: Commit** "Add hf serve".

---

### Task 7: Client mode in the CLI

**Files:**
- Modify: `hf.py`
- Create: `tests/test_client_mode.py` (real `HfServer` from Task 5 on port 0 + FakeSession for Higgsfield; the CLI under test talks real HTTP to it)

**Interfaces:**
- Consumes: `HfServer` (tests only), endpoints from Task 5.
- Produces in `hf.py`:
  - `client_settings() -> tuple[str, str] | None` – `(HF_SERVER without trailing "/", HF_SERVER_TOKEN)`; `HF_SERVER` without token → `CliError(..., exit_code=2)`.
  - `api_base_url()` returns the server URL in client mode; `make_session()` in client mode sets `Authorization: Bearer <token>` and does **not** require `HF_API_KEY`.
  - `wait_for_job(session, request_id: str, timeout: int) -> dict` – local: `poll_until_done` + `record_status`; client: loop `GET /v1/jobs/<id>?wait=min(30, remaining)` until terminal and (stored or not completed or `download_error`); timeout → `CliError` with request ID and "continue later with 'wait'". Returns a status-shaped dict (`request_id`, `status`, `error`).
  - `fetch_outputs(session, result: dict, job: dict, out_dir: Path) -> list[str]` – local: `save_outputs`; client: for each rel in the job record's `files`, download `/v1/files/<rel>` and `/v1/files/<rel-with-.json>` with the session into `out_dir`; `download_error` → `CliError`.
  - Use `wait_for_job` / `fetch_outputs` in `cmd_run`, `cmd_wait`, `cmd_download`, `run_many`.
  - Client mode: `update_job`/`record_status` are no-ops; `load_characters`/`save_character` use `GET /v1/characters` / `PUT /v1/characters/<name>`; `cmd_spend` returns `GET /v1/spend?days=N`; `cmd_gallery` raises `CliError("The gallery is not available in client mode yet.")`; `find_video(<request_id>)` downloads via `fetch_outputs` into `./outputs` when it is not a file.
  - `cmd_doctor` in client mode: `checks = {"mode": "client", "server": "ok" | error, "webhooks": "on" | "off (polling)"}` from `/healthz` + `/v1/status`; no Higgsfield credentials check.
  - Local mode `cmd_doctor` adds `"mode": "local"`.

- [ ] **Step 1: Failing tests**:
  - `test_server_without_token_is_refused`
  - `test_run_against_server` (webhooks off; FakeSession: estimate, submit, status completed; `hf.main(["run", "test/image", "--prompt", "a lake", "--out", out])` → exit 0, file + sidecar in `out`, no local history file written)
  - `test_wait_and_download_after_client_timeout` (Review Focus 4: `run --timeout 1` while job stays queued → exit 1, error contains request ID; then job completes; `hf.main(["download", id])` → files)
  - `test_spend_from_server`
  - `test_characters_are_central` (`character add` then `character list` in a "fresh" client → present)
  - `test_doctor_client_mode`
  - `test_server_cap_message_reaches_client` (server `HF_MAX_USD=0.001` → CLI error contains "above the limit")
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Run** full suite + ruff → pass (all 128 old tests still green).
- [ ] **Step 5: Commit** "Add client mode: the CLI talks to hf serve when HF_SERVER is set".

---

### Task 8: Container image, CI, deployment examples

**Files:**
- Create: `Dockerfile`, `.dockerignore`, `deploy/compose.yaml`, `deploy/.env.example`, `deploy/kubernetes/{secret.example.yaml,pvc.yaml,deployment.yaml,service.yaml,service-webhook.yaml,ingress-webhook.example.yaml,networkpolicy.example.yaml}`, `.github/workflows/release.yml`
- Modify: `.github/workflows/ci.yml` (add job `docker`: `docker build -t hf-test .` and a smoke run `docker run --rm hf-test hf --help`), `pyproject.toml` (`version = "0.2.0"`)

**Decisions:**
- Image: `python:3.14-slim` (check that the tag exists), non-root user `hf` uid 10001, `WORKDIR /app`, copy repo (respect `.dockerignore`: `.env*`, `outputs/`, `.venv/`, `planning/`, `.git/`, `*.json` history/characters, `gallery.html`), `pip install --no-cache-dir -e .` (editable keeps `models.json`/`catalog.json`/`prices.json` next to the code – same as `setup.sh`), `ENV HF_DATA_DIR=/data`, `VOLUME /data`, `EXPOSE 8080 8081`, `HEALTHCHECK` with `python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz')"`, `CMD ["hf", "serve"]`.
- Release: on tags `v*`, build `linux/amd64,linux/arm64` with Buildx + QEMU, push `ghcr.io/<owner>/higgsfield-tools:<version>` and `:<major>.<minor>`; no `latest`. Look up current major versions of `docker/setup-qemu-action`, `docker/setup-buildx-action`, `docker/login-action`, `docker/build-push-action`, `docker/metadata-action` via the GitHub API before writing them (they must run on Node 24).
- Kubernetes: `replicas: 1`, `strategy: Recreate`, PVC 5Gi `ReadWriteOnce`, `securityContext` non-root + `fsGroup: 10001`, liveness/readiness `httpGet /healthz`, env from Secret `hf-secrets`; `HF_WEBHOOK_LISTEN=0.0.0.0:8081`; Service `hf` (8080, cluster-internal), Service `hf-webhook` (8081) as the only target of the example Ingress (`pathType: Exact`, `/webhooks/higgsfield`); NetworkPolicy example: ingress to 8080 only from pods labeled `hf-client: "true"`, to 8081 from the ingress controller namespace (placeholder label). No namespace hard-coded (use `kubectl -n`).
- Compose: image with `build: ..` alternative commented, `env_file: .env`, ports `127.0.0.1:8080:8080` and `8081:8081`, named volume `hf-data:/data`, `restart: unless-stopped`.

- [ ] **Step 1: Write** the files above.
- [ ] **Step 2: Verify locally** (free): `docker build -t hf-test .` succeeds (if Docker is not installed locally, rely on CI and say so); `docker run --rm hf-test hf --help` prints usage; `docker compose -f deploy/compose.yaml config` validates; for each Kubernetes file `python -c "import yaml"`-free check: `kubectl apply --dry-run=client -f` if `kubectl` exists, else skip and note it.
- [ ] **Step 3: Run** `pytest -q && ruff check . && ruff format --check .` → pass.
- [ ] **Step 4: Commit** "Add container image, CI image build, release workflow and deployment examples".

---

### Task 9: Documentation

**Files:**
- Create: `docs/server.md`, `docs/webhook-live-test.md`, `docs/vps-acceptance.md`
- Modify: `README.md`, `README.de.md`, `.claude/skills/higgsfield/SKILL.md`, `.claude/skills/higgsfield/reference.md`, `.env.example` (commented server/client variables)

**Content decisions:**
- `docs/server.md` (operator guide): the three modes; the env var table from Global Constraints with defaults; "fails closed" rules; webhook setup (reveal `whsec_` secret in Console → Settings; set `HF_PUBLIC_URL` + `HF_WEBHOOK_SECRET`; expose only `HF_WEBHOOK_LISTEN` / `/webhooks/higgsfield`); polling fallback behavior and intervals; cost policy incl. `HF_ALLOW_UNPRICED`; Docker Compose quick start; Kubernetes (K3s/K8s) walkthrough using `deploy/kubernetes`; Traefik IngressRoute example (only as an example); NetworkPolicy notes; installing the CLI into an agent container (`git clone --depth 1 --branch vX.Y.Z … && pip install -e .`, set `HF_SERVER`/`HF_SERVER_TOKEN`, give the agent the skill text); outbound internet requirement for uploads; backups (one volume: `hf.db` + `files/`); troubleshooting table (401, 403 cap, webhook 400 signature, no webhooks arriving → check public URL/HTTPS/secret, downloads failing).
- `docs/webhook-live-test.md`: the owner-run script for tomorrow: prerequisites (secret in `.env`, `brew install cloudflared`, Little Snitch rule for `cloudflared` and the venv Python), start `hf serve` with `HF_LISTEN=127.0.0.1:8080`, `HF_WEBHOOK_LISTEN=127.0.0.1:8081`, `cloudflared tunnel --url http://127.0.0.1:8081` → copy the `https://…trycloudflare.com` URL into `HF_PUBLIC_URL`, restart; client shell with `HF_SERVER=http://127.0.0.1:8080`; test 1 `hf estimate` then `hf run higgsfield-ai/soul/v2/standard --prompt … --max-usd 0.01` (webhook path; check server log `webhook_accepted`); test 2 without `HF_PUBLIC_URL`/secret (polling); test 3 duplicate/invalid request with `curl` → 400; expected outputs; total cost ≤ $0.02; cleanup.
- `docs/vps-acceptance.md`: checklist for the K3s VPS (image pulled with pinned tag, Secret created, PVC bound, `/healthz` ready, webhook reachable from outside only on its path, NetworkPolicy blocks API from non-agent pods, Hermes container has `hf` + `HF_SERVER`, one real job completes via webhook, one via fallback poll, Velero backup includes the PVC).
- README (EN + DE): new section "Running as a service" (3 modes, link to `docs/server.md`); command table gets `serve`.
- Skill: client mode section (nothing changes for agents except `hf doctor` shows `mode: client`; the server may refuse by cost policy → tell the user, do not retry with other settings); new errors (`HTTP 401`, `HTTP 403 … above the limit`, `Price unknown … HF_ALLOW_UNPRICED`).

- [ ] **Step 1: Write** the docs.
- [ ] **Step 2: Check** every env var name, path and command in the docs against the code (`grep` each variable name in `server.py`/`hf.py`); every relative link resolves.
- [ ] **Step 3: Run** `pytest -q && ruff check . && ruff format --check .` → pass.
- [ ] **Step 4: Commit** "Document server mode, webhook live test and VPS acceptance".

---

## After the last task

- Whole-branch review against the spec, then report to the owner. The owner merges/pushes.
- Live webhook test: separate session with the owner, following `docs/webhook-live-test.md`; costs stated and confirmed before each paid call.
