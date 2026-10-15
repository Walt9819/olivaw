# -*- coding: utf-8 -*-
"""Draw Olivaw's launcher icon: a white O on the purple the console already uses.

No image library. Pillow is not installed and adding it would be a build dependency for
one file that changes once a year, so the few hundred lines a PNG actually needs are
here: a bitmap, an anti-aliased ring, zlib, and four CRCs.

The O is drawn as a ring rather than set in a typeface on purpose. A letter would come
out different on every machine that has a different font installed, and at 48 pixels the
difference between one grotesque and another is the difference between a legible mark
and a grey blob. A ring is the same everywhere, and at this size the ring IS the letter.

    python tools/make_app_icon.py

Writes the five legacy mipmaps, the adaptive foreground, and the background colour.
"""
from __future__ import print_function

import math
import os
import struct
import sys
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
RES = os.path.join(ROOT, "app", "android", "app", "src", "main", "res")

# app.css --accent and --accent-2. The icon is a gradient between them, which is what the
# console uses for anything that represents Olivaw itself rather than one agent.
ACCENT = (0x5B, 0x5B, 0xD6)
ACCENT2 = (0x8A, 0x63, 0xE8)
WHITE = (0xFF, 0xFF, 0xFF)

# Android's own numbers: the legacy icon is the whole square, while an adaptive icon is
# drawn on a 108dp canvas of which only the middle 72dp is guaranteed to be visible -
# the rest can be cropped to whatever shape the launcher likes.
LEGACY = [("mipmap-mdpi", 48), ("mipmap-hdpi", 72), ("mipmap-xhdpi", 96),
          ("mipmap-xxhdpi", 144), ("mipmap-xxxhdpi", 192)]
ADAPTIVE = [("mipmap-mdpi", 108), ("mipmap-hdpi", 162), ("mipmap-xhdpi", 216),
            ("mipmap-xxhdpi", 324), ("mipmap-xxxhdpi", 432)]


def _png(path, width, height, rows):
    """Write RGBA rows (each a bytearray of width*4) as a PNG."""
    raw = b"".join(b"\x00" + bytes(r) for r in rows)

    def chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    blob = (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9))
            + chunk(b"IEND", b""))
    with open(path, "wb") as fh:
        fh.write(blob)
    return len(blob)


def _coverage(cx, cy, x, y, r_out, r_in, samples=4):
    """How much of pixel (x, y) is inside the ring, 0..1.

    Sampled rather than computed: the exact area of a circle clipped to a square is a
    page of trigonometry for a result nobody can see. Sixteen samples per pixel is
    indistinguishable from exact at these sizes and is four lines.
    """
    hit = 0
    step = 1.0 / samples
    for sy in range(samples):
        for sx in range(samples):
            dx = (x + (sx + 0.5) * step) - cx
            dy = (y + (sy + 0.5) * step) - cy
            d = math.sqrt(dx * dx + dy * dy)
            if r_in <= d <= r_out:
                hit += 1
    return hit / float(samples * samples)


def _disc(cx, cy, x, y, r, samples=4):
    return _coverage(cx, cy, x, y, r, -1.0, samples)


def _blend(base, over, a):
    return tuple(int(round(base[i] * (1 - a) + over[i] * a)) for i in range(3))


def draw(size, full_bleed, ring_fraction=0.30, thickness=0.195):
    """One icon.

    full_bleed: the purple fills the whole square (legacy launcher icon).
    otherwise:  the purple is a disc inside the 108dp canvas, so a launcher that crops to
                a circle, a squircle or a rounded square gets the same mark either way.
    """
    rows = []
    cx = cy = size / 2.0
    r_out = size * ring_fraction
    r_in = r_out - size * thickness * 0.5 - size * 0.015
    r_in = max(r_in, size * 0.08)
    bg_r = size * 0.5 if full_bleed else size * 0.33
    for y in range(size):
        row = bytearray()
        for x in range(size):
            # The background: a vertical gradient between the two accents, so the icon
            # has the same depth as the console's own header.
            t = (x + y) / float(2 * size)
            bg = tuple(int(round(ACCENT[i] + (ACCENT2[i] - ACCENT[i]) * t)) for i in range(3))
            if full_bleed:
                cov_bg = 1.0
            else:
                cov_bg = _disc(cx, cy, x, y, bg_r)
            if cov_bg <= 0.0:
                row += bytes((0, 0, 0, 0))
                continue
            ring = _coverage(cx, cy, x, y, r_out, r_in)
            colour = _blend(bg, WHITE, ring)
            row += bytes((colour[0], colour[1], colour[2], int(round(255 * cov_bg))))
        rows.append(row)
    return rows


def _write_xml(path, text):
    """Write XML, refusing anything a resource compiler would reject.

    `--` inside a comment is illegal in XML and aapt2 fails the whole build over it. The
    CSS variable this file documents is called `--accent`, so the obvious comment to
    write is the one that cannot be written.
    """
    for i, line in enumerate(text.splitlines(), 1):
        if "<!--" in line or line.strip().startswith(("<!--", "--")):
            body = text[text.index("<!--") + 4:text.index("-->")] if "-->" in text else ""
            if "--" in body:
                raise ValueError("%s:%d: '--' inside an XML comment" % (path, i))
    with open(path, "w") as fh:
        fh.write(text)


def main():
    if not os.path.isdir(RES):
        sys.exit("no res directory at %s - run `flutter create` first" % RES)
    made = 0
    for folder, size in LEGACY:
        d = os.path.join(RES, folder)
        if not os.path.isdir(d):
            os.makedirs(d)
        n = _png(os.path.join(d, "ic_launcher.png"), size, size, draw(size, True))
        print("  %-18s ic_launcher.png        %3dx%-3d %6d bytes" % (folder, size, size, n))
        made += 1
    for folder, size in ADAPTIVE:
        d = os.path.join(RES, folder)
        n = _png(os.path.join(d, "ic_launcher_foreground.png"), size, size,
                 draw(size, False))
        print("  %-18s ic_launcher_foreground %3dx%-3d %6d bytes" % (folder, size, size, n))
        made += 1

    vals = os.path.join(RES, "values")
    if not os.path.isdir(vals):
        os.makedirs(vals)
    _write_xml(os.path.join(vals, "ic_launcher_background.xml"),
               '<?xml version="1.0" encoding="utf-8"?>\n'
               '<resources>\n'
               '    <!-- The accent from app.css. Flat, because a launcher may\n'
               '         parallax the background under the foreground. -->\n'
               '    <color name="ic_launcher_background">#5B5BD6</color>\n'
               '</resources>\n')
    anydpi = os.path.join(RES, "mipmap-anydpi-v26")
    if not os.path.isdir(anydpi):
        os.makedirs(anydpi)
    for name in ("ic_launcher.xml", "ic_launcher_round.xml"):
        with open(os.path.join(anydpi, name), "w") as fh:
            fh.write('<?xml version="1.0" encoding="utf-8"?>\n'
                     '<adaptive-icon xmlns:android="http://schemas.android.com/apk/res/android">\n'
                     '    <background android:drawable="@color/ic_launcher_background"/>\n'
                     '    <foreground android:drawable="@mipmap/ic_launcher_foreground"/>\n'
                     '    <monochrome android:drawable="@mipmap/ic_launcher_foreground"/>\n'
                     '</adaptive-icon>\n')
    print("  wrote the adaptive icon and its background colour")
    print("%d images" % made)


if __name__ == "__main__":
    main()
