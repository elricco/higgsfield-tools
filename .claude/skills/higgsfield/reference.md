# Higgsfield-Skill: Referenz

Nur bei Bedarf lesen. Kurzfassung steht in [SKILL.md](SKILL.md).

## Alle Befehle

| Befehl | Ausgabe |
|---|---|
| `models [--output image\|video] [--op OP] [--search TEXT] [--sort name\|price] [--limit N] [--all]` | `{"count", "models": [{"id", "title", "ops", "usd", ...}]}` |
| `info <model_id>` | Titel, `required`, `params`, Doku-`notes`, `docs_url`, `price`, `own_notes` |
| `estimate <model_id> --prompt ... [--image-url] [--param k=v]` | `{"model", "usd"}` oder zusätzlich `"pricing"` (Text bei Token-Abrechnung) |
| `submit <model_id> --prompt ... [...]` | `{"request_id", "status"}` |
| `status <request_id> [--urls]` | `{"request_id", "status", ["error"], ["urls"]}` |
| `wait <request_id> [--timeout 600]` | Endstatus; Polling 2 s → 10 s |
| `download <request_id> [--out outputs]` | `{"request_id", "status", "files": [...]}` |
| `run <model_id> --prompt ... [--timeout] [--out]` | wie `download` |
| `catalog update [--docs-file PATH]` | baut `catalog.json` aus `GET /models` + Doku |
| `prices [Filter wie models]` | schreibt `prices.json`, Ausgabe nur Zähler |

`--param` liest Werte als JSON: `duration=5` → Zahl, `enhance_prompt=true` → bool, `aspect_ratio=16:9` → Text.
Reihenfolge im Body: Defaults aus `models.json` → `--prompt`/`--image-url` → `--param`.

Exit-Codes: `0` ok, `1` Fehler oder Job `failed`/`nsfw`/`canceled`, `2` Zugangsdaten fehlen.

## Dateien

| Datei | Inhalt | Gepflegt |
|---|---|---|
| `catalog.json` | alle Modelle: Titel, Operationen, Parameter, Doku-Notizen | `catalog update` |
| `prices.json` | Preis je Modell mit Datum und dem verwendeten Platzhalter-Body | `prices` |
| `models.json` | eigene Testnotizen + Standardparameter getesteter Modelle | von Hand |
| `outputs/` | Downloads: `<datum>_<modell>_<request_id>[_n].<endung>` | automatisch |
| `.hf-history.json` | request_id → Modell (für Dateinamen) | automatisch |

Preise in `prices.json` gelten für Platzhalter-Eingaben mit Default-Parametern. Für echte Jobs immer `estimate` mit den tatsächlichen Parametern.

## API-Fakten (geprüft)

- Base URL `https://api.higgsfield.ai`, Header `Authorization: Key <id>:<secret>`.
- `GET /models` ist **undokumentiert** (liefert Modelle des Kontos, ohne Schemas). Kann sich ändern.
- Status: `queued` → `in_progress` → `completed` | `failed` | `nsfw` | `canceled`. `failed`/`nsfw` werden nicht berechnet.
- Ausgabe: Bilder `images: [{url}]`, Videos `video: {url}`, Audio `audio`/`audios`. Dateien 7 Tage abrufbar (`expires_at`).
- `submit` sendet einen `Idempotency-Key`; Retries erzeugen keine Doppel-Jobs.
- Abbruch nur solange `queued`: `POST /requests/<id>/cancel` (noch kein CLI-Befehl).
- Token-abgerechnete Modelle (z. B. Seedance 2.5, Kling 4.0 reference/video-edit) liefern bei `estimate` keinen Betrag, sondern eine Preisbeschreibung pro Sekunde/Auflösung.
- In der Doku fehlen teils Pflichtfelder in `required`; die API nennt sie im 400-Fehler.

## Prompt-Muster (ausführlich)

Allgemeine Erfahrungswerte, nur das Kling-Beispiel ist hier getestet.

**Bild**
- Aufbau: *Motiv, Detail* · *Ort/Umgebung* · *Licht/Tageszeit* · *Stil/Medium/Objektiv*.
- Konkrete Begriffe statt Adjektivketten: „35mm, shallow depth of field, overcast light“.
- Format über `aspect_ratio`, nicht im Prompt.

**Video**
- Bild-zu-Video: Das Bild legt Inhalt und Stil fest. Der Prompt beschreibt **was sich bewegt**.
- Kamerabegriffe: `slow push in`, `pull back`, `pan left/right`, `tilt up`, `orbit around`, `static shot`, `handheld`, `tracking shot`.
- Eine Hauptbewegung pro Clip; bei 5 s eher langsam und ruhig.
- `negative_prompt` (wo vorhanden) für Artefakte: „warping, flicker, extra limbs“.

**Konsistenz über mehrere Clips**
- Gleiche Stil-Formulierung in allen Prompts wiederverwenden.
- Clip N+1 vom letzten Frame von Clip N starten (Frame lokal extrahieren → Upload nötig, noch nicht im CLI) oder vom selben Startbild.
- Modelle mit `first-last-frame` oder `reference-to-video` nutzen, wenn Anfang und Ende feststehen.
