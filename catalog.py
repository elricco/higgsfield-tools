"""Model catalog: reads the Higgsfield docs and merges them with the /models list.

All functions here are "pure": they receive text or data and return data,
without network or files. That makes them easy to test. Loading
and saving is done by hf.py.
"""

import json
import re

# Regular expressions (re) describe text patterns.
# Here: "**Endpoint ID:** `kling-video/...`" – the parentheses ( ) capture the ID.
ENDPOINT_ID_PATTERN = re.compile(r"\*\*Endpoint ID:\*\* `([^`]+)`")
# re.MULTILINE: ^ matches at every line start, not only at the start of the text.
SOURCE_PATTERN = re.compile(r"^Source: (\S+)", re.MULTILINE)
USAGE_NOTES_PATTERN = re.compile(r"## Usage notes\n(.*?)(?:\n## |\Z)", re.DOTALL)

# Placeholders for required fields when we only want to query the price.
PLACEHOLDER_URLS = {
    "image": "https://example.com/input.jpg",
    "video": "https://example.com/input.mp4",
    "audio": "https://example.com/input.wav",
}


# --- Reading the docs -------------------------------------------------------


def split_pages(text: str) -> list[str]:
    """Splits llms-full.txt into individual docs pages.

    Every page starts with "# Title" and "Source: URL" on the next line.
    (?=...) is a "lookahead": we split BEFORE this pattern without consuming it.
    """
    return re.split(r"\n(?=# [^\n]+\nSource: )", text)


def extract_schema(page: str) -> dict | None:
    """Extracts the JSON schema from the "Complete JSON schema" block of a page.

    The block is inside a Markdown quote, so every line starts with "> ".
    """
    position = page.find("**Complete JSON schema**")
    if position == -1:
        return None
    lines = []
    inside_code = False
    for line in page[position:].splitlines()[1:]:
        line = line.removeprefix(">").removeprefix(" ")
        if line.startswith("```"):
            if inside_code:
                break  # end of the code block
            inside_code = True
            continue
        if inside_code:
            lines.append(line)
    try:
        return json.loads("\n".join(lines))
    except json.JSONDecodeError:
        return None


def extract_usage_notes(page: str) -> list[str]:
    """Reads the bullet list under "## Usage notes"."""
    match = USAGE_NOTES_PATTERN.search(page)
    if not match:
        return []
    notes = []
    for line in match.group(1).splitlines():
        if line.startswith("* "):
            # Markdown escapes underscores as "\_" – we undo that.
            notes.append(line[2:].replace("\\_", "_").strip())
    return notes


def summarize_param(spec: dict) -> dict:
    """Reduces a parameter description to the essentials."""
    keys = ("type", "default", "enum", "minimum", "maximum", "format")
    # A "dict comprehension": builds a dictionary in one line.
    summary = {key: spec[key] for key in keys if key in spec}
    if "anyOf" in spec:
        # e.g. "string or null" – we keep all possible types.
        summary["type"] = [option["type"] for option in spec["anyOf"] if "type" in option]
    if spec.get("type") == "array" and isinstance(spec.get("items"), dict):
        summary["items"] = summarize_param(spec["items"])
    return summary


def parse_docs(text: str) -> dict:
    """Reads all model pages from llms-full.txt.

    Result: {endpoint_id: {"docs_url", "required", "params", "notes"}}
    """
    models = {}
    for page in split_pages(text):
        endpoint = ENDPOINT_ID_PATTERN.search(page)
        if not endpoint:
            continue  # not a model page (e.g. "Authentication")
        schema = extract_schema(page) or {}
        source = SOURCE_PATTERN.search(page)
        models[endpoint.group(1)] = {
            "docs_url": source.group(1) if source else None,
            "required": schema.get("required", []),
            "params": {
                name: summarize_param(spec)
                for name, spec in schema.get("properties", {}).items()
            },
            "notes": extract_usage_notes(page),
        }
    return models


# --- Merging the catalog ----------------------------------------------------


def build_catalog(api_items: list[dict], docs: dict) -> dict:
    """Merges the API's /models list with the information from the docs.

    "listed": True  -> appears in /models, i.e. visible to your account.
    "documented": True -> the docs know its parameters.
    """
    models = {}
    for item in api_items:
        slug = item["slug"]
        models[slug] = {
            "title": item.get("title"),
            "output": item.get("output_type"),
            "operations": item.get("operation_type") or [],
            "description": item.get("description") or "",
            "listed": True,
            "documented": slug in docs,
            # **dict "unpacks" one dictionary into another.
            **docs.get(slug, {}),
        }
    for slug, doc in docs.items():
        if slug not in models:
            models[slug] = {"title": None, "output": None, "operations": [],
                            "listed": False, "documented": True, **doc}
    return models


def matches(model_id: str, model: dict, output: str | None = None,
            operation: str | None = None, search: str | None = None) -> bool:
    """Checks whether a model matches the filters (None = filter not set)."""
    if output and model.get("output") != output:
        return False
    if operation and operation not in model.get("operations", []):
        return False
    if search:
        haystack = f"{model_id} {model.get('title') or ''}".lower()
        # all(...) is true if every search word occurs.
        if not all(word in haystack for word in search.lower().split()):
            return False
    return True


# --- Placeholders for price queries -----------------------------------------


def placeholder_url(field_name: str) -> str:
    for kind in ("video", "audio"):
        if kind in field_name:
            return PLACEHOLDER_URLS[kind]
    return PLACEHOLDER_URLS["image"]


def placeholder_value(name: str, spec: dict):
    """A valid-looking value for a required field."""
    if "default" in spec:
        return spec["default"]
    if spec.get("enum"):
        return spec["enum"][0]
    kind = spec.get("type")
    if name.endswith("_url") or spec.get("format") == "uri":
        return placeholder_url(name)
    if kind == "array" or name.endswith("_urls"):
        # "image_urls" -> a list with one placeholder URL
        return [placeholder_value(name.removesuffix("s"), spec.get("items", {}))]
    if kind in ("integer", "number"):
        return spec.get("minimum", 1)
    if kind == "boolean":
        return False
    return "test"


def placeholder_payload(model: dict) -> dict:
    """Minimal body for /estimate: only required fields with placeholders.

    Without docs we guess based on the operation (e.g. image2video -> image_url).
    """
    if not model.get("params"):
        payload = {"prompt": "test"}
        operations = model.get("operations", [])
        if "image2video" in operations or "image_edit" in operations:
            payload["image_url"] = PLACEHOLDER_URLS["image"]
        if "video2video" in operations:
            payload["video_url"] = PLACEHOLDER_URLS["video"]
        return payload
    params = model["params"]
    return {name: placeholder_value(name, params.get(name, {})) for name in model.get("required", [])}


MISSING_FIELD_PATTERN = re.compile(r"'(\w+)' is a required property")


def missing_field(error_message: str) -> str | None:
    """Reads the field name from "HTTP 400: 'video_urls' is a required property"."""
    match = MISSING_FIELD_PATTERN.search(error_message)
    return match.group(1) if match else None


def price_sort_key(price: dict | None) -> float:
    """Sort key: known prices ascending, unknown ones at the end."""
    try:
        return float(price["usd"])
    except (TypeError, KeyError, ValueError):
        return float("inf")
