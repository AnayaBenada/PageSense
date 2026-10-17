#!/usr/bin/env bash
# Start everything PageSense needs: Ollama (with the model) and the backend on :8000.
set -euo pipefail
cd "$(dirname "$0")/../backend"

MODEL="${PAGESENSE_LLM_MODEL:-llama3.2}"

if ! curl -sf localhost:11434/api/tags >/dev/null; then
  echo "Starting Ollama…"
  if command -v brew >/dev/null && brew services list | grep -q ollama; then
    brew services start ollama >/dev/null
  else
    (ollama serve >/dev/null 2>&1 &)
  fi
  for _ in $(seq 1 30); do curl -sf localhost:11434/api/tags >/dev/null && break; sleep 1; done
fi

if ! ollama list | grep -q "^${MODEL}"; then
  echo "Pulling ${MODEL}…"
  ollama pull "$MODEL"
fi

if [ ! -x .venv/bin/uvicorn ]; then
  echo "Creating virtualenv…"
  python3 -m venv .venv
  .venv/bin/pip install -q -r requirements.txt
fi

echo "Starting backend on http://localhost:8000 (Ctrl+C to stop)…"
exec .venv/bin/uvicorn app.main:app --port 8000
