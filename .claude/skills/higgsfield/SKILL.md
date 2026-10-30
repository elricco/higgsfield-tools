---
name: higgsfield
description: Bilder und Videos über die Higgsfield-API erzeugen (Text-zu-Bild, Bild-zu-Video, Text-zu-Video, Video-Edit) mit dem CLI hf. Verwenden, wenn der User ein Bild, Foto, Video, einen Clip oder eine Animation generieren will, ein Modell wie SOUL, Kling, Seedance, Wan, Hailuo, Recraft oder Z-Image erwähnt, nach Preisen oder verfügbaren Higgsfield-Modellen fragt oder Higgsfield nennt.
---

# Higgsfield: Bilder und Videos erzeugen

Alle Befehle mit `hf ...` ausführen – funktioniert in jedem Ordner. Downloads landen in `./outputs` des
aktuellen Ordners (anderer Ort: `--out <ordner>`).
Jeder Befehl gibt **eine Zeile JSON** aus; Exit-Code ≠ 0 heißt Fehler (`{"error": ...}`).
Bei Problemen mit Zugangsdaten oder Netzwerk zuerst `hf doctor`.

## Ablauf

1. **Modell finden** (aus lokalem Katalog, kostenlos):
   `hf models --op image2video --sort price --limit 5`
   Operationen: `text2image`, `image_edit`, `image2video`, `text2video`, `video2video`. Alternativ `--output image|video`, `--search "kling 4.0"`.
2. **Parameter prüfen:** `hf info <model_id>` → `required`, `params` (enum/default), `notes`, `price`.
   Ist `documented: false`, sind nur die Pflichtfelder aus `price.payload` bekannt.
3. **Kosten nennen:** `hf estimate <model_id> --prompt "..." [--image-url URL] [--param k=v ...]` (kostenlos).
   Preis mit den *echten* Parametern abfragen – Dauer und Auflösung ändern ihn stark.
4. **Erst nach Bestätigung des Users erzeugen:**
   - Bild: `hf run <model_id> --prompt "..." --param aspect_ratio=16:9`
   - Video: dasselbe mit `run_in_background` und `--timeout 900` – Videos brauchen 1–5 Minuten.
5. **Ergebnis berichten:** nur die Pfade aus `files` und ggf. die Kosten. Keine Roh-JSONs.

Bei langen Jobs oder Abbruch: `submit` → `wait <id>` → `download <id>`; dieselbe `request_id` kostet nicht doppelt.

## Regeln

- **Vor jeder kostenpflichtigen Generierung** Modell + geschätzten Preis nennen und Bestätigung abwarten. Bei Serien die Gesamtsumme nennen.
- `.env` nie lesen oder ausgeben; Zugangsdaten lädt `hf` selbst.
- `--verbose` / `-v` nur zur Fehlersuche, nicht für normale Ausgaben.
- Vor Bild-zu-Video sicherstellen, dass `--image-url` nicht leer ist (öffentliche HTTPS-URL oder existierende lokale Datei).
- Fehlt ein Modell im Katalog oder ist er älter als ~2 Wochen: `hf catalog update` und danach `hf prices` (beides kostenlos).

## Getestete Modelle (Stand 2026-10-30)

| Modell | Zweck | Preis | Ergebnis |
|---|---|---|---|
| `higgsfield-ai/soul/v2/standard` | Text→Bild, Standard-Wahl | 0,004 $ (720p) | PNG 1696×960 bei 16:9, ~18 s |
| `kling-video/v2.5-turbo/standard/image-to-video` | Bild→Video, günstig | 0,179 $ (5 s), 0,357 $ (10 s) | MP4 1276×720, 24 fps, ~60 s, ohne Ton |
| `kling-video/v4.0/image-to-video` | Bild→Video, neuer, **mit Ton** | ~0,134 $/s (720p), ~0,20 $/s (1080p) | MP4 1296×732, 24 fps + AAC-Ton, ~3,5 min für 3 s |

Weitere Werte: SOUL `resolution` nur `720p`/`1080p`, `aspect_ratio` nur `9:16, 16:9, 4:3, 3:4, 1:1, 2:3, 3:2`.
Kling 2.5 Turbo: `duration` 5 oder 10, kein `aspect_ratio` (übernimmt das Format des Bildes).
Kling 4.0 (undokumentiert): `duration` 3–30, `resolution` 720p/**1080p (Default!)**, optional `end_image_url`.
Ohne Angaben kostet es 2 $ (10 s, 1080p) – Dauer und Auflösung immer explizit setzen. Unbekannte Parameter ignoriert die API still.

## Bild → Video verketten

```bash
hf run higgsfield-ai/soul/v2/standard --prompt "..." --param aspect_ratio=16:9
hf status <request_id_des_bildes> --urls   # -> urls[0]
hf run kling-video/v2.5-turbo/standard/image-to-video --prompt "<Bewegung>" --image-url "<urls[0]>"
```

Ergebnis-URLs gelten 7 Tage.

**Lokale Dateien:** `--image-url` akzeptiert auch einen Pfad (`./foto.jpg`, `~/Bilder/x.png`) – das CLI lädt die
Datei automatisch hoch (jpg, png, webp, gif). Nur die URL holen: `hf upload <datei>`.

**Abbrechen:** `hf cancel <request_id>` – nur solange der Job `queued` ist; ein abgebrochener Job wird erstattet.

## Wiederkehrende Charaktere (Soul ID)

Vorhandene Charaktere: `hf character list`. Auf einem neuen Rechner fehlen sie dort zunächst – bekannte IDs mit
`hf character add <Name> <ID>` nachtragen (IDs gelten kontoweit). Nutzen mit `--character <Name>` bei SOUL-Modellen der passenden
Familie (`model_version` v2 → `higgsfield-ai/soul/v2/standard`); setzt `custom_reference_id` und Stärke 1.0
(ändern: `--param custom_reference_strength=0.7`, nie 0).

Neuen Charakter anlegen (getesteter Ablauf, ~2,75 $ gesamt):
1. Basisporträt: `soul/v2/standard`, frontal, schlichter Hintergrund, `batch_size=4`, User wählt eins (~0,02 $).
2. Weitere Ansichten **aus diesem einen Bild** per Edit: `alibaba/qwen-image-3/edit --image-urls <basis>`, je 0,04 $.
   Prompt: Ansicht + „Same woman as in the reference image: identical face, … same hair, same top“.
   Richtung über die **Bildseite** angeben („nose points toward the RIGHT edge of the image“), nicht „her left“ – sonst falsche Seite.
   Getrennte Text-zu-Bild-Läufe ergeben verschiedene Personen – nicht verwenden.
3. Bilder ansehen, Ausreißer neu erzeugen. Bewährt: Profil L, 3/4 L, frontal, 3/4 R, Profil R, frontal lächelnd.
4. `hf character create <Name> <bilder...> --model-version v2` (2,50 $, vorher bestätigen lassen), dann `character status <Name>`.
   Training dauert ~7 min (`not_ready` → `in_progress` → `completed`).

Erfahrungen (Test „Mila“, 2026-10-30): Identität sitzt in Nah- und Halbnah-Aufnahmen sicher, auch mit neuem Ausdruck
und Outfit. In Totalen ist das Gesicht zu klein zum Wiedererkennen. Frisur aus den Trainingsbildern (zurückgebunden)
setzt sich gegen den Prompt („hair loose“) teilweise durch – gewünschte Frisuren schon in den Trainingsbildern variieren.

## Prompt-Muster (kurz)

- **Bild:** Motiv → Umgebung → Licht → Stil/Kamera. *„A quiet alpine lake at sunrise, low mist, editorial photography“*
- **Video aus Bild:** nur **Bewegung** beschreiben, nicht das Bild noch einmal: Kamera + Motivbewegung + Atmosphäre.
  *„slow camera push in over the misty lake, gentle ripples on the water, drifting fog“* (getestet ✓)
- **Konsistenz über mehrere Clips:** gleiches Startbild bzw. letztes Bild des Vorclips, gleiche Stil-Wörter, eine Kamerabewegung pro Clip.

Mehr in [reference.md](reference.md): alle Befehle, Fehlermeldungen, API-Details, ausführliche Prompt-Muster.

## Bekannte Fehler

| Meldung | Ursache / Lösung |
|---|---|
| `hf: command not found` | Nicht eingerichtet → User bitten, `./setup.sh` im Repo `higgsfield-tools` auszuführen |
| `HTTP 400: 'x' is a required property` | Pflichtfeld fehlt → `hf info <id>` |
| `HTTP 400: resolution: '2K' is not one of [...]` | Die Fehlermeldung nennt die erlaubten Werte; die OpenAPI-Datei der Doku ist hier falsch |
| `Netzwerkfehler ... ConnectTimeout` / Download hängt | Firewall (Little Snitch) blockiert Python → User bitten, Domain freizugeben (`*.higgsfield.ai`, `*.cloudfront.net`, Doku: `vercel-dns.com`) |
| `Noch kein Katalog vorhanden` | `hf catalog update` |
| Status `nsfw` / `failed` | Nicht berechnet. Prompt entschärfen bzw. erneut versuchen |
