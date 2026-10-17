"""Regenerate extension/icons/*.png. Requires Pillow (pip install pillow)."""
from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parent.parent / "extension" / "icons"
INDIGO, AMBER, WHITE = (79, 70, 229, 255), (245, 158, 11, 255), (255, 255, 255, 255)

for size in (16, 32, 48, 128):
    S = size * 4  # supersample, then downscale for smooth edges
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, S - 1, S - 1], radius=S * 0.22, fill=INDIGO)
    m = S * 0.22
    d.rounded_rectangle([m, m * 0.85, S - m, S - m * 0.85], radius=S * 0.06, fill=WHITE)  # page
    lw = S * 0.055
    for i, frac in enumerate((0.75, 0.55, 0.65)):  # text lines
        y = m * 0.85 + S * 0.14 + i * S * 0.12
        d.rounded_rectangle([m + S * 0.08, y, m + S * 0.08 + (S - 2 * m - S * 0.16) * frac, y + lw], radius=lw / 2, fill=INDIGO)
    cx, cy, r = S * 0.64, S * 0.66, S * 0.15  # magnifier
    d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=AMBER, width=int(S * 0.06))
    d.line([cx + r * 0.7, cy + r * 0.7, cx + r * 1.6, cy + r * 1.6], fill=AMBER, width=int(S * 0.07))
    img.resize((size, size), Image.LANCZOS).save(OUT / f"icon{size}.png")
print(f"wrote icons to {OUT}")
