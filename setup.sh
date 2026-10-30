#!/usr/bin/env bash
# Sets up hf on this machine. Can be run as often as you like.
#
#   ./setup.sh                 # with python3 from the PATH
#   PYTHON=python3.12 ./setup.sh
#
# Afterwards the command "hf" (via ~/.local/bin) and the Claude skill
# "higgsfield" (via ~/.claude/skills) exist. Both are only links into the repo;
# packages are installed exclusively into this repo's .venv.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON="${PYTHON:-python3}"
BIN_DIR="$HOME/.local/bin"
SKILL_LINK="$HOME/.claude/skills/higgsfield"

step() { printf '\n== %s\n' "$1"; }

step "Checking Python"
if ! "$PYTHON" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)'; then
  echo "Python 3.10 or newer required, found: $("$PYTHON" --version 2>&1)"
  echo "Choose a different Python: PYTHON=/path/to/python3.12 ./setup.sh"
  exit 1
fi
"$PYTHON" --version

step "Virtual environment (.venv) and package installation"
[ -d "$REPO/.venv" ] || "$PYTHON" -m venv "$REPO/.venv"
"$REPO/.venv/bin/python" -m pip install --quiet --upgrade pip
# -e = "editable": the command uses the code in the repo directly, git pull is enough for updates.
"$REPO/.venv/bin/python" -m pip install --quiet -e "$REPO[dev]"
echo "ok: $REPO/.venv"

step "Linking the 'hf' command"
mkdir -p "$BIN_DIR"
ln -sfn "$REPO/.venv/bin/hf" "$BIN_DIR/hf"
echo "ok: $BIN_DIR/hf -> $REPO/.venv/bin/hf"
case ":$PATH:" in
  *":$BIN_DIR:"*) ;;
  *) echo "Note: $BIN_DIR is not on your PATH. Add this to ~/.zshrc:"
     echo "  export PATH=\"\$HOME/.local/bin:\$PATH\"" ;;
esac

step "Linking the Claude skill"
mkdir -p "$(dirname "$SKILL_LINK")"
if [ -e "$SKILL_LINK" ] && [ ! -L "$SKILL_LINK" ]; then
  echo "Skipped: $SKILL_LINK exists and is not a link – please check it yourself."
else
  ln -sfn "$REPO/.claude/skills/higgsfield" "$SKILL_LINK"
  echo "ok: $SKILL_LINK -> $REPO/.claude/skills/higgsfield"
fi

step "Credentials"
if [ ! -f "$REPO/.env" ]; then
  cp "$REPO/.env.example" "$REPO/.env"
  echo "Created $REPO/.env – please enter HF_API_KEY and HF_API_SECRET"
  echo "(values from https://console.higgsfield.ai), then run 'hf doctor'."
  exit 0
fi
echo "ok: .env exists"

step "Checking the setup (hf doctor)"
"$REPO/.venv/bin/hf" doctor || {
  echo
  echo "At least one check failed – see the details under \"checks\" above."
  exit 1
}
echo
echo "Done. Try: hf models --output image --sort price --limit 3"
