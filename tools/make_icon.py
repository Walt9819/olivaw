"""Build src/assets/olivaw.ico from the wizard's own brand mark.

DEV-ONLY, and deliberately so: the .ico is generated here and COMMITTED. Users get a
binary asset, never a build step - nobody installing Olivaw has Pillow, and the whole
product rule is that a non-technical owner installs nothing.

The mark is the one already in the setup UI (src/wizard/web/app.css):

    .brand-mark { border-radius:12px/42px; background:var(--grad); color:#fff }
    --grad: linear-gradient(135deg,#6d5efc 0%,#a565f0 55%,#e26bd0 100%)

so the icon on the desktop and the header of the page it opens are the same object.
The O is drawn as a geometric ring rather than set in a font: at 16x16 a typographic
counter closes up into a blob, and a ring stays legible at every size Windows asks for.

Run: python tools/make_icon.py   (then commit src/assets/olivaw.ico)
"""

import os
import sys

try:
    from PIL import Image, ImageDraw
except ImportError:                                   # pragma: no cover - dev machine only
    sys.exit("This needs Pillow: python -m pip install pillow")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "src", "assets", "olivaw.ico")
PREVIEW = os.path.join(ROOT, "src", "assets", "olivaw-preview.png")

N = 1024                                              # supersampled master
STOPS = [(0.00, (0x6d, 0x5e, 0xfc)),                  # --accent  violet
         (0.55, (0xa5, 0x65, 0xf0)),                  # mid       purple
         (1.00, (0xe2, 0x6b, 0xd0))]                  # --accent-2 magenta
RADIUS = int(N * 0.235)                               # rounded square, ~the UI's 12/42
RING_OUTER = int(N * 0.293)                           # radius, so the O is ~59% wide
RING_WIDTH = int(N * 0.115)
SIZES = [(16, 16), (20, 20), (24, 24), (32, 32), (40, 40),
         (48, 48), (64, 64), (128, 128), (256, 256)]


def lerp(a, b, t):
    return tuple(round(x + (y - x) * t) for x, y in zip(a, b))


def gradient_at(t):
    """Colour at position t (0..1) along the three-stop ramp."""
    for i in range(len(STOPS) - 1):
        t0, c0 = STOPS[i]
        t1, c1 = STOPS[i + 1]
        if t <= t1 or i == len(STOPS) - 2:
            span = (t1 - t0) or 1.0
            return lerp(c0, c1, min(max((t - t0) / span, 0.0), 1.0))
    return STOPS[-1][1]


def build():
    # 135deg in CSS runs top-left -> bottom-right, i.e. t follows (x + y).
    grad = Image.new("RGB", (N, N))
    px = grad.load()
    row = [gradient_at(i / (2.0 * (N - 1))) for i in range(2 * N - 1)]
    for y in range(N):
        for x in range(N):
            px[x, y] = row[x + y]

    mask = Image.new("L", (N, N), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, N - 1, N - 1], RADIUS, fill=255)

    icon = Image.new("RGBA", (N, N), (0, 0, 0, 0))
    icon.paste(grad, (0, 0), mask)

    c = N // 2
    d = ImageDraw.Draw(icon)
    d.ellipse([c - RING_OUTER, c - RING_OUTER, c + RING_OUTER, c + RING_OUTER],
              outline=(255, 255, 255, 255), width=RING_WIDTH)

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    # Resize each size ourselves with LANCZOS: Pillow's ICO writer would do its own,
    # and the 16px frame is the one that has to survive.
    frames = [icon.resize(s, Image.LANCZOS) for s in SIZES]
    frames[-1].save(OUT, format="ICO", sizes=SIZES,
                    append_images=frames[:-1])

    # A contact sheet so a human (or a model) can eyeball every size at once.
    pad, x = 12, 12
    sheet = Image.new("RGBA", (sum(s[0] + pad for s in SIZES) + pad, 256 + 2 * pad),
                      (244, 245, 251, 255))
    for f, s in zip(frames, SIZES):
        sheet.paste(f, (x, pad + 256 - s[1]), f)
        x += s[0] + pad
    sheet.save(PREVIEW)
    print("wrote %s (%d bytes) and %s" % (OUT, os.path.getsize(OUT), PREVIEW))


if __name__ == "__main__":
    build()
