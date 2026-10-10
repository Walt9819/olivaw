r"""Whose window is this? Answered everywhere, permanently.

The complaint
-------------
Each agent gets its own Chrome window (see ``browser_setup``), and until now the only
thing that said which was which was a card page opened as the first tab. That works for
about one minute. The moment the agent navigates - which is the entire point of giving it
a browser - the window is an anonymous Chrome among six anonymous Chromes. Measured on
this machine before this existed, seven windows were open and four had already lost their
card::

    Ventana de Analecta - Google Chrome          <- still identifiable
    Ventana de Abaco - Google Chrome             <- still identifiable
    Ventana de Forja - Google Chrome             <- still identifiable
    Online C Compiler - online editor            <- whose?
    Heraldo Comercial iGalenus                   <- whose?
    Examen Oftalmologico ... - Google Gemini     <- whose?
    En donde estan tus aliados estrategicos?     <- whose?

What was tried and does not work
--------------------------------
``--load-extension`` with a content script is the obvious answer and Chrome 154 ignores it
outright: the extension never loads, the content script never runs, and nothing says so.
Verified here before writing any of this.

Writing ``Local State`` from scratch is the other obvious answer, and it makes Chrome
abort at startup (``bad_optional_access`` in -fno-exceptions mode). So this edits the
handful of keys it cares about *in place* and leaves everything else exactly as Chrome
wrote it.

Three signals, because no single one covers every place you look
----------------------------------------------------------------
* **Colour** - the window frame and tab strip are tinted per agent
  (``browser.theme.user_color``). This is the one that works when you are looking at
  thumbnails in Alt-Tab or at a wall of windows: you recognise it without reading.
* **Profile name and avatar** - what the profile chip, Chrome's own task manager and the
  Windows jump list call this window.
* **The window TITLE** - ``Daneel · Gmail`` instead of ``Gmail``. This is the only one
  that reaches the taskbar hover text and Alt-Tab labels, and it is the one the owner
  actually asked for: a name, not a colour to decode.

How the title is done, and why it needs a held connection
---------------------------------------------------------
There is no flag for it. The title comes from the page, so the page has to be told - and
the only channel into a page we already own is the DevTools port each window is listening
on for the agent anyway.

``Page.addScriptToEvaluateOnNewDocument`` is what survives navigation, and it is scoped to
the CDP **session** that registered it. A connect-stamp-disconnect pass therefore works
once and then quietly stops working at the next navigation - measured, and the reason this
module holds a socket instead. One browser-level session per window, ``Target.setAutoAttach``
so Chrome hands us every new tab, three commands per tab, and then it sits idle. No
polling.

What it costs, stated plainly
-----------------------------
``document.title`` really is changed, so the agent sees ``Daneel · Gmail`` too, and so
would a page that reads its own title. That is the price of the only mechanism Chrome
leaves open, it is visible rather than hidden, and ``OLIVAW_BROWSER_LABEL=0`` turns the
title half off while keeping the colour and the name.

Nothing here can break the browser: every failure path logs and leaves the window exactly
as it was. The agent's own CDP driving is unaffected - DevTools allows several clients at
once, and this one only ever registers a script and reads events.
"""

import io
import json
import os
import socket
import struct
import threading
import time
import urllib.error
import urllib.request

from . import browser_setup

LABELS_NAME = "browser-labels.json"

# Eight frame colours, distinguishable from each other and legible as a tint on both the
# light and the dark Chrome frame. Stored as Chrome stores them: ARGB packed into a signed
# 32-bit int. The hex travels to the console so the owner sees the same colour there.
PALETTE = (
    ("#5B7CFA", 0x5B, 0x7C, 0xFA),
    ("#1E9E6A", 0x1E, 0x9E, 0x6A),
    ("#D2803A", 0xD2, 0x80, 0x3A),
    ("#B45EC9", 0xB4, 0x5E, 0xC9),
    ("#CF5A72", 0xCF, 0x5A, 0x72),
    ("#2A9DB5", 0x2A, 0x9D, 0xB5),
    ("#8A8F3C", 0x8A, 0x8F, 0x3C),
    ("#C2543F", 0xC2, 0x54, 0x3F),
)
# Chrome's built-in avatars. Different enough to tell apart at taskbar size.
AVATARS = (26, 27, 28, 29, 30, 31, 32, 33)

SEP = " · "          # "Daneel · Gmail"


def sk_color(r, g, b):
    """Chrome keeps colours as ARGB in a SIGNED 32-bit int; Python would hand it an
    unsigned one and Chrome would ignore the pref."""
    v = (0xFF << 24) | (r << 16) | (g << 8) | b
    return v - (1 << 32) if v >= (1 << 31) else v


# ── which colour belongs to which agent, and it must never shuffle ───────────
def labels_path(install_dir=None):
    """Beside agents.json, not inside Hermes' home.

    This is Olivaw's own bookkeeping - which agent owns which colour - and it has to be
    the SAME file for everybody who asks. The supervisor knows its install dir and the
    console's status route does not, so defaulting to two different places is how the
    panel ends up previewing a colour the window will never wear.
    """
    if install_dir:
        return os.path.join(install_dir, LABELS_NAME)
    try:
        from . import agents_registry
        return os.path.join(agents_registry.install_root(), LABELS_NAME)
    except Exception:  # noqa: BLE001
        return os.path.join(browser_setup.hermes_home(), LABELS_NAME)


def _read_labels(install_dir=None):
    try:
        with io.open(labels_path(install_dir), encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def slot_for(slug, install_dir=None, assign=True):
    """A stable slot per agent, remembered on disk.

    Deliberately not ``hash(slug) % 8``: with seven agents that collides often, and two
    windows the same colour is the problem this exists to solve. Deliberately not the
    agent's position in the roster either - that shuffles every colour on the machine the
    day an agent is added, so the owner's hard-won "the green one is Ábaco" stops being
    true. So: first come, lowest free slot, written down.
    """
    slug = (slug or "").strip().lower()
    data = _read_labels(install_dir)
    slots = data.setdefault("slots", {}) if isinstance(data.get("slots", {}), dict) else {}
    if slug in slots:
        try:
            return int(slots[slug]) % len(PALETTE)
        except (TypeError, ValueError):
            pass
    if not assign:
        return 0
    taken = set()
    for v in slots.values():
        try:
            taken.add(int(v))
        except (TypeError, ValueError):
            pass
    nxt = 0
    while nxt in taken and nxt < len(PALETTE) * 4:
        nxt += 1
    slots[slug] = nxt
    data["slots"] = slots
    try:
        path = labels_path(install_dir)
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        tmp = path + ".tmp"
        with io.open(tmp, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(data, fh, indent=2, ensure_ascii=False)
        os.replace(tmp, path)
    except OSError:
        pass
    return nxt % len(PALETTE)


def assign_all(slugs, install_dir=None):
    """Give every agent on the machine its slot, in one pass, in the order given.

    Without this the console shows the same colour for everybody: a read must not hand out
    a slot (see `slot_for`), so an agent whose browser has never been launched has none,
    and `look_for(assign=False)` honestly answers "slot 0" for all of them. Seven windows
    previewed as the same blue is worse than no preview.

    Assigning for agents that actually exist is not the thing `slot_for` guards against -
    that is assigning for a name somebody merely typed into a box.
    """
    out = {}
    for slug in (slugs or []):
        slug = (slug or "").strip().lower()
        if slug:
            out[slug] = slot_for(slug, install_dir, assign=True)
    return out


def look_for(slug, install_dir=None, assign=True):
    """The whole visual identity of one agent's window."""
    i = slot_for(slug, install_dir, assign=assign)
    hexa, r, g, b = PALETTE[i]
    return {"slot": i, "hex": hexa, "color": sk_color(r, g, b), "avatar": AVATARS[i]}


# ── the profile: name, avatar, colour ────────────────────────────────────────
def _load(path):
    with io.open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _save(path, data):
    tmp = path + ".olivaw.tmp"
    with io.open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(data, fh, ensure_ascii=False)
    os.replace(tmp, path)


def stamp_profile(profile=None, name="", slug="", install_dir=None, home_url=""):
    """Name, avatar and frame colour, written into the profile Chrome already made.

    Only ever edits keys; never recreates a file. A profile that does not exist yet is not
    an error - Chrome makes it on first launch and the next pass stamps it.

    **Only call this when that profile's Chrome is NOT running.** Chrome keeps both files
    in memory and rewrites them periodically and on exit, so an edit made underneath a
    live window is thrown away at the next flush - measured on a real machine: of six
    running profiles stamped at once, two kept nothing, three kept only the colour and one
    kept only the name. `launch()` gets this right by construction (it stamps just before
    starting the process); `ensure_all` checks.
    """
    udd = browser_setup.data_dir(profile)
    look = look_for(slug or profile or "default", install_dir)
    out = {"ok": True, "changed": False, "profile": profile or "default",
           "data_dir": udd, "hex": look["hex"], "pending": False}

    ls_path = os.path.join(udd, "Local State")
    if os.path.isfile(ls_path):
        try:
            ls = _load(ls_path)
        except (OSError, ValueError):
            ls = None
        if isinstance(ls, dict):
            cache = ls.setdefault("profile", {}).setdefault("info_cache", {})
            row = cache.setdefault("Default", {})
            before = dict(row)
            row["name"] = name or slug or "Olivaw"
            row["is_using_default_name"] = False
            row["is_using_default_avatar"] = False
            row["avatar_icon"] = "chrome://theme/IDR_PROFILE_AVATAR_%d" % look["avatar"]
            row["profile_color_seed"] = look["color"]
            row["profile_highlight_color"] = look["color"]
            row["default_avatar_fill_color"] = look["color"]
            if row != before:
                try:
                    _save(ls_path, ls)
                    out["changed"] = True
                except OSError as e:
                    return dict(out, ok=False, detail=str(e))
    else:
        out["pending"] = True

    pf_path = os.path.join(udd, "Default", "Preferences")
    if os.path.isfile(pf_path):
        try:
            pf = _load(pf_path)
        except (OSError, ValueError):
            pf = None
        if isinstance(pf, dict):
            snapshot = json.dumps(pf, sort_keys=True)
            prof = pf.setdefault("profile", {})
            prof["name"] = name or slug or "Olivaw"
            prof["avatar_index"] = look["avatar"]
            prof["using_default_avatar"] = False
            prof["using_default_name"] = False
            th = pf.setdefault("browser", {}).setdefault("theme", {})
            th["user_color"] = look["color"]
            th["user_color2"] = look["color"]
            th["is_grayscale"] = False
            th["color_variant"] = 1
            # The card page as Home: the one click that always answers "whose window is
            # this?", and it survives everything the agent does to the tab.
            if home_url:
                pf.setdefault("homepage", home_url)
                pf["homepage"] = home_url
                pf["homepage_is_newtabpage"] = False
                pf.setdefault("browser", {})["show_home_button"] = True
            if json.dumps(pf, sort_keys=True) != snapshot:
                try:
                    _save(pf_path, pf)
                    out["changed"] = True
                except OSError as e:
                    return dict(out, ok=False, detail=str(e))
    else:
        out["pending"] = True
    return out


# ── a DevTools client, small enough to read in one sitting ───────────────────
class _WS(object):
    """The few hundred bytes of RFC 6455 a localhost DevTools client actually needs.

    No library: Olivaw runs on whatever Python the owner already has, and "pip install
    websockets" is the kind of step this product exists to avoid.
    """

    def __init__(self, url, timeout=1.0):
        if not url.startswith("ws://"):
            raise ValueError("only ws:// here")
        hostport, _, path = url[5:].partition("/")
        host, _, port = hostport.partition(":")
        self.sock = socket.create_connection((host, int(port or 80)), timeout=5.0)
        self.sock.settimeout(timeout)
        import base64
        key = base64.b64encode(os.urandom(16)).decode()
        self.sock.sendall((
            "GET /%s HTTP/1.1\r\nHost: %s\r\nUpgrade: websocket\r\n"
            "Connection: Upgrade\r\nSec-WebSocket-Key: %s\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n" % (path, hostport, key)).encode())
        buf = b""
        while b"\r\n\r\n" not in buf:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise OSError("handshake closed")
            buf += chunk
            if len(buf) > 65536:
                raise OSError("handshake too long")
        if b" 101 " not in buf.split(b"\r\n", 1)[0]:
            raise OSError("devtools did not upgrade")

    def send(self, text):
        data = text.encode("utf-8")
        mask = os.urandom(4)
        n = len(data)
        if n < 126:
            head = struct.pack("!BB", 0x81, 0x80 | n)
        elif n < 65536:
            head = struct.pack("!BBH", 0x81, 0x80 | 126, n)
        else:
            head = struct.pack("!BBQ", 0x81, 0x80 | 127, n)
        self.sock.sendall(head + mask + bytes(b ^ mask[i % 4]
                                              for i, b in enumerate(data)))

    def _exact(self, n):
        out = b""
        while len(out) < n:
            chunk = self.sock.recv(n - len(out))
            if not chunk:
                raise OSError("closed")
            out += chunk
        return out

    def recv(self):
        """One text frame, or None for a frame we do not care about.

        A recv timeout is NOT a dead socket - it is Chrome having nothing to say, which is
        most of the time - so it is raised as socket.timeout for the caller to shrug at.
        """
        b1, b2 = self._exact(2)
        op = b1 & 0x0F
        n = b2 & 0x7F
        if n == 126:
            n = struct.unpack("!H", self._exact(2))[0]
        elif n == 127:
            n = struct.unpack("!Q", self._exact(8))[0]
        if n > 8 * 1024 * 1024:
            raise OSError("frame too large")
        payload = self._exact(n) if n else b""
        if op == 0x8:
            raise OSError("devtools closed the connection")
        if op == 0x9:                       # ping -> pong, same payload
            mask = os.urandom(4)
            self.sock.sendall(struct.pack("!BB", 0x8A, 0x80 | len(payload)) + mask +
                              bytes(b ^ mask[i % 4] for i, b in enumerate(payload)))
            return None
        return payload.decode("utf-8", "replace") if op == 0x1 else None

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass


# The script every page gets. setInterval rather than a MutationObserver on purpose: an
# observer wide enough to catch a single-page app renaming itself fires on every DOM
# change of every page, and each fix() writes document.title, which is another mutation.
# A 1.5s tick costs nothing measurable and cannot feed itself.
#
# Two properties the first version did not have, both learned from a real window:
#
#   * **It heals.** The first version only declined to prefix a title that ALREADY began
#     with the tag - it could not undo one that had been prefixed twice. On a Canva tab,
#     which rewrites its own title constantly, that degenerated into 857 copies and a
#     16KB title. Stripping every leading copy and writing exactly one means any such
#     state collapses on the next tick, whatever caused it.
#   * **It installs once.** Each injection used to add another interval, so a window that
#     had been labelled a few times was running several copies of this. Now a second
#     injection just updates the tag - which is also what makes renaming an agent take
#     effect in a page that is already open, instead of only in the next one.
_TITLE_JS = """(() => {
  const TAG = %s;
  const fix = () => { try {
    const g = (typeof window !== "undefined") ? window : globalThis;
    const tag = (g.__olivawLabel && g.__olivawLabel.tag) || TAG;
    let t = document.title || "";
    if (!t) return;
    while (t.indexOf(tag) === 0) t = t.slice(tag.length);
    if (!t) return;
    const want = tag + t;
    if (document.title !== want) document.title = want;
  } catch (e) {} };
  const w = (typeof window !== "undefined") ? window : globalThis;
  if (w.__olivawLabel) {
    // Renamed. Strip the PREVIOUS tag first, or it is left stranded in the middle:
    // "Despues · Antes · Gmail".
    const prev = w.__olivawLabel.tag;
    w.__olivawLabel.tag = TAG;
    try {
      let t = document.title || "";
      while (prev && t.indexOf(prev) === 0) t = t.slice(prev.length);
      if (t && t !== document.title) document.title = t;
    } catch (e) {}
    fix();
    return;
  }
  w.__olivawLabel = { tag: TAG };
  fix();
  setInterval(fix, 1500);
})();"""


def _js_string(text):
    """A JS string literal that is safe wherever it lands.

    `json.dumps` escapes quotes and backslashes but leaves `<`, `>` and `&` alone, so a
    name containing `</script>` would survive verbatim. Nothing here ever puts this inside
    an HTML <script> tag - it goes over the DevTools wire - but the name is whatever the
    owner typed into a text box, this is the one place her text becomes code, and the
    four extra replacements cost nothing. U+2028/9 are line terminators to a JS parser.
    """
    out = json.dumps(text)
    for ch, esc in (("<", "\\u003c"), (">", "\\u003e"), ("&", "\\u0026"),
                    (" ", "\\u2028"), (" ", "\\u2029")):
        out = out.replace(ch, esc)
    return out


def title_script(name):
    return _TITLE_JS % _js_string((name or "Olivaw") + SEP)


def labelling_on():
    return (os.environ.get("OLIVAW_BROWSER_LABEL", "1") or "1").strip().lower() \
        not in ("0", "false", "no", "off")


def browser_ws(port, timeout=3.0):
    """The browser-level DevTools socket for a window, or "" when nothing answers."""
    try:
        raw = urllib.request.urlopen(
            "http://127.0.0.1:%d/json/version" % int(port), timeout=timeout).read()
        return (json.loads(raw.decode("utf-8", "replace"))
                .get("webSocketDebuggerUrl") or "")
    except (urllib.error.URLError, OSError, ValueError):
        return ""


class Labeller(threading.Thread):
    """Holds one DevTools session per window and names every tab that appears in it."""

    def __init__(self, port, name, log=None):
        threading.Thread.__init__(self, name="olivaw-label-%s" % port)
        self.daemon = True
        self.port = int(port)
        self.label = name or ""
        self.log = log
        self.stop_flag = threading.Event()
        # Set by whoever just saw this port answer. Without it a window that was closed
        # and reopened waits out the backoff - which doubles to a minute or two - while
        # the supervisor calls ensure() every fifteen seconds and is told "already
        # running". Measured: closing a window and reopening it left the new one
        # anonymous for longer than anybody would wait before calling it broken.
        self.wake_flag = threading.Event()
        self.pages = 0
        self.last_error = ""

    def _say(self, msg):
        if self.log:
            try:
                self.log(msg)
            except Exception:  # noqa: BLE001
                pass

    def stop(self):
        self.stop_flag.set()
        self.wake_flag.set()               # so it leaves the backoff at once

    def wake(self):
        """Somebody just saw this port answer. Stop waiting."""
        self.wake_flag.set()

    def run(self):
        backoff = 5
        while not self.stop_flag.is_set():
            # Cleared HERE, before the work, not between the work and the wait: `stop()`
            # and `wake()` both set this flag, and a clear() in the gap throws away a stop
            # that arrived a microsecond earlier - which showed up as a thread that
            # ignored stop() until its backoff ran out.
            self.wake_flag.clear()
            try:
                worked = self._session()
                backoff = 5 if worked else min(backoff * 2, 60)
            except Exception as e:  # noqa: BLE001
                self.last_error = str(e)
                backoff = min(backoff * 2, 60)
            if self.stop_flag.is_set():
                break
            # A window that is simply closed is the ordinary case, not an incident: wait
            # and look again rather than logging about it every few seconds forever. The
            # wait ends early when ensure() says the port is answering again.
            self.wake_flag.wait(backoff)

    def _session(self):
        url = browser_ws(self.port)
        if not url:
            return False
        ws = _WS(url, timeout=1.0)
        seen = set()
        nid = [0]

        def send(method, params=None, session=None):
            nid[0] += 1
            msg = {"id": nid[0], "method": method, "params": params or {}}
            if session:
                msg["sessionId"] = session
            ws.send(json.dumps(msg))

        try:
            # Chrome hands us every page target, present and future, on this one socket.
            send("Target.setAutoAttach", {"autoAttach": True,
                                          "waitForDebuggerOnStart": False,
                                          "flatten": True})
            while not self.stop_flag.is_set():
                try:
                    raw = ws.recv()
                except socket.timeout:
                    continue
                if not raw:
                    continue
                try:
                    msg = json.loads(raw)
                except ValueError:
                    continue
                method = msg.get("method")
                p = msg.get("params") or {}
                # A closed tab's session is gone; forgetting it keeps this set the size of
                # the window's open tabs rather than of every tab it has ever had.
                if method == "Target.detachedFromTarget":
                    seen.discard(p.get("sessionId"))
                    self.pages = len(seen)
                    continue
                if method != "Target.attachedToTarget":
                    continue
                sid = p.get("sessionId")
                info = p.get("targetInfo") or {}
                if info.get("type") != "page" or not sid or sid in seen:
                    continue
                seen.add(sid)
                self.pages = len(seen)
                # Built per tab, not once per session: the owner can rename an agent while
                # its window is open, and `ensure` hands the new name to this thread - but
                # a script string captured at connect time would keep using the old one
                # until the window was closed and reopened.
                js = title_script(self.label)
                send("Page.enable", {}, sid)
                # The registration is what survives navigation; the evaluate is what
                # names the page that is already open.
                send("Page.addScriptToEvaluateOnNewDocument", {"source": js}, sid)
                send("Runtime.evaluate", {"expression": js}, sid)
        finally:
            ws.close()
        return True


# ── the supervisor's side ────────────────────────────────────────────────────
_RUNNING = {}
_LOCK = threading.Lock()


def ensure(port, name, log=None):
    """Make sure one window is being labelled. Cheap and idempotent."""
    if not labelling_on():
        return {"ok": True, "labelling": False, "detail": "desactivado por entorno"}
    key = int(port)
    with _LOCK:
        th = _RUNNING.get(key)
        if th is not None and th.is_alive():
            if th.label != (name or ""):
                th.label = name or ""      # a rename takes effect on the next tab
            # Every caller of ensure() has just seen this port answer, so a thread sitting
            # out a backoff from when the window was closed should go and look now.
            th.wake()
            return {"ok": True, "labelling": True, "started": False, "pages": th.pages}
        th = Labeller(key, name, log=log)
        _RUNNING[key] = th
        th.start()
    return {"ok": True, "labelling": True, "started": True}


def stop_all():
    with _LOCK:
        for th in _RUNNING.values():
            th.stop()
        _RUNNING.clear()


def ensure_all(agents=None, hermes=None, log=None, install_dir=None, stamp=True):
    """Every agent: stamp the profile, then keep its window labelled.

    `agents` is the registry's extra rows; the main agent is always first and is not in
    there. Returns one row per agent so the caller can say what changed.

    `stamp=False` skips the profile half. Chrome reads the name and colour once at
    startup, so re-deciding them every few seconds buys nothing and costs two JSON parses
    per agent of a file that is ~100KB - while attaching the labeller to a window the
    owner just opened is exactly the thing that should be quick.
    """
    rows = [{"slug": "default", "profile": "default", "name": "Agente principal"}]
    for a in (agents or []):
        slug = (a.get("slug") or "").strip().lower()
        if not slug:
            continue
        rows.append({"slug": slug, "profile": (a.get("profile") or slug).strip(),
                     "name": a.get("name") or slug})

    # Every agent gets its slot in one pass, in roster order, before anything is stamped:
    # the console previews these, and an agent whose browser has never been launched would
    # otherwise have no slot and preview as everybody else's colour.
    assign_all([r["slug"] for r in rows], install_dir=install_dir)

    out = []
    for r in rows:
        prof = None if r["profile"] == "default" else r["profile"]
        try:
            port = browser_setup.port_for(prof, hermes)
        except Exception:  # noqa: BLE001
            port = 0
        live = bool(port) and browser_setup.probe(browser_setup.cdp_url(port))["ok"]

        # A running Chrome owns those files and will overwrite anything written under it,
        # so the stamp waits for a pass when the window is closed. That is not a delay
        # worth engineering around: Chrome only READS them at startup, so a colour written
        # now would not show until the next launch either way.
        if stamp and not live:
            card = browser_setup.card_path(prof)
            home = ("file:///" + card.replace("\\", "/").lstrip("/")) if card else ""
            res = stamp_profile(prof, r["name"], r["slug"], install_dir=install_dir,
                                home_url=home)
        else:
            res = {"ok": True, "changed": False, "pending": False,
                   "profile": r["profile"],
                   "hex": look_for(r["slug"], install_dir, assign=False)["hex"]}
        res["slug"] = r["slug"]
        res["name"] = r["name"]
        res["port"] = port
        res["open"] = live
        # Said out loud so the caller can tell "nothing to do" from "waiting for this
        # window to close", which look identical in a log otherwise.
        res["stamp_deferred"] = bool(stamp and live)
        if live:
            res.update(ensure(port, r["name"], log=log))
        else:
            res["labelling"] = False
        out.append(res)
    return out
