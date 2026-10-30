# hf – Higgsfield-CLI

🇬🇧 [English version](README.md)

Schlankes Python-CLI, um über die [Higgsfield-API](https://docs.higgsfield.ai) Bilder und Videos zu erzeugen.
Jeder Befehl gibt genau eine Zeile JSON aus. Das hält den Kontext klein, wenn Claude das CLI über den
Skill in `.claude/skills/higgsfield/` benutzt.

## Einrichten (auch auf einem neuen Rechner)

Voraussetzungen: macOS oder Linux, Python ≥ 3.10, Git. Optional `ffmpeg` (Einzelbilder aus Videos).

```bash
git clone <repo-url> higgsfield-tools
cd higgsfield-tools
./setup.sh
```

`setup.sh` kann jederzeit erneut laufen (z. B. nach `git pull`) und macht Folgendes:

1. prüft die Python-Version (anderes Python: `PYTHON=python3.12 ./setup.sh`),
2. legt `.venv` im Repo an und installiert das Paket dort **editierbar** – nichts wird global installiert,
3. verlinkt den Befehl `~/.local/bin/hf` → `.venv/bin/hf` (muss im `PATH` stehen),
4. verlinkt den Claude-Skill `~/.claude/skills/higgsfield` → `.claude/skills/higgsfield`,
5. legt `.env` aus `.env.example` an, falls sie fehlt,
6. ruft `hf doctor` auf.

Danach:

1. **Zugangsdaten:** Key-ID und Secret aus der [Higgsfield Console](https://console.higgsfield.ai) in `.env`
   eintragen (wird von Git ignoriert). Bereits gesetzte Umgebungsvariablen haben Vorrang.
2. **Firewall** (falls z. B. Little Snitch läuft): dem Python aus der `.venv` ausgehendes HTTPS erlauben zu
   `*.higgsfield.ai` (API), `*.cloudfront.net` (Ergebnisse und Uploads) und `docs.higgsfield.ai`
   (Vercel, nur für `catalog update`).
3. **Prüfen:** `hf doctor` – alle Checks sollten `ok` sein.
4. **Katalog auffrischen:** `hf catalog update && hf prices` (beides kostenlos).
5. **Charaktere** (Soul ID) aus `characters.json` sind nicht im Repo. Auf dem alten Rechner `hf character list`,
   auf dem neuen je `hf character add <Name> <ID>`.

Deinstallieren: `rm ~/.local/bin/hf ~/.claude/skills/higgsfield` und den Repo-Ordner löschen.

## Beispiele

`hf` funktioniert in jedem Ordner. Downloads landen in `./outputs` des aktuellen Ordners.

```bash
# Katalog und Preise aktualisieren (kostenlos)
hf catalog update
hf prices

# Günstigste Bild-zu-Video-Modelle, Details zu einem Modell
hf models --op image2video --sort price --limit 5
hf info kling-video/v2.5-turbo/standard/image-to-video

# Kosten vorab (kostenlos)
hf estimate higgsfield-ai/soul/v2/standard --prompt "A quiet alpine lake at sunrise"

# Bild erzeugen: in einem Schritt …
hf run higgsfield-ai/soul/v2/standard --prompt "A quiet alpine lake at sunrise" --param aspect_ratio=16:9

# … oder einzeln
hf submit higgsfield-ai/soul/v2/standard --prompt "..."
hf wait <request_id>
hf download <request_id>

# Bild → Video, auch mit lokaler Datei
hf run kling-video/v2.5-turbo/standard/image-to-video --prompt "slow camera push in" --image-url ./foto.jpg

# Wiederkehrender Charakter
hf character create Mila bild1.png bild2.png ... --model-version v2
hf run higgsfield-ai/soul/v2/standard --character Mila --prompt "..."
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
| `cancel <request_id>` | Job abbrechen, solange er `queued` ist |
| `upload <datei>` | lokale Datei hochladen, gibt öffentliche URL aus |
| `character create\|status\|list\|add` | Soul-ID-Charaktere trainieren und verwalten |
| `doctor` | Einrichtung prüfen: Zugangsdaten, API, Doku, Katalog |

Generierungen kennen außerdem `--image-urls A B ...` (Edit-Modelle; lokale Dateien werden hochgeladen) und
`--character <Name>` (SOUL mit trainiertem Charakter).

Alle Befehle kennen `--verbose` für vollständige Antworten und Stacktraces.
Exit-Codes: `0` ok, `1` Fehler bzw. Job fehlgeschlagen, `2` Zugangsdaten fehlen.

## Dateien

| Datei | Zweck |
| - | - |
| `hf.py` | das CLI |
| `catalog.py` | Katalog-Logik (Doku auslesen, zusammenführen, Platzhalter) |
| `pyproject.toml` | Paketbeschreibung, Abhängigkeiten, Befehl `hf` |
| `setup.sh` | Einrichtung auf diesem Rechner |
| `models.json` | eigene Notizen und Standardparameter getesteter Modelle (von Hand gepflegt) |
| `catalog.json`, `prices.json` | erzeugt von `catalog update` bzw. `prices` |
| `characters.json` | Charakter-Name → Soul-ID (lokal, nicht in Git) |
| `.claude/skills/higgsfield/` | Skill für Claude Code |

Die Datendateien liegen immer im Repo, egal aus welchem Ordner `hf` aufgerufen wird.

## Tests

```bash
.venv/bin/python -m pytest -q
```

Die Tests laufen ohne Netzwerk: HTTP wird durch eine `FakeSession` ersetzt.

## Hinweise

- `GET /models` ist nicht dokumentiert und kann sich ändern. Fällt er weg, bleibt die Doku als Quelle.
- Ergebnisse liegen nur 7 Tage bei Higgsfield, deshalb immer `download`.
- Homebrew-Python ist nur ad-hoc signiert; Little Snitch kann es deshalb mit einem signierten „Python“
  verwechseln und warnen. Pfad in der Meldung prüfen: Er sollte auf das Python der `.venv` zeigen.
- Später möglich: Webhooks statt Polling.
