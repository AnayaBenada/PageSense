#!/usr/bin/env bash
# Build the Chrome Web Store upload: dist/pagesense-<version>.zip with manifest.json at its root.
set -euo pipefail
cd "$(dirname "$0")/.."
version="$(python3 -c 'import json; print(json.load(open("extension/manifest.json"))["version"])')"
out="dist/pagesense-$version.zip"
mkdir -p dist
rm -f "$out"
(cd extension && zip -qr -X "../$out" . -x '.*' -x '*/.*')
echo "$out"
unzip -l "$out" | tail -1
