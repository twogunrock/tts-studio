"""Draws the TTS Studio icon: the on-air dot and a sound wave on the control bar's charcoal.

    python dev/make_icon.py      ->  static/icon.ico (16-256 px), static/icon-256.png, static/icon-32.png

Needs Pillow (a development tool only; the studio itself doesn't). Small sizes get a simpler mark (three bars)
drawn for that size, not a shrunken copy of the big one, so they stay crisp in a taskbar or browser tab.
"""
import os
from PIL import Image, ImageDraw, ImageFilter

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BOOTH, RED, WHITE = (30, 35, 40, 255), (224, 53, 43, 255), (233, 237, 240, 255)


def draw(size):
    k = 8  # draw large, then shrink: smooth edges at every size
    s = size * k
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    pad = round(s * (0.03 if size <= 32 else 0.06))
    d.rounded_rectangle([pad, pad, s - pad, s - pad], radius=s * 0.22, fill=BOOTH)
    small = size <= 32
    cy = s / 2
    # The on-air dot, with a soft glow at the larger sizes.
    dx, dr = s * (0.30 if small else 0.29), s * (0.12 if small else 0.095)
    if not small:
        glow = Image.new("RGBA", (s, s), (0, 0, 0, 0))
        ImageDraw.Draw(glow).ellipse([dx - dr * 2, cy - dr * 2, dx + dr * 2, cy + dr * 2], fill=(224, 53, 43, 110))
        img = Image.alpha_composite(img, glow.filter(ImageFilter.GaussianBlur(s * 0.03)))
        d = ImageDraw.Draw(img)
    d.ellipse([dx - dr, cy - dr, dx + dr, cy + dr], fill=RED)
    # The sound wave.
    heights = [0.34, 0.56, 0.28] if small else [0.26, 0.48, 0.64, 0.42, 0.24]
    x0, x1 = s * (0.50 if small else 0.47), s * 0.81
    gap = (x1 - x0) / len(heights)
    w = gap * (0.70 if small else 0.64)
    for i, h in enumerate(heights):
        x = x0 + i * gap + (gap - w) / 2
        d.rounded_rectangle([x, cy - s * h / 2, x + w, cy + s * h / 2], radius=w / 2, fill=WHITE)
    return img.resize((size, size), Image.LANCZOS)


sizes = [16, 20, 24, 32, 40, 48, 64, 128, 256]
images = [draw(n) for n in sizes]
images[-1].save(os.path.join(HERE, "static", "icon.ico"), sizes=[(n, n) for n in sizes], append_images=images[:-1])
images[-1].save(os.path.join(HERE, "static", "icon-256.png"))
images[sizes.index(32)].save(os.path.join(HERE, "static", "icon-32.png"))
# A contact sheet to look at (not shipped).
sheet = Image.new("RGBA", (sum(sizes) + 20 * len(sizes) + 20, 300), (228, 231, 234, 255))
x = 20
for n, im in zip(sizes, images):
    sheet.alpha_composite(im, (x, 150 - n // 2)); x += n + 20
sheet.save(os.path.join(os.environ.get("ICON_SHEET_DIR", HERE), "icon-sheet.png"))
