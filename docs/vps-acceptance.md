# Acceptance checklist for a Kubernetes server (e.g. K3s on a VPS)

Work through this once the cluster runs. Commands assume the namespace `agents`; adjust as needed.
Paid steps are marked 💲 – confirm the estimate first.

## Deployment

- [ ] Image pinned to an exact version (`ghcr.io/<owner>/higgsfield-tools:X.Y.Z`), pulled successfully
      (for a private image: pull secret configured).
- [ ] Secret `hf-secrets` created from `deploy/kubernetes/secret.example.yaml` (filled copy not committed).
- [ ] `kubectl -n agents get pvc hf-data` → `Bound`.
- [ ] `kubectl -n agents rollout status deploy/hf` → successfully rolled out; pod `Ready` (health check `/healthz`).
- [ ] `kubectl -n agents logs deploy/hf | head -1` → `server_started` with `"mode": "webhooks"`.

## Network

- [ ] From outside: `curl -X POST https://<host>/webhooks/higgsfield` → `400` (unsigned request rejected).
- [ ] From outside: `https://<host>/v1/status` and `https://<host>/healthz` → not reachable / `404`.
- [ ] From outside: port 8080 not reachable (`nmap -Pn -p 8080 <server IP>`).
- [ ] NetworkPolicy: from a pod **without** label `hf-client: "true"`,
      `curl http://hf.agents.svc.cluster.local:8080/healthz` fails; from an agent pod **with** the label it works.

## Agents

- [ ] The agent container has `hf` (`hf --help`), `HF_SERVER` and `HF_SERVER_TOKEN`; `ffmpeg -version` works if
      agents should join clips (`hf concat`, `--from-last-frame`).
- [ ] `hf doctor` in the agent container → `"mode": "client"`, `"server": "ok"`, `"webhooks": "on"`.
- [ ] `hf character list` shows the known characters (add them once with `hf character add <name> <id>`).
- [ ] The agent has the skill text (`SKILL.md`, `reference.md`).

## Jobs

- [ ] 💲 `hf run higgsfield-ai/soul/v2/standard --prompt "..." --max-usd 0.01` from the agent completes;
      server log shows `webhook_accepted` and `stored`, **no** `polled` for that job.
- [ ] Fallback: temporarily set `HF_FALLBACK_POLL_SECONDS=30` and block the webhook route (or remove the ingress);
      💲 one job still completes via `polled` within about a minute. Restore afterwards.
- [ ] Cost policy: with `HF_MAX_USD=0.001`, a SOUL job is refused with `HTTP 403 ... above the limit`. Restore.
- [ ] `hf spend` from the agent shows the jobs above.

## Operations

- [ ] Backup (e.g. Velero) includes the `hf-data` volume; a restore test shows `hf.db` and `files/`.
- [ ] `kubectl -n agents rollout restart deploy/hf` while a job is running: after the restart the job still
      completes (open jobs are resumed from `hf.db`).
- [ ] Webhook secret rotation is documented for the owner (Console → Settings; old secret valid for 24 hours).
