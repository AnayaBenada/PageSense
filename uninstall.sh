#!/usr/bin/env bash
# Stop and remove the PageSense background service.
#   ./uninstall.sh          keeps your saved library (~/.pagesense) and the code
#   ./uninstall.sh --all    also deletes the library and the backend's virtualenv
# Ollama and its models are left alone; remove a model with: ollama rm llama3.2
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
LABEL="com.pagesense.backend"

if [ "$(uname -s)" = Darwin ]; then
  launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
  rm -f "$HOME/Library/LaunchAgents/$LABEL.plist"
else
  systemctl --user disable --now pagesense.service 2>/dev/null || true
  rm -f "$HOME/.config/systemd/user/pagesense.service"
  systemctl --user daemon-reload 2>/dev/null || true
fi
echo "Stopped the PageSense backend and removed it from login items."

if [ "${1:-}" = --all ]; then
  rm -rf "$HOME/.pagesense" "$ROOT/backend/.venv"
  echo "Deleted your saved library and the backend's virtualenv."
else
  echo "Your saved library is still in ~/.pagesense (run with --all to delete it)."
fi
echo "Remove the extension from chrome://extensions."
