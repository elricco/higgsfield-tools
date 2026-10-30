# Higgsfield skill: reference

Only read when needed. The short version is in [SKILL.md](SKILL.md).

## All commands

| Command | Output |
|---|---|
| `models [--output image\|video] [--op OP] [--search TEXT] [--sort name\|price] [--limit N] [--all]` | `{"count", "models": [{"id", "title", "ops", "usd", ...}]}` |
| `info <model_id>` | title, `required`, `params`, docs `notes`, `docs_url`, `price`, `own_notes` |
| `estimate <model_id> --prompt ... [--image-url] [--param k=v]` | `{"model", "usd"}` or additionally `"pricing"` (text for token-based billing) |
| `submit <model_id> --prompt ... [--max-usd USD] [...]` | `{"request_id", "status", "usd"}` |
| `status <request_id> [--urls]` | `{"request_id", "status", ["error"], ["urls"]}` |
| `cancel <request_id>` | `{"request_id", "status": "canceled"}`; error if the job is already running |
| `upload <file>` | `{"file", "url"}` – public URL of the uploaded file |
| `wait <request_id> [--timeout 600]` | final status; polling 2 s → 10 s |
| `download <request_id> [--out outputs]` | `{"request_id", "status", "files": [...]}` |
| `run <model_id> --prompt ... [--max-usd USD] [--timeout] [--out]` | like `download` plus `"usd"` |
| `gallery [--scan FOLDER] [--no-open]` | `{"file", "jobs", "missing_files", ["added_files"]}`; writes `gallery.html` and opens it |
| `spend [--days N]` | `{"since", "estimated_usd", "by_model", "jobs", "running", "not_charged", "price_unknown", "untracked"}` |
| `catalog update [--docs-file PATH]` | builds `catalog.json` from `GET /models` + docs |
| `prices [filters like models]` | writes `prices.json`, output only counters |
| `character create <name> <images...> [--model-version v2]` | start Soul ID training ($2.50) |
| `character status <name>` / `character list` | training status / locally known characters |
| `character add <name> <id>` | register an existing ID locally (checks it for free) |
| `doctor` | checks credentials, API, docs, catalog age |

Additional generation options: `--image-urls A B ...` (edit models), `--character <name>` (SOUL).

`--param` reads values as JSON: `duration=5` → number, `enhance_prompt=true` → bool, `aspect_ratio=16:9` → text.
Order in the body: defaults from `models.json` → `--prompt`/`--image-url` → `--param`.

`submit`/`run` always ask `estimate` first (free) and log the price. Limit: `--max-usd`, otherwise `HF_MAX_USD` from the
environment/`.env`. If the price is higher or unknown, nothing is submitted (exit code 1).

Exit codes: `0` ok, `1` error or job `failed`/`nsfw`/`canceled`, `2` credentials missing.

## Files

All files except `outputs/` live in the `higgsfield-tools` repo (where `~/.local/bin/hf` points to),
regardless of the current directory.

| File | Content | Maintained by |
|---|---|---|
| `characters.json` | character name → Soul ID (not in Git) | `character create/add` |
| `catalog.json` | all models: title, operations, parameters, docs notes | `catalog update` |
| `prices.json` | price per model with date and the placeholder body used | `prices` |
| `models.json` | own test notes + default parameters of tested models | by hand |
| `./outputs/` (current directory) | downloads: `<date>_<model>_<request_id>[_n].<extension>` plus a `.json` sidecar each (prompt, payload, usd) | automatic |
| `gallery.html` | overview of all results with filters; rebuilt after every `run`/`download` | automatic |
| `.hf-history.json` | job log: request_id → model, prompt, payload, usd, status, absolute file paths | automatic |

Prices in `prices.json` apply to placeholder inputs with default parameters. For real jobs always run `estimate` with the actual parameters.

## API facts (verified)

- Base URL `https://api.higgsfield.ai`, header `Authorization: Key <id>:<secret>`.
- `GET /models` is **undocumented** (returns the account's models, without schemas). May change.
- Status: `queued` → `in_progress` → `completed` | `failed` | `nsfw` | `canceled`. `failed`/`nsfw` are not charged.
- Output: images `images: [{url}]`, videos `video: {url}`, audio `audio`/`audios`. Files can be fetched for 7 days (`expires_at`).
- `submit` sends an `Idempotency-Key`; retries do not create duplicate jobs.
- Cancel only while `queued`: `POST /requests/<id>/cancel` (`hf cancel`), otherwise `400`.
- Upload: `POST /files/generate-upload-url {"content_type"}` → `PUT` to `upload_url` with `upload_headers`, without credentials → `public_url` (CloudFront). Types: jpeg, png, webp, gif, wav, mp4.
- Token-billed models (e.g. Seedance 2.5, Kling 4.0 reference/video-edit) return no amount from `estimate`, but a pricing description per second/resolution.
- The docs sometimes miss required fields in `required`; the API names them in the 400 error.

## Prompt patterns (detailed)

General experience, only the Kling example is tested here.

**Image**
- Structure: *subject, detail* · *place/setting* · *light/time of day* · *style/medium/lens*.
- Concrete terms instead of adjective chains: "35mm, shallow depth of field, overcast light".
- Format via `aspect_ratio`, not in the prompt.

**Video**
- Image-to-video: the image defines content and style. The prompt describes **what moves**.
- Camera terms: `slow push in`, `pull back`, `pan left/right`, `tilt up`, `orbit around`, `static shot`, `handheld`, `tracking shot`.
- One main movement per clip; for 5 s rather slow and calm.
- `negative_prompt` (where available) against artifacts: "warping, flicker, extra limbs".

**Consistency across several clips**
- Reuse the same style wording in all prompts.
- Start clip N+1 from the last frame of clip N (`ffmpeg -sseof -0.1 -i clip.mp4 -frames:v 1 last.png`, then `--image-url last.png`) or from the same start image.
- Use models with `first-last-frame` or `reference-to-video` when start and end are fixed.
