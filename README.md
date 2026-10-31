# hf – Higgsfield CLI

[![CI](https://github.com/elricco/higgsfield-tools/actions/workflows/ci.yml/badge.svg)](https://github.com/elricco/higgsfield-tools/actions/workflows/ci.yml)

🇩🇪 [Deutsche Version](README.de.md)

A lean Python CLI for generating images and videos via the [Higgsfield API](https://docs.higgsfield.ai).
Every command prints exactly one line of JSON. This keeps the context small when Claude uses the CLI through
the skill in `.claude/skills/higgsfield/`.

## Setup (including on a new machine)

Requirements: macOS or Linux, Python ≥ 3.10, Git. Optional: `ffmpeg` (extracting frames from videos).

```bash
git clone <repo-url> higgsfield-tools
cd higgsfield-tools
./setup.sh
```

`setup.sh` can be re-run at any time (e.g. after `git pull`) and does the following:

1. checks the Python version (different interpreter: `PYTHON=python3.12 ./setup.sh`),
2. creates `.venv` inside the repo and installs the package there in **editable** mode – nothing is installed globally,
3. links the command `~/.local/bin/hf` → `.venv/bin/hf` (must be on your `PATH`),
4. links the Claude skill `~/.claude/skills/higgsfield` → `.claude/skills/higgsfield`,
5. creates `.env` from `.env.example` if it is missing,
6. runs `hf doctor`.

Afterwards:

1. **Credentials:** put the key ID and secret from the [Higgsfield Console](https://console.higgsfield.ai) into
   `.env` (ignored by Git). Environment variables that are already set take precedence.
2. **Firewall** (e.g. if Little Snitch is running): allow the `.venv` Python outbound HTTPS to
   `*.higgsfield.ai` (API), `*.cloudfront.net` (results and uploads) and `docs.higgsfield.ai`
   (Vercel, only needed for `catalog update`).
3. **Check:** `hf doctor` – all checks should report `ok`.
4. **Refresh the catalog:** `hf catalog update && hf prices` (both free).
5. **Characters** (Soul ID) from `characters.json` are not in the repo. Run `hf character list` on the old
   machine and `hf character add <name> <id>` for each one on the new machine.

Uninstall: `rm ~/.local/bin/hf ~/.claude/skills/higgsfield` and delete the repo folder.

## Examples

`hf` works from any directory. Downloads go to `./outputs` in the current directory.

```bash
# Refresh catalog and prices (free)
hf catalog update
hf prices

# Cheapest image-to-video models, details for one model
hf models --op image2video --sort price --limit 5
hf info kling-video/v2.5-turbo/standard/image-to-video

# Cost estimate up front (free)
hf estimate higgsfield-ai/soul/v2/standard --prompt "A quiet alpine lake at sunrise"

# Generate an image in one step (never pay more than $0.01) …
hf run higgsfield-ai/soul/v2/standard --prompt "A quiet alpine lake at sunrise" --param aspect_ratio=16:9 --max-usd 0.01

# … or step by step
hf submit higgsfield-ai/soul/v2/standard --prompt "..."
hf wait <request_id>
hf download <request_id>

# Image → video, also from a local file
hf run kling-video/v2.5-turbo/standard/image-to-video --prompt "slow camera push in" --image-url ./photo.jpg

# Continue from the last frame of that clip, then join both (needs ffmpeg)
hf run kling-video/v2.5-turbo/standard/image-to-video --prompt "the camera keeps drifting" --from-last-frame <request_id>
hf concat <request_id_1> <request_id_2>

# Recurring character
hf character views base.png --details "dark curly hair tied back, green eyes" --max-usd 0.20
hf character create Mila base.png outputs/*.png --model-version v2 --max-usd 2.50
hf run higgsfield-ai/soul/v2/standard --character Mila --prompt "..."

# Several jobs in parallel from a JSON Lines file: check the total first, then run with a limit
hf batch examples/batch.jsonl --dry-run
hf batch examples/batch.jsonl --max-usd 0.02

# Prompt templates: {a|b|c} is replaced by one of the options
hf run higgsfield-ai/soul/v2/standard --prompt "a {red|blue} kite over a {green|grey} hill"   # one random variant
hf run ... --prompt "..." --variant-seed 7          # the same variant again with the same number
hf estimate ... --prompt "..." --variants all       # how many variants, total price
hf run ... --prompt "..." --variants 5 --max-usd 0.05   # 5 different variants in parallel

# What did today cost?
hf spend --days 1

# All results at a glance in the browser (search, filter by model, images/videos)
hf gallery
hf gallery --scan ~/some/folder   # add older result files to the job log first
hf gallery --scan outputs         # also imports jobs from client mode (read from their .json sidecars)
```

Next to every downloaded file there is a `.json` sidecar with prompt, parameters, model and estimated cost.

## Commands

| Command | Purpose |
| - | - |
| `catalog update` | model list (API `GET /models`) + parameters (docs) into `catalog.json` |
| `prices [filters]` | prices of all matching models via `estimate` into `prices.json` |
| `models [--output image\|video] [--op OP] [--search TEXT] [--sort price] [--limit N]` | list models |
| `info <model>` | parameters, required fields, price, own notes |
| `estimate <model> --prompt ...` | cost estimate in USD |
| `submit <model> --prompt ... [--image-url URL] [--param k=v] [--max-usd USD]` | submit a job |
| `status <request_id> [--urls]` | current status, optionally result URLs |
| `wait <request_id> [--timeout 600]` | wait until finished |
| `download <request_id> [--out outputs]` | save the result files |
| `run <model> --prompt ... [--max-usd USD]` | submit + wait + download |
| `spend [--days N]` | estimated costs from the job log, per model |
| `batch <file.jsonl> [--dry-run] [--max-usd TOTAL]` | run many jobs in parallel; `--dry-run` only shows the prices |
| `concat <clips...> [--out-file PATH]` | join clips (files or request_ids) into one video with ffmpeg, free |
| `gallery [--scan FOLDER] [--no-open]` | HTML overview of all results, opens in the browser |
| `cancel <request_id>` | cancel a job while it is still `queued` |
| `upload <file>` | upload a local file, prints its public URL |
| `character views\|create\|status\|list\|add` | create training views, train and manage Soul ID characters |
| `doctor` | check the setup: credentials, API, docs, catalog (client mode: the server) |
| `serve` | run as a service for agents (see [docs/server.md](docs/server.md)) |

Generation commands also accept `--image-urls A B ...` (edit models; local files are uploaded),
`--character <name>` (SOUL with a trained character) and `--from-last-frame <video or request_id>`
(continues a clip from its last frame, needs ffmpeg).

**Prompt templates:** `{a|b|c}` in `--prompt` stands for one of the options (braces without `|` stay plain text).
`estimate`, `submit` and `run` pick one variant at random and print it as `"choices"`; `--variant-seed N` makes the
choice repeatable. `run --variants N` creates N different variants (`all`: every combination) in parallel, and
`estimate --variants N|all` shows their number and total price. The job log and the `.json` sidecar keep the
`template` and the `choices` (in client mode the sidecar written by the server has only the finished prompt).
Lines in a `batch` file may use placeholders too; each line gets one random variant.

`run` and `download` rebuild the gallery after every download; `--open` also opens it.

`submit` and `run` ask for the price first (free) and log it. With `--max-usd`, or `HF_MAX_USD` in `.env`, they refuse
to submit a job whose estimate is higher or unknown. For `batch`, `run --variants` and `character views`, `--max-usd` is the limit for
all jobs together, while `HF_MAX_USD` still applies to each job.

All commands accept `--verbose` for full responses and stack traces.
Exit codes: `0` ok, `1` error or failed job, `2` missing credentials.

## Files

| File | Purpose |
| - | - |
| `hf.py` | the CLI |
| `catalog.py` | catalog logic (parsing the docs, merging, placeholders) |
| `gallery.py` | builds the HTML gallery from the job log |
| `server.py`, `store.py`, `webhooks.py` | `hf serve`: HTTP service, SQLite job store, webhook signature check |
| `Dockerfile`, `deploy/` | container image and examples for Docker Compose and Kubernetes |
| `examples/batch.jsonl` | example input for `hf batch` |
| `pyproject.toml` | package metadata, dependencies, the `hf` command |
| `setup.sh` | setup on this machine |
| `models.json` | own notes and default parameters for tested models (maintained by hand) |
| `catalog.json`, `prices.json` | generated by `catalog update` and `prices` |
| `characters.json` | character name → Soul ID (local, not in Git) |
| `.hf-history.json` | job log: model, prompt, estimated cost, status, file paths (local, not in Git) |
| `gallery.html` | generated overview of all results (local, not in Git) |
| `.claude/skills/higgsfield/` | skill for Claude Code |

The data files always live in the repo, regardless of the directory `hf` is called from.

## Running as a service

Besides running locally, `hf` can run as a service for agents (`hf serve`, usually in a container): it holds the
Higgsfield credentials, receives completion **webhooks** (with polling as a safety net), stores all results and the
job log centrally and enforces a cost limit for all agents. Agents use the same commands with
`HF_SERVER` and `HF_SERVER_TOKEN` set (client mode).

- Operator guide (modes, settings, webhooks, Docker Compose, Kubernetes, agents): [docs/server.md](docs/server.md)
- Live webhook test on a Mac with a temporary tunnel: [docs/webhook-live-test.md](docs/webhook-live-test.md)
- Acceptance checklist for a Kubernetes server: [docs/vps-acceptance.md](docs/vps-acceptance.md)

## Planning documents

Design specs and implementation plans live on the separate branch `planning`, which is never merged into
`main`. Locally it is mounted as the folder `planning/` (ignored by `main`):

```bash
git fetch origin planning:planning     # after a fresh clone, if the branch was pushed
git worktree add planning planning     # mount it as ./planning
```

Commit and push changes to it from inside `planning/` (`git push origin planning`).

## Tests

```bash
.venv/bin/python -m pytest -q     # tests
.venv/bin/ruff check .            # lint
.venv/bin/ruff format .           # format
```

The tests run without network access: HTTP is replaced by a `FakeSession`.
GitHub Actions runs all three on every push, with Python 3.10 and the newest Python.

## Notes

- `GET /models` is undocumented and may change. If it disappears, the docs remain as a source.
- Higgsfield keeps results for 7 days only, so always `download`.
- Homebrew Python is only ad-hoc signed, so Little Snitch may confuse it with a signed "Python" and show a
  warning. Check the path in the alert: it should point to the `.venv` Python.
- Possible later: webhooks instead of polling.
