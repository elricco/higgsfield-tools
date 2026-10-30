# hf – Higgsfield-CLI

Schlankes Python-CLI, um über die [Higgsfield-API](https://docs.higgsfield.ai) Bilder und Videos zu erzeugen.
Jeder Befehl gibt genau eine Zeile JSON aus. Das hält den Kontext klein, wenn Claude das CLI über den
Skill in `.claude/skills/higgsfield/` benutzt.

## Installation

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Zugangsdaten

Key-ID und Secret aus der [Higgsfield Console](https://console.higgsfield.ai) in eine `.env` eintragen
(Vorlage: `.env.example`, wird von Git ignoriert):

```bash
cp .env.example .env   # dann Werte eintragen
```

`hf.py` lädt `.env` beim Start selbst. Bereits gesetzte Umgebungsvariablen (`export HF_API_KEY=...`) haben Vorrang.

## Beispiele

```bash
# Katalog und Preise aktualisieren (kostenlos)
python hf.py catalog update
python hf.py prices

# Günstigste Bild-zu-Video-Modelle, Details zu einem Modell
python hf.py models --op image2video --sort price --limit 5
python hf.py info kling-video/v2.5-turbo/standard/image-to-video

# Kosten vorab (kostenlos)
python hf.py estimate higgsfield-ai/soul/v2/standard --prompt "A quiet alpine lake at sunrise"

# Bild erzeugen: in einem Schritt …
python hf.py run higgsfield-ai/soul/v2/standard --prompt "A quiet alpine lake at sunrise" --param aspect_ratio=16:9

# … oder einzeln
python hf.py submit higgsfield-ai/soul/v2/standard --prompt "..."
python hf.py wait <request_id>
python hf.py download <request_id>

# Bild → Video
python hf.py status <request_id_des_bildes> --urls
python hf.py run kling-video/v2.5-turbo/standard/image-to-video --prompt "slow camera push in" --image-url "<url>"
```

## Befehle

| Befehl | Zweck |
| - | - |
| `catalog update` | Modellliste (API `GET /models`) + Parameter (Doku) nach `catalog.json` |
| `prices [Filter]` | Preise aller passenden Modelle per `estimate` nach `prices.json` |
| `models [--output image\|video] [--op OP] [--search TEXT] [--sort price] [--limit N]` | Modelle auflisten |
| `info <model>` | Parameter, Pflichtfelder, Preis, eigene Notizen |
| `estimate <model> --prompt ...` | Kosten vorab in USD |
| `submit <model> --prompt ... [--image-url URL] [--param k=v]` | Job abschicken |
| `status <request_id> [--urls]` | aktueller Status, optional Ergebnis-URLs |
| `wait <request_id> [--timeout 600]` | warten bis fertig |
| `download <request_id> [--out outputs]` | Dateien speichern |
| `run <model> --prompt ...` | submit + wait + download |

Alle Befehle kennen `--verbose` für vollständige Antworten und Stacktraces.
Exit-Codes: `0` ok, `1` Fehler bzw. Job fehlgeschlagen, `2` Zugangsdaten fehlen.

## Dateien

| Datei | Zweck |
| - | - |
| `hf.py` | das CLI |
| `catalog.py` | Katalog-Logik (Doku auslesen, zusammenführen, Platzhalter) |
| `models.json` | eigene Notizen und Standardparameter getesteter Modelle (von Hand gepflegt) |
| `catalog.json`, `prices.json` | erzeugt von `catalog update` bzw. `prices` |
| `outputs/` | Downloads (ignoriert von Git) |
| `.claude/skills/higgsfield/` | Skill für Claude Code |

## Tests

```bash
python -m pytest -q
```

Die Tests laufen ohne Netzwerk: HTTP wird durch eine `FakeSession` ersetzt.

## Hinweise

- `GET /models` ist nicht dokumentiert und kann sich ändern. Fällt er weg, bleibt die Doku als Quelle.
- Ergebnisse liegen nur 7 Tage bei Higgsfield, deshalb immer `download`.
- Mit einer App-Firewall (z. B. Little Snitch) braucht Python Zugriff auf `*.higgsfield.ai`,
  `*.cloudfront.net` (Ergebnisse) und die Doku (`docs.higgsfield.ai` → Vercel).
- Später möglich: Upload lokaler Dateien (`/files/generate-upload-url`), `cancel`-Befehl, Webhooks statt Polling.
