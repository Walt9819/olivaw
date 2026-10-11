# -*- coding: utf-8 -*-
"""Watching, and driving, an agent's Chrome window from somewhere else.

Chrome already streams: ``Page.startScreencast`` sends a JPEG every time the page changes
and ``Input.dispatchMouseEvent`` puts a click back in. Olivaw already holds a DevTools
socket to every agent window for the labelling, and already knows which port belongs to
which agent. So the new part here is small, and it is mostly about the three things
Chrome does that a naive reader of the protocol would not expect. All three were measured
on Chrome 154, not guessed:

1. **An occluded window sends nothing.** Windows tells Chrome a covered window is not
   visible and the stream goes completely silent - no error, no end, just no frames. The
   only hint is ``Page.screencastVisibilityChanged``. Two launch flags keep it rendering
   (see browser_setup.launch), and the viewer is told when it happens anyway, because a
   phone staring at a frozen picture deserves to know why.

2. **Frames are change-driven, and "no change" can mean none at all.** A screencast frame
   is produced when the page composites one. Open a stream onto a tab that finished
   painting a minute ago and Chrome sends *nothing* - not one frame, nothing - until
   something moves. A viewer would sit in front of a blank rectangle with no error to
   show for it, which is the worst possible version of this feature. Re-issuing
   ``startScreencast`` does not help either, for the same reason.
   ``Page.captureScreenshot`` does: it composites on demand. So a still is taken when the
   session opens, whenever the viewer asks, and on a slow heartbeat while nothing is
   moving - which also covers the window having been occluded, and gives an idle page a
   floor of about one frame every couple of seconds instead of a frozen picture.

3. **Backpressure is the caller's problem.** Chrome will not send the next frame until
   the last one is acked, so acking immediately is how you get 60fps - and how you get a
   queue of stale frames if the phone is slower than the page. The queue here is one
   frame deep: a late viewer should see *now*, not a backlog of five seconds ago.

Driving is armed, never standing. That browser is signed into the owner's accounts, so a
session that merely *can* click is a session that will eventually click something nobody
meant: control expires on its own, the window wears a banner the whole time it is live,
and the banner is removed by the same code path that disarms.
"""

import base64
import json
import os
import re
import socket
import threading
import time
import urllib.error
import urllib.request

from . import browser_label

# What goes over mobile data. Measured on Chrome 154: q40/720x500/everyNth=4 gives 15fps
# at ~75 KB/s with a tap landing in 76ms, which is the point where it still feels like a
# browser. q60/900x620/everyNth=1 gives 60fps at ~402 KB/s, which is wifi territory.
QUALITY = {
    "ahorro": {"quality": 30, "maxWidth": 600, "maxHeight": 420, "everyNthFrame": 6},
    "normal": {"quality": 40, "maxWidth": 720, "maxHeight": 500, "everyNthFrame": 4},
    "nitido": {"quality": 60, "maxWidth": 900, "maxHeight": 620, "everyNthFrame": 1},
}
DEFAULT_QUALITY = "normal"

# How long a hand stays on the wheel without being asked again.
ARM_SECONDS = 180
MAX_ARM = 900

_BANNER_ID = "__olivaw_remote_banner__"

# RFC 3986: a scheme is a letter followed by letters, digits, "+", "-" or ".".
_SCHEME = re.compile(r"^([a-zA-Z][a-zA-Z0-9+.\-]*):")

# Shown inside the page itself, not in the window frame, because that is the part an
# agent's own screenshot would also catch - if something is driving this browser, the
# evidence should be in the picture.
_BANNER_JS = u"""(() => {
  const ID = %s, ON = %s, WHO = %s;
  let el = document.getElementById(ID);
  if (!ON) { if (el) el.remove(); return "off"; }
  if (!el) {
    el = document.createElement("div");
    el.id = ID;
    el.style.cssText = "position:fixed;left:0;right:0;top:0;z-index:2147483647;" +
      "background:#b45309;color:#fff;font:600 13px/28px system-ui,sans-serif;" +
      "text-align:center;height:28px;pointer-events:none;letter-spacing:.2px;" +
      "box-shadow:0 1px 6px rgba(0,0,0,.35)";
    (document.body || document.documentElement).appendChild(el);
  }
  el.textContent = "Olivaw: " + WHO + " está manejando esta ventana";
  return "on";
})()"""


def _js(value):
    """A JSON string is a JavaScript string, and json.dumps escapes everything that
    would otherwise end the literal early."""
    return json.dumps(value, ensure_ascii=True)


def _targets(port, timeout=2.0):
    try:
        raw = urllib.request.urlopen(
            "http://127.0.0.1:%d/json/list" % int(port), timeout=timeout).read()
        return json.loads(raw.decode("utf-8", "replace"))
    except (urllib.error.URLError, OSError, ValueError):
        return []


def pages(port):
    """The tabs a person would see, newest-looking first, without the machinery."""
    out = []
    for t in _targets(port):
        if t.get("type") != "page":
            continue
        url = t.get("url") or ""
        if url.startswith("devtools://") or url.startswith("chrome-extension://"):
            continue
        out.append({"id": t.get("id"), "title": (t.get("title") or "").strip(),
                    "url": url, "ws": t.get("webSocketDebuggerUrl") or ""})
    return out


class Session(object):
    """One remote view of one agent's browser.

    Owns a DevTools socket and a reader thread. Frames go into a one-deep slot the
    server drains; commands go in from whatever thread the server is on, behind a lock,
    because a WebSocket frame written by two threads at once is a corrupt frame.
    """

    def __init__(self, port, agent="", who=u"el teléfono", quality=DEFAULT_QUALITY,
                 log=None):
        self.port = int(port)
        self.agent = agent
        self.who = who
        self.quality = quality if quality in QUALITY else DEFAULT_QUALITY
        self.log = log or (lambda *_a: None)
        self._ws = None
        self._lock = threading.Lock()
        self._seq = 0
        self._stop = threading.Event()
        self._reader = None
        self.frame = None            # (bytes, metadata) - only ever the newest
        self.frame_at = 0.0
        self._wake = threading.Event()
        self.meta = {}
        self.visible = True
        self.armed_until = 0.0
        self.target = None
        self.error = ""
        self.frames_sent = 0
        self._shots = set()          # ids of captureScreenshot calls still in flight
        self._metrics = set()        # ... and of getLayoutMetrics
        self._shot_at = 0.0

    # ── lifecycle ──────────────────────────────────────────────────────────────────
    def open(self, target_id=""):
        tabs = pages(self.port)
        if not tabs:
            self.error = u"Esa ventana no tiene ninguna pestaña abierta."
            return False
        tab = None
        if target_id:
            tab = next((t for t in tabs if t["id"] == target_id), None)
        tab = tab or tabs[0]
        if not tab.get("ws"):
            self.error = u"Chrome no ofrece conexión para esa pestaña."
            return False
        try:
            # A long socket timeout would stall every command behind a quiet page; a
            # short one turns "nothing happened" into an exception on every tick. The
            # reader shrugs at timeouts, so this is just how often it gets to check
            # whether it has been told to stop.
            self._ws = browser_label._WS(tab["ws"], timeout=1.0)
        except OSError as e:
            self.error = u"No pude conectar con esa pestaña (%s)." % e
            return False
        self.target = tab
        self._send("Page.enable")
        self._send("Runtime.enable")
        self._send("Page.bringToFront")
        self._reader = threading.Thread(target=self._read_loop,
                                        name="olivaw-screencast-%d" % self.port)
        self._reader.daemon = True
        self._reader.start()
        self.start_stream()
        # The first picture does not wait for the page to do something, and neither
        # does the size a tap is scaled against: see the notes at the top about a tab
        # that finished painting before anyone looked at it.
        self.measure()
        self.shoot()
        return True

    def close(self):
        self._stop.set()
        try:
            self.disarm()
        except Exception:  # noqa: BLE001
            pass
        try:
            self._send("Page.stopScreencast")
        except Exception:  # noqa: BLE001
            pass
        if self._ws:
            try:
                self._ws.close()
            except Exception:  # noqa: BLE001
                pass
        self._wake.set()

    # ── talking to Chrome ──────────────────────────────────────────────────────────
    def _send(self, method, params=None):
        with self._lock:
            if not self._ws:
                raise OSError("sin conexión")
            self._seq += 1
            self._ws.send(json.dumps({"id": self._seq, "method": method,
                                      "params": params or {}}))
            return self._seq

    def start_stream(self, quality=None):
        if quality and quality in QUALITY:
            self.quality = quality
        self._send("Page.startScreencast", dict(format="jpeg", **QUALITY[self.quality]))

    def _read_loop(self):
        while not self._stop.is_set():
            try:
                raw = self._ws.recv()
            except socket.timeout:
                # BEFORE the OSError arm, never after: since Python 3.10 socket.timeout
                # IS TimeoutError, which subclasses OSError, so catching OSError first
                # treats every quiet second as a dead connection. That killed the reader
                # on the first second of a page that was not moving - no frames ever
                # arrived, and because the metadata that sizes a tap comes with a frame,
                # taps then landed wherever the fallback size happened to put them.
                continue
            except OSError:
                if not self._stop.is_set():
                    self.error = u"Chrome cerró la conexión."
                    self._wake.set()
                return
            except Exception:  # noqa: BLE001
                continue
            if not raw:
                continue
            try:
                msg = json.loads(raw)
            except ValueError:
                continue
            method = msg.get("method")
            if method == "Page.screencastFrame":
                p = msg["params"]
                try:
                    self._send("Page.screencastFrameAck",
                               {"sessionId": p.get("sessionId")})
                except Exception:  # noqa: BLE001
                    pass
                self.meta = p.get("metadata") or self.meta
                # One slot, overwritten: a viewer that fell behind wants the newest
                # picture, not the oldest one it has not seen yet.
                self.frame = base64.b64decode(p.get("data") or "")
                self.frame_at = time.time()
                self._wake.set()
            elif method == "Page.screencastVisibilityChanged":
                self.visible = bool((msg.get("params") or {}).get("visible"))
                self._wake.set()
            elif method == "Inspector.detached":
                self.error = u"La pestaña se cerró."
                self._wake.set()
                return
            elif msg.get("id") in self._metrics:
                self._metrics.discard(msg["id"])
                r = msg.get("result") or {}
                vp = r.get("cssLayoutViewport") or {}
                w, h = vp.get("clientWidth"), vp.get("clientHeight")
                if w and h:
                    self.meta = dict(self.meta or {})
                    self.meta.update({"deviceWidth": w, "deviceHeight": h})
            elif msg.get("id") in self._shots:
                # A still we asked for. It goes into the same slot as a streamed frame,
                # because to the viewer it is the same thing: what the page looks like now.
                self._shots.discard(msg["id"])
                data = ((msg.get("result") or {}).get("data") or "")
                if data:
                    self.frame = base64.b64decode(data)
                    self.frame_at = time.time()
                    self._wake.set()

    def wait_frame(self, timeout=1.0):
        """Block until there is something new to send, or the timeout runs out."""
        self._wake.wait(timeout)
        self._wake.clear()
        return self.frame

    def measure(self):
        """How big the page is, asked for rather than waited for.

        The size a tap is scaled against normally rides along with a screencast frame -
        which means a tab that is not moving has no size, and the first tap on it lands
        wherever the fallback guess puts it. (It landed 60 pixels below the button.)
        Page.getLayoutMetrics answers without needing the page to do anything.
        """
        try:
            with self._lock:
                if not self._ws:
                    return False
                self._seq += 1
                self._metrics.add(self._seq)
                self._ws.send(json.dumps({"id": self._seq,
                                          "method": "Page.getLayoutMetrics",
                                          "params": {}}))
        except Exception:  # noqa: BLE001
            return False
        return True

    def shoot(self):
        """Ask the page to composite one picture, now, whether or not it felt like it.

        Scaled to the same width the stream uses, so a still and a moving frame are the
        same size on the phone - otherwise every idle page would arrive at full device
        resolution and cost ten times the bandwidth of the video it replaces.
        """
        q = QUALITY[self.quality]
        params = {"format": "jpeg", "quality": q["quality"],
                  "captureBeyondViewport": False, "fromSurface": True}
        w = float(self.meta.get("deviceWidth") or 0)
        h = float(self.meta.get("deviceHeight") or 0)
        if w > 0 and h > 0:
            scale = min(1.0, q["maxWidth"] / w, q["maxHeight"] / h)
            params["clip"] = {"x": 0, "y": 0, "width": w, "height": h, "scale": scale}
        try:
            with self._lock:
                if not self._ws:
                    return False
                self._seq += 1
                self._shots.add(self._seq)
                self._ws.send(json.dumps({"id": self._seq,
                                          "method": "Page.captureScreenshot",
                                          "params": params}))
            self._shot_at = time.time()
        except Exception:  # noqa: BLE001
            return False
        return True

    def refresh(self):
        """What the viewer's "refresh" button does, and the idle heartbeat."""
        try:
            self._send("Page.bringToFront")
        except Exception:  # noqa: BLE001
            pass
        return self.shoot()

    def heartbeat(self, quiet_for=2.0):
        """Called by the viewer loop when nothing has arrived for a while.

        Bounded by _shot_at so a page that is genuinely stuck does not turn into a
        screenshot request every pass; one still every couple of seconds is enough to
        tell "nothing is happening" from "nothing is working".
        """
        if self.frame is not None and time.time() - self.frame_at < quiet_for:
            return False
        if time.time() - self._shot_at < quiet_for:
            return False
        self.measure()
        return self.shoot()

    # ── driving ────────────────────────────────────────────────────────────────────
    def arm(self, seconds=ARM_SECONDS):
        """Hands on the wheel, for a while, and visibly."""
        self.armed_until = time.time() + max(10, min(int(seconds or 0) or ARM_SECONDS,
                                                     MAX_ARM))
        self._banner(True)
        return self.armed_until

    def disarm(self):
        self.armed_until = 0.0
        self._banner(False)

    @property
    def armed(self):
        return time.time() < self.armed_until

    def _banner(self, on):
        try:
            self._send("Runtime.evaluate", {
                "expression": _BANNER_JS % (_js(_BANNER_ID), "true" if on else "false",
                                            _js(self.who)),
                "returnByValue": True})
        except Exception:  # noqa: BLE001
            pass

    def _css(self, nx, ny):
        """Normalised 0..1 from the phone to CSS pixels in the page.

        The phone cannot know how big the page is - it has a scaled JPEG - so it sends
        a fraction of the image and the conversion happens here, against the metadata
        Chrome attached to the frame that phone is actually looking at.
        """
        w = float(self.meta.get("deviceWidth") or 0) or 1280.0
        h = float(self.meta.get("deviceHeight") or 0) or 800.0
        return max(0.0, min(1.0, float(nx))) * w, max(0.0, min(1.0, float(ny))) * h

    def tap(self, nx, ny, kind="click", button="left", clicks=1, dx=0, dy=0):
        if not self.armed:
            return {"ok": False, "detail": u"El control no está activo."}
        x, y = self._css(nx, ny)
        try:
            if kind == "move":
                self._send("Input.dispatchMouseEvent",
                           {"type": "mouseMoved", "x": x, "y": y, "button": "none",
                            "buttons": 0})
            elif kind == "scroll":
                self._send("Input.dispatchMouseEvent",
                           {"type": "mouseWheel", "x": x, "y": y, "button": "none",
                            "buttons": 0, "deltaX": float(dx), "deltaY": float(dy)})
            else:
                n = max(1, min(int(clicks or 1), 3))
                self._send("Input.dispatchMouseEvent",
                           {"type": "mousePressed", "x": x, "y": y, "button": button,
                            "clickCount": n, "buttons": 1})
                self._send("Input.dispatchMouseEvent",
                           {"type": "mouseReleased", "x": x, "y": y, "button": button,
                            "clickCount": n, "buttons": 0})
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "detail": str(e)}
        return {"ok": True}

    def type_text(self, text):
        """Characters, one Input.insertText.

        insertText rather than a key event per character: it is one round trip instead of
        three per letter, and it is what makes an accent typed on a phone keyboard arrive
        as that accent instead of as a dead key nobody dispatched.
        """
        if not self.armed:
            return {"ok": False, "detail": u"El control no está activo."}
        text = (text or "")[:4000]
        if not text:
            return {"ok": True}
        try:
            self._send("Input.insertText", {"text": text})
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "detail": str(e)}
        return {"ok": True}

    _KEYS = {
        "Enter": (13, "Enter", "\r"), "Backspace": (8, "Backspace", ""),
        "Tab": (9, "Tab", ""), "Escape": (27, "Escape", ""),
        "ArrowUp": (38, "ArrowUp", ""), "ArrowDown": (40, "ArrowDown", ""),
        "ArrowLeft": (37, "ArrowLeft", ""), "ArrowRight": (39, "ArrowRight", ""),
        "Delete": (46, "Delete", ""), "Home": (36, "Home", ""), "End": (35, "End", ""),
        "PageUp": (33, "PageUp", ""), "PageDown": (34, "PageDown", ""),
    }

    def key(self, name):
        """One named key. Only the list above - a phone does not need F13, and an
        arbitrary keycode from outside is an instruction to Chrome we did not write."""
        if not self.armed:
            return {"ok": False, "detail": u"El control no está activo."}
        got = self._KEYS.get(name)
        if not got:
            return {"ok": False, "detail": u"Esa tecla no está permitida."}
        code, key_name, text = got
        try:
            for kind in ("keyDown", "keyUp"):
                p = {"type": kind, "key": key_name, "code": key_name,
                     "windowsVirtualKeyCode": code, "nativeVirtualKeyCode": code}
                if text and kind == "keyDown":
                    p["text"] = text
                self._send("Input.dispatchKeyEvent", p)
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "detail": str(e)}
        return {"ok": True}

    def navigate(self, url):
        """Go somewhere. http(s) only: file:// would turn a remote tap into a reader of
        this machine's disk, and javascript: would turn it into code in the owner's
        logged-in session."""
        if not self.armed:
            return {"ok": False, "detail": u"El control no está activo."}
        url = (url or "").strip()
        if not url:
            return {"ok": False, "detail": u"Sin dirección."}
        # Decide on the scheme BEFORE filling one in. Checking for "://" and prepending
        # https:// when it is missing looks equivalent and is not: "javascript:alert(1)"
        # has no "://", so it became "https://javascript:alert(1)" and sailed past the
        # startswith check below. Anything that already names a scheme must name one of
        # ours; only a bare "gmail.com" gets https:// put in front of it.
        scheme = _SCHEME.match(url)
        if scheme:
            if scheme.group(1).lower() not in ("http", "https"):
                return {"ok": False, "detail": u"Sólo http o https."}
        elif url.startswith("//"):
            return {"ok": False, "detail": u"Dirección incompleta."}
        else:
            url = "https://" + url
        if not url.lower().startswith(("http://", "https://")):
            return {"ok": False, "detail": u"Sólo http o https."}
        try:
            self._send("Page.navigate", {"url": url})
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "detail": str(e)}
        return {"ok": True, "url": url}

    def back(self):
        if not self.armed:
            return {"ok": False, "detail": u"El control no está activo."}
        try:
            self._send("Runtime.evaluate", {"expression": "history.back()"})
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "detail": str(e)}
        return {"ok": True}

    def state(self):
        return {"agent": self.agent, "port": self.port, "quality": self.quality,
                "visible": self.visible, "armed": self.armed,
                "armed_for": max(0, int(self.armed_until - time.time())),
                "title": (self.target or {}).get("title", ""),
                "url": (self.target or {}).get("url", ""),
                "error": self.error}


def port_of(agent, install_dir=None):
    """Which debugging port belongs to this agent, without starting anything."""
    from . import browser_setup
    profile = None if agent in ("", "default") else agent
    try:
        return int(browser_setup.port_for(profile))
    except Exception:  # noqa: BLE001
        return 0


def available(agent, install_dir=None):
    """Is there a window to look at right now?"""
    port = port_of(agent, install_dir)
    if not port:
        return {"ok": False, "detail": u"Este agente no tiene navegador configurado."}
    tabs = pages(port)
    if not tabs:
        return {"ok": False, "port": port,
                "detail": u"El navegador de este agente no está abierto."}
    return {"ok": True, "port": port, "tabs": tabs}
