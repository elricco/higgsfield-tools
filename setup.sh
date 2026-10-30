#!/usr/bin/env bash
# Richtet hf auf diesem Rechner ein. Kann beliebig oft ausgeführt werden.
#
#   ./setup.sh                 # mit python3 aus dem PATH
#   PYTHON=python3.12 ./setup.sh
#
# Danach gibt es den Befehl "hf" (über ~/.local/bin) und den Claude-Skill
# "higgsfield" (über ~/.claude/skills). Beides sind nur Verweise ins Repo;
# installiert wird ausschließlich in die .venv dieses Repos.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON="${PYTHON:-python3}"
BIN_DIR="$HOME/.local/bin"
SKILL_LINK="$HOME/.claude/skills/higgsfield"

step() { printf '\n== %s\n' "$1"; }

step "Python prüfen"
if ! "$PYTHON" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)'; then
  echo "Python 3.10 oder neuer nötig, gefunden: $("$PYTHON" --version 2>&1)"
  echo "Anderes Python wählen: PYTHON=/pfad/zu/python3.12 ./setup.sh"
  exit 1
fi
"$PYTHON" --version

step "Virtuelle Umgebung (.venv) und Paket installieren"
[ -d "$REPO/.venv" ] || "$PYTHON" -m venv "$REPO/.venv"
"$REPO/.venv/bin/python" -m pip install --quiet --upgrade pip
# -e = "editable": der Befehl nutzt direkt den Code im Repo, git pull genügt für Updates.
"$REPO/.venv/bin/python" -m pip install --quiet -e "$REPO[dev]"
echo "ok: $REPO/.venv"

step "Befehl 'hf' verlinken"
mkdir -p "$BIN_DIR"
ln -sfn "$REPO/.venv/bin/hf" "$BIN_DIR/hf"
echo "ok: $BIN_DIR/hf -> $REPO/.venv/bin/hf"
case ":$PATH:" in
  *":$BIN_DIR:"*) ;;
  *) echo "Hinweis: $BIN_DIR steht nicht im PATH. In ~/.zshrc ergänzen:"
     echo "  export PATH=\"\$HOME/.local/bin:\$PATH\"" ;;
esac

step "Claude-Skill verlinken"
mkdir -p "$(dirname "$SKILL_LINK")"
if [ -e "$SKILL_LINK" ] && [ ! -L "$SKILL_LINK" ]; then
  echo "Übersprungen: $SKILL_LINK existiert und ist kein Verweis – bitte selbst prüfen."
else
  ln -sfn "$REPO/.claude/skills/higgsfield" "$SKILL_LINK"
  echo "ok: $SKILL_LINK -> $REPO/.claude/skills/higgsfield"
fi

step "Zugangsdaten"
if [ ! -f "$REPO/.env" ]; then
  cp "$REPO/.env.example" "$REPO/.env"
  echo "$REPO/.env angelegt – bitte HF_API_KEY und HF_API_SECRET eintragen"
  echo "(Werte aus https://console.higgsfield.ai), dann 'hf doctor' ausführen."
  exit 0
fi
echo "ok: .env vorhanden"

step "Einrichtung prüfen (hf doctor)"
"$REPO/.venv/bin/hf" doctor || {
  echo
  echo "Mindestens eine Prüfung ist fehlgeschlagen – Details stehen oben unter \"checks\"."
  exit 1
}
echo
echo "Fertig. Testen mit: hf models --output image --sort price --limit 3"
