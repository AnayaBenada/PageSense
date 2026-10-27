#!/usr/bin/env bash
# Render the Chrome Web Store images from store/art.html into store/*.png.
# Screenshots are composed from page + panel captures in $1 (default store/raw):
#   p1-site.png p1-panel.png (ask), p2-* (Smart Find), p3-* (library), p4-* (setup).
set -euo pipefail
cd "$(dirname "$0")/.."
RAW="$(cd "${1:-store/raw}" && pwd)"
CHROME="${CHROME:-/Applications/Google Chrome.app/Contents/MacOS/Google Chrome}"
ART="file://$PWD/store/art.html"

render() { # url width height out
  "$CHROME" --headless=new --disable-gpu --hide-scrollbars --force-color-profile=srgb --allow-file-access-from-files \
    --window-size="$2,$3" --screenshot="$4" "$1" >/dev/null 2>&1
}
render "$ART?promo=small" 440 280 store/promo-small.png
render "$ART?promo=marquee" 1400 560 store/promo-marquee.png
names=(ask find library setup)
for i in 1 2 3 4; do
  [ -f "$RAW/p$i-site.png" ] || continue
  render "$ART?shot=file://$RAW/p$i-site.png,file://$RAW/p$i-panel.png" 1280 800 "store/screenshot-$i-${names[$((i-1))]}.png"
done

# The store wants 24-bit PNGs (no alpha channel).
backend/.venv/bin/python - <<'EOF'
from pathlib import Path
from PIL import Image
for p in sorted(Path("store").glob("*.png")):
    im = Image.open(p)
    if im.mode != "RGB":
        im.convert("RGB").save(p)
    print(p, Image.open(p).size, Image.open(p).mode)
EOF
