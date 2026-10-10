r"""Whose window is this? — the colour, the name, and the socket that keeps saying so.

Three things are tested here, and they fail in three different ways:

  * **The profile stamp.** Chrome reads ``Local State`` at startup and *aborts* on one it
    does not like — measured: writing that file from scratch gets ``bad_optional_access``
    and no browser at all. So the stamp only ever edits keys, and the test that matters is
    the boring one: everything we did not write came back byte-identical.

  * **The WebSocket client.** There is no library on a machine that has never run `pip`,
    so Olivaw ships the few hundred bytes of RFC 6455 a localhost DevTools client needs.
    It is exercised against a real socket here, including the case that actually bit: an
    idle read timing out is Chrome having nothing to say, NOT a dead connection. The first
    version treated them the same and gave up two seconds after attaching, which looked
    exactly like "the feature does not work".

  * **The title script.** It ships as a string and runs in a browser, so it is tested by
    running it — in Node, against a fake document — rather than by asserting on its text.
    Prefixing twice would turn a title into "Daneel · Daneel · Gmail" on every tick.

Run: python tools/test_browser_label.py
"""

import base64
import hashlib
import io
import json
import os
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SRC = os.path.join(ROOT, "src")
sys.path.insert(0, SRC)

from wizard import browser_label as BL  # noqa: E402

FAILED = []
CHECKS = [0]


def ok(cond, label, extra=""):
    CHECKS[0] += 1
    if not cond:
        FAILED.append(label + ((" — " + str(extra)) if extra else ""))
        print("FAIL " + label + ((" — " + str(extra)) if extra else ""))


def eq(got, want, label):
    ok(got == want, "%s (got %r, want %r)" % (label, got, want))


# ── colours ──────────────────────────────────────────────────────────────────
def test_every_agent_gets_its_own_colour():
    d = tempfile.mkdtemp(prefix="olivaw-label-")
    try:
        slugs = ["default", "daneel", "heraldo", "analecta", "squadron", "baco",
                 "forja", "octavo"]
        got = [BL.look_for(s, d)["hex"] for s in slugs]
        eq(len(set(got)), len(BL.PALETTE), "the first eight agents are eight colours")
        eq(len(set(BL.look_for(s, d)["avatar"] for s in slugs)), len(BL.PALETTE),
           "and eight avatars")

        # Stability is the whole point of writing it down. A colour that moved when an
        # agent was added would undo the owner's "the green one is Ábaco" every time.
        BL.look_for("noveno", d)
        again = [BL.look_for(s, d)["hex"] for s in slugs]
        eq(again, got, "and nobody's colour moves when a ninth agent arrives")

        # Merely LOOKING must not consume a slot: the console asks for this on every
        # repaint, and a slot handed out is a slot the next real agent cannot have.
        before = io.open(BL.labels_path(d), encoding="utf-8").read()
        BL.look_for("solo-mirando", d, assign=False)
        eq(io.open(BL.labels_path(d), encoding="utf-8").read(), before,
           "asking without assigning writes nothing")
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_the_colour_is_the_integer_chrome_expects():
    # Chrome stores ARGB in a SIGNED 32-bit int. Handing it the unsigned one is not an
    # error anywhere — the pref is simply ignored and the window stays grey.
    eq(BL.sk_color(0xB4, 0x5E, 0xC9), -4956471, "purple round-trips to Chrome's integer")
    eq(BL.sk_color(0, 0, 0), -16777216, "black is the most negative one")
    for hexa, r, g, b in BL.PALETTE:
        v = BL.sk_color(r, g, b)
        ok(-(2 ** 31) <= v < 2 ** 31, "%s fits in a signed 32-bit int" % hexa)
        eq("#%02X%02X%02X" % (r, g, b), hexa, "%s matches its own channels" % hexa)


# ── the profile stamp ────────────────────────────────────────────────────────
REAL_LOCAL_STATE = {
    "browser": {"enabled_labs_experiments": ["x"], "shortcut_migration_version": "154"},
    "profile": {"info_cache": {"Default": {
        "active_time": 1791605530.76, "avatar_icon": "chrome://theme/IDR_PROFILE_AVATAR_26",
        "background_apps": False, "gaia_id": "", "is_using_default_name": True,
        "metrics_bucket_index": 1, "name": "Your Chrome", "user_name": ""}},
        "last_used": "Default", "profiles_order": ["Default"]},
    "user_experience_metrics": {"stability": {"stats_version": "154.0.8037.98"}},
}
REAL_PREFS = {
    "browser": {"window_placement": {"bottom": 900, "left": 80}},
    "profile": {"avatar_index": 26, "created_by_version": "154.0.8037.98",
                "exit_type": "Normal", "name": "Your Chrome"},
    "extensions": {"last_chrome_version": "154.0.8037.98"},
    "intl": {"selected_languages": "es,en"},
}


def _profile(tmp, profile_name=None):
    """A chrome-debug directory shaped the way Chrome actually leaves one."""
    os.environ["HERMES_HOME"] = tmp
    sub = tmp if not profile_name else os.path.join(tmp, "profiles", profile_name)
    udd = os.path.join(sub, "chrome-debug")
    os.makedirs(os.path.join(udd, "Default"), exist_ok=True)
    with io.open(os.path.join(udd, "Local State"), "w", encoding="utf-8") as fh:
        json.dump(REAL_LOCAL_STATE, fh)
    with io.open(os.path.join(udd, "Default", "Preferences"), "w", encoding="utf-8") as fh:
        json.dump(REAL_PREFS, fh)
    return udd


def test_stamping_a_profile_edits_and_never_rewrites():
    tmp = tempfile.mkdtemp(prefix="olivaw-udd-")
    home = os.environ.get("HERMES_HOME")
    try:
        udd = _profile(tmp)
        res = BL.stamp_profile(None, "Agente principal", "default", install_dir=tmp,
                               home_url="file:///C:/x/card.html")
        ok(res["ok"] and res["changed"], "stamping works")
        ok(not res["pending"], "and the profile was there to stamp")

        ls = json.load(io.open(os.path.join(udd, "Local State"), encoding="utf-8"))
        row = ls["profile"]["info_cache"]["Default"]
        eq(row["name"], "Agente principal", "the profile is named after the agent")
        eq(row["is_using_default_name"], False, "and Chrome is told it is not a default")
        ok(row["profile_color_seed"] < 0, "the colour is the signed integer")
        # The keys we never heard of are Chrome's, and Chrome aborts on a file it does not
        # recognise. This is the assertion that stands between the owner and no browser.
        eq(ls["user_experience_metrics"],
           REAL_LOCAL_STATE["user_experience_metrics"], "unrelated sections survive")
        eq(ls["browser"], REAL_LOCAL_STATE["browser"], "and so do the browser ones")
        eq(row["active_time"], 1791605530.76, "and untouched keys inside the row")
        eq(row["metrics_bucket_index"], 1, "all of them")

        pf = json.load(io.open(os.path.join(udd, "Default", "Preferences"),
                               encoding="utf-8"))
        eq(pf["profile"]["name"], "Agente principal", "the preference name too")
        ok(pf["browser"]["theme"]["user_color"] < 0, "the frame colour is set")
        eq(pf["browser"]["window_placement"],
           REAL_PREFS["browser"]["window_placement"],
           "without losing where the window was")
        eq(pf["intl"], REAL_PREFS["intl"], "or the owner's languages")
        eq(pf["homepage"], "file:///C:/x/card.html", "Home goes to the card")
        eq(pf["homepage_is_newtabpage"], False, "and Home is really the card")
        eq(pf["browser"]["show_home_button"], True, "with a button to press")

        second = BL.stamp_profile(None, "Agente principal", "default", install_dir=tmp,
                                  home_url="file:///C:/x/card.html")
        ok(second["ok"] and not second["changed"], "stamping twice changes nothing")
    finally:
        if home is None:
            os.environ.pop("HERMES_HOME", None)
        else:
            os.environ["HERMES_HOME"] = home
        shutil.rmtree(tmp, ignore_errors=True)


def test_a_profile_that_is_not_there_yet_is_not_an_error():
    tmp = tempfile.mkdtemp(prefix="olivaw-udd-")
    home = os.environ.get("HERMES_HOME")
    try:
        os.environ["HERMES_HOME"] = tmp
        res = BL.stamp_profile("nuevo", "Nuevo", "nuevo", install_dir=tmp)
        ok(res["ok"], "an agent whose browser has never run is fine")
        ok(res["pending"], "and is reported as still to do")
        ok(not os.path.exists(os.path.join(tmp, "profiles", "nuevo", "chrome-debug",
                                           "Local State")),
           "nothing was created behind Chrome's back")

        # A half-written or hand-edited file: leave it alone rather than replace it.
        udd = _profile(tmp, "roto")
        with io.open(os.path.join(udd, "Local State"), "w", encoding="utf-8") as fh:
            fh.write("{not json")
        res = BL.stamp_profile("roto", "Roto", "roto", install_dir=tmp)
        ok(res["ok"], "a corrupt Local State does not raise")
        eq(io.open(os.path.join(udd, "Local State"), encoding="utf-8").read(),
           "{not json", "and is left exactly as it was")
    finally:
        if home is None:
            os.environ.pop("HERMES_HOME", None)
        else:
            os.environ["HERMES_HOME"] = home
        shutil.rmtree(tmp, ignore_errors=True)


# ── the websocket client, against a real socket ──────────────────────────────
GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


class FakeWS(object):
    """A WebSocket server just real enough to be wrong about, in a thread."""

    def __init__(self, behaviour="echo"):
        self.behaviour = behaviour
        self.srv = socket.socket()
        self.srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.srv.bind(("127.0.0.1", 0))
        self.srv.listen(1)
        self.port = self.srv.getsockname()[1]
        self.got = []
        self.pongs = []
        self.conn = None
        self.thread = threading.Thread(target=self._serve)
        self.thread.daemon = True
        self.thread.start()

    @property
    def url(self):
        return "ws://127.0.0.1:%d/devtools/browser/abc" % self.port

    def _serve(self):
        try:
            conn, _ = self.srv.accept()
        except OSError:
            return
        self.conn = conn
        buf = b""
        while b"\r\n\r\n" not in buf:
            chunk = conn.recv(4096)
            if not chunk:
                return
            buf += chunk
        key = ""
        for line in buf.decode("latin-1").split("\r\n"):
            if line.lower().startswith("sec-websocket-key:"):
                key = line.split(":", 1)[1].strip()
        accept = base64.b64encode(
            hashlib.sha1((key + GUID).encode()).digest()).decode()
        conn.sendall(("HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\n"
                      "Connection: Upgrade\r\nSec-WebSocket-Accept: %s\r\n\r\n"
                      % accept).encode())
        if self.behaviour == "idle":
            time.sleep(6)
            return
        try:
            while True:
                text = self._read(conn)
                if text is None:
                    return
                if text == "":
                    continue
                self.got.append(text)
                if self.behaviour == "attach" and "setAutoAttach" in text:
                    self.attach("S1")
                elif self.behaviour == "close":
                    conn.sendall(struct.pack("!BB", 0x88, 0))
                    return
                elif self.behaviour == "echo":
                    self._write(conn, text)
        except OSError:
            return

    @staticmethod
    def _exact(conn, n):
        out = b""
        while len(out) < n:
            c = conn.recv(n - len(out))
            if not c:
                raise OSError("closed")
            out += c
        return out

    def _read(self, conn):
        try:
            b1, b2 = self._exact(conn, 2)
        except OSError:
            return None
        n = b2 & 0x7F
        if n == 126:
            n = struct.unpack("!H", self._exact(conn, 2))[0]
        elif n == 127:
            n = struct.unpack("!Q", self._exact(conn, 8))[0]
        mask = self._exact(conn, 4) if (b2 & 0x80) else b"\0\0\0\0"
        data = self._exact(conn, n) if n else b""
        op = b1 & 0x0F
        if op == 0x8:
            return None
        body = bytes(c ^ mask[i % 4] for i, c in enumerate(data))
        if op == 0xA:
            # The client answering our ping. Recorded, not echoed: echoing it back as a
            # message is what made "the socket still works afterwards" read a pong.
            self.pongs.append(body)
            return ""
        return body.decode("utf-8")

    @staticmethod
    def _write(conn, text):
        data = text.encode("utf-8")
        n = len(data)
        if n < 126:
            head = struct.pack("!BB", 0x81, n)
        elif n < 65536:
            head = struct.pack("!BBH", 0x81, 126, n)
        else:
            head = struct.pack("!BBQ", 0x81, 127, n)
        conn.sendall(head + data)

    def attach(self, sid, url="https://example.com/"):
        self._write(self.conn, json.dumps({
            "method": "Target.attachedToTarget",
            "params": {"sessionId": sid,
                       "targetInfo": {"type": "page", "url": url}}}))

    def detach(self, sid):
        self._write(self.conn, json.dumps({
            "method": "Target.detachedFromTarget", "params": {"sessionId": sid}}))

    def ping(self, payload=b"hi"):
        self.conn.sendall(struct.pack("!BB", 0x89, len(payload)) + payload)

    def shut(self):
        try:
            self.srv.close()
        except OSError:
            pass


def test_the_websocket_client_speaks_the_protocol():
    srv = FakeWS("echo")
    try:
        ws = BL._WS(srv.url, timeout=3.0)
        for payload in ("hola", "x" * 200, "y" * 70000):
            ws.send(payload)
            eq(ws.recv(), payload,
               "a %d-byte frame round-trips" % len(payload))
        # Chrome sends accented text constantly (page titles); a client that only masked
        # ASCII would corrupt every one of them.
        ws.send(u"Ábaco · título")
        eq(ws.recv(), u"Ábaco · título", "and so does one with accents")
        ws.close()
    finally:
        srv.shut()


def test_an_idle_socket_is_not_a_dead_one():
    """The bug that made the first version give up two seconds after attaching.

    DevTools says nothing for minutes at a time. Treating a read timeout as a
    disconnection meant the held session — the only thing that keeps the title through a
    navigation — was dropped almost immediately, and the symptom was simply "it stops
    working after a while".
    """
    srv = FakeWS("idle")
    try:
        ws = BL._WS(srv.url, timeout=0.4)
        try:
            ws.recv()
            ok(False, "an idle read raises")
        except socket.timeout:
            ok(True, "an idle read raises socket.timeout")
        except OSError as e:
            ok(False, "an idle read raises socket.timeout", "got OSError %s" % e)
        ws.close()
    finally:
        srv.shut()


def test_a_closed_socket_is_a_dead_one():
    srv = FakeWS("close")
    try:
        ws = BL._WS(srv.url, timeout=3.0)
        ws.send("{}")
        try:
            ws.recv()
            ok(False, "a close frame ends the session")
        except socket.timeout:
            ok(False, "a close frame ends the session", "got a timeout instead")
        except OSError:
            ok(True, "a close frame ends the session")
        ws.close()
    finally:
        srv.shut()


def test_a_ping_is_answered():
    """An unanswered ping is a connection Chrome is entitled to drop, and the session that
    goes with it is the one holding every tab's title."""
    srv = FakeWS("echo")
    try:
        ws = BL._WS(srv.url, timeout=3.0)
        srv.ping(b"abc")
        eq(ws.recv(), None, "a ping is not handed to the caller as a message")
        for _ in range(30):
            if srv.pongs:
                break
            time.sleep(0.05)
        eq(srv.pongs, [b"abc"], "a pong went back, carrying the ping's own payload")
        ws.send("after")
        eq(ws.recv(), "after", "and the socket still works afterwards")
        ws.close()
    finally:
        srv.shut()


def test_the_labeller_attaches_and_names_every_page():
    """The whole dance, against a fake DevTools: discover, attach, register, evaluate."""
    srv = FakeWS("attach")
    try:
        import urllib.request

        real = urllib.request.urlopen

        class FakeResp(object):
            def __init__(self, body):
                self.body = body

            def read(self):
                return self.body

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def fake_open(url, timeout=None):
            if "/json/version" in str(url):
                return FakeResp(json.dumps({"webSocketDebuggerUrl": srv.url}).encode())
            raise OSError("unexpected url %s" % url)

        urllib.request.urlopen = fake_open
        try:
            th = BL.Labeller(1234, "Daneel")
            th.start()
            for _ in range(60):
                time.sleep(0.1)
                if len(srv.got) >= 4:
                    break
            th.stop()
            th.join(timeout=3)
        finally:
            urllib.request.urlopen = real

        methods = [json.loads(g).get("method") for g in srv.got]
        eq(methods[0], "Target.setAutoAttach", "it asks Chrome for every tab, now and later")
        ok("Page.enable" in methods, "it enables the page domain")
        ok("Page.addScriptToEvaluateOnNewDocument" in methods,
           "it registers the script that survives a navigation")
        ok("Runtime.evaluate" in methods,
           "and names the page that is already open")
        # Both carry the name: registering without evaluating leaves the current tab
        # anonymous until it navigates, which is most of the time.
        for g in srv.got:
            m = json.loads(g)
            if m.get("method") in ("Page.addScriptToEvaluateOnNewDocument",
                                   "Runtime.evaluate"):
                blob = json.dumps(m["params"])
                ok("Daneel" in blob, "%s carries the agent's name" % m["method"])
        sessions = [json.loads(g).get("sessionId") for g in srv.got
                    if json.loads(g).get("method") != "Target.setAutoAttach"]
        ok(all(s == "S1" for s in sessions),
           "every command goes to the page's own session", sessions)
    finally:
        srv.shut()


def test_a_rename_reaches_a_window_that_is_already_open():
    """The owner can rename an agent while its browser is up.

    The script is a string, and one built once at connect time would keep using the old
    name for every new tab until the window was closed and reopened - which, for a window
    that stays open for days, means the rename appears not to have worked.
    """
    srv = FakeWS("attach")
    try:
        import urllib.request

        real = urllib.request.urlopen

        class FakeResp(object):
            def __init__(self, body):
                self.body = body

            def read(self):
                return self.body

        urllib.request.urlopen = lambda url, timeout=None: FakeResp(
            json.dumps({"webSocketDebuggerUrl": srv.url}).encode())
        try:
            th = BL.Labeller(4321, "Antes")
            th.start()
            for _ in range(60):
                time.sleep(0.1)
                if len(srv.got) >= 4:
                    break
            first = [g for g in srv.got if "Runtime.evaluate" in g]
            ok(first and "Antes" in first[0], "the first tab gets the name it had")

            th.label = "Despues"
            srv.attach("S2", "https://two.example/")
            for _ in range(60):
                time.sleep(0.1)
                if len([g for g in srv.got if "Runtime.evaluate" in g]) >= 2:
                    break
            later = [g for g in srv.got if "Runtime.evaluate" in g]
            ok(len(later) >= 2, "a second tab is stamped too", len(later))
            ok(later and "Despues" in later[-1],
               "and it carries the NEW name", later[-1][:120] if later else "")
            eq(th.pages, 2, "both tabs are counted")

            # A tab that closes is forgotten, or this set grows for as long as the window
            # lives - one entry per tab it has ever had.
            srv.detach("S1")
            for _ in range(40):
                time.sleep(0.05)
                if th.pages == 1:
                    break
            eq(th.pages, 1, "a closed tab is forgotten")
            th.stop()
            th.join(timeout=3)
        finally:
            urllib.request.urlopen = real
    finally:
        srv.shut()


def test_the_labeller_survives_a_window_that_is_not_there():
    """Most passes find nothing: a closed window must be a shrug, not a crash or a spin."""
    th = BL.Labeller(1, "Nadie")          # port 1: nothing is listening
    started = time.time()
    th.start()
    time.sleep(0.6)
    th.stop()
    th.join(timeout=3)
    ok(not th.is_alive(), "it stops when asked")
    ok(time.time() - started < 3, "and did not block")
    eq(th.pages, 0, "with nothing stamped")


def test_a_reopened_window_does_not_sit_out_the_backoff():
    """Close a window, open it again, and it has to be named in seconds - not minutes.

    The retry backoff doubles while there is nothing to connect to, which is right for a
    window that stays closed. But the supervisor calls `ensure` every fifteen seconds, and
    a thread that is already alive was simply told "already running" and went on sleeping.
    Measured before the nudge existed: a reopened window stayed anonymous past half a
    minute, which is well past the point where anyone would call the feature broken.
    """
    th = BL.Labeller(1, "Nadie")           # nothing is listening on port 1, so it backs off
    th.start()
    try:
        time.sleep(0.5)                     # let it fail once and settle into the wait
        ok(th.is_alive(), "it keeps trying rather than giving up")
        ok(not th.wake_flag.is_set(), "and is waiting, not spinning")
        BL._RUNNING[1] = th
        res = BL.ensure(1, "Nadie")
        eq(res.get("started"), False, "ensure does not start a second thread")
        eq(th.wake_flag.is_set(), True, "the sleeping labeller is woken, not left waiting")
    finally:
        BL._RUNNING.pop(1, None)
        th.stop()
        th.join(timeout=3)
    ok(not th.is_alive(), "stopping it wakes it too, so it exits at once")


def test_the_fast_pass_does_not_touch_the_profiles():
    """The supervisor runs this every fifteen seconds, forever.

    `Local State` is about 100KB and there are two files per agent, so stamping on the
    fast loop meant reading and parsing fourteen JSON documents four times a minute on a
    seven-agent machine to find out nothing had changed. Chrome reads the colour once, at
    startup; the pass that has to be quick is the one that attaches the labeller to a
    window the owner just opened.
    """
    tmp = tempfile.mkdtemp(prefix="olivaw-udd-")
    home = os.environ.get("HERMES_HOME")
    try:
        udd = _profile(tmp)
        before = io.open(os.path.join(udd, "Local State"), encoding="utf-8").read()
        touched = []
        real = BL.stamp_profile
        # Without these the probe reaches THIS machine, where port 9222 is a real agent's
        # window - so the pass would see it as open and defer the stamp it is testing.
        real_probe, real_port = BL.browser_setup.probe, BL.browser_setup.port_for
        BL.browser_setup.probe = lambda url=None, timeout=1.0: {"ok": False, "browser": ""}
        BL.browser_setup.port_for = lambda prof=None, hermes=None: 9222
        BL.stamp_profile = lambda *a, **kw: (touched.append(a) or
                                             {"ok": True, "changed": False,
                                              "pending": False})
        try:
            rows = BL.ensure_all(agents=[], install_dir=tmp, stamp=False)
            eq(touched, [], "the fast pass stamps nothing")
            ok(rows and rows[0].get("hex"),
               "but it still reports the colour, so the console can show it")
            BL.ensure_all(agents=[], install_dir=tmp, stamp=True)
            eq(len(touched), 1, "the slow pass does stamp")
        finally:
            BL.stamp_profile = real
            BL.browser_setup.probe, BL.browser_setup.port_for = real_probe, real_port
        eq(io.open(os.path.join(udd, "Local State"), encoding="utf-8").read(), before,
           "and neither pass rewrote the profile behind Chrome's back")
    finally:
        if home is None:
            os.environ.pop("HERMES_HOME", None)
        else:
            os.environ["HERMES_HOME"] = home
        shutil.rmtree(tmp, ignore_errors=True)


def test_a_running_window_is_not_stamped_underneath_itself():
    """Chrome owns those files while it is up.

    It keeps Local State and Preferences in memory and rewrites them periodically and on
    exit, so an edit made under a live window is thrown away at the next flush. Measured
    on a real machine: six running profiles stamped at once kept, between them, two
    colours, one name and nothing else - a feature that looked applied and was not.
    """
    tmp = tempfile.mkdtemp(prefix="olivaw-udd-")
    home = os.environ.get("HERMES_HOME")
    real_probe = BL.browser_setup.probe
    real_port = BL.browser_setup.port_for
    try:
        _profile(tmp)
        touched = []
        real_stamp = BL.stamp_profile
        BL.stamp_profile = lambda *a, **kw: (touched.append(a[0]) or
                                             {"ok": True, "changed": True,
                                              "pending": False})
        BL.browser_setup.port_for = lambda prof=None, hermes=None: 9222
        try:
            # Window open: stamp deferred, labelling attached.
            BL.browser_setup.probe = lambda url=None, timeout=1.0: {"ok": True,
                                                                    "browser": "Chrome"}
            os.environ["OLIVAW_BROWSER_LABEL"] = "0"   # no real threads in a unit test
            rows = BL.ensure_all(agents=[], install_dir=tmp, stamp=True)
            eq(touched, [], "a window that is open is not stamped underneath itself")
            eq(rows[0]["open"], True, "and the row says the window is open")
            eq(rows[0]["stamp_deferred"], True,
               "and that the stamp is waiting, not that there was nothing to do")

            # Window closed: this is the moment the write survives.
            BL.browser_setup.probe = lambda url=None, timeout=1.0: {"ok": False,
                                                                    "browser": ""}
            rows = BL.ensure_all(agents=[], install_dir=tmp, stamp=True)
            eq(len(touched), 1, "a closed window IS stamped")
            eq(rows[0]["open"], False, "and the row says so")
            eq(rows[0]["stamp_deferred"], False, "with nothing left waiting")
        finally:
            BL.stamp_profile = real_stamp
            os.environ.pop("OLIVAW_BROWSER_LABEL", None)
    finally:
        BL.browser_setup.probe = real_probe
        BL.browser_setup.port_for = real_port
        if home is None:
            os.environ.pop("HERMES_HOME", None)
        else:
            os.environ["HERMES_HOME"] = home
        shutil.rmtree(tmp, ignore_errors=True)


def test_the_switch_that_turns_the_title_off():
    old = os.environ.get("OLIVAW_BROWSER_LABEL")
    try:
        for val, want in (("0", False), ("off", False), ("no", False),
                          ("1", True), ("", True)):
            os.environ["OLIVAW_BROWSER_LABEL"] = val
            eq(BL.labelling_on(), want, "OLIVAW_BROWSER_LABEL=%r" % val)
        os.environ.pop("OLIVAW_BROWSER_LABEL", None)
        eq(BL.labelling_on(), True, "and it is on when nobody said otherwise")

        os.environ["OLIVAW_BROWSER_LABEL"] = "0"
        res = BL.ensure(9999, "Nadie")
        eq(res.get("labelling"), False, "turning it off starts no thread")
        ok(9999 not in BL._RUNNING, "and leaves nothing running")
    finally:
        if old is None:
            os.environ.pop("OLIVAW_BROWSER_LABEL", None)
        else:
            os.environ["OLIVAW_BROWSER_LABEL"] = old


def test_one_thread_per_window_not_one_per_call():
    """`ensure` runs on the supervisor's 15-second loop. One thread per call would be a
    new socket to Chrome every fifteen seconds, forever."""
    try:
        a = BL.ensure(9998, "Uno")
        b = BL.ensure(9998, "Uno")
        ok(a.get("started"), "the first call starts the labeller")
        ok(not b.get("started"), "the second one does not start a second")
        eq(len([k for k in BL._RUNNING if k == 9998]), 1, "one thread for that window")
        BL.ensure(9998, "Otro nombre")
        eq(BL._RUNNING[9998].label, "Otro nombre",
           "a rename reaches the running labeller instead of restarting it")
    finally:
        BL.stop_all()


# ── the script that actually runs in the page ────────────────────────────────
def test_the_title_script_does_what_it_says():
    node = shutil.which("node")
    if not node:
        print("   (node not available — title script behaviour skipped)")
        return
    # Run the shipped string, not a paraphrase of it.
    harness = """
%s
const fake = { title: "Gmail" };
globalThis.document = fake;
globalThis.setInterval = () => 0;
%s
out.push(fake.title);
%s
out.push(fake.title);
fake.title = "Bandeja de entrada";
%s
out.push(fake.title);
console.log(JSON.stringify(out));
"""
    script = BL.title_script(u'Da"neel')
    code = ("const out = [];\n" + script + "\nout.push(document.title);\n"
            + script + "\nout.push(document.title);\n"
            + 'document.title = "Bandeja de entrada";\n' + script
            + "\nout.push(document.title);\nconsole.log(JSON.stringify(out));")
    pre = ('globalThis.document = { title: "Gmail" };\n'
           'globalThis.setInterval = () => 0;\n')
    p = _node(node, pre + code)
    ok(p.returncode == 0, "the script runs without throwing", p.stderr[-300:])
    if p.returncode != 0:
        return
    got = json.loads(p.stdout.strip().splitlines()[-1])
    eq(got[0], u'Da"neel \u00b7 Gmail', "it puts the agent's name in front of the title")
    eq(got[1], u'Da"neel \u00b7 Gmail',
       "running it twice does not prefix twice")
    eq(got[2], u'Da"neel \u00b7 Bandeja de entrada',
       "and a page that renames itself gets named again")
    ok('"Da\\"neel' in script or "Da\\\"neel" in script,
       "a name with a quote in it cannot break out of the script")


def _node(node, code):
    """Run JS from a FILE, never from `node -e`.

    The 857-copy case is a 16KB string literal, and Windows caps a command line at 32767
    characters - so passing it inline silently truncated the script and the failure looked
    like a syntax error in code that is perfectly valid.
    """
    fd, path = tempfile.mkstemp(suffix=".mjs")
    os.close(fd)
    with io.open(path, "w", encoding="utf-8") as fh:
        fh.write(code)
    try:
        return subprocess.run([node, path], capture_output=True, text=True,
                              encoding="utf-8", timeout=60)
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


def test_the_script_heals_a_title_it_already_ruined():
    """The failure this is written from, with its real numbers.

    On a Canva tab - a page that rewrites its own title constantly - the first version of
    this script produced a 16,335-character title made of 857 copies of the agent's tag.
    It could decline to prefix a title that already began with the tag, but it could not
    undo one that had been prefixed twice, so nothing ever brought it back.

    Whatever a page does to its own title, running this must leave exactly one tag.
    """
    node = shutil.which("node")
    if not node:
        print("   (node not available - healing behaviour skipped)")
        return
    script = BL.title_script(u"Agente principal")
    tag = u"Agente principal \u00b7 "
    base = u"\u00bfEn d\u00f3nde est\u00e1n tus aliados estrat\u00e9gicos?"
    pre = ("globalThis.setInterval = () => 0;\n"
           "globalThis.window = globalThis;\n")
    cases = [
        ("a clean title", base),
        ("one already correct", tag + base),
        ("two copies", tag + tag + base),
        ("the 857 that actually happened", tag * 857 + base),
        ("an empty title", ""),
        ("a title that is only the tag", tag),
    ]
    for label, start in cases:
        code = (pre + "globalThis.document = { title: " + json.dumps(start) + " };\n"
                + script + "\n"
                + script + "\n"      # injected twice, as a relabelled window is
                + "console.log(JSON.stringify(document.title));")
        p = _node(node, code)
        ok(p.returncode == 0, "%s: the script runs" % label, p.stderr[-200:])
        if p.returncode != 0:
            continue
        got = json.loads(p.stdout.strip().splitlines()[-1])
        if start == "" :
            eq(got, "", "%s is left alone" % label)
        elif start == tag:
            # Nothing but the tag means there is no page title to keep; adding another
            # copy would be the start of exactly the runaway this test exists for.
            ok(got.count(tag) <= 1, "%s does not grow" % label, got[:80])
        else:
            eq(got, tag + base, "%s collapses to exactly one tag" % label)

    # And the interval is installed once however many times it is injected.
    code = (pre + "let n = 0; globalThis.setInterval = () => { n++; return 0; };\n"
            "globalThis.document = { title: " + json.dumps(base) + " };\n"
            + script + "\n" + script + "\n" + script + "\n"
            "console.log(JSON.stringify(n));")
    p = _node(node, code)
    ok(p.returncode == 0, "the install-once check runs", p.stderr[-200:])
    if p.returncode == 0:
        eq(json.loads(p.stdout.strip().splitlines()[-1]), 1,
           "three injections install one interval, not three")

    # A rename reaches a document that is already open.
    code = (pre + "globalThis.document = { title: " + json.dumps(base) + " };\n"
            + BL.title_script(u"Antes") + "\n"
            + BL.title_script(u"Despues") + "\n"
            "console.log(JSON.stringify(document.title));")
    p = _node(node, code)
    if p.returncode == 0:
        got = json.loads(p.stdout.strip().splitlines()[-1])
        ok(got.startswith(u"Despues \u00b7 "), "a rename takes effect in an open page", got[:60])
        ok("Antes" not in got, "and the old name is gone, not stacked in front", got[:60])


def test_the_script_is_not_a_hand_rolled_string():
    """The name is whatever the owner typed into "Cómo lo llaman los otros agentes", and
    it is interpolated into JavaScript that runs on every page the agent opens. Built with
    `"` + name + `"` instead of json.dumps, a quote in a name is a syntax error at best."""
    for name in (u'Da"neel', u"O'Brien", u"salto\nde linea", u"back\\slash",
                 u"</script><img src=x onerror=alert(1)>"):
        script = BL.title_script(name)
        eq(script.count(BL._js_string(name + BL.SEP)), 1,
           "%r is escaped into the script" % name)
        # The whole tag lives on one line, which a raw newline in a name would break.
        tag_line = [ln for ln in script.split("\n") if "const TAG" in ln]
        eq(len(tag_line), 1, "the tag is still a single statement for %r" % name)
        ok("</script" not in tag_line[0],
           "a name cannot close the script tag (%r)" % name)


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
