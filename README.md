# hf – Higgsfield-CLI

Schlankes Python-CLI, um über die [Higgsfield-API](https://docs.higgsfield.ai) Bilder und Videos zu erzeugen.
Jeder Befehl gibt genau eine Zeile JSON aus.

> Status: Gerüst. Implementiert ist bisher nur `models`.

## Installation

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Zugangsdaten

Key-ID und Secret aus der [Higgsfield Console](https://console.higgsfield.ai) als Umgebungsvariablen setzen
(siehe `.env.example`):

```bash
export HF_API_KEY="..."
export HF_API_SECRET="..."
```

## Befehle

| Befehl | Zweck |
| - | - |
| `estimate <model> --prompt ...` | Kosten vorab in USD |
| `submit <model> --prompt ... [--image-url URL] [--param k=v]` | Job abschicken |
| `status <request_id>` | aktueller Status |
| `wait <request_id> [--timeout 600]` | warten bis fertig |
| `download <request_id> [--out outputs]` | Dateien speichern |
| `run <model> --prompt ...` | submit + wait + download |
| `models` | Modelle aus `models.json` |

Alle Befehle kennen `--verbose` für ausführlichere Ausgabe.
