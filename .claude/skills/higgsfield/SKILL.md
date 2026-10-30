---
name: higgsfield
description: Generate images and videos via the Higgsfield API (text-to-image, image-to-video, text-to-video, video edit) with the CLI hf. Use when the user wants to generate an image, photo, video, clip or animation, mentions a model such as SOUL, Kling, Seedance, Wan, Hailuo, Recraft or Z-Image, asks about prices or available Higgsfield models, or names Higgsfield.
---

# Higgsfield: generating images and videos

Run all commands as `hf ...` – works from any directory. Downloads go to `./outputs` in the current
directory (different location: `--out <folder>`).
Every command prints **one line of JSON**; exit code ≠ 0 means error (`{"error": ...}`).
For problems with credentials or the network, run `hf doctor` first.

## Workflow

1. **Find a model** (from the local catalog, free):
   `hf models --op image2video --sort price --limit 5`
   Operations: `text2image`, `image_edit`, `image2video`, `text2video`, `video2video`. Alternatively `--output image|video`, `--search "kling 4.0"`.
2. **Check parameters:** `hf info <model_id>` → `required`, `params` (enum/default), `notes`, `price`.
   If `documented: false`, only the required fields from `price.payload` are known.
3. **State the cost:** `hf estimate <model_id> --prompt "..." [--image-url URL] [--param k=v ...]` (free).
   Ask for the price with the *real* parameters – duration and resolution change it a lot.
4. **Generate only after the user confirms**, with the confirmed price as a hard limit:
   - Image: `hf run <model_id> --prompt "..." --param aspect_ratio=16:9 --max-usd <confirmed price>`
   - Video: the same with `run_in_background` and `--timeout 900` – videos take 1–5 minutes.
   `--max-usd` estimates again and refuses to submit if the price is higher or unknown.
5. **Report the result:** only the paths from `files` and the cost (`usd`). No raw JSON.
   Next to every file there is a `.json` sidecar with prompt, parameters and cost.
   To show results to the user: `hf gallery` (opens an HTML overview of all results in the browser),
   or add `--open` to `run`/`download`.

For long jobs or after an interruption: `submit` → `wait <id>` → `download <id>`; the same `request_id` is not charged twice.

## Rules

- **Before every paid generation** state the model + estimated price and wait for confirmation. For series, state the total.
- Never read or print `.env`; `hf` loads the credentials itself.
- `--verbose` / `-v` only for debugging, not for normal output.
- Before image-to-video make sure `--image-url` is not empty (public HTTPS URL or an existing local file).
- If a model is missing from the catalog or the catalog is older than ~2 weeks: `hf catalog update`, then `hf prices` (both free).
- Spending so far: `hf spend` (all time) or `hf spend --days 1` (today). Estimates, not an invoice.
- To repeat or vary an earlier result, read its sidecar `.json` (prompt + `payload`).

## Tested models (as of 2026-10-30)

| Model | Purpose | Price | Result |
|---|---|---|---|
| `higgsfield-ai/soul/v2/standard` | text→image, default choice | $0.004 (720p) | PNG 1696×960 at 16:9, ~18 s |
| `kling-video/v2.5-turbo/standard/image-to-video` | image→video, cheap | $0.179 (5 s), $0.357 (10 s) | MP4 1276×720, 24 fps, ~60 s, no sound |
| `kling-video/v4.0/image-to-video` | image→video, newer, **with sound** | ~$0.134/s (720p), ~$0.20/s (1080p) | MP4 1296×732, 24 fps + AAC audio, ~3.5 min for 3 s |

More values: SOUL `resolution` only `720p`/`1080p`, `aspect_ratio` only `9:16, 16:9, 4:3, 3:4, 1:1, 2:3, 3:2`.
Kling 2.5 Turbo: `duration` 5 or 10, no `aspect_ratio` (takes the format of the image).
Kling 4.0 (undocumented): `duration` 3–30, `resolution` 720p/**1080p (default!)**, optional `end_image_url`.
Without these it costs $2 (10 s, 1080p) – always set duration and resolution explicitly. The API silently ignores unknown parameters.

## Chaining image → video

```bash
hf run higgsfield-ai/soul/v2/standard --prompt "..." --param aspect_ratio=16:9
hf status <request_id_of_the_image> --urls   # -> urls[0]
hf run kling-video/v2.5-turbo/standard/image-to-video --prompt "<motion>" --image-url "<urls[0]>"
```

Result URLs are valid for 7 days.

**Local files:** `--image-url` also accepts a path (`./photo.jpg`, `~/Pictures/x.png`) – the CLI uploads the
file automatically (jpg, png, webp, gif). Only get the URL: `hf upload <file>`.

**Cancel:** `hf cancel <request_id>` – only while the job is `queued`; a canceled job is refunded.

## Recurring characters (Soul ID)

Existing characters: `hf character list`. On a new machine the list starts empty – add known IDs with
`hf character add <name> <id>` (IDs are valid account-wide). Use with `--character <name>` on SOUL models of the matching
family (`model_version` v2 → `higgsfield-ai/soul/v2/standard`); sets `custom_reference_id` and strength 1.0
(change it: `--param custom_reference_strength=0.7`, never 0).

Creating a new character (tested workflow, ~$2.75 total):
1. Base portrait: `soul/v2/standard`, frontal, plain background, `batch_size=4`, the user picks one (~$0.02).
2. Further views **from this one image** via edit: `alibaba/qwen-image-3/edit --image-urls <base>`, $0.04 each.
   Prompt: view + "Same woman as in the reference image: identical face, … same hair, same top".
   Give the direction by **image side** ("nose points toward the RIGHT edge of the image"), not "her left" – otherwise you get the wrong side.
   Separate text-to-image runs produce different people – do not use them.
3. Look at the images, regenerate outliers. Proven set: profile L, 3/4 L, frontal, 3/4 R, profile R, frontal smiling.
4. `hf character create <name> <images...> --model-version v2` ($2.50, get confirmation first), then `character status <name>`.
   Training takes ~7 min (`not_ready` → `in_progress` → `completed`).

Learnings (test "Mila", 2026-10-30): the identity holds reliably in close-ups and medium shots, also with a new expression
and outfit. In wide shots the face is too small to be recognizable. The hairstyle from the training images (tied back)
partly wins against the prompt ("hair loose") – vary the desired hairstyles in the training images already.

## Prompt patterns (short)

- **Image:** subject → setting → light → style/camera. *"A quiet alpine lake at sunrise, low mist, editorial photography"*
- **Video from image:** describe only the **motion**, not the image again: camera + subject motion + atmosphere.
  *"slow camera push in over the misty lake, gentle ripples on the water, drifting fog"* (tested ✓)
- **Consistency across several clips:** same start image or the last frame of the previous clip, same style words, one camera move per clip.

More in [reference.md](reference.md): all commands, error messages, API details, detailed prompt patterns.

## Known errors

| Message | Cause / solution |
|---|---|
| `hf: command not found` | Not set up → ask the user to run `./setup.sh` in the `higgsfield-tools` repo |
| `HTTP 400: 'x' is a required property` | Required field missing → `hf info <id>` |
| `HTTP 400: resolution: '2K' is not one of [...]` | The error message lists the allowed values; the OpenAPI file in the docs is wrong here |
| `Network error ... ConnectTimeout` / download hangs | Firewall (Little Snitch) blocks Python → ask the user to allow the domain (`*.higgsfield.ai`, `*.cloudfront.net`, docs: `vercel-dns.com`) |
| `No catalog yet` | `hf catalog update` |
| `Estimated cost ... is above the limit` | Nothing was submitted. Tell the user the new price and ask again |
| `Price unknown (...)` with `--max-usd` | Token-priced model: explain `pricing` to the user; after confirmation run without `--max-usd` |
| Status `nsfw` / `failed` | Not charged. Tone down the prompt or try again |
