"""hf – schlankes CLI für die Higgsfield-API.

Jeder Befehl gibt genau eine Zeile JSON aus, damit Claude (oder ein Skript)
die Ausgabe leicht lesen kann. Mit --verbose gibt es mehr Details.
"""

import argparse
import json
import os
import sys
import traceback
from pathlib import Path

# --- Konstanten -------------------------------------------------------------

BASE_URL = "https://api.higgsfield.ai"

# Status-Werte, bei denen ein Job fertig ist (egal ob Erfolg oder Fehler).
TERMINAL_STATUSES = {"completed", "failed", "nsfw", "canceled"}

# Path(__file__).parent ist der Ordner, in dem diese Datei liegt.
# So findet das CLI models.json auch, wenn man es aus einem anderen Ordner aufruft.
MODELS_FILE = Path(__file__).parent / "models.json"


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


# --- Befehle ----------------------------------------------------------------
# Jede Funktion bekommt die geparsten Argumente (args) und gibt ein Dictionary
# zurück. main() kümmert sich um die Ausgabe.


def cmd_models(args: argparse.Namespace) -> dict:
    models = load_models()
    if args.verbose:
        return {"models": models}
    # Kompakt: nur ID, Typ und Standardparameter.
    return {
        "models": [
            {"id": model_id, "type": info.get("type"), "defaults": info.get("defaults", {})}
            for model_id, info in models.items()
        ]
    }


def not_implemented(args: argparse.Namespace) -> dict:
    raise CliError(f"Befehl '{args.command}' ist noch nicht implementiert.")


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
    generation.add_argument("--image-url", help="Eingabebild für Bild-zu-Video")
    generation.add_argument(
        "--param", action="append", default=[], metavar="KEY=VALUE",
        help="weiterer Modellparameter, mehrfach erlaubt",
    )

    p = sub.add_parser("estimate", parents=[common, generation], help="Kosten vorab schätzen")
    p.set_defaults(func=not_implemented)

    p = sub.add_parser("submit", parents=[common, generation], help="Job abschicken")
    p.set_defaults(func=not_implemented)

    p = sub.add_parser("status", parents=[common], help="Status eines Jobs")
    p.add_argument("request_id")
    p.set_defaults(func=not_implemented)

    p = sub.add_parser("wait", parents=[common], help="warten, bis ein Job fertig ist")
    p.add_argument("request_id")
    p.add_argument("--timeout", type=int, default=600, help="Sekunden (Standard: 600)")
    p.set_defaults(func=not_implemented)

    p = sub.add_parser("download", parents=[common], help="Ergebnisdateien herunterladen")
    p.add_argument("request_id")
    p.add_argument("--out", type=Path, default=Path("outputs"))
    p.set_defaults(func=not_implemented)

    p = sub.add_parser("run", parents=[common, generation], help="submit + wait + download")
    p.add_argument("--timeout", type=int, default=600)
    p.add_argument("--out", type=Path, default=Path("outputs"))
    p.set_defaults(func=not_implemented)

    p = sub.add_parser("models", parents=[common], help="bekannte Modelle auflisten")
    p.set_defaults(func=cmd_models)

    return parser


# --- Einstieg ---------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = args.func(args)
    except CliError as error:
        if args.verbose:
            traceback.print_exc()
        print_json({"error": str(error)})
        return error.exit_code
    print_json(result)
    return 0


# Dieser Block läuft nur, wenn man die Datei direkt startet (python hf.py ...),
# nicht wenn ein Test sie importiert.
if __name__ == "__main__":
    sys.exit(main())
