# -*- coding: utf-8 -*-
"""A face for each agent.

Why this exists
---------------
The console listed agents as a dot and a line of text. That reads as a list of settings,
and the owner does not think of them that way any more - they are *workers*, and she
picks one the way she picks a person in a chat app: by face, instantly, without reading.

What an agent's face can be, in order of what the owner chose:

* **a picture she uploaded** - a photo, a logo, something generated elsewhere;
* **an emoji** on a coloured disc;
* **its initials** on a coloured disc - the default, and never ugly.

The colour is not decorative. It is the SAME colour the agent's Chrome window wears
(``browser_label.look_for``), so the green face in the sidebar and the green-framed window
on the taskbar are visibly one agent. Overriding it here overrides only the face; the
window keeps its slot, because that one is Chrome's and is handed out first-come.

Why the bytes are re-encoded
----------------------------
An uploaded image is a file from outside, and it is served back to the console - same
origin as the page, holding the owner's token. So: the magic bytes are checked, and when
Pillow is available the image is *decoded and re-encoded by us*, which means the bytes on
disk are ours and nothing of the original container survives. Pillow is not a hard
dependency (this ships to machines that only have what the installer put there), so
without it a strict sniff plus a small size cap is the gate, and the file is served with
an explicit Content-Type and ``nosniff``.
"""

import base64
import io
import json
import os
import re
import time

try:
    from . import browser_label
except Exception:  # noqa: BLE001  - the console must still open without it
    browser_label = None

try:
    from . import agents_registry
except Exception:  # noqa: BLE001
    agents_registry = None


SLUG_RE = re.compile(r"^[a-z0-9]{1,24}$")

# Before decoding: what the console is allowed to POST. After decoding: what may rest on
# disk. The second is the one that matters - a 256px PNG is ~40KB, so anything near the
# cap means the re-encode did not happen and the original is being kept as-is.
MAX_UPLOAD = 6 * 1024 * 1024
MAX_STORED = 768 * 1024
BOX = 256                      # a face is never shown bigger than 96px; 256 covers retina

# (prefix, extension, mime). Checked in order; WebP needs the RIFF container AND the tag.
_MAGIC = (
    (b"\x89PNG\r\n\x1a\n", "png", "image/png"),
    (b"\xff\xd8\xff", "jpg", "image/jpeg"),
    (b"GIF87a", "gif", "image/gif"),
    (b"GIF89a", "gif", "image/gif"),
)

# A small, deliberately boring set. Enough to say "this one watches the money" without
# turning the sidebar into a sticker album.
EMOJI = (u"\U0001F916", u"\U0001F4BC", u"\U0001F4CA", u"\U0001F4DA", u"\U0001F50D",
         u"\U0001F3A8", u"\U0001F527", u"\U0001F4E3", u"\U0001F3E5", u"\U0001F4C8",
         u"\U0001F5C2", u"⚙️", u"\U0001F680", u"\U0001F9ED", u"\U0001F514",
         u"\U0001F4DD")


def _install_dir(install_dir=None):
    if install_dir:
        return install_dir
    if agents_registry is not None:
        try:
            return agents_registry.install_root()
        except Exception:  # noqa: BLE001
            pass
    return os.path.join(os.path.expanduser("~"), ".olivaw")


def store_dir(install_dir=None):
    return os.path.join(_install_dir(install_dir), "avatars")


def meta_path(install_dir=None):
    return os.path.join(store_dir(install_dir), "faces.json")


def _read_meta(install_dir=None):
    p = meta_path(install_dir)
    try:
        with io.open(p, encoding="utf-8-sig") as fh:
            data = json.load(fh)
    except Exception:  # noqa: BLE001
        return {}
    return data if isinstance(data, dict) else {}


def _write_meta(data, install_dir=None):
    d = store_dir(install_dir)
    os.makedirs(d, exist_ok=True)
    p = meta_path(install_dir)
    tmp = p + ".tmp"
    with io.open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
    os.replace(tmp, p)


def _entry(slug, install_dir=None):
    rec = _read_meta(install_dir).get(slug)
    return rec if isinstance(rec, dict) else {}


# ── what the face looks like ─────────────────────────────────────────────────

def initials(name):
    """At most two letters, from the words that actually carry the name.

    "Agente principal" is two words but one of them is a category, so it would give "AP" -
    which is right, because that is what it is called. No cleverness beyond skipping the
    joining words, which are never what anybody reads.
    """
    words = [w for w in re.split(r"[\s_\-.]+", (name or "").strip()) if w]
    skip = {"de", "del", "la", "el", "los", "las", "y", "the", "of"}
    big = [w for w in words if w.lower() not in skip] or words
    if not big:
        return u"?"
    if len(big) == 1:
        return big[0][:2].upper()
    return (big[0][:1] + big[1][:1]).upper()


def _default_hex(slug, install_dir=None):
    """The colour this agent's Chrome window wears, so the two agree.

    ``assign=False``: merely drawing a face must not consume a window colour slot - that
    is the same rule the browser panel follows, and for the same reason.
    """
    if browser_label is None:
        return u"#5B7CFA"
    try:
        return browser_label.look_for(slug, install_dir, assign=False).get("hex") or u"#5B7CFA"
    except Exception:  # noqa: BLE001
        return u"#5B7CFA"


def _valid_hex(value):
    v = (value or "").strip()
    return v if re.match(r"^#[0-9A-Fa-f]{6}$", v) else ""


def look(slug, name="", install_dir=None):
    """Everything the console needs to draw one face, and nothing it has to ask twice for.

    ``v`` is the version stamp. It rides in the image URL so a changed picture is a
    different URL - otherwise the browser keeps showing the old face until a hard reload,
    which looks exactly like the upload having failed.
    """
    slug = (slug or "").strip().lower()
    rec = _entry(slug, install_dir)
    hexa = _valid_hex(rec.get("color")) or _default_hex(slug, install_dir)
    out = {"slug": slug, "hex": hexa, "initials": initials(name or slug),
           "kind": "initials", "emoji": "", "v": int(rec.get("v") or 0)}
    kind = rec.get("kind")
    if kind == "image" and rec.get("file"):
        if os.path.exists(os.path.join(store_dir(install_dir), rec["file"])):
            out["kind"] = "image"
            out["mime"] = rec.get("mime") or "image/png"
        # A file that is gone is not an error worth a message: the face falls back to
        # initials, which is what it looked like before she ever uploaded one.
    elif kind == "emoji" and rec.get("emoji"):
        out["kind"] = "emoji"
        out["emoji"] = rec["emoji"]
    return out


def look_all(agents, install_dir=None):
    out = {}
    for a in (agents or []):
        slug = (a.get("slug") or "").strip().lower()
        if slug:
            out[slug] = look(slug, a.get("name") or slug, install_dir)
    return out


# ── setting one ──────────────────────────────────────────────────────────────

def sniff(raw):
    """(ext, mime) for an image we are willing to keep, else None."""
    for prefix, ext, mime in _MAGIC:
        if raw.startswith(prefix):
            return ext, mime
    if raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return "webp", "image/webp"
    return None


def _decode(data_b64):
    s = (data_b64 or "").strip()
    if s.startswith("data:"):
        s = s.split(",", 1)[-1]
    s = re.sub(r"\s+", "", s)
    if not s:
        raise ValueError(u"No llegó ninguna imagen.")
    if len(s) > MAX_UPLOAD * 4 // 3 + 8:
        raise ValueError(u"Esa imagen es demasiado grande. Prueba con una de menos de 5 MB.")
    try:
        return base64.b64decode(s, validate=True)
    except Exception:  # noqa: BLE001
        raise ValueError(u"No pude leer esa imagen.")


def _square_png(raw):
    """Centre-crop to a square and re-encode as PNG. None when Pillow is not installed.

    Centre-crop rather than letterbox: a face is drawn inside a circle, so the bars of a
    letterboxed photo would be the first thing the owner sees and the subject the second.
    """
    try:
        from PIL import Image
    except Exception:  # noqa: BLE001
        return None
    try:
        im = Image.open(io.BytesIO(raw))
        im.load()                                 # force the decode HERE, inside the guard
        # Animations lose everything but the first frame, which is the right answer for a
        # 32px disc and spares the console a spinning avatar.
        if getattr(im, "is_animated", False):
            im.seek(0)
        im = im.convert("RGBA")
        w, h = im.size
        if not w or not h:
            return None
        side = min(w, h)
        im = im.crop(((w - side) // 2, (h - side) // 2,
                      (w - side) // 2 + side, (h - side) // 2 + side))
        if side > BOX:
            im = im.resize((BOX, BOX), Image.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, format="PNG", optimize=True)
        return buf.getvalue()
    except Exception:  # noqa: BLE001
        return None


def set_picture(slug, data_b64="", install_dir=None):
    slug = (slug or "").strip().lower()
    if not SLUG_RE.match(slug):
        raise ValueError(u"«%s» no es un agente válido." % slug)
    raw = _decode(data_b64)
    if len(raw) > MAX_UPLOAD:
        raise ValueError(u"Esa imagen es demasiado grande. Prueba con una de menos de 5 MB.")
    kind = sniff(raw)
    if not kind:
        raise ValueError(u"Eso no parece una imagen. Usa un PNG, JPG, GIF o WebP.")
    ext, mime = kind
    small = _square_png(raw)
    if small is not None:
        raw, ext, mime = small, "png", "image/png"
    elif len(raw) > MAX_STORED:
        # No Pillow on this machine, so the file cannot be shrunk - and a 4 MB avatar
        # would be re-downloaded by every panel that draws it.
        raise ValueError(
            u"Esa imagen pesa demasiado para usarla como foto (%d KB). "
            u"Recórtala a 256×256 o usa una más pequeña." % (len(raw) // 1024))

    d = store_dir(install_dir)
    os.makedirs(d, exist_ok=True)
    name = "%s.%s" % (slug, ext)
    tmp = os.path.join(d, name + ".tmp")
    with io.open(tmp, "wb") as fh:
        fh.write(raw)
    os.replace(tmp, os.path.join(d, name))

    meta = _read_meta(install_dir)
    rec = meta.get(slug) if isinstance(meta.get(slug), dict) else {}
    # Any older file for this agent in another format would otherwise sit there forever
    # and, worse, be the one `read` finds if the recorded name is ever lost.
    for other in ("png", "jpg", "gif", "webp"):
        if other != ext:
            try:
                os.remove(os.path.join(d, "%s.%s" % (slug, other)))
            except OSError:
                pass
    rec.update({"kind": "image", "file": name, "mime": mime,
                "v": int(rec.get("v") or 0) + 1, "at": int(time.time()),
                "bytes": len(raw)})
    meta[slug] = rec
    _write_meta(meta, install_dir)
    return {"ok": True, "look": look(slug, "", install_dir), "bytes": len(raw)}


def set_mark(slug, emoji="", color="", install_dir=None):
    """An emoji, or just a colour. Both optional, and clearing both is a real choice."""
    slug = (slug or "").strip().lower()
    if not SLUG_RE.match(slug):
        raise ValueError(u"«%s» no es un agente válido." % slug)
    emoji = (emoji or "").strip()[:8]
    if emoji and emoji not in EMOJI:
        # Not a security boundary (it is escaped on the way out like everything else) - it
        # keeps the stored value to something that will actually render on a Windows box.
        raise ValueError(u"Ese símbolo no está en la lista.")
    meta = _read_meta(install_dir)
    rec = meta.get(slug) if isinstance(meta.get(slug), dict) else {}
    rec["kind"] = "emoji" if emoji else "initials"
    rec["emoji"] = emoji
    col = _valid_hex(color)
    if color is not None and color != "":
        if not col:
            raise ValueError(u"Ese color no es válido.")
        rec["color"] = col
    elif color == "":
        rec.pop("color", None)
    rec["v"] = int(rec.get("v") or 0) + 1
    rec["at"] = int(time.time())
    meta[slug] = rec
    _write_meta(meta, install_dir)
    return {"ok": True, "look": look(slug, "", install_dir)}


def clear(slug, install_dir=None):
    """Back to initials in the agent's own colour. The file goes with it."""
    slug = (slug or "").strip().lower()
    if not SLUG_RE.match(slug):
        raise ValueError(u"«%s» no es un agente válido." % slug)
    meta = _read_meta(install_dir)
    rec = meta.get(slug) if isinstance(meta.get(slug), dict) else {}
    d = store_dir(install_dir)
    for other in ("png", "jpg", "gif", "webp"):
        try:
            os.remove(os.path.join(d, "%s.%s" % (slug, other)))
        except OSError:
            pass
    meta[slug] = {"kind": "initials", "emoji": "", "v": int(rec.get("v") or 0) + 1,
                  "at": int(time.time())}
    _write_meta(meta, install_dir)
    return {"ok": True, "look": look(slug, "", install_dir)}


def forget(slug, install_dir=None):
    """Drop every trace of a deleted agent's face, file and row alike."""
    slug = (slug or "").strip().lower()
    if not SLUG_RE.match(slug):
        return {"ok": False}
    d = store_dir(install_dir)
    for other in ("png", "jpg", "gif", "webp"):
        try:
            os.remove(os.path.join(d, "%s.%s" % (slug, other)))
        except OSError:
            pass
    meta = _read_meta(install_dir)
    if slug in meta:
        meta.pop(slug, None)
        _write_meta(meta, install_dir)
    return {"ok": True}


# ── serving one ──────────────────────────────────────────────────────────────

def read(slug, install_dir=None):
    """(bytes, mime) for the GET route, or None.

    The filename is rebuilt from the slug and a fixed extension list rather than taken
    from the stored record, so a hand-edited faces.json cannot point this at a path
    outside the avatars folder.
    """
    slug = (slug or "").strip().lower()
    if not SLUG_RE.match(slug):
        return None
    rec = _entry(slug, install_dir)
    if rec.get("kind") != "image":
        return None
    d = store_dir(install_dir)
    for ext, mime in (("png", "image/png"), ("jpg", "image/jpeg"),
                      ("gif", "image/gif"), ("webp", "image/webp")):
        p = os.path.join(d, "%s.%s" % (slug, ext))
        if os.path.isfile(p):
            try:
                with io.open(p, "rb") as fh:
                    raw = fh.read(MAX_STORED + 1)
            except OSError:
                return None
            if len(raw) > MAX_STORED or not sniff(raw):
                return None     # changed on disk since we wrote it: do not serve it
            return raw, mime
    return None
