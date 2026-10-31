# Running hf as a service

`hf` can run in three ways. The commands and the skill are the same in all of them.

| Mode | How | Who talks to Higgsfield | Job log and results |
| - | - | - | - |
| **Local** | default (no `HF_SERVER`) | the CLI itself | `.hf-history.json`, `./outputs`, `gallery.html` on this machine |
| **Server** | `hf serve` (usually in a container) | the server, with its own credentials | SQLite file and `files/` in `HF_DATA_DIR` |
| **Client** | `HF_SERVER` + `HF_SERVER_TOKEN` set | the server, on behalf of the client | on the server; `run`/`download` copy files into `./outputs` |

The server is meant for agents (for example Hermes or Paperclip agents in a cluster): they generate images and
videos with the usual `hf run ...`, never need the Higgsfield keys, and all results end up in one place.
One instance belongs to one person or team – there are no user accounts.

## How a job runs through the server

1. The agent runs `hf run <model> --prompt ...` with `HF_SERVER` set.
2. The server estimates the price (free) and applies its cost policy (see below).
3. The server submits the job to Higgsfield – with a webhook address if webhooks are on.
4. Higgsfield reports completion **by webhook**; if that never arrives, the server's **polling** finds it.
5. The server downloads the result and its `.json` sidecar into `HF_DATA_DIR/files/<date>/`. A failed download is
   retried after 1, 2, 4 ... minutes (at most hourly) until Higgsfield deletes the files after 7 days.
6. The agent's `hf run` was waiting on the server (long-polling) and now copies the files into `./outputs`.

## Settings (environment variables)

| Variable | Used by | Default | Meaning |
| - | - | - | - |
| `HF_API_KEY`, `HF_API_SECRET` | local, server | – | Higgsfield credentials. Clients never need them. |
| `HF_SERVER_TOKEN` | server, client | – | Shared token between agents and the server (`openssl rand -hex 32`). |
| `HF_SERVER` | client | – | Base URL of the server, e.g. `http://hf.agents.svc.cluster.local:8080`. Switches the CLI into client mode. |
| `HF_DATA_DIR` | server | `/data` | Job log (`hf.db`) and stored results (`files/`). |
| `HF_LISTEN` | server | `0.0.0.0:8080` | Address of the API. Keep it internal. |
| `HF_WEBHOOK_LISTEN` | server | – | Optional separate address only for the webhook, e.g. `0.0.0.0:8081`. Then the webhook is served **only** there, and exactly this port can be made public. |
| `HF_PUBLIC_URL` | server | – | Public HTTPS base URL under which Higgsfield reaches the webhook. |
| `HF_WEBHOOK_SECRET` | server | – | Webhook signing secret (`whsec_...`) from the Higgsfield Console. |
| `HF_FALLBACK_POLL_SECONDS` | server | `300` | With webhooks on: jobs still open after this many seconds are polled as a safety net. |
| `HF_MAX_USD` | all | – | Cost limit per job. On the server it applies to every agent. |
| `HF_ALLOW_UNPRICED` | server | – | Models that may run although their price is unknown while `HF_MAX_USD` is set (comma-separated IDs, or `*`). |

Locally, `.env` in the repository is read as well; variables already set in the shell win. Do **not** put
`HF_SERVER` into `.env` unless that machine should always be a client.

### The server refuses to start ("fails closed") when

- `HF_API_KEY`, `HF_API_SECRET` or `HF_SERVER_TOKEN` is missing,
- only one of `HF_PUBLIC_URL` / `HF_WEBHOOK_SECRET` is set (set both for webhooks, or neither for polling),
- `HF_PUBLIC_URL` does not start with `https://`, or `HF_WEBHOOK_SECRET` is not a valid `whsec_` secret,
- `HF_SERVER` is set (a server must not run in client mode),
- a number or `host:port` value cannot be read.

## Webhooks or polling

- **Webhooks on** = `HF_PUBLIC_URL` **and** `HF_WEBHOOK_SECRET` are set. Every job is submitted with
  `hf_webhook=<HF_PUBLIC_URL>/webhooks/higgsfield`. Deliveries are checked with the Standard Webhooks signature
  (HMAC-SHA256, at most 5 minutes old); unsigned or wrongly signed requests – and signed ones without
`request_id`/`status` – get `400`. Repeated deliveries
  (Higgsfield retries for up to 2 hours) are recognized by their `webhook-id` and handled once.
  Polling stays on as a safety net for jobs open longer than `HF_FALLBACK_POLL_SECONDS`.
- **Webhooks off** (neither set): the server polls every open job about every 5 seconds. Nothing has to be public.

Without the secret there are no webhooks at all: an unsigned webhook could be faked by anyone who knows the URL.

### Setting up webhooks

1. Higgsfield Console → **Settings** → reveal the **webhook signing secret** (starts with `whsec_`).
   It is created the first time it is revealed; until then deliveries are not signed.
2. Give the server a public HTTPS address that leads to the webhook port – and only to it:
   with `HF_WEBHOOK_LISTEN=0.0.0.0:8081`, publish port 8081 (reverse proxy, ingress or tunnel) and nothing else.
   The URL must use HTTPS and resolve to public IP addresses only (Higgsfield rejects others).
3. Set `HF_PUBLIC_URL=https://<that host>` and `HF_WEBHOOK_SECRET=whsec_...`, restart the server.
4. Check: the start log line says `"mode": "webhooks"`; `hf doctor` on a client shows `"webhooks": "on"`;
   from outside, `curl -X POST https://<host>/webhooks/higgsfield` answers `400` (rejected: unsigned).

If the secret is rotated in the Console, the old one keeps working for 24 hours – update `HF_WEBHOOK_SECRET` and
restart within that time.

## Cost policy

- Every job is estimated first (free) and its estimated price is stored; `hf spend` shows the sum.
- With `HF_MAX_USD` set, the server refuses (`HTTP 403`) jobs that cost more – also Soul ID trainings (about $2.50).
- If the price is **unknown** (token-priced models such as some Seedance or Kling 4.0 variants, or a failed
  estimate) and `HF_MAX_USD` is set, the server refuses as well. Only the owner can allow such models with
  `HF_ALLOW_UNPRICED`. Agents cannot override this; the error tells them to ask the owner.
- `--max-usd` on the client still works in addition.

## Quick start with Docker Compose

```bash
cp deploy/.env.example deploy/.env      # fill in the values
docker compose -f deploy/compose.yaml up -d
docker compose -f deploy/compose.yaml logs -f
```

The example publishes the API on `127.0.0.1:8080` and the webhook on `127.0.0.1:8081` – both only on this
machine. For webhooks, an HTTPS reverse proxy or a tunnel on the same machine forwards to port 8081. If the proxy
runs elsewhere, publish `8081:8081` instead (the webhook only accepts signed deliveries either way).

A client on the same machine:

```bash
export HF_SERVER=http://127.0.0.1:8080 HF_SERVER_TOKEN=<the token from deploy/.env>
hf doctor
hf run higgsfield-ai/soul/v2/standard --prompt "A quiet alpine lake at sunrise" --max-usd 0.01
```

## Kubernetes (K3s, K8s)

The files in `deploy/kubernetes/` work in any namespace (`kubectl -n <namespace> apply -f ...`):

| File | What it does |
| - | - |
| `secret.example.yaml` | Template for the `hf-secrets` Secret – copy, fill in, never commit the filled copy |
| `pvc.yaml` | 5 GiB volume for `hf.db` and the results |
| `deployment.yaml` | One replica (`Recreate`, because SQLite), non-root, health checks on `/healthz`, webhook on port 8081 |
| `service.yaml` | `hf:8080` – the API for agents inside the cluster |
| `service-webhook.yaml` | `hf-webhook:8081` – the only Service an ingress should expose |
| `ingress-webhook.example.yaml` | Example Ingress that exposes only `/webhooks/higgsfield` over HTTPS |
| `networkpolicy.example.yaml` | API only from pods labeled `hf-client: "true"`, webhook only from the ingress namespace |

```bash
kubectl -n agents apply -f secret.yaml -f deploy/kubernetes/pvc.yaml \
  -f deploy/kubernetes/deployment.yaml -f deploy/kubernetes/service.yaml
# with webhooks:
kubectl -n agents apply -f deploy/kubernetes/service-webhook.yaml -f my-ingress.yaml
```

Pin the image version (`ghcr.io/elricco/higgsfield-tools:<version>`); there is deliberately no `latest` tag.
If you run your own fork, publish the image with the release workflow (push a tag `vX.Y.Z`) and use your own
`ghcr.io/<owner>/higgsfield-tools`.

### Example: Traefik IngressRoute (only if you use Traefik)

```yaml
apiVersion: traefik.io/v1alpha1
kind: IngressRoute
metadata:
  name: hf-webhook
spec:
  entryPoints: [websecure]
  routes:
    - match: Host(`hf.example.com`) && Path(`/webhooks/higgsfield`)
      kind: Rule
      services:
        - name: hf-webhook
          port: 8081
  tls:
    certResolver: letsencrypt
```

With a default-deny NetworkPolicy in the namespace, also allow the ingress controller to reach port 8081
(see `networkpolicy.example.yaml`).

## Agents as clients

An agent container needs the `hf` CLI, two variables and the skill text.

```dockerfile
RUN git clone --depth 1 --branch v0.3.0 https://github.com/elricco/higgsfield-tools /opt/higgsfield-tools \
 && python3 -m venv /opt/higgsfield-tools/.venv \
 && /opt/higgsfield-tools/.venv/bin/pip install -e /opt/higgsfield-tools \
 && ln -s /opt/higgsfield-tools/.venv/bin/hf /usr/local/bin/hf
```

- Environment: `HF_SERVER=http://hf.<namespace>.svc.cluster.local:8080` and `HF_SERVER_TOKEN` (from a Secret).
- Label the agent pod `hf-client: "true"` if you use the example NetworkPolicy.
- Skill: `.claude/skills/higgsfield/SKILL.md` (and `reference.md`) is plain Markdown. Claude Code loads it as a
  skill; other agents can be given the same text as their instructions or skill.
- **Outbound internet**: uploads of local input images go directly from the agent to the presigned upload
  address Higgsfield hands out, not through the server. Agent containers usually have outbound access
  anyway (for their LLM provider). A server-side upload proxy is a planned extension.
- Characters (Soul ID) are stored centrally on the server: a fresh agent container knows them immediately.

What does **not** work in client mode yet: `hf gallery` (planned as a page of the server), and results of
`hf concat` are not added to the central job log.

## Backups

Request bodies are limited to 1 MB and slow clients are disconnected after 30 seconds (this also protects the
public webhook port).

Everything the server knows lives in `HF_DATA_DIR`: `hf.db` (job log, webhook IDs of the last 7 days,
characters) and `files/`.
Back up that one volume (for example with Velero on Kubernetes, or a volume backup with Docker).
Higgsfield deletes its copies after 7 days – the server's copy is the one that lasts.

## Logs

The server writes one JSON line per event to stdout (`docker logs`, `kubectl logs`):
`server_started`, `submitted`, `refused`, `training_started`, `webhook_accepted`, `webhook_rejected`, `polled`,
`poll_failed`, `stored`, `download_failed` (with `attempts` and `gave_up`), `request_error`, `worker_error`,
`server_stopped`. Secrets and tokens never appear.

## Troubleshooting

| Symptom | Cause / fix |
| - | - |
| `HTTP 401: Missing or wrong HF_SERVER_TOKEN.` | Client and server use different tokens. |
| `HTTP 403: Estimated cost ... is above the limit` | The server's `HF_MAX_USD` blocks the job. Cheaper settings, or the owner raises the limit. |
| `HTTP 403: Price unknown for ... ask the owner` | Token-priced model while `HF_MAX_USD` is set. The owner can allow it with `HF_ALLOW_UNPRICED`. |
| Log shows `webhook_rejected` | Wrong `HF_WEBHOOK_SECRET`, or the server clock is more than 5 minutes off. |
| No `webhook_accepted` at all, jobs finish only after `HF_FALLBACK_POLL_SECONDS` | Higgsfield cannot reach `HF_PUBLIC_URL`: not HTTPS, not public, wrong path or port. Polling still completes the jobs. |
| `download_failed` with `"gave_up": false` | Temporary; the server tries again with growing pauses. |
| `download_failed` with `"gave_up": true` | The result expired at Higgsfield (7 days) before a download worked – check the firewall for the CDN. |
| `hf doctor` (client): `unreachable` | `HF_SERVER` wrong, server not running, or a NetworkPolicy blocks the agent pod. |
| Server exits right after start with an error | See "fails closed" above – the message names the variable. |
