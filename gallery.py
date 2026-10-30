"""Gallery: turns the job log into a single HTML page.

Like catalog.py, all functions here are "pure": they get data and return
text. Reading the job log and writing the file is done by hf.py.
"""

import html
import json
import re
import shlex
from pathlib import Path

# File extension -> how the browser shows it.
MEDIA_KINDS = {
    ".png": "image",
    ".jpg": "image",
    ".jpeg": "image",
    ".webp": "image",
    ".gif": "image",
    ".mp4": "video",
    ".mov": "video",
    ".webm": "video",
    ".mp3": "audio",
    ".wav": "audio",
}
# A request_id looks like 01a18e05-d3c1-75ee-9ca0-781c72b85a9f.
REQUEST_ID_PATTERN = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


def media_kind(path: str) -> str | None:
    return MEDIA_KINDS.get(Path(path).suffix.lower())


def find_request_id(file_name: str) -> str | None:
    """Reads the request_id from a file name like 2026-10-30_model_<id>_2.png."""
    match = REQUEST_ID_PATTERN.search(file_name)
    return match.group(0) if match else None


def repeat_command(job: dict) -> str | None:
    """The hf command that would submit the same job again."""
    if not job.get("model") or not job.get("prompt"):
        return None
    # shlex.quote puts quotes around text with spaces, so the shell reads it as one argument.
    parts = ["hf", "run", job["model"], "--prompt", shlex.quote(job["prompt"])]
    for key, value in (job.get("payload") or {}).items():
        if key == "prompt":
            continue
        # json.dumps turns 5 into "5", True into "true" and lists into JSON – exactly what --param reads.
        text = value if isinstance(value, str) else json.dumps(value)
        parts += ["--param", shlex.quote(f"{key}={text}")]
    return " ".join(parts)


def format_usd(usd: float | None) -> str:
    return f"${usd:.3f}" if usd is not None else "–"


def render_media(path: str) -> str:
    uri = html.escape(Path(path).as_uri())
    kind = media_kind(path)
    if kind == "video":
        # "#t=0.1" makes the browser show an early frame as preview instead of a black box.
        return f'<video src="{uri}#t=0.1" controls preload="metadata" muted playsinline></video>'
    if kind == "audio":
        return f'<audio src="{uri}" controls preload="none"></audio>'
    return f'<a href="{uri}" target="_blank"><img src="{uri}" loading="lazy" alt=""></a>'


def render_card(item: dict) -> str:
    """One job as an HTML card. item comes from hf.gallery_items()."""
    # html.escape turns < > & " into harmless text, so a prompt cannot break the page.
    esc = html.escape
    kinds = sorted({media_kind(path) or "other" for path in item["files"]})
    params = [
        f"<span class=chip>{esc(key)}: {esc(str(value))}</span>"
        for key, value in (item.get("payload") or {}).items()
        if key != "prompt" and not str(value).startswith("http")
    ]
    chips = "<p class=chips>" + "".join(params) + "</p>" if params else ""
    command = repeat_command(item)
    repeat = "<p class=small>Repeat:</p><pre>" + esc(command) + "</pre>" if command else ""
    media = "".join(render_media(path) for path in item["files"])
    paths = "<br>".join(esc(path) for path in item["files"])
    model = item.get("model") or ""
    prompt = item.get("prompt") or ""
    search_text = f"{model} {prompt} {item['request_id']}".lower()
    return f"""
<article class=card data-model="{esc(model)}" data-kinds="{esc(" ".join(kinds))}" data-search="{esc(search_text)}">
  <div class=media>{media}</div>
  <div class=body>
    <p class=prompt>{esc(prompt or "no prompt recorded")}</p>
    <p class=meta><span>{esc(model or "unknown model")}</span><span>{format_usd(item.get("usd"))}</span><span>{esc(item["date"])}</span></p>
    {chips}
    <details><summary>Details</summary>
      <p class=small>request_id: <code>{esc(item["request_id"])}</code></p>
      {repeat}
      <p class=small>{paths}</p>
    </details>
  </div>
</article>"""


def render_gallery(items: list[dict], generated: str) -> str:
    """The complete page. items must already be sorted (newest first)."""
    models = sorted({item["model"] for item in items if item.get("model")})
    options = "".join(f'<option value="{html.escape(model)}">{html.escape(model)}</option>' for model in models)
    total = sum(item["usd"] for item in items if item.get("usd") is not None)
    cards = "".join(render_card(item) for item in items)
    return f"""<!doctype html>
<html lang=en>
<head>
<meta charset=utf-8>
<meta name=viewport content="width=device-width, initial-scale=1">
<title>hf gallery</title>
<style>
:root {{ --bg: #f6f5f2; --card: #ffffff; --text: #1d1d1f; --muted: #6b6b70; --line: #e2e0da; --chip: #efede8; }}
@media (prefers-color-scheme: dark) {{
  :root {{ --bg: #141416; --card: #1e1e21; --text: #ececee; --muted: #9a9aa2; --line: #2e2e33; --chip: #2a2a2e; }}
}}
* {{ box-sizing: border-box; }}
body {{ margin: 0; padding: 24px 16px; background: var(--bg); color: var(--text); font: 15px/1.45 -apple-system, system-ui, sans-serif; }}
header {{ max-width: 1400px; margin: 0 auto 20px; display: flex; flex-wrap: wrap; gap: 12px; align-items: center; }}
h1 {{ font-size: 20px; margin: 0 16px 0 0; }}
.summary {{ color: var(--muted); margin-right: auto; }}
input, select {{ font: inherit; padding: 6px 10px; border: 1px solid var(--line); border-radius: 8px; background: var(--card); color: var(--text); }}
main {{ max-width: 1400px; margin: 0 auto; display: grid; gap: 16px; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); }}
.card {{ background: var(--card); border: 1px solid var(--line); border-radius: 12px; overflow: hidden; display: flex; flex-direction: column; }}
.card[hidden] {{ display: none; }}
.media {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(120px, 1fr)); gap: 2px; background: var(--line); }}
.media img, .media video {{ display: block; width: 100%; height: 100%; max-height: 420px; object-fit: cover; background: #000; }}
.media audio {{ width: 100%; padding: 8px; background: var(--card); }}
.body {{ padding: 12px 14px; }}
.prompt {{ margin: 0 0 8px; }}
.meta {{ margin: 0; display: flex; flex-wrap: wrap; gap: 4px 12px; color: var(--muted); font-size: 13px; }}
.chips {{ margin: 8px 0 0; display: flex; flex-wrap: wrap; gap: 4px; }}
.chip {{ background: var(--chip); border-radius: 6px; padding: 1px 7px; font-size: 12px; }}
details {{ margin-top: 8px; font-size: 13px; color: var(--muted); }}
pre {{ white-space: pre-wrap; word-break: break-all; background: var(--chip); padding: 8px; border-radius: 6px; font-size: 12px; }}
.small {{ margin: 6px 0; word-break: break-all; }}
.empty {{ color: var(--muted); }}
</style>
</head>
<body>
<header>
  <h1>hf gallery</h1>
  <span class=summary>{len(items)} jobs · estimated {format_usd(total)} · generated {html.escape(generated)}</span>
  <input id=search type=search placeholder="Search prompt, model, id">
  <select id=model><option value="">All models</option>{options}</select>
  <select id=kind><option value="">Images and videos</option><option value=image>Images</option><option value=video>Videos</option></select>
</header>
<main>{cards or "<p class=empty>No results yet. Run a job with hf run.</p>"}</main>
<script>
// Hides every card that does not match the search box and the two dropdowns.
const controls = ["search", "model", "kind"].map((id) => document.getElementById(id));
function applyFilters() {{
  const [search, model, kind] = controls.map((control) => control.value.toLowerCase());
  for (const card of document.querySelectorAll(".card")) {{
    card.hidden = !(card.dataset.search.includes(search)
      && (!model || card.dataset.model.toLowerCase() === model)
      && (!kind || card.dataset.kinds.split(" ").includes(kind)));
  }}
}}
controls.forEach((control) => control.addEventListener("input", applyFilters));
</script>
</body>
</html>
"""
