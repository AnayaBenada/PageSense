#!/usr/bin/env bash
# Build the Chrome Web Store upload, dist/pagesense-<version>.zip, with manifest.json at its root.
# One listing holds both modes: on first open the panel asks whether to use Student or Everyday
# mode (extension/edition.js stays null), and either can be switched in settings.
set -euo pipefail
cd "$(dirname "$0")/.."
version="$(python3 -c 'import json; print(json.load(open("extension/manifest.json"))["version"])')"
grep -q 'PAGESENSE_EDITION = null;' extension/edition.js \
  || { echo "extension/edition.js must leave PAGESENSE_EDITION null so the panel asks which mode" >&2; exit 1; }
python3 - <<'EOF'
import json
m = json.load(open("extension/manifest.json"))
assert len(m["name"]) <= 75 and len(m["description"]) <= 132, "the store limits names to 75 and descriptions to 132 characters"
EOF

mkdir -p dist
out="$PWD/dist/pagesense-$version.zip"
rm -f "$out"
(cd extension && zip -qr -X "$out" . -x '.*' '*/.*')
echo "dist/$(basename "$out")  ($(unzip -l "$out" | tail -1 | awk '{print $2}') files)"
