r"""One console at a time, running the code that is on disk now.

The console is started from an icon, and nothing stopped a second one starting beside
the first. Each takes a random port and serves whatever Python it loaded the day it
started - so a window left open since Tuesday went on answering with Tuesday's routes,
and an owner who had updated since met «ruta desconocida» on a page whose JavaScript
had come off the disk a second earlier. The UI was new and the server behind it was four
days old. That is the bug these tests are about.

Run: python tools/test_console_one.py
"""

import io
import json
import os
import shutil
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from wizard import console_one as C  # noqa: E402

PASSED, FAILED = [], []


def ok(name, cond, extra=""):
    (PASSED if cond else FAILED).append(name)
    print(("  ok   " if cond else "  FAIL ") + name +
          (("\n       " + str(extra)) if (extra and not cond) else ""))


def section(t):
    print("\n=== %s ===" % t)


SAND = tempfile.mkdtemp(prefix="olivaw-console-")


class Fake(BaseHTTPRequestHandler):
    """A console that says what it is - or something else entirely on the same port."""
    answer = {"app": "olivaw-console", "version": "1.0.0", "pid": 999}

    def do_GET(self):
        if self.path != "/who":
            self.send_response(404)
            self.end_headers()
            return
        raw = json.dumps(Fake.answer).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *a):
        pass


def serve():
    srv = HTTPServer(("127.0.0.1", 0), Fake)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


# ── the note ──────────────────────────────────────────────────────────────────────────
section("the note a console leaves")

C.write(SAND, 4321, "1.2.3")
got = C.read(SAND)
ok("it says where and which version", got["port"] == 4321 and got["version"] == "1.2.3",
   got)
ok("and who, so the next one can retire it", got["pid"] == os.getpid())
ok("the note holds no token",
   "token" not in io.open(os.path.join(SAND, C.NOTE), encoding="utf-8").read())


# ── deciding what to do about one that is already there ──────────────────────────────
section("a second console starting")

ok("with no note at all, it just starts", C.take_over(SAND, "1.2.3")[0] == "start")

srv = serve()
try:
    C.write(SAND, srv.server_port, "1.2.3")
    Fake.answer = {"app": "olivaw-console", "version": "1.2.3", "pid": 999}

    what, where = C.take_over(SAND, "1.2.3")
    ok("a live console of the same version is not replaced", what == "open", what)
    ok("the owner is handed its address instead",
       where == "http://127.0.0.1:%d/" % srv.server_port, where)

    # The one that matters: after an update, opening the console must give the NEW one.
    Fake.answer = {"app": "olivaw-console", "version": "1.0.70", "pid": 999}
    note = C.read(SAND)
    live = C.alive(note)
    ok("an older console is recognised as older", live["version"] == "1.0.70", live)

    # Not retired here - that would kill this suite's own fake - but the decision is
    # the same one take_over makes, and the version comparison is what drives it.
    ok("and the versions do not match, which is what drives the replacing",
       str(live["version"]) != "1.2.3")

    # Something else on that port is not a console and must not be mistaken for one.
    Fake.answer = {"app": "some-other-thing", "version": "9"}
    ok("whatever else is listening there is not one of ours", C.alive(note) is None)
finally:
    srv.shutdown()

# A note that outlived its process - a power cut, a pid reused by something else.
ok("a note whose port answers nothing is ignored",
   C.take_over(SAND, "1.2.3")[0] == "start")

C.write(SAND, 65000, "1.2.3")
ok("and so is one pointing at a port nobody is on",
   C.alive(C.read(SAND), timeout=0.4) is None)


# ── retiring the old one ─────────────────────────────────────────
# The decision is one thing and carrying it out is another: this starts a REAL process
# that answers /who, retires it the way a new console would, and checks the port goes
# quiet. A kill that silently failed would leave two consoles and the bug intact.
section("retiring an older console")

import socket as _socket    # noqa: E402
import subprocess           # noqa: E402
import time as _time        # noqa: E402

_helper = os.path.join(SAND, "old_console.py")
io.open(_helper, "w", encoding="utf-8").write(
    "import json, sys" + chr(10) +
    "from http.server import BaseHTTPRequestHandler, HTTPServer" + chr(10) +
    "class H(BaseHTTPRequestHandler):" + chr(10) +
    "    def do_GET(self):" + chr(10) +
    "        raw = json.dumps({'app': 'olivaw-console'," + chr(10) +
    "                          'version': '0.0.1'}).encode()" + chr(10) +
    "        self.send_response(200)" + chr(10) +
    "        self.send_header('Content-Length', str(len(raw)))" + chr(10) +
    "        self.end_headers()" + chr(10) +
    "        self.wfile.write(raw)" + chr(10) +
    "    def log_message(self, *a):" + chr(10) +
    "        pass" + chr(10) +
    "HTTPServer(('127.0.0.1', int(sys.argv[1])), H).serve_forever()" + chr(10))

_probe = _socket.socket()
_probe.bind(("127.0.0.1", 0))
_port = _probe.getsockname()[1]
_probe.close()

_proc = subprocess.Popen([sys.executable, "-I", _helper, str(_port)])
for _ in range(40):
    if C.alive({"port": _port}, timeout=0.3):
        break
    _time.sleep(0.1)

_note = {"pid": _proc.pid, "port": _port}
ok("an older console answers before it is retired", C.alive(_note) is not None)
ok("retiring it reports success", C.retire(_note) is True)
ok("and the port really does go quiet", C.alive(_note, timeout=0.4) is None)
try:
    _gone = _proc.wait(timeout=5) is not None
except Exception:  # noqa: BLE001
    _gone = False
ok("the process itself is gone", _gone)


# ── the console that outlived an update ─────────────────────────────
# Reported from a Mac the morning after an update: «no pude preguntarle por las llaves
# - ruta desconocida», under a panel that looked perfectly new. Both halves were true.
# The supervisor restarts itself and its bridges when the code changes; the console is a
# separate process started from an icon, and nobody restarted it. So the page came off
# the disk new and the Python behind it was from before the update.
#
# Two things have to be true for nobody to meet that again: the update retires a console
# it finds running, and a console that survives anyway explains itself instead of saying
# "ruta desconocida".
section("a console that outlived an update")

sys.path.insert(0, os.path.join(ROOT, "src"))
from wizard import wizard_server as WS  # noqa: E402

_saved_running = WS.running_version
_saved_disk = WS._version_on_disk
try:
    WS.running_version = lambda: "1.0.70"
    WS._version_on_disk = lambda: "1.0.81"
    # A route this build genuinely does not have, which is what an old console meets
    # when a new page asks it for something added since.
    _out = WS.Handler.dispatch(None, "/api/algo/que/no/existe", {})
    ok("an unknown route on an out-of-date console is explained, not shrugged at",
       _out.get("stale_console") is True, _out)
    ok("and the sentence names both versions",
       "1.0.70" in _out.get("detail", "") and "1.0.81" in _out.get("detail", ""),
       _out.get("detail"))
    ok("and says what to do about it",
       "brirla" in _out.get("detail", ""), _out.get("detail"))

    # A route that is genuinely nonsense, on a console that is perfectly current, is
    # still just an unknown route - the explanation must not become the answer to
    # everything.
    WS._version_on_disk = lambda: "1.0.70"
    _out = WS.Handler.dispatch(None, "/api/no/such/thing", {})
    ok("while a current console still says plainly that it does not know the route",
       _out.get("stale_console") is None and "desconocida" in _out.get("detail", ""),
       _out)
finally:
    WS.running_version = _saved_running
    WS._version_on_disk = _saved_disk

# And the half that means nobody sees it at all: the update itself retires one.
import launcher  # noqa: E402

_retired = []
_saved_read, _saved_alive = C.read, C.alive
_saved_retire, _saved_clear = C.retire, C.clear
try:
    launcher.INSTALL_DIR = SAND
    C.read = lambda install_dir: {"pid": 4242, "port": 4242}
    C.alive = lambda note, timeout=1.5: {"app": "olivaw-console", "version": "1.0.70"}
    C.retire = lambda note: (_retired.append(note) or True)
    C.clear = lambda install_dir, only_mine=True: None
    launcher._retire_console("1.0.81")
    ok("an update retires a console left running on older code", len(_retired) == 1,
       _retired)

    _retired[:] = []
    C.alive = lambda note, timeout=1.5: {"app": "olivaw-console", "version": "1.0.81"}
    launcher._retire_console("1.0.81")
    ok("but leaves alone one that is already the new code", not _retired, _retired)

    _retired[:] = []
    C.alive = lambda note, timeout=1.5: None
    launcher._retire_console("1.0.81")
    ok("and a note for a console that is not there kills nothing", not _retired)

    def _boom(note):
        raise OSError("no permission")

    C.alive = lambda note, timeout=1.5: {"app": "olivaw-console", "version": "1.0.70"}
    C.retire = _boom
    launcher._retire_console("1.0.81")
    ok("a console that cannot be retired does not fail the update", True)
finally:
    C.read, C.alive, C.retire, C.clear = (_saved_read, _saved_alive,
                                          _saved_retire, _saved_clear)


# ── the address it serves on ──────────────────────────────────────
# A fresh ephemeral port every launch is fine until somebody pins the window. Chrome's
# "Install as app" freezes the whole URL, port and all, so that icon goes on opening
# wherever the console was the day it was made. One owner's pinned app spent a week
# opening a console from before an update - today's page over last week's routes - and
# then, once that process died, a dead page.
section("the address the console serves on")

from wizard import wizard_server as WS2  # noqa: E402

_probe = __import__("socket").socket()
_probe.bind(("127.0.0.1", 0))
_mine = _probe.getsockname()[1]
_probe.close()

ok("the port we had last time is the port we take again",
   WS2._free_port(_mine) == _mine)

# Unless somebody else is on it, in which case any port beats refusing to start.
_held = __import__("socket").socket()
_held.bind(("127.0.0.1", 0))
_held.listen(1)
_taken = _held.getsockname()[1]
try:
    _got = WS2._free_port(_taken)
    ok("a port somebody else holds is given up, not fought over", _got != _taken)
    ok("and what we get instead is a real port", isinstance(_got, int) and _got > 0)
finally:
    _held.close()

ok("with nothing remembered, any free port will do",
   isinstance(WS2._free_port(0), int))


# ── tidying up ────────────────────────────────────────────────────────────────────────
section("leaving")

C.write(SAND, 1234, "1.2.3")
C.clear(SAND)
# Not deleted: emptied of everything that claimed to be running. The address stays,
# because that is what a window somebody pinned needs to find next time, and it is the
# one part of the note that does not die with the process.
_left = C.read(SAND)
ok("a console stops claiming to be running on the way out", not _left.get("pid"))
ok("and stops claiming a version", not _left.get("version"))
ok("but the address it served on is remembered", _left.get("port") == 1234)
ok("so nobody is found there", C.alive(_left, timeout=0.4) is None)
ok("and the next console starts rather than opening it",
   C.take_over(SAND, "1.2.3")[0] == "start")

C.write(SAND, 1234, "1.2.3")
got = C.read(SAND)
got["pid"] = os.getpid() + 1
io.open(os.path.join(SAND, C.NOTE), "w", encoding="utf-8").write(json.dumps(got))
C.clear(SAND)
ok("but never somebody else's", C.read(SAND).get("port") == 1234)

shutil.rmtree(SAND, ignore_errors=True)

print("\n%d passed, %d failed" % (len(PASSED), len(FAILED)))
for f in FAILED:
    print("  - " + f)
sys.exit(1 if FAILED else 0)
