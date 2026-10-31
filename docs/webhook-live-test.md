# Live webhook test on your Mac

A real test of the server with webhooks before the VPS exists. A temporary Cloudflare tunnel makes **only the
webhook port** of `hf serve` reachable from the internet.

**Cost:** two SOUL images at about $0.004 each – about **$0.01 in total** (estimates are free).
Every paid command below is marked with 💲; confirm the price shown by `hf estimate` before running it.

## 0. Preparation (once)

1. **Webhook secret:** Higgsfield Console → **Settings** → reveal the webhook signing secret (`whsec_...`).
   Add it to `.env` in the repository yourself:
   ```
   HF_WEBHOOK_SECRET=whsec_...
   ```
   (`.env` is not committed. Do not paste the secret into a chat.)
2. **Tunnel tool:** `brew install cloudflared`
3. **Firewall (Little Snitch):** allow `cloudflared` outgoing connections (to Cloudflare, port 443 and 7844).
   The `.venv` Python already has its rules for `*.higgsfield.ai` and `*.cloudfront.net`.
4. Three terminal windows, all in the repository folder.

## 1. Start the tunnel (terminal 2)

```bash
cloudflared tunnel --url http://127.0.0.1:8081
```

Copy the `https://<random-words>.trycloudflare.com` address it prints. Leave it running.

## 2. Start the server (terminal 1)

```bash
export HF_SERVER_TOKEN=$(openssl rand -hex 16); echo "$HF_SERVER_TOKEN"   # needed in terminal 3
export HF_DATA_DIR=/tmp/hf-serve-test
export HF_LISTEN=127.0.0.1:8080 HF_WEBHOOK_LISTEN=127.0.0.1:8081
export HF_PUBLIC_URL=https://<random-words>.trycloudflare.com
.venv/bin/hf serve
```

Expected first log line: `"event": "server_started" ... "webhook": "127.0.0.1:8081", "mode": "webhooks"`.

## 3. Check what is reachable from outside (terminal 3)

```bash
curl -s -o /dev/null -w "%{http_code}\n" -X POST https://<random-words>.trycloudflare.com/webhooks/higgsfield
# expected: 400 (unsigned request rejected); terminal 1 logs "webhook_rejected"
curl -s -o /dev/null -w "%{http_code}\n" https://<random-words>.trycloudflare.com/v1/status
# expected: 404 (the API is not on the webhook port)
```

## 4. Client (terminal 3)

```bash
export HF_SERVER=http://127.0.0.1:8080 HF_SERVER_TOKEN=<token from terminal 1>
hf doctor
# expected: "mode": "client", "server": "ok", "webhooks": "on"
```

## 5. Test 1 – completion by webhook

```bash
hf estimate higgsfield-ai/soul/v2/standard --prompt "A red kite over a green hill, overcast light"
# 💲 about $0.004:
hf run higgsfield-ai/soul/v2/standard --prompt "A red kite over a green hill, overcast light" --max-usd 0.01
```

Expected:
- terminal 1 logs `submitted`, then `webhook_accepted` (status `completed`), then `stored`
- **no** `polled` line for this job (the webhook was faster than the safety poll)
- terminal 3 prints one JSON line with `"status": "completed"` and a file in `./outputs`, plus its `.json` sidecar
- `ls /tmp/hf-serve-test/files/*/` shows the stored copy

## 6. Test 2 – polling fallback without webhooks

Stop the server in terminal 1 (Ctrl+C), then start it without webhooks. The empty values matter: they keep
`.env` from setting the secret again.

```bash
HF_PUBLIC_URL= HF_WEBHOOK_SECRET= .venv/bin/hf serve
```

Expected start line: `"mode": "polling"`. Then in terminal 3:

```bash
hf doctor                     # "webhooks": "off (polling)"
# 💲 about $0.004:
hf run higgsfield-ai/soul/v2/standard --prompt "A blue rowing boat on a calm lake" --max-usd 0.01
```

Expected: terminal 1 logs `submitted`, a few `polled` lines, then `stored`; terminal 3 gets the file.

## 7. Wrap up

```bash
hf spend          # (terminal 3, still in client mode) about $0.008 for 2 jobs
```

- Terminal 1: Ctrl+C. Terminal 2: Ctrl+C (the tunnel address stops working).
- `unset HF_SERVER HF_SERVER_TOKEN` in terminal 3 – otherwise `hf` stays in client mode in that window.
- Optional: `rm -rf /tmp/hf-serve-test`. `HF_WEBHOOK_SECRET` may stay in `.env`; it has no effect without
  `HF_PUBLIC_URL`.

## If something does not work

| Symptom | What to check |
| - | - |
| Server refuses to start: "Set both HF_PUBLIC_URL and HF_WEBHOOK_SECRET" | The secret is missing in `.env`, or `HF_PUBLIC_URL` is not exported. |
| Step 3 does not answer | The tunnel is not running or Little Snitch blocks `cloudflared`. |
| Test 1 finishes only after about 5 minutes with a `polled` line | The webhook did not arrive: check the tunnel address in `HF_PUBLIC_URL`; look for `webhook_rejected` (wrong secret). |
| `HTTP 401` on the client | Different `HF_SERVER_TOKEN` in terminals 1 and 3. |
