"""hf – schlankes CLI für die Higgsfield-API.

Jeder Befehl gibt genau eine Zeile JSON aus, damit Claude (oder ein Skript)
die Ausgabe leicht lesen kann. Mit --verbose gibt es mehr Details.
"""

import argparse
import json
import mimetypes
import os
import random
import sys
import time
import traceback
import uuid
from datetime import date
from pathlib import Path
from urllib.parse import urlparse

import requests

# Unser eigenes Modul catalog.py (liegt im selben Ordner).
import catalog

# --- Konstanten -------------------------------------------------------------

BASE_URL = "https://api.higgsfield.ai"
# Die komplette Doku in einer Textdatei – enthält die Parameter aller Modelle.
DOCS_FULL_URL = "https://docs.higgsfield.ai/llms-full.txt"

# Status-Werte, bei denen ein Job fertig ist (egal ob Erfolg oder Fehler).
TERMINAL_STATUSES = {"completed", "failed", "nsfw", "canceled"}

# Path(__file__).parent ist der Ordner, in dem diese Datei liegt.
# So findet das CLI models.json auch, wenn man es aus einem anderen Ordner aufruft.
PROJECT_DIR = Path(__file__).parent
# Eigene Notizen und Standardparameter zu getesteten Modellen (von Hand gepflegt).
MODELS_FILE = PROJECT_DIR / "models.json"
# Automatisch erzeugt mit 'hf catalog update' bzw. 'hf prices'.
CATALOG_FILE = PROJECT_DIR / "catalog.json"
PRICES_FILE = PROJECT_DIR / "prices.json"
ENV_FILE = PROJECT_DIR / ".env"
# Merkt sich, welches Modell zu welcher request_id gehört (für Dateinamen).
HISTORY_FILE = PROJECT_DIR / ".hf-history.json"

# Sekunden, die ein einzelner HTTP-Aufruf höchstens dauern darf.
HTTP_TIMEOUT = 30
# Wie oft ein Aufruf bei vorübergehenden Fehlern insgesamt versucht wird.
MAX_ATTEMPTS = 4
# HTTP-Status-Codes, bei denen sich ein neuer Versuch lohnt.
RETRY_STATUS_CODES = {429, 500, 502, 503, 504}
# Polling laut Doku: mit 2 s beginnen, langsam bis 10 s steigern.
POLL_START_DELAY = 2.0
POLL_MAX_DELAY = 10.0
# Dateitypen, die der Upload laut Doku annimmt.
UPLOAD_CONTENT_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif", "audio/wav", "audio/x-wav", "video/mp4"}
# Uploads großer Videos dürfen länger dauern als normale API-Aufrufe.
UPLOAD_TIMEOUT = 300
# Dateiendungen, falls die URL keine verrät.
FALLBACK_EXTENSIONS = {"images": ".jpg", "video": ".mp4", "audio": ".mp3", "audios": ".mp3"}


# --- Fehler und Ausgabe -----------------------------------------------------


class CliError(Exception):
    """Ein erwarteter Fehler: wird als kurze Meldung ausgegeben, ohne Stacktrace."""

    def __init__(self, message: str, exit_code: int = 1):
        super().__init__(message)
        self.exit_code = exit_code


def print_json(data: dict) -> None:
    """Gibt ein Dictionary als eine Zeile JSON aus."""
    # ensure_ascii=False lässt Umlaute lesbar statt als ü.
    print(json.dumps(data, ensure_ascii=False))


# --- Konfiguration ----------------------------------------------------------


def load_dotenv(path: Path) -> None:
    """Liest KEY=VALUE-Zeilen aus einer .env-Datei in die Umgebungsvariablen.

    Bereits gesetzte Variablen werden nicht überschrieben, d. h. ein
    'export HF_API_KEY=...' im Terminal hat Vorrang vor der Datei.
    """
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        # Leerzeilen, Kommentare und Zeilen ohne '=' überspringen.
        if not line or line.startswith("#") or "=" not in line:
            continue
        # split("=", 1) trennt nur am ersten '=' – das Secret darf selbst '=' enthalten.
        name, value = line.split("=", 1)
        name = name.strip().removeprefix("export ").strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(name, value)


def load_credentials() -> tuple[str, str]:
    """Liest Key und Secret aus den Umgebungsvariablen.

    Die Werte werden nie ausgegeben – auch nicht in Fehlermeldungen.
    """
    key = os.environ.get("HF_API_KEY")
    secret = os.environ.get("HF_API_SECRET")
    if not key or not secret:
        raise CliError("HF_API_KEY und HF_API_SECRET müssen gesetzt sein.", exit_code=2)
    return key, secret


def load_models() -> dict:
    """Liest die lokale Modellliste aus models.json."""
    try:
        return json.loads(MODELS_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise CliError(f"{MODELS_FILE.name} nicht gefunden.")
    except json.JSONDecodeError as error:
        raise CliError(f"{MODELS_FILE.name} ist kein gültiges JSON: {error}")


def read_json_file(path: Path, default):
    """Liest eine JSON-Datei oder gibt default zurück, wenn es sie nicht gibt."""
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        raise CliError(f"{path.name} ist kein gültiges JSON.")


def write_json_file(path: Path, data) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def load_catalog() -> dict:
    data = read_json_file(CATALOG_FILE, None)
    if data is None:
        raise CliError("Noch kein Katalog vorhanden. Zuerst 'hf catalog update' ausführen.")
    return data


def load_history() -> dict:
    """Liest {request_id: model_id} aus der History-Datei (leer, falls es sie nicht gibt)."""
    if not HISTORY_FILE.exists():
        return {}
    try:
        return json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def remember_request(request_id: str, model_id: str) -> None:
    history = load_history()
    history[request_id] = model_id
    HISTORY_FILE.write_text(json.dumps(history, indent=2), encoding="utf-8")


# --- Parameter --------------------------------------------------------------


def parse_param(text: str) -> tuple[str, object]:
    """Zerlegt 'key=value' in ('key', value).

    Der Wert wird als JSON gelesen, wenn das klappt: 'duration=5' ergibt die
    Zahl 5, 'enhance_prompt=true' den Wahrheitswert True. Alles andere bleibt Text.
    """
    if "=" not in text:
        raise CliError(f"Parameter '{text}' hat nicht die Form key=value.")
    key, raw_value = text.split("=", 1)
    try:
        value = json.loads(raw_value)
    except json.JSONDecodeError:
        value = raw_value
    return key.strip(), value


def build_payload(args: argparse.Namespace) -> dict:
    """Baut den JSON-Body für eine Generierung.

    Reihenfolge (spätere gewinnen): Modell-Defaults aus models.json,
    dann --prompt/--image-url, dann --param.
    """
    model_info = load_models().get(args.model_id, {})
    # dict(...) erzeugt eine Kopie, damit wir die geladenen Defaults nicht verändern.
    payload = dict(model_info.get("defaults", {}))
    payload["prompt"] = args.prompt
    if args.image_url:
        payload["image_url"] = resolve_input_url(args.image_url)
    for text in args.param:
        key, value = parse_param(text)
        payload[key] = value
    return payload


# --- HTTP -------------------------------------------------------------------


def make_session() -> requests.Session:
    """Erzeugt eine Session, die bei jedem Aufruf den Auth-Header mitschickt."""
    key, secret = load_credentials()
    session = requests.Session()
    session.headers["Authorization"] = f"Key {key}:{secret}"
    return session


def retry_delay(attempt: int, retry_after: str | None = None) -> float:
    """Wartezeit vor dem nächsten Versuch: 1, 2, 4 ... Sekunden plus etwas Zufall.

    Schickt der Server einen Retry-After-Header, halten wir uns daran.
    """
    if retry_after and retry_after.isdigit():
        return float(retry_after)
    return 2 ** (attempt - 1) + random.uniform(0, 0.5)


def describe_error(response: requests.Response) -> str:
    """Macht aus einer Fehlerantwort eine kurze, lesbare Meldung."""
    try:
        detail = response.json().get("detail")
    except (ValueError, AttributeError):
        return response.text[:200] or response.reason
    if isinstance(detail, dict):
        # z. B. {"code": "account_queue_full", "message": "..."}
        return detail.get("message") or json.dumps(detail)
    if isinstance(detail, list):
        # Validierungsfehler: Liste mit {"loc": [...], "msg": "..."}
        parts = []
        for item in detail:
            location = ".".join(str(x) for x in item.get("loc", []) if x not in ("body", ""))
            message = str(item.get("msg", "")).lstrip(": ")
            parts.append(f"{location}: {message}" if location else message)
        return "; ".join(parts)
    # Bei fehlenden Pflichtfeldern beginnt der Text mit ": " (leerer Feldpfad).
    return str(detail).lstrip(": ")


def api_request(
    session: requests.Session,
    method: str,
    path: str,
    json_body: dict | None = None,
    headers: dict | None = None,
) -> dict:
    """Schickt einen API-Aufruf und gibt die JSON-Antwort zurück.

    - Timeout bei jedem Aufruf.
    - Neue Versuche bei Netzwerkfehlern, 429 und 5xx (mit Backoff).
    - Keine neuen Versuche bei anderen 4xx – da stimmt etwas an der Anfrage nicht.
    """
    url = path if path.startswith("http") else BASE_URL + path
    for attempt in range(1, MAX_ATTEMPTS + 1):
        is_last_attempt = attempt == MAX_ATTEMPTS
        try:
            response = session.request(method, url, json=json_body, headers=headers, timeout=HTTP_TIMEOUT)
        except (requests.ConnectionError, requests.Timeout) as error:
            if is_last_attempt:
                raise CliError(f"Netzwerkfehler nach {MAX_ATTEMPTS} Versuchen: {type(error).__name__}")
            time.sleep(retry_delay(attempt))
            continue

        if response.status_code in RETRY_STATUS_CODES and not is_last_attempt:
            time.sleep(retry_delay(attempt, response.headers.get("Retry-After")))
            continue

        if response.status_code >= 400:
            raise CliError(f"HTTP {response.status_code}: {describe_error(response)}")

        # Manche Antworten (z. B. cancel) haben keinen Inhalt.
        return response.json() if response.content else {}

    # Wird nie erreicht – die Schleife endet immer mit return oder raise.
    raise CliError("Unerwarteter Fehler in api_request.")


def download_file(url: str, target: Path) -> None:
    """Lädt eine Datei in Stücken herunter (spart Speicher bei großen Videos).

    Bewusst ohne Session: Die Zugangsdaten gehen nur an api.higgsfield.ai,
    nicht an das CDN, auf dem die Ergebnisse liegen.
    """
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            # 'with' sorgt dafür, dass die Verbindung danach sicher geschlossen wird.
            with requests.get(url, stream=True, timeout=HTTP_TIMEOUT) as response:
                if response.status_code in RETRY_STATUS_CODES and attempt < MAX_ATTEMPTS:
                    time.sleep(retry_delay(attempt))
                    continue
                if response.status_code >= 400:
                    raise CliError(f"Download fehlgeschlagen: HTTP {response.status_code}")
                with target.open("wb") as file:
                    for chunk in response.iter_content(chunk_size=1024 * 1024):
                        file.write(chunk)
                return
        except (requests.ConnectionError, requests.Timeout) as error:
            if attempt == MAX_ATTEMPTS:
                raise CliError(f"Download fehlgeschlagen: {type(error).__name__}")
            time.sleep(retry_delay(attempt))


# --- Upload -----------------------------------------------------------------


def guess_content_type(path: Path) -> str:
    """Ermittelt den Dateityp anhand der Endung, z. B. .jpg -> image/jpeg."""
    # guess_type liefert ein Tupel (typ, kodierung) – die Kodierung brauchen wir nicht.
    content_type, _ = mimetypes.guess_type(path.name)
    if content_type not in UPLOAD_CONTENT_TYPES:
        allowed = ", ".join(sorted(UPLOAD_CONTENT_TYPES))
        raise CliError(f"Dateityp von '{path.name}' wird nicht unterstützt ({content_type}). Erlaubt: {allowed}")
    return content_type


def upload_file(session: requests.Session, path: Path) -> str:
    """Lädt eine lokale Datei zu Higgsfield hoch und gibt die öffentliche URL zurück.

    Ablauf laut Doku:
    1. Bei der API eine vorsignierte Upload-URL anfordern.
    2. Die Datei per PUT direkt an den Speicher schicken – mit genau den
       Headern aus der Antwort und OHNE unsere Zugangsdaten.
    3. Die public_url als image_url/video_url in der Generierung verwenden.
    """
    if not path.is_file():
        raise CliError(f"Datei nicht gefunden: {path}")
    content_type = guess_content_type(path)
    upload = api_request(session, "POST", "/files/generate-upload-url",
                         json_body={"content_type": content_type})

    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            # requests.put statt session.put: kein Authorization-Header an den Speicher.
            response = requests.put(upload["upload_url"], data=path.read_bytes(),
                                    headers=upload.get("upload_headers", {}), timeout=UPLOAD_TIMEOUT)
        except (requests.ConnectionError, requests.Timeout) as error:
            if attempt == MAX_ATTEMPTS:
                raise CliError(f"Upload fehlgeschlagen: {type(error).__name__}")
            time.sleep(retry_delay(attempt))
            continue
        if response.status_code in RETRY_STATUS_CODES and attempt < MAX_ATTEMPTS:
            time.sleep(retry_delay(attempt))
            continue
        if response.status_code >= 400:
            raise CliError(f"Upload fehlgeschlagen: HTTP {response.status_code}")
        return upload["public_url"]
    raise CliError("Unerwarteter Fehler in upload_file.")


def resolve_input_url(value: str, session: requests.Session | None = None) -> str:
    """Gibt URLs unverändert zurück, lädt lokale Dateien vorher hoch."""
    if value.startswith(("http://", "https://")):
        return value
    # expanduser() macht aus "~/Bilder/x.jpg" einen vollständigen Pfad.
    return upload_file(session or make_session(), Path(value).expanduser())


# --- Ergebnisse -------------------------------------------------------------


def compact_status(result: dict) -> dict:
    """Reduziert eine Status-Antwort auf die wichtigen Felder."""
    output = {"request_id": result.get("request_id"), "status": result.get("status")}
    if result.get("error"):
        output["error"] = result["error"]
    elif result.get("status") in {"failed", "nsfw", "canceled"}:
        # Damit main() einen Exit-Code ungleich 0 liefert.
        output["error"] = f"Job beendet mit Status '{result['status']}'"
    return output


def extract_outputs(result: dict) -> list[tuple[str, str]]:
    """Sammelt alle Ergebnis-URLs als Liste von (feldname, url).

    Bilder kommen als Liste ("images"), Videos als einzelnes Objekt ("video").
    "audio" und "audios" können dieselbe Datei enthalten – Duplikate fallen weg.
    """
    outputs = []
    seen = set()
    for field in ("images", "video", "audio", "audios"):
        value = result.get(field)
        if not value:
            continue
        # Einzelnes Objekt in eine Liste packen, damit beide Fälle gleich laufen.
        items = value if isinstance(value, list) else [value]
        for item in items:
            url = item.get("url")
            if url and url not in seen:
                seen.add(url)
                outputs.append((field, url))
    return outputs


def output_filename(model_id: str, request_id: str, field: str, url: str,
                    index: int, total: int, day: date) -> str:
    """Baut den Dateinamen: <datum>_<modell>_<request_id>[_<nr>].<endung>"""
    model_slug = model_id.replace("/", "-")
    extension = Path(urlparse(url).path).suffix or FALLBACK_EXTENSIONS.get(field, "")
    number = f"_{index}" if total > 1 else ""
    return f"{day:%Y-%m-%d}_{model_slug}_{request_id}{number}{extension}"


def poll_until_done(session: requests.Session, request_id: str, timeout: int) -> dict:
    """Fragt den Status ab, bis der Job fertig ist oder die Zeit abläuft."""
    # time.monotonic() ist eine Uhr, die nie zurückspringt – ideal für Zeitmessung.
    deadline = time.monotonic() + timeout
    delay = POLL_START_DELAY
    while True:
        result = api_request(session, "GET", f"/requests/{request_id}/status")
        if result.get("status") in TERMINAL_STATUSES:
            return result
        if time.monotonic() + delay > deadline:
            raise CliError(
                f"Timeout nach {timeout} s, Job {request_id} läuft noch "
                f"(Status '{result.get('status')}'). Später mit 'wait' fortsetzen."
            )
        # Etwas Zufall (Jitter), damit viele Clients nicht im Gleichtakt abfragen.
        time.sleep(delay + random.uniform(0, 0.5))
        delay = min(delay * 1.5, POLL_MAX_DELAY)


def save_outputs(result: dict, model_id: str, out_dir: Path) -> list[str]:
    """Lädt alle Ergebnisse herunter und gibt die Dateipfade zurück."""
    outputs = extract_outputs(result)
    if not outputs:
        raise CliError("Die Antwort enthält keine Ergebnisdateien.")
    # parents=True legt auch Zwischenordner an, exist_ok=True ignoriert vorhandene.
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    # enumerate(..., start=1) liefert Nummer und Element: (1, a), (2, b), ...
    for index, (field, url) in enumerate(outputs, start=1):
        name = output_filename(model_id, result["request_id"], field, url,
                               index, len(outputs), date.today())
        target = out_dir / name
        download_file(url, target)
        paths.append(str(target))
    return paths


# --- Befehle ----------------------------------------------------------------
# Jede Funktion bekommt die geparsten Argumente (args) und gibt ein Dictionary
# zurück. main() kümmert sich um die Ausgabe.


def cmd_estimate(args: argparse.Namespace) -> dict:
    payload = build_payload(args)
    result = api_request(make_session(), "POST", f"/estimate/{args.model_id}", json_body=payload)
    output = {"model": args.model_id, "usd": result.get("usd")}
    # Token-basierte Modelle liefern statt einer Zahl eine Preisbeschreibung.
    if result.get("pricing_description"):
        output["pricing"] = result["pricing_description"]
    if args.verbose:
        output["payload"] = payload
        output["response"] = result
    return output


def submit_generation(session: requests.Session, args: argparse.Namespace) -> dict:
    """Schickt einen Job ab und merkt sich das Modell zur request_id."""
    payload = build_payload(args)
    # Ein Idempotency-Key pro Job: Wiederholt api_request den Aufruf nach einem
    # Timeout, erkennt der Server ihn und legt keinen zweiten (bezahlten) Job an.
    headers = {"Idempotency-Key": str(uuid.uuid4())}
    result = api_request(session, "POST", f"/{args.model_id}", json_body=payload, headers=headers)
    remember_request(result["request_id"], args.model_id)
    return result


def cmd_submit(args: argparse.Namespace) -> dict:
    result = submit_generation(make_session(), args)
    return result if args.verbose else compact_status(result)


def cmd_status(args: argparse.Namespace) -> dict:
    result = api_request(make_session(), "GET", f"/requests/{args.request_id}/status")
    if args.verbose:
        return result
    output = compact_status(result)
    if args.urls:
        # Öffentliche Ergebnis-URLs, z. B. als --image-url für Bild-zu-Video (7 Tage gültig).
        output["urls"] = [url for _, url in extract_outputs(result)]
    return output


def cmd_upload(args: argparse.Namespace) -> dict:
    return {"file": str(args.file), "url": upload_file(make_session(), args.file)}


def cmd_cancel(args: argparse.Namespace) -> dict:
    try:
        api_request(make_session(), "POST", f"/requests/{args.request_id}/cancel")
    except CliError as error:
        if str(error).startswith("HTTP 400"):
            # Laut Doku: 400 heißt, der Job läuft bereits und kann nicht mehr abgebrochen werden.
            raise CliError(f"Job {args.request_id} läuft bereits und kann nicht mehr abgebrochen werden.")
        raise
    return {"request_id": args.request_id, "status": "canceled"}


def cmd_wait(args: argparse.Namespace) -> dict:
    result = poll_until_done(make_session(), args.request_id, args.timeout)
    return result if args.verbose else compact_status(result)


def cmd_download(args: argparse.Namespace) -> dict:
    result = api_request(make_session(), "GET", f"/requests/{args.request_id}/status")
    if result.get("status") != "completed":
        output = compact_status(result)
        output.setdefault("error", f"Job ist noch nicht fertig (Status '{result.get('status')}').")
        return output
    model_id = load_history().get(args.request_id, "unknown")
    files = save_outputs(result, model_id, args.out)
    return {"request_id": args.request_id, "status": "completed", "files": files}


def cmd_run(args: argparse.Namespace) -> dict:
    session = make_session()
    submitted = submit_generation(session, args)
    result = poll_until_done(session, submitted["request_id"], args.timeout)
    if result.get("status") != "completed":
        return compact_status(result)
    files = save_outputs(result, args.model_id, args.out)
    return {"request_id": result["request_id"], "status": "completed", "files": files}


def filtered_models(args: argparse.Namespace) -> dict:
    """Alle Katalog-Modelle, die zu --output/--op/--search passen."""
    models = load_catalog()["models"]
    return {
        model_id: model
        for model_id, model in models.items()
        if (model.get("listed") or args.all)
        and catalog.matches(model_id, model, args.output, args.op, args.search)
    }


def price_summary(price: dict | None) -> dict:
    """Kurzform eines Preis-Eintrags für die Ausgabe."""
    if not price:
        return {}
    if price.get("usd") is not None:
        return {"usd": price["usd"]}
    if price.get("pricing"):
        return {"usd": None, "pricing": "variabel (siehe 'hf info')"}
    return {"usd": None, "price_error": price.get("error")}


def cmd_models(args: argparse.Namespace) -> dict:
    models = filtered_models(args)
    prices = read_json_file(PRICES_FILE, {})
    notes = load_models()
    rows = []
    for model_id, model in models.items():
        row = {"id": model_id, "title": model.get("title"), "ops": model.get("operations")}
        row.update(price_summary(prices.get(model_id)))
        if notes.get(model_id, {}).get("tested"):
            row["tested"] = True
        if not model.get("documented"):
            row["documented"] = False
        rows.append(row)
    if args.sort == "price":
        # sorted(..., key=...) sortiert nach dem Wert, den die key-Funktion liefert.
        rows.sort(key=lambda row: catalog.price_sort_key(prices.get(row["id"])))
    if args.limit:
        rows = rows[:args.limit]
    return {"count": len(rows), "models": rows}


def cmd_info(args: argparse.Namespace) -> dict:
    models = load_catalog()["models"]
    model = models.get(args.model_id)
    if model is None:
        raise CliError(f"Modell '{args.model_id}' ist nicht im Katalog. Tipp: 'hf models --search ...'")
    output = {"id": args.model_id, **model}
    if not args.verbose:
        output.pop("description", None)
    price = read_json_file(PRICES_FILE, {}).get(args.model_id)
    if price:
        output["price"] = price
    own_notes = load_models().get(args.model_id)
    if own_notes:
        output["own_notes"] = own_notes
    return output


def download_docs(docs_file: Path | None) -> str:
    """Liest llms-full.txt – aus einer lokalen Datei oder aus dem Netz."""
    if docs_file:
        return docs_file.read_text(encoding="utf-8")
    response = requests.get(DOCS_FULL_URL, timeout=HTTP_TIMEOUT)
    response.raise_for_status()
    return response.text


def cmd_catalog_update(args: argparse.Namespace) -> dict:
    result = api_request(make_session(), "GET", "/models")
    items = result.get("items", [])
    output = {}
    if result.get("total") and result["total"] > len(items):
        output["warning"] = f"API meldet {result['total']} Modelle, geliefert wurden {len(items)}."
    try:
        docs = catalog.parse_docs(download_docs(args.docs_file))
    except (requests.RequestException, OSError) as error:
        # Ohne Doku geht es trotzdem – nur ohne Parameter-Details.
        docs = {}
        output["docs_error"] = f"Doku nicht geladen ({type(error).__name__}), Katalog ohne Parameter."
    models = catalog.build_catalog(items, docs)
    write_json_file(CATALOG_FILE, {"updated": date.today().isoformat(), "models": models})
    listed = [m for m in models.values() if m["listed"]]
    return {
        "listed": len(listed),
        "documented": sum(1 for m in listed if m["documented"]),
        "only_in_docs": sorted(slug for slug, m in models.items() if not m["listed"]),
        "file": CATALOG_FILE.name,
        **output,
    }


def estimate_with_learning(session: requests.Session, model_id: str, model: dict,
                           payload: dict, max_tries: int = 5) -> dict:
    """Fragt den Preis ab und ergänzt fehlende Pflichtfelder automatisch.

    Die Doku listet nicht immer alle Pflichtfelder. Meldet die API
    "'video_urls' is a required property", fügen wir einen Platzhalter hinzu
    und versuchen es erneut. Das Ergebnis zeigt so auch die echten Pflichtfelder.
    """
    entry = {"checked": date.today().isoformat()}
    for _ in range(max_tries):  # "_" heißt: die Zählvariable brauchen wir nicht
        try:
            result = api_request(session, "POST", f"/estimate/{model_id}", json_body=payload)
        except CliError as error:
            field = catalog.missing_field(str(error))
            if field and field not in payload:
                spec = model.get("params", {}).get(field, {})
                payload = {**payload, field: catalog.placeholder_value(field, spec)}
                continue
            entry.update(usd=None, error=str(error), payload=payload)
            return entry
        entry.update(usd=result.get("usd"), payload=payload)
        if result.get("pricing_description"):
            entry["pricing"] = result["pricing_description"]
        return entry
    entry.update(usd=None, error=f"Nach {max_tries} Versuchen noch Pflichtfelder offen.", payload=payload)
    return entry


def cmd_prices(args: argparse.Namespace) -> dict:
    models = filtered_models(args)
    if not models:
        raise CliError("Kein Modell passt zu den Filtern.")
    session = make_session()
    notes = load_models()
    prices = read_json_file(PRICES_FILE, {})
    # Achtung: nicht "error" nennen – main() wertet diesen Schlüssel als Fehlschlag.
    counts = {"usd": 0, "variable": 0, "failed": 0}
    for model_id, model in models.items():
        # Platzhalter für Pflichtfelder plus unsere getesteten Standardwerte.
        payload = {**catalog.placeholder_payload(model), **notes.get(model_id, {}).get("defaults", {})}
        entry = estimate_with_learning(session, model_id, model, payload)
        prices[model_id] = entry
        if entry.get("usd") is not None:
            counts["usd"] += 1
        elif entry.get("pricing"):
            counts["variable"] += 1
        else:
            counts["failed"] += 1
    write_json_file(PRICES_FILE, prices)
    return {"checked": len(models), **counts, "file": PRICES_FILE.name}


# --- Argumente --------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hf", description="Higgsfield-API vom Terminal aus.")
    # Eltern-Parser für Optionen, die bei jedem Unterbefehl erlaubt sein sollen.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--verbose", "-v", action="store_true", help="mehr Ausgabe, Stacktraces bei Fehlern")

    sub = parser.add_subparsers(dest="command", required=True)

    # Optionen für alle Befehle, die einen Job erzeugen (estimate, submit, run).
    generation = argparse.ArgumentParser(add_help=False)
    generation.add_argument("model_id", help="z. B. higgsfield-ai/soul/standard")
    generation.add_argument("--prompt", required=True)
    generation.add_argument("--image-url", help="Eingabebild: URL oder lokale Datei (wird hochgeladen)")
    generation.add_argument(
        "--param", action="append", default=[], metavar="KEY=VALUE",
        help="weiterer Modellparameter, mehrfach erlaubt",
    )

    p = sub.add_parser("estimate", parents=[common, generation], help="Kosten vorab schätzen")
    p.set_defaults(func=cmd_estimate)

    p = sub.add_parser("submit", parents=[common, generation], help="Job abschicken")
    p.set_defaults(func=cmd_submit)

    p = sub.add_parser("status", parents=[common], help="Status eines Jobs")
    p.add_argument("request_id")
    p.add_argument("--urls", action="store_true", help="auch die Ergebnis-URLs ausgeben")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("cancel", parents=[common], help="Job abbrechen (nur solange 'queued')")
    p.add_argument("request_id")
    p.set_defaults(func=cmd_cancel)

    p = sub.add_parser("upload", parents=[common], help="lokale Datei hochladen, gibt öffentliche URL aus")
    p.add_argument("file", type=Path)
    p.set_defaults(func=cmd_upload)

    p = sub.add_parser("wait", parents=[common], help="warten, bis ein Job fertig ist")
    p.add_argument("request_id")
    p.add_argument("--timeout", type=int, default=600, help="Sekunden (Standard: 600)")
    p.set_defaults(func=cmd_wait)

    p = sub.add_parser("download", parents=[common], help="Ergebnisdateien herunterladen")
    p.add_argument("request_id")
    p.add_argument("--out", type=Path, default=Path("outputs"))
    p.set_defaults(func=cmd_download)

    p = sub.add_parser("run", parents=[common, generation], help="submit + wait + download")
    p.add_argument("--timeout", type=int, default=600)
    p.add_argument("--out", type=Path, default=Path("outputs"))
    p.set_defaults(func=cmd_run)

    # Filter für models und prices.
    filters = argparse.ArgumentParser(add_help=False)
    filters.add_argument("--output", choices=["image", "video"], help="nur Bild- oder Videomodelle")
    filters.add_argument("--op", help="Operation, z. B. text2image, image2video, text2video, video2video, image_edit")
    filters.add_argument("--search", help="Suchwörter in ID/Titel, z. B. 'kling 4.0'")
    filters.add_argument("--all", action="store_true", help="auch Modelle, die nur in der Doku stehen")

    p = sub.add_parser("models", parents=[common, filters], help="Modelle aus dem Katalog auflisten")
    p.add_argument("--sort", choices=["name", "price"], default="name")
    p.add_argument("--limit", type=int, help="nur die ersten N Einträge")
    p.set_defaults(func=cmd_models)

    p = sub.add_parser("info", parents=[common], help="Parameter und Preis eines Modells")
    p.add_argument("model_id")
    p.set_defaults(func=cmd_info)

    p = sub.add_parser("prices", parents=[common, filters], help="Preise per estimate abfragen (kostenlos)")
    p.set_defaults(func=cmd_prices)

    p = sub.add_parser("catalog", parents=[common], help="Modellkatalog verwalten")
    catalog_sub = p.add_subparsers(dest="catalog_command", required=True)
    p = catalog_sub.add_parser("update", parents=[common], help="Katalog aus API und Doku neu aufbauen")
    p.add_argument("--docs-file", type=Path, help="lokale llms-full.txt statt Download")
    p.set_defaults(func=cmd_catalog_update)

    return parser


# --- Einstieg ---------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    load_dotenv(ENV_FILE)
    try:
        result = args.func(args)
    except CliError as error:
        if args.verbose:
            traceback.print_exc()
        print_json({"error": str(error)})
        return error.exit_code
    print_json(result)
    # Ein Ergebnis mit "error" (z. B. Job failed) zählt als Fehlschlag.
    return 1 if "error" in result else 0


# Dieser Block läuft nur, wenn man die Datei direkt startet (python hf.py ...),
# nicht wenn ein Test sie importiert.
if __name__ == "__main__":
    sys.exit(main())
