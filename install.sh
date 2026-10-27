#!/usr/bin/env bash
# Install PageSense on macOS or Linux:
#
#   curl -fsSL https://raw.githubusercontent.com/AnayaBenada/PageSense/main/install.sh | bash
#
# or, from a clone of the repo, ./install.sh
#
# It installs Ollama and the LLM if needed, sets up the backend in a virtualenv,
# downloads the embedding models, and runs the backend as a background service
# that starts at login. Re-run it any time to update. Undo with ./uninstall.sh.
#
# Options (environment variables):
#   PAGESENSE_DIR=~/PageSense      where to put the code when not run from a clone
#   PAGESENSE_BRANCH=main          which branch to download
#   PAGESENSE_PORT=8000            backend port
#   PAGESENSE_LLM_MODEL=llama3.2   Ollama model (llama3.2:1b for machines with <8 GB RAM)
#   PAGESENSE_NO_SERVICE=1         don't install the background service
set -euo pipefail

REPO="AnayaBenada/PageSense"
BRANCH="${PAGESENSE_BRANCH:-main}"
PORT="${PAGESENSE_PORT:-8000}"
MODEL="${PAGESENSE_LLM_MODEL:-llama3.2}"
DATA="$HOME/.pagesense"
LABEL="com.pagesense.backend"

bold() { printf '\n\033[1m%s\033[0m\n' "$*"; }
fail() { printf '\n\033[31mError:\033[0m %s\n' "$*" >&2; exit 1; }

OS="$(uname -s)"
[ "$OS" = Darwin ] || [ "$OS" = Linux ] || fail "PageSense supports macOS and Linux (on Windows, use WSL)."

# ---------- 1. Code ----------
here="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd || true)"
if [ -n "$here" ] && [ -d "$here/backend/app" ] && [ -d "$here/extension" ]; then
  ROOT="$here"
else
  ROOT="${PAGESENSE_DIR:-$HOME/PageSense}"
  if [ -d "$ROOT/.git" ]; then
    bold "Updating $ROOT"
    git -C "$ROOT" pull --ff-only
  else
    bold "Downloading PageSense ($BRANCH) to $ROOT"
    tmp="$(mktemp -d)"
    curl -fsSL "https://github.com/$REPO/archive/refs/heads/$BRANCH.tar.gz" | tar -xz -C "$tmp" --strip-components=1
    # Replace the code but keep the virtualenv, so updates don't reinstall everything.
    mkdir -p "$ROOT"
    rm -rf "$ROOT/backend/app" "$ROOT/extension"
    cp -R "$tmp/." "$ROOT/"
    rm -rf "$tmp"
  fi
fi
BACKEND="$ROOT/backend"
mkdir -p "$DATA/logs"

# ---------- 2. Python ----------
# Use Python 3.10+ if it's installed; otherwise (e.g. a Mac with only the system
# Python 3.9) uv downloads a private copy, without needing admin rights.
bold "Checking Python"
PY=""
for cand in python3.13 python3.12 python3.11 python3.10 python3; do
  if command -v "$cand" >/dev/null && "$cand" -c 'import sys; sys.exit(sys.version_info < (3, 10))' 2>/dev/null; then
    PY="$(command -v "$cand")"; break
  fi
done
UV=""
get_uv() {
  UV="$DATA/bin/uv"
  [ -x "$UV" ] || curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR="$DATA/bin" INSTALLER_NO_MODIFY_PATH=1 sh >/dev/null
}
if [ -n "$PY" ]; then
  echo "Using $("$PY" --version) at $PY"
else
  echo "Python 3.10 or newer not found; PageSense will use its own copy."
  get_uv
fi

# ---------- 3. Ollama + model ----------
bold "Checking Ollama"
find_ollama() {
  command -v ollama && return
  for p in /opt/homebrew/bin/ollama /usr/local/bin/ollama \
           /Applications/Ollama.app/Contents/Resources/ollama "$HOME/Applications/Ollama.app/Contents/Resources/ollama"; do
    [ -x "$p" ] && { echo "$p"; return; }
  done
  return 1
}
OLLAMA="$(find_ollama || true)"
if ! curl -sf localhost:11434/api/tags >/dev/null; then
  if [ -z "$OLLAMA" ]; then
    if [ "$OS" = Linux ]; then
      curl -fsSL https://ollama.com/install.sh | sh
    elif command -v brew >/dev/null; then
      brew install ollama
    else
      echo "Downloading the Ollama app (about 200 MB)…"
      apps=/Applications; [ -w "$apps" ] || { apps="$HOME/Applications"; mkdir -p "$apps"; }
      tmp="$(mktemp -d)"
      curl -fL# -o "$tmp/Ollama.zip" https://ollama.com/download/Ollama-darwin.zip
      ditto -xk "$tmp/Ollama.zip" "$apps"
      rm -rf "$tmp"
    fi
    OLLAMA="$(find_ollama)" || fail "Ollama didn't install. Install it from https://ollama.com/download and run this again."
  fi
  echo "Starting Ollama…"
  case "$OLLAMA" in
    */Ollama.app/*) open "${OLLAMA%%/Contents/*}" ;;
    *) if [ "$OS" = Darwin ] && command -v brew >/dev/null && brew list ollama >/dev/null 2>&1; then
         brew services start ollama >/dev/null
       elif [ "$OS" = Linux ] && systemctl list-unit-files ollama.service 2>/dev/null | grep -q ollama; then
         sudo systemctl start ollama
       else
         (nohup "$OLLAMA" serve >/dev/null 2>&1 &)
       fi ;;
  esac
  for _ in $(seq 1 60); do curl -sf localhost:11434/api/tags >/dev/null && break; sleep 1; done
  curl -sf localhost:11434/api/tags >/dev/null || fail "Ollama didn't start. Open the Ollama app (or run 'ollama serve') and run this again."
fi
if ! curl -sf localhost:11434/api/tags | grep -qE "\"name\": *\"$MODEL(:latest)?\""; then
  echo "Downloading $MODEL (about 2 GB, one time)…"
  if [ -n "$OLLAMA" ]; then
    "$OLLAMA" pull "$MODEL"
  else
    curl -sf localhost:11434/api/pull -d "{\"model\": \"$MODEL\", \"stream\": false}" >/dev/null || fail "Couldn't download $MODEL."
  fi
fi

# ---------- 4. Backend ----------
bold "Installing the backend"
VPY="$BACKEND/.venv/bin/python"
if [ ! -x "$VPY" ]; then
  if [ -n "$PY" ] && "$PY" -m venv "$BACKEND/.venv" 2>/dev/null; then :
  else
    rm -rf "$BACKEND/.venv"
    [ -n "$UV" ] || get_uv
    "$UV" venv -q --python 3.12 "$BACKEND/.venv"
  fi
fi
pip_install() {
  if "$VPY" -m pip --version >/dev/null 2>&1; then
    "$VPY" -m pip install -q "$@"
  else
    [ -n "$UV" ] || get_uv
    "$UV" pip install -q --python "$VPY" "$@"
  fi
}
pip_install --upgrade pip 2>/dev/null || true
if [ "$OS" = Linux ] && ! command -v nvidia-smi >/dev/null; then
  # Without a GPU, the CPU build of PyTorch avoids a multi-GB CUDA download.
  pip_install torch --index-url https://download.pytorch.org/whl/cpu
fi
pip_install -r "$BACKEND/requirements.txt"

echo "Downloading the search models (about 200 MB, one time)…"
(cd "$BACKEND" && "$VPY" -c '
from sentence_transformers import CrossEncoder, SentenceTransformer
from app.config import Settings
cfg = Settings()
SentenceTransformer(cfg.embed_model, device="cpu")
if cfg.rerank_model:
    CrossEncoder(cfg.rerank_model, device="cpu")
' >"$DATA/logs/install.log" 2>&1) || echo "(Couldn't pre-download them; the backend will on first start. Details: $DATA/logs/install.log)"

# ---------- 5. Background service ----------
UVICORN="$BACKEND/.venv/bin/uvicorn"
if [ "${PAGESENSE_NO_SERVICE:-0}" = 1 ]; then
  bold "Skipping the background service"
  echo "Start the backend yourself with: $ROOT/scripts/start.sh"
else
  bold "Starting PageSense in the background"
  # Stop an earlier install first, then make sure nothing else holds the port.
  if [ "$OS" = Darwin ]; then
    launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null && sleep 2 || true
  else
    systemctl --user stop pagesense.service 2>/dev/null || true
  fi
  if curl -s -o /dev/null "localhost:$PORT"; then
    fail "Something is already running on port $PORT (maybe a backend you started yourself). Stop it, or re-run with PAGESENSE_PORT=8001."
  fi
  if [ "$OS" = Darwin ]; then
    plist="$HOME/Library/LaunchAgents/$LABEL.plist"
    mkdir -p "$(dirname "$plist")"
    cat >"$plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$UVICORN</string><string>app.main:app</string>
    <string>--host</string><string>127.0.0.1</string>
    <string>--port</string><string>$PORT</string>
  </array>
  <key>WorkingDirectory</key><string>$BACKEND</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PAGESENSE_LLM_MODEL</key><string>$MODEL</string>
    <key>PATH</key><string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
  </dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>10</integer>
  <key>StandardOutPath</key><string>$DATA/logs/backend.log</string>
  <key>StandardErrorPath</key><string>$DATA/logs/backend.log</string>
</dict>
</plist>
EOF
    launchctl bootstrap "gui/$(id -u)" "$plist"
  else
    unit="$HOME/.config/systemd/user/pagesense.service"
    mkdir -p "$(dirname "$unit")"
    cat >"$unit" <<EOF
[Unit]
Description=PageSense backend
After=network.target

[Service]
WorkingDirectory=$BACKEND
Environment=PAGESENSE_LLM_MODEL=$MODEL
ExecStart=$UVICORN app.main:app --host 127.0.0.1 --port $PORT
Restart=on-failure
StandardOutput=append:$DATA/logs/backend.log
StandardError=append:$DATA/logs/backend.log

[Install]
WantedBy=default.target
EOF
    systemctl --user daemon-reload
    systemctl --user enable --now pagesense.service
  fi

  echo -n "Waiting for the backend"
  ok=0
  for _ in $(seq 1 90); do
    if curl -sf "localhost:$PORT/health" | grep -q '"status": *"ok"'; then ok=1; break; fi
    echo -n "."; sleep 2
  done
  echo
  [ "$ok" = 1 ] || fail "The backend didn't come up on port $PORT. See $DATA/logs/backend.log (is something else using port $PORT?)"
  echo "Backend running at http://localhost:$PORT (starts automatically at login)."
fi

# ---------- 6. Extension ----------
bold "Last step: add the extension to Chrome"
if [ "$OS" = Darwin ] && [ ! -d "/Applications/Google Chrome.app" ] && [ ! -d "$HOME/Applications/Google Chrome.app" ]; then
  echo "  (Chrome isn't installed yet: get it from https://www.google.com/chrome/ first.)"
fi
cat <<EOF
  1. Open chrome://extensions in Chrome
  2. Turn on "Developer mode" (top right)
  3. Click "Load unpacked" and choose this folder:
       $ROOT/extension
  4. Pin PageSense, open any article, and press Alt+Shift+P
EOF
if [ "$PORT" != 8000 ]; then
  echo "  5. In the panel's settings (⚙), set the backend URL to http://localhost:$PORT"
fi
[ "$OS" = Darwin ] && open "$ROOT/extension" 2>/dev/null || true
echo
echo "To uninstall: $ROOT/uninstall.sh"
