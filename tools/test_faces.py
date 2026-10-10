r"""A face and a name for each agent.

Two modules, one subject: what identifies an agent to its owner.

**avatars.py** stores the face. What is worth testing is not "does it save a file":

  * **an uploaded image is a file from outside.** It is served back to the console - the
    same origin as the page, which holds the owner's token - so the bytes must be ours.
    The magic-byte gate, the re-encode, and the refusal to serve anything that stopped
    looking like an image are the whole point.
  * **a slug is a filename here.** `../../` must not reach out of the folder, in either
    direction: writing or reading.
  * **the colour agrees with the Chrome window.** The sidebar's green face and the
    green-framed window on the taskbar are one agent, and that only holds if both ask
    browser_label. Drawing a face must also not CONSUME a window colour, which is the
    same rule the browser panel follows.
  * **the version stamp moves.** It rides in the image URL; if it did not change, a new
    picture would look to the owner exactly like an upload that failed.

**renaming.py** moves a name. An agent carries its name in four places, and the one that
matters is the fourth: its own prompt. A rename that does not reach CLAUDE.md produces an
agent that calls itself the old thing forever. So:

  * the prompt is **edited, never regenerated** - that file has been written by hand and
    by the agent since setup;
  * renaming twice leaves one block, not two;
  * the old name is kept in the sentence, because the agent's own notes are full of it;
  * a failure in one of the four is reported, not swallowed - a .env that did not take
    means it still signs with the old name.

Run: python tools/test_faces.py
"""

import base64
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SRC = os.path.join(ROOT, "src")
sys.path.insert(0, SRC)

from wizard import avatars  # noqa: E402
from wizard import browser_label  # noqa: E402
from wizard import renaming  # noqa: E402

FAILED = []
CHECKS = [0]


def ok(cond, label, extra=""):
    CHECKS[0] += 1
    if not cond:
        FAILED.append(label + ((" :: " + str(extra)) if extra else ""))
        print("FAIL " + label + ((" :: " + str(extra)) if extra else ""))


def eq(got, want, label):
    ok(got == want, "%s (got %r, want %r)" % (label, got, want))


def _dir():
    return tempfile.mkdtemp(prefix="olivaw-faces-")


# A real 4x4 PNG and a real 1x1 JPEG, so the sniff is exercised against actual containers.
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAQAAAAECAYAAACp8Z5+AAAAFUlEQVR42mNk+M/wn4GB"
    "gYGJAQ0AAEAcAQGS0YvUAAAAAElFTkSuQmCC")
JPEG = base64.b64decode(
    "/9j/4AAQSkZJRgABAQEAYABgAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8U"
    "HRofHh0aHBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/wAALCAABAAEBAREA"
    "/8QAFAABAAAAAAAAAAAAAAAAAAAACf/EABQQAQAAAAAAAAAAAAAAAAAAAAD/2gAIAQEA"
    "AD8AKp//2Q==")


# ── the face ─────────────────────────────────────────────────────────────────

def test_an_agent_always_has_a_face():
    """Initials in its own colour is the floor, not a placeholder.

    A sidebar row with an empty circle is worse than no circle, and it is the state every
    agent is in before the owner has chosen anything.
    """
    d = _dir()
    look = avatars.look("daneel", u"Daneel", d)
    eq(look["kind"], "initials", "with nothing chosen, the face is its initials")
    eq(look["initials"], u"DA", "one word gives its first two letters")
    ok(re.match(r"^#[0-9A-Fa-f]{6}$", look["hex"]), "and a real colour", look["hex"])
    eq(avatars.initials(u"Agente principal"), u"AP", "two words give one letter each")
    eq(avatars.initials(u"Banco de la Nación"), u"BN",
       "the joining words are skipped, because nobody reads them")
    eq(avatars.initials(u"Ábaco"), u"ÁB", "accents are letters")
    eq(avatars.initials(u""), u"?", "and a nameless agent still gets a disc")


def test_the_face_wears_the_colour_of_its_own_chrome_window():
    """The one reason the colour is not decorative.

    browser_label hands out a slot per agent, first come, written down. If this picked its
    own colour, the owner's "the green one is Ábaco" would be true in one place and false
    in the other.
    """
    d = _dir()
    browser_label.assign_all(["default", "daneel", "heraldo"], d)
    for slug in ("default", "daneel", "heraldo"):
        eq(avatars.look(slug, slug, d)["hex"],
           browser_label.look_for(slug, d, assign=False)["hex"],
           "%s's face is the colour of its window" % slug)


def test_drawing_a_face_does_not_consume_a_window_colour():
    """Same rule the browser panel follows: a READ must not assign.

    Otherwise merely opening the console hands slots to every agent in roster order, and
    the colours shuffle the day one is added - which is the thing slot_for exists to stop.
    """
    d = _dir()
    before = os.path.exists(browser_label.labels_path(d))
    avatars.look("nuevo", u"Nuevo", d)
    after = json.load(io.open(browser_label.labels_path(d), encoding="utf-8")) \
        if os.path.exists(browser_label.labels_path(d)) else {}
    ok(not before, "no colours were written down yet")
    ok("nuevo" not in (after.get("slots") or {}),
       "and looking at the agent did not claim one", after)


def test_an_uploaded_picture_is_re_encoded_by_us():
    """The bytes served back to the console are ours, not the uploader's.

    With Pillow present the image is decoded and re-written as PNG, which drops every
    byte of the original container. Without it the sniff plus a size cap is the gate, and
    the test says which path ran rather than pretending both are the same.
    """
    d = _dir()
    res = avatars.set_picture("daneel", base64.b64encode(JPEG).decode(), d)
    ok(res["ok"], "a JPEG is accepted")
    got = avatars.read("daneel", d)
    ok(got is not None, "and can be read back")
    raw, mime = got
    try:
        import PIL  # noqa: F401
        has_pil = True
    except Exception:  # noqa: BLE001
        has_pil = False
    if has_pil:
        eq(mime, "image/png", "re-encoded to PNG")
        ok(raw.startswith(b"\x89PNG"), "and the bytes really are a PNG")
        ok(raw != JPEG, "nothing of the original container survived")
    else:
        eq(mime, "image/jpeg", "kept as-is when there is no Pillow to re-encode with")


def test_the_re_encode_still_happens_when_the_console_is_started_as_a_script():
    """The way the shortcut starts it, which is the only way an owner ever does.

    Python puts a script's own directory first on sys.path. For wizard_server.py that is
    src/wizard, and that directory holds numbers.py - so stdlib `numbers` becomes this
    package's module, which cannot be imported outside its package. Pillow imports
    `numbers` from its PNG plugin, swallows the failure, registers no PNG decoder, and
    quietly stops being able to open any image. Nothing raises. The only visible symptom
    was an uploaded avatar stored at its original size, original container and all - the
    one thing the re-encode exists to prevent.

    So this runs a real child process with that directory first on the path, imports the
    server the way the script does, and uploads a PNG that is not square.
    """
    code = (
        "import sys, os, io, base64, json, tempfile\n"
        "root = sys.argv[1]\n"
        # Exactly what `python src/wizard/wizard_server.py` puts there.
        "sys.path.insert(0, os.path.join(root, 'src', 'wizard'))\n"
        "sys.path.insert(0, os.path.join(root, 'src'))\n"
        "os.environ['OLIVAW_INSTALL_DIR'] = tempfile.mkdtemp()\n"
        "from wizard import wizard_server\n"       # the bootstrap that must clean the path
        "from wizard import avatars\n"
        "raw = open(sys.argv[2], 'rb').read()\n"
        "r = avatars.set_picture('zz', base64.b64encode(raw).decode(),\n"
        "                        os.environ['OLIVAW_INSTALL_DIR'])\n"
        "print(json.dumps({'sent': len(raw), 'stored': r['bytes']}))\n")
    d = _dir()
    src = os.path.join(d, "wide.png")
    # 64x16: nothing but a crop could make this square, so an unchanged byte count is
    # proof the re-encode did not run.
    try:
        from PIL import Image
    except Exception:  # noqa: BLE001
        print("   (no Pillow - the re-encode path does not exist on this machine)")
        return
    Image.new("RGB", (64, 16), (200, 30, 90)).save(src, format="PNG")
    runner = os.path.join(d, "run_as_script.py")
    io.open(runner, "w", encoding="utf-8").write(code)
    p = subprocess.run([sys.executable, runner, ROOT, src],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=300)
    ok(p.returncode == 0, "the console imports with its own directory on the path",
       (p.stderr or "")[-300:])
    if p.returncode != 0:
        return
    line = [l for l in (p.stdout or "").strip().splitlines() if l.startswith("{")]
    ok(bool(line), "the child reported a result", (p.stdout or "")[-200:])
    if not line:
        return
    got = json.loads(line[-1])
    ok(got["stored"] != got["sent"],
       "an uploaded image is still re-encoded, not passed through", got)


def test_a_file_that_is_not_an_image_is_refused():
    d = _dir()
    for label, blob in (("an HTML page", b"<html><script>alert(1)</script></html>"),
                        ("an SVG", b"<svg xmlns='http://www.w3.org/2000/svg'></svg>"),
                        ("a zip", b"PK\x03\x04rest"),
                        ("empty", b"")):
        try:
            avatars.set_picture("daneel", base64.b64encode(blob).decode(), d)
            ok(False, "%s is refused" % label)
        except ValueError:
            ok(True, "%s is refused" % label)
    try:
        avatars.set_picture("daneel", "not base64 at all !!!", d)
        ok(False, "a body that is not base64 is refused")
    except ValueError:
        ok(True, "a body that is not base64 is refused")


def test_a_slug_cannot_reach_out_of_the_folder():
    """The slug becomes a filename, in both directions."""
    d = _dir()
    for bad in ("../../etc/passwd", "..", "a/b", "A" * 40, "", "daneel.png", "dan eel"):
        try:
            avatars.set_picture(bad, base64.b64encode(PNG).decode(), d)
            ok(False, "writing refuses %r" % bad)
        except ValueError:
            ok(True, "writing refuses %r" % bad)
        eq(avatars.read(bad, d), None, "reading refuses %r" % bad)

    # Reading is normally gated twice over - the slug is checked AND the record has to
    # exist - so the first gate is invisible until the second one is satisfied by a
    # hand-written faces.json. That is the state this covers: a row that claims a picture
    # for a slug that is really a path. Without the check in read(), the filename is built
    # from it and the server hands out whatever is there.
    os.makedirs(avatars.store_dir(d), exist_ok=True)
    io.open(os.path.join(avatars.store_dir(d), "faces.json"), "w",
            encoding="utf-8").write(json.dumps({
                "../../secret": {"kind": "image", "file": "x.png", "v": 1},
                "..\\\\..\\\\secret": {"kind": "image", "file": "x.png", "v": 1}}))
    outside = os.path.join(os.path.dirname(os.path.dirname(avatars.store_dir(d))),
                           "secret.png")
    io.open(outside, "wb").write(PNG)
    eq(avatars.read("../../secret", d), None,
       "a hand-written row pointing out of the folder is still refused")
    eq(avatars.read("..\\\\..\\\\secret", d), None, "the same, spelled the Windows way")


def test_a_picture_that_changed_on_disk_is_not_served():
    """faces.json says "image"; the file says otherwise. Serve nothing.

    This is the state after a hand edit or a half-finished copy, and it is the only one
    where the console would hand a browser bytes nobody checked.
    """
    d = _dir()
    avatars.set_picture("daneel", base64.b64encode(PNG).decode(), d)
    path = os.path.join(avatars.store_dir(d), "daneel.png")
    io.open(path, "wb").write(b"<html>not an image any more</html>")
    eq(avatars.read("daneel", d), None, "a file that stopped being an image is not served")
    eq(avatars.look("daneel", u"Daneel", d)["kind"], "image",
       "the record still says image - the fallback is at serve time, not a silent rewrite")


def test_the_version_stamp_moves_on_every_change():
    """It is the cache key in the <img> URL. A stamp that did not move looks, to the
    owner, exactly like an upload that silently failed."""
    d = _dir()
    v0 = avatars.look("daneel", u"D", d)["v"]
    avatars.set_picture("daneel", base64.b64encode(PNG).decode(), d)
    v1 = avatars.look("daneel", u"D", d)["v"]
    avatars.set_mark("daneel", emoji=avatars.EMOJI[0], install_dir=d)
    v2 = avatars.look("daneel", u"D", d)["v"]
    avatars.clear("daneel", d)
    v3 = avatars.look("daneel", u"D", d)["v"]
    ok(v0 < v1 < v2 < v3, "every change bumps it", (v0, v1, v2, v3))


def test_choosing_a_symbol_removes_the_picture_file():
    d = _dir()
    avatars.set_picture("daneel", base64.b64encode(PNG).decode(), d)
    avatars.clear("daneel", d)
    eq(avatars.read("daneel", d), None, "clearing stops serving it")
    ok(not os.path.exists(os.path.join(avatars.store_dir(d), "daneel.png")),
       "and the file is gone, not orphaned")
    eq(avatars.look("daneel", u"Daneel", d)["kind"], "initials", "back to initials")


def test_a_symbol_must_be_one_we_ship():
    d = _dir()
    avatars.set_mark("daneel", emoji=avatars.EMOJI[2], install_dir=d)
    eq(avatars.look("daneel", u"D", d)["emoji"], avatars.EMOJI[2], "a listed symbol sticks")
    for bad in (u"<img src=x>", u"\u0000", u"zzz"):
        try:
            avatars.set_mark("daneel", emoji=bad, install_dir=d)
            ok(False, "refuses %r" % bad)
        except ValueError:
            ok(True, "refuses %r" % bad)


def test_a_colour_must_be_a_colour():
    d = _dir()
    avatars.set_mark("daneel", color="#1E9E6A", install_dir=d)
    eq(avatars.look("daneel", u"D", d)["hex"], "#1E9E6A", "a hex colour is kept")
    for bad in ("red", "#12345", "javascript:alert(1)", "#12345g", "url(x)"):
        try:
            avatars.set_mark("daneel", color=bad, install_dir=d)
            ok(False, "refuses %r" % bad)
        except ValueError:
            ok(True, "refuses %r" % bad)
    eq(avatars.look("daneel", u"D", d)["hex"], "#1E9E6A",
       "and a refused colour did not overwrite the good one")


def test_a_deleted_agent_takes_its_face_with_it():
    d = _dir()
    avatars.set_picture("baco", base64.b64encode(PNG).decode(), d)
    avatars.forget("baco", d)
    eq(avatars.read("baco", d), None, "nothing left to serve")
    ok("baco" not in avatars._read_meta(d), "and no row left behind")
    ok(not os.listdir(avatars.store_dir(d)) or
       "baco.png" not in os.listdir(avatars.store_dir(d)), "nor a file")


def test_the_console_and_the_server_ship_the_same_symbols():
    """The picker is drawn from a list in app.js and validated against a tuple in
    avatars.py. They are two copies of one fact, so they are compared here: a symbol in
    the picker that the server refuses is a button that does nothing."""
    js = io.open(os.path.join(ROOT, "src", "wizard", "web", "app.js"),
                 encoding="utf-8").read()
    m = re.search(r"var FACE_EMOJI = \[(.*?)\];", js, re.S)
    ok(m is not None, "app.js declares the symbol list")
    if not m:
        return
    listed = re.findall(r'"((?:[^"\\]|\\.)*)"', m.group(1))
    decoded = []
    for s in listed:
        # JS "\u{1F916}" and "\u2699\uFE0F" -> the characters Python stores.
        s = re.sub(r"\\u\{([0-9A-Fa-f]+)\}", lambda mm: chr(int(mm.group(1), 16)), s)
        s = re.sub(r"\\u([0-9A-Fa-f]{4})", lambda mm: chr(int(mm.group(1), 16)), s)
        decoded.append(s)
    eq(decoded, list(avatars.EMOJI), "the picker offers exactly what the server accepts")

    m2 = re.search(r"var FACE_HEX = \[(.*?)\];", js, re.S)
    ok(m2 is not None, "app.js declares the colour list")
    if m2:
        hexes = re.findall(r'"(#[0-9A-Fa-f]{6})"', m2.group(1))
        eq([h.upper() for h in hexes],
           [p[0].upper() for p in browser_label.PALETTE],
           "and the same eight colours the windows wear")


# ── the name ─────────────────────────────────────────────────────────────────

GENERATED = (u"# Eres el cerebro de Daneel — un agente Hermes\n\n"
             u"## Quién eres\n"
             u"Eres el núcleo de razonamiento de un agente Hermes llamado **Daneel**, "
             u"al que llega **Walt** como dueño y desarrollador.\n\n"
             u"## Lo que aprendí solo\n"
             u"El cliente del martes se llama Ruiz. No me borres.\n")


def _ws(text=GENERATED):
    d = _dir()
    p = os.path.join(d, "CLAUDE.md")
    io.open(p, "w", encoding="utf-8", newline="\n").write(text)
    return d, p


def test_a_rename_reaches_the_agents_own_prompt():
    """The one that decides what it says when somebody asks who it is."""
    d, p = _ws()
    eq(renaming.rewrite_prompt(p, u"Chalenus", u"Daneel"), "written", "the file is written")
    got = io.open(p, encoding="utf-8").read()
    ok(u"Te llamas **Chalenus**" in got, "it is told its name")
    ok(u"Antes te llamabas «Daneel»" in got,
       "and that the old name in its own notes is still it")
    ok(u"El cliente del martes se llama Ruiz" in got,
       "what the agent wrote itself is untouched")
    ok(got.startswith(u"# Eres el cerebro de Chalenus"),
       "the generated heading is retitled too", got.splitlines()[0])
    ok(u"llamado **Chalenus**" in got, "and the generated identity line")
    ok(u"Daneel" not in got.split(renaming.BEGIN)[0],
       "no stale name left above the block", got.split(renaming.BEGIN)[0][-120:])


def test_renaming_twice_leaves_one_block():
    d, p = _ws()
    renaming.rewrite_prompt(p, u"Chalenus", u"Daneel")
    renaming.rewrite_prompt(p, u"Giskard", u"Chalenus")
    got = io.open(p, encoding="utf-8").read()
    eq(got.count(renaming.BEGIN), 1, "one marker")
    eq(got.count(renaming.END), 1, "one closer")
    ok(u"Te llamas **Giskard**" in got, "the current name")
    ok(u"«Chalenus»" in got, "and the one it had a moment ago")
    ok(u"El cliente del martes se llama Ruiz" in got, "still not touching its own notes")


def test_a_hand_written_prompt_is_added_to_not_rewritten():
    """The common case after a few months: the file no longer looks generated at all."""
    hand = u"# Mis reglas\n\nContesta corto. Nunca uses emojis.\n"
    d, p = _ws(hand)
    renaming.rewrite_prompt(p, u"Forja", u"forja")
    got = io.open(p, encoding="utf-8").read()
    ok(got.startswith(u"# Mis reglas"), "their heading survives")
    ok(u"Nunca uses emojis" in got, "their rules survive")
    ok(u"Te llamas **Forja**" in got, "and the name is appended")


def test_an_agent_with_no_prompt_file_gets_one():
    d = _dir()
    p = os.path.join(d, "CLAUDE.md")
    eq(renaming.rewrite_prompt(p, u"Nueva", u""), "created", "a missing file is created")
    got = io.open(p, encoding="utf-8").read()
    ok(u"Te llamas **Nueva**" in got, "with the name in it")
    ok(u"Antes te llamabas" not in got, "and no invented history")


def test_a_name_is_cleaned_not_refused():
    eq(renaming.clean_name(u"  Mi   Agente  "), u"Mi Agente", "runs of spaces collapse")
    eq(renaming.clean_name(u"Ábaco"), u"Ábaco", "accents are kept")
    eq(len(renaming.clean_name(u"x" * 90)), renaming.MAX_NAME, "a long one is clipped")
    ok(u"\n" not in renaming.clean_name(u"Dos\nLineas"), "control characters go")
    for bad in (u"", u"   ", u"\u0000\u0001"):
        try:
            renaming.clean_name(bad)
            ok(False, "an empty name is refused: %r" % bad)
        except ValueError:
            ok(True, "an empty name is refused: %r" % bad)


def test_an_unknown_agent_is_named_not_renamed():
    """And the sentence has to be the right one.

    The registry refuses a junk row on its own, so "it raised something" proves nothing:
    without the lookup in renaming.py the owner gets a conflict message about two agents
    sharing a resource, when what happened is that the agent she named does not exist.
    """
    d = _dir()
    for bad in ("../x", "", "A" * 40, "dan eel"):
        try:
            renaming.rename(bad, u"X", install_dir=d)
            ok(False, "a malformed name is refused: %r" % bad)
        except ValueError as e:
            ok(u"válido" in str(e), "a malformed name is refused by shape: %r" % bad, str(e))
    try:
        renaming.rename("nosuchagent", u"X", install_dir=d)
        ok(False, "an agent that does not exist is refused")
    except ValueError as e:
        ok(u"ningún agente" in str(e) and u"nosuchagent" in str(e),
           "and the message says which agent is missing, not something about conflicts",
           str(e))


def test_the_block_is_the_same_whichever_way_it_is_built():
    """block() is what the test above reads and what rewrite_prompt writes. If they ever
    stop being the same function, the marker survives and the sentence does not."""
    b = renaming.block(u"Heraldo", u"heraldo")
    ok(b.startswith(renaming.BEGIN) and b.rstrip().endswith(renaming.END),
       "it is marker-delimited")
    ok(u"Te llamas **Heraldo**" in b, "and says the name once")
    eq(renaming.block(u"Solo", u"Solo").count(u"Antes te llamabas"), 0,
       "renaming to the same name says nothing about a past")


def main():
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            print("-- " + name)
            try:
                fn()
            except Exception as e:  # noqa: BLE001
                CHECKS[0] += 1
                FAILED.append("%s raised %s: %s" % (name, type(e).__name__, e))
                print("FAIL %s raised %s: %s" % (name, type(e).__name__, e))
    print("\n%d passed, %d failed" % (CHECKS[0] - len(FAILED), len(FAILED)))
    for f in FAILED:
        print("  FAILED: " + f)
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
