"""Modellkatalog: liest die Higgsfield-Doku und führt sie mit der /models-Liste zusammen.

Alle Funktionen hier sind "rein": Sie bekommen Text oder Daten und geben Daten
zurück, ohne Netzwerk oder Dateien. Das macht sie leicht testbar. Das Laden
und Speichern übernimmt hf.py.
"""

import json
import re

# Reguläre Ausdrücke (re) beschreiben Textmuster.
# Hier: "**Endpoint ID:** `kling-video/...`" – die Klammern ( ) fangen die ID ein.
ENDPOINT_ID_PATTERN = re.compile(r"\*\*Endpoint ID:\*\* `([^`]+)`")
# re.MULTILINE: ^ passt auf jeden Zeilenanfang, nicht nur auf den Textanfang.
SOURCE_PATTERN = re.compile(r"^Source: (\S+)", re.MULTILINE)
USAGE_NOTES_PATTERN = re.compile(r"## Usage notes\n(.*?)(?:\n## |\Z)", re.DOTALL)

# Platzhalter für Pflichtfelder, wenn wir nur den Preis abfragen wollen.
PLACEHOLDER_URLS = {
    "image": "https://example.com/input.jpg",
    "video": "https://example.com/input.mp4",
    "audio": "https://example.com/input.wav",
}


# --- Doku auslesen ----------------------------------------------------------


def split_pages(text: str) -> list[str]:
    """Teilt llms-full.txt in einzelne Doku-Seiten.

    Jede Seite beginnt mit "# Titel" und in der nächsten Zeile "Source: URL".
    (?=...) ist ein "Lookahead": Wir trennen VOR diesem Muster, ohne es zu verschlucken.
    """
    return re.split(r"\n(?=# [^\n]+\nSource: )", text)


def extract_schema(page: str) -> dict | None:
    """Holt das JSON-Schema aus dem Block "Complete JSON schema" einer Seite.

    Der Block steht in einem Markdown-Zitat, jede Zeile beginnt also mit "> ".
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
                break  # Ende des Codeblocks
            inside_code = True
            continue
        if inside_code:
            lines.append(line)
    try:
        return json.loads("\n".join(lines))
    except json.JSONDecodeError:
        return None


def extract_usage_notes(page: str) -> list[str]:
    """Liest die Aufzählung unter "## Usage notes"."""
    match = USAGE_NOTES_PATTERN.search(page)
    if not match:
        return []
    notes = []
    for line in match.group(1).splitlines():
        if line.startswith("* "):
            # Markdown maskiert Unterstriche als "\_" – das machen wir rückgängig.
            notes.append(line[2:].replace("\\_", "_").strip())
    return notes


def summarize_param(spec: dict) -> dict:
    """Reduziert eine Parameter-Beschreibung auf das Wesentliche."""
    keys = ("type", "default", "enum", "minimum", "maximum", "format")
    # Eine "dict comprehension": baut ein Dictionary in einer Zeile.
    summary = {key: spec[key] for key in keys if key in spec}
    if "anyOf" in spec:
        # z. B. "string oder null" – wir merken uns alle möglichen Typen.
        summary["type"] = [option["type"] for option in spec["anyOf"] if "type" in option]
    if spec.get("type") == "array" and isinstance(spec.get("items"), dict):
        summary["items"] = summarize_param(spec["items"])
    return summary


def parse_docs(text: str) -> dict:
    """Liest alle Modellseiten aus llms-full.txt.

    Ergebnis: {endpoint_id: {"docs_url", "required", "params", "notes"}}
    """
    models = {}
    for page in split_pages(text):
        endpoint = ENDPOINT_ID_PATTERN.search(page)
        if not endpoint:
            continue  # keine Modellseite (z. B. "Authentication")
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


# --- Katalog zusammenführen -------------------------------------------------


def build_catalog(api_items: list[dict], docs: dict) -> dict:
    """Führt die /models-Liste der API mit den Doku-Infos zusammen.

    "listed": True  -> steht in /models, also für dein Konto sichtbar.
    "documented": True -> die Doku kennt Parameter dafür.
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
            # **dict "entpackt" ein Dictionary in ein anderes.
            **docs.get(slug, {}),
        }
    for slug, doc in docs.items():
        if slug not in models:
            models[slug] = {"title": None, "output": None, "operations": [],
                            "listed": False, "documented": True, **doc}
    return models


def matches(model_id: str, model: dict, output: str | None = None,
            operation: str | None = None, search: str | None = None) -> bool:
    """Prüft, ob ein Modell zu den Filtern passt (None = Filter nicht gesetzt)."""
    if output and model.get("output") != output:
        return False
    if operation and operation not in model.get("operations", []):
        return False
    if search:
        haystack = f"{model_id} {model.get('title') or ''}".lower()
        # all(...) ist wahr, wenn jedes Suchwort vorkommt.
        if not all(word in haystack for word in search.lower().split()):
            return False
    return True


# --- Platzhalter für Preisabfragen ------------------------------------------


def placeholder_url(field_name: str) -> str:
    for kind in ("video", "audio"):
        if kind in field_name:
            return PLACEHOLDER_URLS[kind]
    return PLACEHOLDER_URLS["image"]


def placeholder_value(name: str, spec: dict):
    """Ein gültig aussehender Wert für ein Pflichtfeld."""
    if "default" in spec:
        return spec["default"]
    if spec.get("enum"):
        return spec["enum"][0]
    kind = spec.get("type")
    if name.endswith("_url") or spec.get("format") == "uri":
        return placeholder_url(name)
    if kind == "array" or name.endswith("_urls"):
        # "image_urls" -> eine Liste mit einer Platzhalter-URL
        return [placeholder_value(name.removesuffix("s"), spec.get("items", {}))]
    if kind in ("integer", "number"):
        return spec.get("minimum", 1)
    if kind == "boolean":
        return False
    return "test"


def placeholder_payload(model: dict) -> dict:
    """Minimaler Body für /estimate: nur Pflichtfelder mit Platzhaltern.

    Ohne Doku raten wir anhand der Operation (z. B. image2video -> image_url).
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
    """Liest aus "HTTP 400: 'video_urls' is a required property" den Feldnamen."""
    match = MISSING_FIELD_PATTERN.search(error_message)
    return match.group(1) if match else None


def price_sort_key(price: dict | None) -> float:
    """Sortierschlüssel: bekannte Preise aufsteigend, unbekannte ans Ende."""
    try:
        return float(price["usd"])
    except (TypeError, KeyError, ValueError):
        return float("inf")
