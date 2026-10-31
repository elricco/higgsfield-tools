# hf as a service – design

Status: approved by the owner (2026-10-31) · Date: 2026-10-31

## Intent

Today `hf` is a CLI that runs on one machine: it submits a job to Higgsfield, polls until the job is done,
downloads the result and writes a job log, sidecar files and a gallery locally.

The owner is setting up a VPS (K3s) where **agents** (Hermes Agent, orchestrated by Paperclip) will
generate images and videos as part of larger tasks: a social media post written for an image, a caption
laid over an image, clips joined into a film and then cut, voiced and captioned. For that, results need to
be **collected centrally**, and there has to be one place that says where an original lives and what state
it is in. Agents should be told when a job is done instead of each of them polling Higgsfield.

So `hf` gets a second way to run: as a small **service** (`hf serve`) in a container. It holds the Higgsfield
credentials, receives completion **webhooks** from Higgsfield (with polling as a safety net), stores the
results and the job log centrally, and serves agents over a small HTTP API. The existing CLI talks to that
service when `HF_SERVER` is set, so agents keep using the same `hf` commands and the same skill.

It must stay **generally usable**: anyone can run their own instance with Docker, Docker Compose, K3s or
Kubernetes. Nothing may depend on the owner's specific infrastructure (Traefik, Twingate, Velero); those
appear only as documented examples.

## What "done" means for the first version

The owner's goal: **webhooks work on the VPS**. Concretely: an agent in the cluster runs
`hf run <model> --prompt ...` against the service; Higgsfield calls the service's public webhook path when
the job finishes; the service verifies the signature, downloads and stores the result, and the agent's
`hf run` returns with the stored file. If the webhook never arrives, the fallback poll still completes the job.

The VPS does not exist yet, so the first version is **done** when (decided with the owner):

1. Code, container image, deployment examples and documentation exist; all tests, lint and the image build
   pass in CI.
2. A **live webhook test on the owner's Mac** succeeds: `hf serve` runs locally, a temporary public HTTPS
   tunnel (e.g. a Cloudflare quick tunnel) makes only the webhook path reachable, a few cheap real jobs
   (under $0.02 in total, confirmed by the owner before running) complete via webhook, and one job completes
   via the fallback poll with webhooks switched off. The owner reveals the `whsec_` secret in the Higgsfield
   Console and puts it into `.env` themselves; the firewall (Little Snitch) must allow the tunnel tool.
   The live test happens in a separate session (planned for the day after this spec); everything up to it is
   prepared beforehand, including a step-by-step test script in the docs.
3. A **VPS acceptance checklist** exists in the docs, to be worked through once the VPS runs.

## Goals

1. **Three modes, one program**
   - *Local mode* (no `HF_SERVER`): exactly today's behavior. No regressions.
   - *Server mode* (`hf serve`): long-running service in a container.
   - *Client mode* (`HF_SERVER` + `HF_SERVER_TOKEN` set): the CLI sends everything to the service.
     Agents never need Higgsfield credentials.
2. **Webhooks configured by environment.** They are active only when **both** `HF_PUBLIC_URL` and
   `HF_WEBHOOK_SECRET` are set. Otherwise the service polls. Polling always stays on as a fallback
   (frequent without webhooks, rare with webhooks).
3. **Central job log and storage** on the server: every job with model, prompt, payload, estimated cost,
   status, timestamps and stored files; a `.json` sidecar next to each stored file (same content as today).
4. **Agents get notified** without polling Higgsfield themselves: the service offers a long-poll wait
   ("hold the request until the job is finished and stored, or until the timeout").
5. **Deployable anywhere**: one container image, configuration only via environment variables, data in
   one volume. Examples for Docker Compose and Kubernetes (works for K3s and K8s).
6. **Documented**: every mode, every variable, the webhook setup and the deployment examples are
   written down (operator guide plus README and skill updates). This is a standing requirement from the owner.

## Non-goals (first version)

- Several users or teams sharing one instance (accounts, per-user quotas, per-user results). One instance
  belongs to one person or one team; "for everyone" means everyone can run their own instance.
- Running more than one replica of the service.
- A web UI beyond what exists. The served gallery is a later item.
- An MCP server. The CLI plus skill stay the agent interface (keeps the agent's context small).

## Anti-goals (would make it a failure even if it technically works)

- **Anything public except the webhook path.** The API, files and health check are for the internal
  network only. The webhook path is the single public entry and only accepts correctly signed deliveries.
- **Unsigned webhooks being trusted.** Without `HF_WEBHOOK_SECRET`, webhooks are not used at all.
- **Higgsfield credentials leaving the server**, being printed, logged or committed. Clients authenticate
  to the service with their own token only.
- **Breaking local mode** or the one-line-JSON contract of the CLI.
- **A paid job being submitted twice** because of retries between client and service.
- **Results lost** because Higgsfield deletes them after 7 days: the service downloads every completed job.
- Heavy frameworks or a pile of dependencies that make the code hard to read for a learner.

## Constraints (from the existing project)

- Python ≥ 3.10, standard library plus `requests`. CI tests Python 3.10 and the newest Python.
- Code is a learning project: simple, readable, commented where a concept appears first, English.
- Every CLI command prints exactly one line of JSON; non-zero exit code on error; no stack traces
  without `--verbose`.
- Costs: estimate before submitting; `--max-usd` / `HF_MAX_USD` limits stay as they are.
- Tests mock all HTTP; no network in CI. `ruff check` and `ruff format --check` must pass.
- Secrets only from environment variables (or `.env` locally). Never ask for keys in chat.
- Pinned versions, no `latest`-only image tags (matches the owner's VPS rules).

## Decisions made with the owner

- One instance per person/team (not multi-tenant).
- Webhooks via environment: `HF_PUBLIC_URL` set (plus secret) → webhooks; otherwise polling.
  Polling remains the fallback in all cases (also recommended by the Higgsfield docs).
- Local mode keeps working as today; the service is an addition.
- Must work in Docker, K3s and K8s; nothing tied to the owner's infrastructure.
- Everything gets documented.
- The "later" items below are wanted eventually and are recorded here so they are not lost.
- Existing local data (`.hf-history.json`, `./outputs`) is **not imported** into a service; a new instance
  starts empty and local mode keeps its own data. *(Our call; an import command is listed under "Later".)*

## Higgsfield webhook facts (verified in the docs, 2026-10-31)

- Pass the endpoint as query parameter `hf_webhook=<url-encoded HTTPS URL>` on the generation submission.
  The URL must be HTTPS, resolve only to public IPs, contain no credentials, and answer within 10 seconds.
- Deliveries are `POST` with a JSON envelope `{request_id, status, error, expires_at, payload}`; `payload`
  holds `images` / `video` / `audio` (and for some operations other artifacts) like the status response.
  Sent for `completed`, `failed`, `nsfw`.
- Signed with **Standard Webhooks**: headers `webhook-id`, `webhook-timestamp`, `webhook-signature`
  (`v1,<base64>` entries, space-separated). Secret starts with `whsec_`; it is created in the Higgsfield
  Console (Settings) the first time it is revealed – until then deliveries are unsigned.
- Manual verification (no extra library needed): strip `whsec_`, base64-decode → HMAC key; sign
  `{webhook-id}.{webhook-timestamp}.{raw body}` with HMAC-SHA256; base64; constant-time compare with each
  `v1,` entry; reject timestamps more than 5 minutes off. Verify the **raw** body before parsing JSON.
- During secret rotation two signatures are sent for 24 hours.
- Retries on network errors and 5xx for up to 2 hours; 4xx is permanent. Duplicates are possible →
  deduplicate by `webhook-id`. Return 2xx only after the event is durably recorded.
- The legacy `X-Webhook-Secret-Key` header is being retired; do not use it.

## Design

### Modes and how the CLI finds the service

- `HF_SERVER` (e.g. `http://hf.agents.svc.cluster.local:8080`) and `HF_SERVER_TOKEN` switch the CLI into
  client mode. If `HF_SERVER` is set without a token, the CLI refuses with a clear error.
- `hf doctor` reports the mode. In client mode it checks the service (`/healthz` and an authenticated call)
  and shows the service's own status: webhook mode on/off, open jobs, storage path, version.

### Service API (sketch – exact paths are the builder's call)

All endpoints except the webhook path and `/healthz` require `Authorization: Bearer <HF_SERVER_TOKEN>`
(constant-time comparison).

The service should **mirror the Higgsfield API paths the CLI already uses**, so client mode is mostly
"different base URL and token" in `api_request`, and every command keeps working:

| Path (Higgsfield-compatible) | Service behavior |
|---|---|
| `POST /{model_id}` (generation) | Applies the server-side limit (`HF_MAX_USD` of the service), adds `hf_webhook` if webhooks are on, forwards with the server's credentials and the client's `Idempotency-Key`, records the job, returns Higgsfield's response. |
| `GET /requests/{id}/status` | Answers from the job store (updated by webhooks/polling); falls back to asking Higgsfield for unknown IDs. |
| `POST /requests/{id}/cancel`, `POST /estimate/{model}`, `POST /files/generate-upload-url`, `GET /models`, `GET /v1/custom-references/{id}` | Forwarded unchanged with the server's credentials. |
| `POST /v1/custom-references` (Soul ID training, ~$2.50) | Like a generation: the service's `HF_MAX_USD` applies (price via `/estimate/soul-id`), the training is recorded in the store and counted by `spend`. *(Our call: otherwise an agent could bypass the cap.)* |

Service-specific endpoints:

| Endpoint | Purpose |
|---|---|
| `GET /v1/jobs/{id}?wait=<seconds>` | Job record from the store; with `wait`, holds the request until the job is terminal **and** its files are stored, or the time is up. This is how agents get "notified". |
| `GET /v1/jobs?since=…&status=…` | List jobs (for `spend` and later for the gallery). |
| `GET /v1/files/<path>` | Download a stored result file or sidecar. |
| `GET /v1/spend?days=N` | Same output as local `hf spend`, computed from the central store. |
| `GET/PUT /v1/characters[/{name}]` | Central character registry (name → Soul ID, model version, status), replacing the local `characters.json` in client mode, so a fresh agent container knows all characters. *(Our call.)* |
| `POST /webhooks/higgsfield` | Public. Verify signature, deduplicate, record, answer 2xx quickly; downloading happens in the background. |
| `GET /healthz` | Liveness/readiness for Docker/K8s, no auth, no details. |

### CLI behavior in client mode

- `run` / `wait`: submit (via the mirrored path), then use `/v1/jobs/{id}?wait=` in a loop until done or the
  CLI's `--timeout`; then fetch the stored files from `/v1/files/...` into `./outputs` (with sidecars), so the
  agent gets local paths exactly like today.
- `download`: fetches from the service's storage, not from the Higgsfield CDN.
- `status`, `cancel`, `estimate`, `upload`, `models`, `info`, `prices`, `character …`, `batch`,
  `character views`, `--from-last-frame`, `concat` keep working (the last two run ffmpeg on the client).
- `spend` asks the service. The local `.hf-history.json` and `characters.json` are not used in client mode;
  `character create/add/list/status` and `--character <name>` use the central registry.
- `concat` and `--from-last-frame` accept request IDs; in client mode the video is fetched from the service.
- **Uploads**: the client still PUTs the file directly to the presigned Higgsfield storage URL (no proxying).
  *(Our call: agent containers already need outbound internet for their LLM providers and messengers;
  a proxied upload is a "later" item for locked-down setups. The docs state this outbound requirement.)*
- `gallery` in client mode: not in the first version (see "Later"); give a clear message.
- `--max-usd` still works on the client; additionally the **service enforces its own `HF_MAX_USD`** so an
  agent cannot bypass the owner's per-job cap.
- **Unknown prices** (token-priced models, failed estimate) while the service has a cap: the service
  **refuses** the job (decided with the owner). Only the operator can allow exceptions, via
  `HF_ALLOW_UNPRICED` – a comma-separated list of model IDs (or `*` for all). Agents cannot override this
  from a request; the error message tells them to ask the owner.

### Server internals

- **HTTP server**: Python standard library (`http.server.ThreadingHTTPServer`). Small load (a handful of
  agents), keeps the project dependency-free and readable. *(Our call; see "Left to the builder".)*
- **Job store**: SQLite file in the data directory (several threads/agents write concurrently; JSON is not
  safe for that). Tables at least for jobs (same fields as today's job log plus `stored`/`files`/
  `expires_at`/`last_polled`) and for processed webhook IDs (deduplication).
- **Storage**: files under the data directory, same file name scheme as today
  (`<date>_<model>_<request_id>[_n].<ext>`) plus sidecar `.json`. One volume holds database and files, so a
  volume backup (e.g. Velero) captures everything.
- **Background worker** (thread):
  - Downloads results of completed jobs (from webhook or poll), writes sidecars, marks the job stored.
    Retries; must finish well before `expires_at`.
  - **Polling**: without webhooks, polls open jobs with today's backoff idea (start a few seconds, cap
    ~10 s). With webhooks, a rare safety poll for jobs that are open longer than expected
    (`HF_FALLBACK_POLL_SECONDS`, default 300).
  - After a restart, resumes open jobs and pending downloads from the store.
- **Webhook URL** sent to Higgsfield: `HF_PUBLIC_URL` + `/webhooks/higgsfield`.
- **Startup checks** ("fails closed"): refuse to start without `HF_API_KEY`/`HF_API_SECRET`/`HF_SERVER_TOKEN`.
  If only one of `HF_PUBLIC_URL`/`HF_WEBHOOK_SECRET` is set, refuse to start with a clear message
  (half-configured webhooks are a mistake, not a mode).
- **Logging**: one JSON line per event to stdout (job submitted, webhook accepted/rejected, download done,
  poll result). Never log secrets, tokens or full signed URLs.

### Configuration (environment variables)

| Variable | Where | Meaning |
|---|---|---|
| `HF_API_KEY`, `HF_API_SECRET` | local, server | Higgsfield credentials (never needed by clients) |
| `HF_SERVER_TOKEN` | server, client | Shared token between agents and the service |
| `HF_SERVER` | client | Base URL of the service; switches the CLI into client mode |
| `HF_DATA_DIR` | server | Data directory (default `/data` in the container) |
| `HF_LISTEN` | server | Address and port (default `0.0.0.0:8080`) |
| `HF_PUBLIC_URL` | server | Public HTTPS base URL; with the secret, turns webhooks on |
| `HF_WEBHOOK_SECRET` | server | `whsec_…` signing secret from the Higgsfield Console |
| `HF_WEBHOOK_LISTEN` | server | Optional separate address/port only for the webhook path (e.g. `0.0.0.0:8081`). If set, the webhook is served **only** there, so exactly this port can be exposed publicly (tunnel, ingress). *(Our call, added while planning.)* |
| `HF_FALLBACK_POLL_SECONDS` | server | Safety poll interval when webhooks are on (default 300) |
| `HF_MAX_USD` | all | Per-job cost limit (on the server: enforced for all clients) |
| `HF_ALLOW_UNPRICED` | server | Model IDs (comma-separated, or `*`) that may run despite an unknown price while `HF_MAX_USD` is set |

### Packaging and deployment

- **Dockerfile**: slim Python base image with a pinned version, non-root user, the package installed,
  `hf serve` as the default command, `/data` as volume, a `HEALTHCHECK` on `/healthz`.
- **Image publishing**: GitHub Actions builds the image on every push (no push to a registry) and publishes
  to GHCR on version tags (`v*`), with the version as tag. amd64 and arm64.
- **Examples** in a `deploy/` folder:
  - Docker Compose (service + volume + `.env`).
  - Kubernetes manifests: Deployment (`replicas: 1`, `strategy: Recreate` because of SQLite), Service,
    PersistentVolumeClaim, Secret template, an example route that exposes **only** `/webhooks/higgsfield`
    publicly, and an example NetworkPolicy that allows the API only from agent pods.
  - Notes for Traefik (the owner's ingress) as one example, without making it a requirement.
- **Agents**: document how to install the `hf` CLI into an agent container (e.g. `pip install` from the Git
  repository at a tag) and how to give the agent the skill text (the skill is plain Markdown; Claude Code
  loads it as a skill, other agents can be given the same text).

### Documentation (required)

- `docs/server.md` – operator guide: modes, variables, quick start with Docker Compose, Kubernetes, webhook
  setup step by step (reveal secret in the console, set public URL, expose only the webhook path), how
  polling fallback behaves, backups, security notes, troubleshooting.
- README (English and German): short section on the three modes with a link to `docs/server.md`.
- Skill (`SKILL.md`, `reference.md`): client mode, `hf doctor` in client mode, what changes for agents
  (practically nothing), new errors.

### Testing

- Pure functions (signature verification, request routing decisions, store queries) with unit tests,
  including signature tests built from a known secret, a rotated-secret case (two signatures), an old
  timestamp, a modified body and a duplicate `webhook-id`.
- Service tests: start the real HTTP server on `127.0.0.1` with port 0 in a thread, Higgsfield replaced by
  the existing `FakeSession` approach; cover submit → webhook → stored → `wait` returns; webhook missing →
  fallback poll completes the job; auth required; webhook path rejects bad signatures.
- Client-mode tests for `run`, `download`, `spend` against a fake service.
- CI additionally builds the Docker image (no push) so a broken Dockerfile is caught.

## Later ("nice to have") – wanted, not in the first version

1. **Register derived results** (`hf register <file> --from <request_id or entry> --note "..."`):
   an agent that laid a caption over an image, joined clips, cut, voiced or subtitled a film can register its
   own output. Locally: a job-log entry; in client mode: upload to the service's storage. Each entry points to
   its source(s), forming a **provenance chain** (image → video → film → captioned film). Marked like
   `concat` today (`kind: local`, no cost) so `spend` ignores it. Optional for agents ("mode"): described in
   the skill as an optional step, never automatic.
2. **Gallery served by the service**: the existing gallery (filters for search, model, image/video) as a
   page of the service, reachable on the internal network (e.g. via Twingate); shows derived results with
   "derived from …" links. Locally it stays `gallery.html`.
3. **S3-compatible storage** as an alternative to the volume (for larger setups or offsite storage).
4. Daily or monthly budget cap enforced by the service (in addition to the per-job cap).
5. Retention policy for stored files (e.g. delete after N days unless registered as used).
6. More than one replica (would need a shared database instead of SQLite).
7. Import of an existing local job log and output folder into a service instance.
8. Upload proxy through the service for setups where agent containers have no outbound internet.

## Left to the builder

- Packaging so that `pip install` from the Git repository includes the data files (`models.json`,
  `catalog.json`, `prices.json`) the CLI reads; today `pyproject.toml` only lists the modules.
- Exact endpoint paths and response shapes (keep CLI output unchanged).
- Module layout (e.g. `server.py`, `store.py`, `webhooks.py` next to `hf.py`), database schema details,
  thread and lock handling.
- How the client forwards the `Idempotency-Key` and how the service avoids double submission on retries.
- Exact polling intervals within the limits above.
- Docker base image version and Kubernetes manifest details.
- Whether to use stdlib `http.server` or a very small framework – only if `http.server` turns out to be
  clearly insufficient; the default is the standard library.

## Follow-ups from the code review (2026-10-31) – all done

Small items the whole-branch review found; all fixed on `feature/hf-service` before merging.

- [x] `GET /v1/jobs?since=` from this spec is not implemented (only `?status=`).
- [x] A signed webhook without `request_id`, or whose JSON is not an object, gets `500` instead of a clean `400`.
- [x] The generation path is forwarded URL-decoded: `%3F` in a model ID could add query parameters (only for holders
  of the server token). Reject `?`/`#` in model IDs or forward the raw path.
- [x] `/v1/files/...` reads the whole file into memory; stream it (large videos vs. a 256 MiB pod limit).
- [x] The Dockerfile `HEALTHCHECK` hard-codes port 8080 (breaks if `HF_LISTEN` changes).
- [x] The Compose example publishes the webhook port 8081 on all interfaces; `127.0.0.1:8081` is tighter when a reverse
  proxy runs on the same host.
- [x] The client's `download_server_file` has no retries and leaves a partial file on error.
- [x] The `webhook_events` table grows forever (belongs to the retention item above).
