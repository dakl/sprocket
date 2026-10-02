# /// script
# dependencies = ["pillow", "numpy"]
# ///
"""Writes AppIcon.png: viewer/icon.png in the macOS icon shape.

macOS draws a Dock icon exactly as the image is. A bundle's .icns and an icon
set at runtime both arrive as plain pictures, so the rounded shape, the margin
and the shadow have to be in the picture. This follows Apple's template: a
1024 px canvas, an 824 px squircle in the middle, and a soft shadow below it.

Run: uv run make_icon.py
"""

from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

CANVAS = 1024
BODY = 824
SUPERSAMPLE = 4
# A superellipse |x|^n + |y|^n = 1 with n = 5 is close to Apple's continuous
# corner. A plain rounded rectangle has a visible join where the curve starts.
EXPONENT = 5

here = Path(__file__).resolve().parent
artwork = Image.open(here.parent / "viewer" / "icon.png").convert("RGB")
artwork = artwork.resize((BODY, BODY), Image.LANCZOS)

size = BODY * SUPERSAMPLE
axis = (np.arange(size) + 0.5) / size * 2 - 1
inside = np.abs(axis[None, :]) ** EXPONENT + np.abs(axis[:, None]) ** EXPONENT <= 1
mask = Image.fromarray((inside * 255).astype(np.uint8)).resize((BODY, BODY), Image.LANCZOS)

margin = (CANVAS - BODY) // 2
canvas = Image.new("RGBA", (CANVAS, CANVAS), (0, 0, 0, 0))

shadow = Image.new("L", (CANVAS, CANVAS), 0)
shadow.paste(mask, (margin, margin + 10))
shadow = shadow.filter(ImageFilter.GaussianBlur(14)).point(lambda value: value * 0.35)
canvas.paste(Image.new("RGBA", (CANVAS, CANVAS), (0, 0, 0, 255)), (0, 0), shadow)

canvas.paste(artwork, (margin, margin), mask)
canvas.save(here / "AppIcon.png")
print("wrote", here / "AppIcon.png")
