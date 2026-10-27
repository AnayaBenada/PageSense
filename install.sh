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
bold "Checking Python"
PY=""
for cand in python3.13 python3.12 python3.11 python3.10 python3; do
  if command -v "$cand" >/dev/null && "$cand" -c 'import sys; sys.exit(sys.version_info < (3, 10))' 2>/dev/null; then
    PY="$(command -v "$cand")"; break
  fi
done
if [ -z "$PY" ]; then
  if [ "$OS" = Darwin ] && command -v brew >/dev/null; then
    brew install python@3.12
    PY="$(brew --prefix)/bin/python3.12"
  elif [ "$OS" = Darwin ]; then
    fail "Python 3.10 or newer is required. Install it from https://www.python.org/downloads/ and run this again."
  else
    fail "Python 3.10 or newer is required, e.g.: sudo apt install python3 python3-venv"
  fi
fi
echo "Using $("$PY" --version) at $PY"

# ---------- 3. Ollama + model ----------
bold "Checking Ollama"
if ! command -v ollama >/dev/null; then
  if [ "$OS" = Linux ]; then
    curl -fsSL https://ollama.com/install.sh | sh
  elif command -v brew >/dev/null; then
    brew install ollama
  else
    fail "Ollama is required. Install it from https://ollama.com/download, open it once, and run this again."
  fi
fi
if ! curl -sf localhost:11434/api/tags >/dev/null; then
  echo "Starting Ollama…"
  if [ "$OS" = Darwin ] && command -v brew >/dev/null && brew list ollama >/dev/null 2>&1; then
    brew services start ollama >/dev/null
  elif [ "$OS" = Darwin ] && [ -d /Applications/Ollama.app ]; then
    open -a Ollama
  elif [ "$OS" = Linux ] && command -v systemctl >/dev/null && systemctl list-unit-files ollama.service >/dev/null 2>&1; then
    sudo systemctl start ollama
  else
    (nohup ollama serve >/dev/null 2>&1 &)
  fi
  for _ in $(seq 1 30); do curl -sf localhost:11434/api/tags >/dev/null && break; sleep 1; done
  curl -sf localhost:11434/api/tags >/dev/null || fail "Ollama didn't start. Try running 'ollama serve' in another terminal."
fi
if ! ollama list | awk 'NR>1 {print $1}' | grep -qx "$MODEL\(:latest\)\{0,1\}"; then
  echo "Downloading $MODEL (about 2 GB, one time)…"
  ollama pull "$MODEL"
fi

# ---------- 4. Backend ----------
bold "Installing the backend"
if [ ! -x "$BACKEND/.venv/bin/python" ]; then
  "$PY" -m venv "$BACKEND/.venv" || fail "Couldn't create a virtualenv. On Debian/Ubuntu: sudo apt install python3-venv"
fi
VPY="$BACKEND/.venv/bin/python"
"$VPY" -m pip install -q --upgrade pip
if [ "$OS" = Linux ] && ! command -v nvidia-smi >/dev/null; then
  # Without a GPU, the CPU build of PyTorch avoids a multi-GB CUDA download.
  "$VPY" -m pip install -q torch --index-url https://download.pytorch.org/whl/cpu
fi
"$VPY" -m pip install -q -r "$BACKEND/requirements.txt"

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
