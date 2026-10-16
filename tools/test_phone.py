# -*- coding: utf-8 -*-
"""The phone's door: the certificate, pairing, the permission table, and the rendezvous.

This suite is about one question - *can a phone do something a phone should not be able
to do* - so most of it is written as attempts that must fail. The certificate and the
relay are exercised for real (a TLS handshake against a server this starts, and a
rendezvous in its own process), because both are code we wrote from scratch and "it
imports" proves nothing about either.

Chrome is not required: the parts that need a browser are measured by hand in the
scratchpad harness. What is tested here of screencast.py is the part that decides what a
remote tap is allowed to reach, which must hold whether or not a browser is running.
"""
import atexit
import hashlib
import io
import re
import json
import os
import shutil
import socket
import ssl
import struct
import subprocess
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src"))

SAND = tempfile.mkdtemp(prefix="olivaw-test-phone-")
os.environ["OLIVAW_INSTALL_DIR"] = SAND
os.environ["HERMES_HOME"] = os.path.join(SAND, "hermes")
os.environ["OLIVAW_CRON_SCHEDULER"] = os.path.join(SAND, "scheduler.py")
os.environ["OLIVAW_BRIDGE_JS"] = os.path.join(SAND, "bridge.js")

from wizard import pairing, relay_client, remote, screencast, selfcert  # noqa: E402

PORT = 8893
passed = failed = 0


def ok(label, cond, extra=""):
    global passed, failed
    if cond:
        passed += 1
    else:
        failed += 1
        print("  FAIL %s  %s" % (label, extra))


def section(name):
    print("\n-- %s" % name)


# ── the certificate ────────────────────────────────────────────────────────────────────
section("a certificate this machine makes for itself")

got = selfcert.ensure(SAND)
ok("it is created", got["created"] and os.path.isfile(got["cert"]))
again = selfcert.ensure(SAND)
ok("and then reused, not remade", not again["created"])
ok("same fingerprint both times", again["fingerprint"] == got["fingerprint"])
ok("the fingerprint is a sha256", len(got["fingerprint"]) == 64)

ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
ctx.load_cert_chain(got["cert"], got["key"])
ok("OpenSSL accepts the hand-rolled certificate and key", True)

srv = socket.socket()
srv.bind(("127.0.0.1", 0))
srv.listen(1)
cert_port = srv.getsockname()[1]


def _serve_one():
    try:
        c, _ = srv.accept()
        with ctx.wrap_socket(c, server_side=True) as s:
            s.recv(8)
            s.sendall(b"ok")
    except Exception:  # noqa: BLE001
        pass


threading.Thread(target=_serve_one, daemon=True).start()
cli = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
cli.check_hostname = False
cli.verify_mode = ssl.CERT_NONE
with socket.create_connection(("127.0.0.1", cert_port), timeout=10) as raw:
    with cli.wrap_socket(raw) as s:
        der = s.getpeercert(binary_form=True)
        s.sendall(b"ping")
        answer = s.recv(8)
ok("a real TLS handshake completes against it", answer == b"ok")
ok("the certificate on the wire is the one the QR names",
   hashlib.sha256(der).hexdigest() == got["fingerprint"])
ok("the two words shown on both screens come from the certificate",
   selfcert.short_code(got["fingerprint"]) == selfcert.short_code(got["fingerprint"])
   and selfcert.short_code("a" * 64) != selfcert.short_code("b" * 64))

key = selfcert.generate_key(1024)
ok("a generated key really is a key: d*e == 1 mod phi",
   (key["d"] * key["e"]) % ((key["p"] - 1) * (key["q"] - 1)) == 1)
ok("and its modulus is the product of its primes", key["p"] * key["q"] == key["n"])
ok("a DER integer with the top bit set is padded, not read as negative",
   selfcert._int(0xFF) == b"\x02\x02\x00\xff")

# ── the permission table ───────────────────────────────────────────────────────────────
section("default deny")

ALL = list(pairing.ALL_CAPS)
ok("a route nobody listed cannot be called", not pairing.allowed("invented/route", ALL))
ok("a route added to the console tomorrow is closed until it is listed",
   not pairing.allowed("agents/delete-everything", ALL))
for route in ("phone/offer", "phone/enable", "phone/caps", "phone/relay",
              "phone/revoke"):
    ok("a phone cannot manage pairing: %s" % route, not pairing.allowed(route, ALL))
for route in ("provider/install", "telegram/capture", "session/login", "teams/apply",
              "numbers/add", "rescue/start", "workspace/pick"):
    ok("and cannot reach %s even with every permission" % route,
       not pairing.allowed(route, ALL))
ok("looking needs 'ver'", pairing.allowed("agents/list", ["ver"]))
ok("talking needs 'hablar'", pairing.allowed("talk/send", ["ver", "hablar"])
   and not pairing.allowed("talk/send", ["ver"]))
ok("renaming needs 'config'", pairing.allowed("agent/rename", ["ver", "config"])
   and not pairing.allowed("agent/rename", ["ver", "hablar", "tareas", "operar"]))
# NEVER is a second lock behind default-deny, so proving it works means giving it
# something to block: a route that IS listed, under a forbidden prefix.
_saved = dict(pairing.ROUTES)
pairing.ROUTES["phone/status"] = "ver"
ok("a forbidden prefix wins even over a route that is listed",
   not pairing.allowed("phone/status", ALL))
pairing.ROUTES.clear()
pairing.ROUTES.update(_saved)
ok("and the prefix list still names the pairing routes",
   "phone/" in pairing.NEVER)

# A timing property cannot be caught by asking the function for an answer, so this is
# checked where it lives: the comparison has to BE constant-time, not merely correct.
_src = open(os.path.join(ROOT, "src", "wizard", "pairing.py"), encoding="utf-8").read()
ok("the device token is compared in constant time",
   "hmac.compare_digest(str(dev.get(\"token\", \"\")), want)" in _src)
ok("and every entry is compared, so the clock does not say which one matched",
   "found = dev" in _src and "return found" in _src and "break" not in
   _src.split("def device_for")[1].split("def touch")[0])
_relay_src = open(os.path.join(ROOT, "relay", "relay.py"), encoding="utf-8").read()
ok("the relay compares its room key in constant time",
   "hmac.compare_digest(room.key_hash, want)" in _relay_src)
ok("and stores only the hash of it",
   "hashlib.sha256(key.encode(\"utf-8\")).hexdigest()" in _relay_src)

ok("every listed capability is one of the real ones",
   all(c in pairing.ALL_CAPS for c in pairing.ROUTES.values()))
ok("every capability has something to say for itself in Spanish",
   all(c in pairing.CAP_LABEL for c in pairing.ALL_CAPS))
ok("a new phone is not given the browser or the settings",
   "navegador" not in pairing.DEFAULT_CAPS and "config" not in pairing.DEFAULT_CAPS)

# ── the store ──────────────────────────────────────────────────────────────────────────
section("the device store")


class FakeApi(object):
    seen = []

    def dispatch(self, path, body):
        FakeApi.seen.append(path)
        return {"ok": True, "echo": path}


remote._shim = FakeApi()
_real_port_of = screencast.port_of   # kept: the last block in this file tests it
screencast.port_of = lambda agent, install_dir=None: 9876   # nothing is listening there

# Decided here, before a phone is ever paired, because pairing now turns the rendezvous
# ON by default - and a default means the shipped address, which is on the internet. A
# test suite that quietly dials Cloudflare when somebody runs it is not something to
# leave lying around. Saying 'off' is a decision, so pairing leaves it alone; the
# rendezvous section further down turns it on against a relay of our own.
relay_client.save_config(SAND, url="ws://127.0.0.1:1", on=False)

pairing.set_enabled(SAND, True)
st = remote.start(SAND, logger=lambda m: None, port=PORT)
ok("the door opens", st.get("running"), st)
FP = st.get("fingerprint") or got["fingerprint"]


class Phone(object):
    """Exactly what the app does: pin the fingerprint, carry a bearer token."""

    def __init__(self, fingerprint, port=PORT):
        self.fp, self.port, self.token = fingerprint, port, ""

    def request(self, method, path, body=None, token=None, headers=()):
        c = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        c.check_hostname = False
        c.verify_mode = ssl.CERT_NONE
        raw = socket.create_connection(("127.0.0.1", self.port), timeout=10)
        s = c.wrap_socket(raw)
        if selfcert.fingerprint(s.getpeercert(binary_form=True)) != self.fp:
            s.close()
            raise ssl.SSLError("pinned certificate mismatch")
        data = json.dumps(body or {}).encode()
        head = ["%s %s HTTP/1.1" % (method, path), "Host: olivaw",
                "Content-Length: %d" % len(data), "Connection: close"]
        tok = self.token if token is None else token
        if tok:
            head.append("Authorization: Bearer %s" % tok)
        head += list(headers)
        s.sendall(("\r\n".join(head) + "\r\n\r\n").encode() + data)
        buf = b""
        while True:
            try:
                chunk = s.recv(65536)
            except Exception:  # noqa: BLE001
                break
            if not chunk:
                break
            buf += chunk
        s.close()
        h, _, b = buf.partition(b"\r\n\r\n")
        code = int(h.split(b" ")[1]) if b" " in h else 0
        try:
            return code, json.loads(b.decode("utf-8", "replace") or "{}")
        except ValueError:
            return code, {"raw": b[:80].decode("latin-1")}


p = Phone(FP)
code, res = p.request("GET", "/ping")
ok("/ping answers without a token", code == 200 and res.get("app") == "olivaw", res)
ok("and says nothing about this machine",
   set(res) <= {"ok", "app", "protocol", "pairing"}, res)
try:
    Phone("0" * 64).request("GET", "/ping")
    ok("a different certificate is refused by the phone", False)
except ssl.SSLError:
    ok("a different certificate is refused by the phone", True)

section("pairing")
code, res = p.request("POST", "/pair", {"pin": "000000"})
ok("no open window, no token", code == 403, res)
offer = pairing.offer(SAND)
code, res = p.request("POST", "/pair", {"pin": "999999"})
ok("the wrong code is refused", code == 403, res)
code, res = p.request("POST", "/pair", {"pin": offer["pin"], "name": u"Pixel de Walt",
                                        "platform": "android"})
ok("the code on screen works", code == 200 and res.get("token"), res)
p.token = res.get("token", "")
ok("a new phone gets the useful half and nothing else",
   sorted(res.get("caps", [])) == sorted(pairing.DEFAULT_CAPS), res.get("caps"))
code, res = p.request("POST", "/pair", {"pin": offer["pin"]})
ok("one code, one device", code == 403, res)

offer2 = pairing.offer(SAND)
for _ in range(pairing.PIN_TRIES):
    p.request("POST", "/pair", {"pin": "111111"})
code, res = p.request("POST", "/pair", {"pin": offer2["pin"]})
ok("guessing burns the offer, right answer included", code == 403, res)

stored = json.load(open(os.path.join(SAND, pairing.STORE), encoding="utf-8"))
ok("the token itself is never stored",
   all(p.token not in json.dumps(d) for d in stored["devices"]))
ok("only its hash is", any(d["token"] == hashlib.sha256(p.token.encode()).hexdigest()
                           for d in stored["devices"]))
ok("the pairing offer is gone from disk once it is used",
   not os.path.isfile(os.path.join(SAND, pairing.OFFER_STORE)))

section("what a token opens")
FakeApi.seen = []
ok("no token, no API", p.request("POST", "/api/agents/list", {}, token="")[0] == 401)
ok("an invented token, no API",
   p.request("POST", "/api/agents/list", {}, token="x" * 40)[0] == 401)
ok("the paired phone may look", p.request("POST", "/api/agents/list", {})[0] == 200)
ok("and talk", p.request("POST", "/api/talk/send", {"text": "hola"})[0] == 200)
ok("but not rename", p.request("POST", "/api/agent/rename", {})[0] == 403)
ok("nor install anything", p.request("POST", "/api/provider/install", {})[0] == 403)
ok("nor pair another phone", p.request("POST", "/api/phone/offer", {})[0] == 403)
ok("and nothing forbidden ever reached the console",
   set(FakeApi.seen) == {"/api/agents/list", "/api/talk/send"}, FakeApi.seen)

section("the browser is its own permission")
WS_HEAD = ["Upgrade: websocket", "Sec-WebSocket-Key: abcdefghijklmnop",
           "Sec-WebSocket-Version: 13"]
ok("without it, no stream",
   p.request("GET", "/ws/browser?agent=default", None, headers=WS_HEAD)[0] == 403)
dev_id = pairing.devices(SAND)["devices"][0]["id"]
pairing.set_caps(SAND, dev_id, ["ver", "navegador"])
ok("with it, it is let through to a browser that is not open",
   p.request("GET", "/ws/browser?agent=default", None, headers=WS_HEAD)[0] == 409)
ok("taking a permission away takes it away",
   p.request("POST", "/api/talk/send", {"text": "x"})[0] == 403)
ok("'ver' cannot be taken away by accident",
   "ver" in pairing.set_caps(SAND, dev_id, [])["caps"])

section("the master switch and revoking")
pairing.set_enabled(SAND, False)
ok("off shuts every device at once",
   p.request("POST", "/api/agents/list", {})[0] == 401)
pairing.set_enabled(SAND, True)
ok("on gives them back", p.request("POST", "/api/agents/list", {})[0] == 200)
pairing.revoke(SAND, dev_id)
ok("a revoked device is a stranger",
   p.request("POST", "/api/agents/list", {})[0] == 401)
ok("and is gone from the list",
   all(d["id"] != dev_id for d in pairing.devices(SAND)["devices"]))

# ── what a remote tap may reach ───────────────────────────────────────────────────────
section("what a remote tap may reach")


class Loose(screencast.Session):
    def __init__(self):
        self.armed_until = time.time() + 999
        self.sent = []
        self.meta = {"deviceWidth": 1000, "deviceHeight": 500}
        self._lock = threading.Lock()
        self._ws = None

    def _send(self, method, params=None):
        self.sent.append((method, params))
        return 1


loose = Loose()
for bad in ("javascript:alert(1)", "JaVaScRiPt:alert(1)", "data:text/html,<b>x",
            "file:///c:/windows/win.ini", "chrome://settings", "vbscript:x",
            "//evil.com", ""):
    ok("a tap cannot navigate to %r" % bad, not loose.navigate(bad).get("ok"))
ok("but a plain address is fine", loose.navigate("gmail.com")["url"] == "https://gmail.com")
ok("and a real url is left alone",
   loose.navigate("http://192.168.1.5:8080/x")["url"] == "http://192.168.1.5:8080/x")
ok("an arbitrary key is refused", not loose.key("F13").get("ok"))
ok("a named one is not", loose.key("Enter").get("ok"))
ok("coordinates are scaled against the page, not trusted",
   loose._css(0.5, 0.5) == (500.0, 250.0))
ok("and are clamped, so a tap cannot be aimed outside the window",
   loose._css(9.0, -3.0) == (1000.0, 0.0))

cold = Loose()
cold.armed_until = 0
for what in (lambda: cold.tap(0.5, 0.5), lambda: cold.type_text("hola"),
             lambda: cold.key("Enter"), lambda: cold.navigate("gmail.com"),
             lambda: cold.back()):
    ok("nothing is driveable before arming", not what().get("ok"))
ok("and nothing was sent to Chrome", not cold.sent, cold.sent)

# ── the rendezvous ─────────────────────────────────────────────────────────────────────
section("the rendezvous")

# A relay of our own, on a port nobody else is on.
#
# Found rather than fixed, and killed through atexit rather than at the end of the
# file, because a run that dies half way used to leave the relay listening - and the
# NEXT run would bind beside it (SO_REUSEADDR lets them both), attach the host to one
# instance and the guest to another, and fail with 'refused' while the relay it asked
# swore it had no rooms. Five of them had piled up before anybody noticed.
def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


RELAY_PORT = _free_port()
relay_proc = subprocess.Popen(
    [sys.executable, "-I", os.path.join(ROOT, "relay", "relay.py"),
     "--port", str(RELAY_PORT)],
    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
atexit.register(lambda: relay_proc.kill())
time.sleep(1.5)
cfg = relay_client.save_config(SAND, url="ws://127.0.0.1:%d" % RELAY_PORT, on=True)
relay_client.start(SAND, PORT, logger=lambda m: None)
for _ in range(60):
    if relay_client.state(SAND)["connected"]:
        break
    time.sleep(0.2)
ok("this machine dials out and stays attached",
   relay_client.state(SAND)["connected"], relay_client.state(SAND))


class Guest(object):
    """The phone's half of the pipe: one WebSocket carrying TCP streams."""

    def __init__(self, port, room):
        import base64
        self.sock = socket.create_connection(("127.0.0.1", port), timeout=10)
        k = base64.b64encode(os.urandom(16)).decode()
        self.sock.sendall((
            "GET /attach?role=guest&room=%s HTTP/1.1\r\nHost: r\r\nUpgrade: websocket\r\n"
            "Connection: Upgrade\r\nSec-WebSocket-Key: %s\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n" % (room, k)).encode())
        buf = b""
        while b"\r\n\r\n" not in buf:
            buf += self.sock.recv(4096)
        if b" 101 " not in buf.split(b"\r\n", 1)[0]:
            raise OSError("refused")
        self.lock = threading.Lock()
        self.streams = {}
        self.next = 1
        threading.Thread(target=self._loop, daemon=True).start()

    def _ws_send(self, payload):
        mask = os.urandom(4)
        n = len(payload)
        if n < 126:
            h = struct.pack("!BB", 0x82, 0x80 | n)
        elif n < 65536:
            h = struct.pack("!BBH", 0x82, 0x80 | 126, n)
        else:
            h = struct.pack("!BBQ", 0x82, 0x80 | 127, n)
        with self.lock:
            self.sock.sendall(h + mask +
                              bytes(b ^ mask[i % 4] for i, b in enumerate(payload)))

    def _exact(self, n):
        out = b""
        while len(out) < n:
            c = self.sock.recv(n - len(out))
            if not c:
                raise OSError("closed")
            out += c
        return out

    def _loop(self):
        try:
            while True:
                b1, b2 = self._exact(2)
                n = b2 & 0x7F
                if n == 126:
                    n = struct.unpack("!H", self._exact(2))[0]
                elif n == 127:
                    n = struct.unpack("!Q", self._exact(8))[0]
                mask = self._exact(4) if b2 & 0x80 else b""
                body = self._exact(n) if n else b""
                if mask:
                    body = bytes(b ^ mask[i % 4] for i, b in enumerate(body))
                if (b1 & 0x0F) == 0x8 or len(body) < 5:
                    continue
                kind, sid = struct.unpack("!BI", body[:5])
                peer = self.streams.get(sid)
                if peer and kind == 2:
                    peer.sendall(body[5:])
                elif peer and kind == 3:
                    peer.close()
        except Exception:  # noqa: BLE001
            pass

    def tunnel(self):
        a, b = socket.socketpair()
        sid = self.next
        self.next += 1
        self.streams[sid] = b
        self._ws_send(struct.pack("!BI", 1, sid))

        def up():
            try:
                while True:
                    d = b.recv(32768)
                    if not d:
                        break
                    self._ws_send(struct.pack("!BI", 2, sid) + d)
            except Exception:  # noqa: BLE001
                pass
        threading.Thread(target=up, daemon=True).start()
        return a


guest = Guest(RELAY_PORT, cfg["room"])
time.sleep(0.4)


def through_relay(method, path, body=None, token=""):
    c = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    c.check_hostname = False
    c.verify_mode = ssl.CERT_NONE
    s = c.wrap_socket(guest.tunnel())
    if selfcert.fingerprint(s.getpeercert(binary_form=True)) != FP:
        raise ssl.SSLError("pinned certificate mismatch through the relay")
    data = json.dumps(body or {}).encode()
    head = ["%s %s HTTP/1.1" % (method, path), "Host: olivaw",
            "Content-Length: %d" % len(data), "Connection: close"]
    if token:
        head.append("Authorization: Bearer %s" % token)
    s.sendall(("\r\n".join(head) + "\r\n\r\n").encode() + data)
    buf = b""
    # Read until the body is as long as the headers said, rather than until the socket
    # goes quiet. Treating a quiet socket as the end made this flaky on a loaded machine:
    # the answer arrived in pieces, recv timed out between two of them, and a half-read
    # reply came back as an empty dict - a red test for a reason that was not its subject.
    need = None
    while True:
        try:
            chunk = s.recv(65536)
        except socket.timeout:
            body_so_far = buf.partition(b"\r\n\r\n")[2]
            if need is None or len(body_so_far) < need:
                s.close()
                raise AssertionError(
                    "the relay answered too slowly (%d bytes of %s)"
                    % (len(body_so_far), need))
            break
        except Exception:  # noqa: BLE001
            break
        if not chunk:
            break
        buf += chunk
        head, sep, body = buf.partition(b"\r\n\r\n")
        if sep and need is None:
            for line in head.split(b"\r\n"):
                if line.lower().startswith(b"content-length:"):
                    need = int(line.split(b":", 1)[1].strip())
        if need is not None and len(body) >= need:
            break
    s.close()
    h, _, b = buf.partition(b"\r\n\r\n")
    return int(h.split(b" ")[1]), json.loads(b.decode("utf-8", "replace") or "{}")


code, res = through_relay("GET", "/ping")
ok("a phone that is not on this wifi reaches this machine",
   code == 200 and res.get("app") == "olivaw", res)
ok("and the certificate at the far end is this machine's, not the relay's", True)

off3 = pairing.offer(SAND)
code, res = through_relay("POST", "/pair", {"pin": off3["pin"], "name": "Pixel"})
ok("it can pair from outside", code == 200 and res.get("token"), res)
away = res.get("token", "")
ok("and use the API", through_relay("POST", "/api/agents/list", {}, away)[0] == 200)
ok("with exactly the permissions it has at home",
   through_relay("POST", "/api/provider/install", {}, away)[0] == 403)

# A stream number that is already somebody's.
#
# The room belongs to the COMPUTER, so every phone paired to it meets in that one
# room, and the rendezvous hands whatever the house says to every guest in it. Both
# ends used to number their streams from 1, so a second phone - or this one
# reconnecting before its old socket was noticed as dead - opened stream 1 while
# stream 1 was already live. The house took the number: it replaced the socket in
# the table without closing it, leaving a connection open with a thread draining it
# into a conversation whose other end had just been swapped out, and handing the
# established session's bytes to a socket that had never done a handshake.
#
# Handshake first, so there IS something to steal, then the duplicate, then ask the
# established session a question: under the old code the answer went to the wrong
# socket and nothing came back.
_raw = guest.tunnel()
_dup = guest.next - 1
_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
_ctx.check_hostname = False
_ctx.verify_mode = ssl.CERT_NONE
_held = _ctx.wrap_socket(_raw)
guest._ws_send(struct.pack("!BI", 1, _dup))      # the same number, again
time.sleep(0.5)
_body = json.dumps({}).encode()
_held.sendall(("\r\n".join([
    "POST /api/agents/list HTTP/1.1", "Host: olivaw",
    "Authorization: Bearer %s" % away,
    "Content-Length: %d" % len(_body), "Connection: close"]).encode()
    + b"\r\n\r\n" + _body))
_held.settimeout(8)
try:
    _answer = _held.recv(400)
except Exception:  # noqa: BLE001
    _answer = b""
ok("a repeated stream number does not take over the live one",
   b" 200 " in _answer, _answer[:60])
try:
    _held.close()
except OSError:
    pass

# And the phone does not pick the colliding number in the first place.
_dart = io.open(os.path.join(ROOT, 'app', 'lib', 'relay.dart'), encoding='utf-8').read()
ok("the phone starts its streams somewhere nobody else will be",
   'int _next = 1 + Random.secure()' in _dart)

# The rendezvous hashes before it reads the room, so the claim cannot be raced:
# input gates hold across a STORAGE call, and an await on crypto.subtle is not one.
_wk = io.open(os.path.join(ROOT, 'relay', 'worker', 'worker.js'), encoding='utf-8').read()
ok("the rendezvous hashes the key before it reads the room",
   _wk.index('const hash =') < _wk.index("storage.get(\"key\")"))

raw = guest.tunnel()
raw.sendall(b"GET /ping HTTP/1.1\r\nHost: x\r\n\r\n")
time.sleep(0.5)
raw.settimeout(1.0)
try:
    answer = raw.recv(400)
except Exception:  # noqa: BLE001
    answer = b""
ok("plaintext through the pipe gets nothing: the far end speaks only TLS",
   b"olivaw" not in answer.lower(), answer[:60])

try:
    Guest(RELAY_PORT, "no-such-room")
    ok("an unknown room is refused", False)
except OSError:
    ok("an unknown room is refused", True)

import base64 as _b64  # noqa: E402
s = socket.create_connection(("127.0.0.1", RELAY_PORT), timeout=5)
s.sendall(("GET /attach?role=host&room=%s&key=wrong HTTP/1.1\r\nHost: r\r\n"
           "Upgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: %s\r\n"
           "Sec-WebSocket-Version: 13\r\n\r\n"
           % (cfg["room"], _b64.b64encode(os.urandom(16)).decode())).encode())
head = s.recv(200)
ok("and a room cannot be stolen from the machine that owns it",
   b" 101 " not in head.split(b"\r\n", 1)[0], head[:60])
s.close()
s = socket.create_connection(("127.0.0.1", RELAY_PORT), timeout=5)
s.sendall(("GET /attach?role=host&room=brandnew HTTP/1.1\r\nHost: r\r\n"
           "Upgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: %s\r\n"
           "Sec-WebSocket-Version: 13\r\n\r\n"
           % _b64.b64encode(os.urandom(16)).decode()).encode())
head = s.recv(200)
ok("a host with no key at all is refused",
   b" 101 " not in head.split(b"\r\n", 1)[0], head[:60])
s.close()

# Moving the rendezvous, which used to do nothing at all.
#
# start() is called on a tick and was idempotent on ANY live thread, so once attached it
# never looked at the address again: the console would save a new one, report 'connected'
# - because it WAS connected, to the old one - and nobody could be met anywhere until the
# next restart. Two relays, because the only honest way to ask 'did it move' is to see it
# arrive somewhere else.
PORT2 = _free_port()
relay2 = subprocess.Popen(
    [sys.executable, "-I", os.path.join(ROOT, "relay", "relay.py"),
     "--port", str(PORT2)],
    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
atexit.register(lambda: relay2.kill())
time.sleep(1.5)
relay_client.save_config(SAND, url="ws://127.0.0.1:%d" % PORT2, on=True)
relay_client.start(SAND, PORT, logger=lambda m: None)
for _ in range(60):
    if relay_client.state(SAND)["connected"] and relay_client.state(SAND).get("url", "").endswith(":%d" % PORT2):
        break
    time.sleep(0.2)
ok("a new address is attached to, not just saved",
   relay_client.state(SAND).get("url", "").endswith(":%d" % PORT2), relay_client.state(SAND))
try:
    _met = Guest(PORT2, cfg["room"]) is not None
except OSError as e:  # the room is not there: nothing moved
    _met = False
ok("and the phone can be met there", _met)

# Back home, because everything after this is addressed at the first relay.
relay_client.save_config(SAND, url="ws://127.0.0.1:%d" % RELAY_PORT, on=True)
relay_client.start(SAND, PORT, logger=lambda m: None)
for _ in range(60):
    st2 = relay_client.state(SAND)
    if st2["connected"] and st2.get("url", "").endswith(":%d" % RELAY_PORT):
        break
    time.sleep(0.2)
ok("and it can move back", relay_client.state(SAND)["connected"], relay_client.state(SAND))

# ── the way home ───────────────────────────────────────────────────────────────────────
# A phone that was paired before the rendezvous existed has to be able to learn about it,
# and the only moment it can is while it can still reach the house.
out, code = remote.call_api("net/home", {}, ["ver"])
ok("net/home answers a phone that may look", code == 200 and out.get("ok") is True, (code, out))
ok("net/home says which addresses to try", isinstance(out.get("addresses"), list), out)
ok("net/home gives the port", isinstance(out.get("port"), int) and out["port"] > 0, out)

out, code = remote.call_api("net/home", {}, [])
ok("net/home is refused without permission", code == 403, (code, out))

# What it says about the rendezvous has to match what is actually configured - this
# runs after the relay section above, so here one IS on.
_cfg = relay_client.config(remote.INSTALL_DIR)
_said = remote.call_api("net/home", {}, ["ver"])[0].get("relay")
ok("net/home agrees with the rendezvous actually configured",
   (_said or {}).get("url", "") == (_cfg.get("url", "") if _cfg.get("on") else ""),
   (_said, _cfg))

# ── which browser belongs to which agent ───────────────────────────────────────────────
# port_of used to answer with port_for(), which for an agent that has no browser
# configured hands back a FREE port - and for the default agent hands back 9222 whatever
# is sitting on it. Showing a phone one agent's window under another agent's name is the
# one failure here that looks exactly like success.
from wizard import browser_setup  # noqa: E402

# The default agent moved to another port is the case that separates the two: port_for()
# answers PORT_BASE for the default agent whatever it is actually using, so an answer of
# 9222 here would be a different agent's window - or a stranger's - under this one's name.
_fake_cfg = {None: "http://127.0.0.1:9341", "uno": "http://127.0.0.1:9287", "dos": ""}
_real_get = browser_setup.hermes_ctl.config_get
browser_setup.hermes_ctl.config_get = (
    lambda key, hermes=None, profile=None: _fake_cfg.get(profile, ""))
try:
    ok("the default agent is followed to the port it actually uses",
       _real_port_of("default") == 9341, _real_port_of("default"))
    ok("a named agent is followed to its configured port",
       _real_port_of("uno") == 9287, _real_port_of("uno"))
    got = _real_port_of("dos")
    ok("an agent with no browser never borrows 9222", got != 9222, got)
finally:
    browser_setup.hermes_ctl.config_get = _real_get

# ── which agent a phone is actually talking to ─────────────────────────────────────────
# wizard_server resolves the agent from body["profile"] and ignores body["slug"] for
# everything scoped to one agent. The app sent "slug", so opening Daneel and typing a
# message held a conversation with the DEFAULT agent under Daneel's name, and a routine
# switched off on the phone was switched off on the wrong agent's jobs.json. Nothing
# errored; it was the right-looking answer from the wrong agent.
_MAIN = os.path.join(ROOT, "app", "lib", "main.dart")
if os.path.exists(_MAIN):
    _dart = io.open(_MAIN, encoding="utf-8").read()
    ok("the app addresses agents by profile",
       "'profile': widget.agent.slug" in _dart)
    ok("the app no longer sends slug for per-agent work",
       "'slug': widget.agent.slug" not in _dart)

# And the server's half of that contract, so the test fails from either side.
import inspect as _inspect  # noqa: E402
from wizard import wizard_server as _ws  # noqa: E402
_src = _inspect.getsource(_ws._target_profile)
ok("the server resolves an agent from 'profile'", 'body.get("profile")' in _src, _src[:80])

# ── files inside a conversation ────────────────────────────────────────────────────────
# A stored turn still carries the raw `MEDIA:<path>` lines an agent wrote to hand a file
# to the runtime. The runtime delivered the file and nothing rewrote the transcript, so
# a reader gets an absolute path on somebody else's computer where a picture should be.
# history() now puts every turn through the same reader a live reply gets.
_msg = ('Aquí tienes la primera.' + chr(10) +
        'MEDIA:' + os.path.join(SAND, 'slide-01.png') + chr(10) +
        'MEDIA:' + os.path.join(SAND, 'falta.png'))
with io.open(os.path.join(SAND, 'slide-01.png'), 'wb') as _fh:
    _fh.write(bytes([0x89]) + b'PNG' + b'0' * 64)
from wizard import talk as _tk  # noqa: E402
_body, _files = _tk.read_files_out(_msg)
# read_files_out keeps a line whose file is gone, on purpose: in a LIVE reply the
# owner may still want to know what the agent tried to send. history() is the one
# that drops it, because weeks later a dead path helps nobody.
ok("the line for a file that IS there leaves the sentence",
   'slide-01.png' not in _body, _body)
ok("the sentence itself survives", 'primera' in _body, _body)
ok("a file that exists is listed", any(f.get('name') == 'slide-01.png' for f in _files),
   _files)
ok("an image is recognised as one",
   any(f.get('kind') == 'image' for f in _files), _files)
ok("and gets an id to fetch it by",
   any(f.get('id') for f in _files), _files)
# A path the agent invented, or a file since deleted, must not become a broken
# attachment: there is nothing to fetch and nothing to show.
ok("a path that is not there is not offered",
   not any(f.get('name') == 'falta.png' for f in _files), _files)

# ── a rendezvous nobody is using ───────────────────────────────────────────────────────
# The normal state of an attachment is SILENCE: the phone is in a pocket. The socket
# timeout is a heartbeat interval, not a death, and reading it as one made this machine
# attach, drop twenty seconds later and back off - forever, without ever being
# reachable. The fix is that a timeout at a frame boundary sends a ping and waits again.
_src = io.open(os.path.join(ROOT, 'src', 'wizard', 'relay_client.py'),
               encoding='utf-8').read()
ok("a quiet attachment is not treated as a dead one",
   'except socket.timeout:' in _src and 'QUIET_PINGS' in _src)
ok("and it pings rather than just waiting forever",
   "self.send(b'', op=0x9)" in _src)
# socket.timeout IS TimeoutError IS OSError, so the order of the handlers decides
# whether this works at all.
_recv = _src[_src.index('def recv(self)'):]
ok("the timeout is caught before the generic failure",
   _recv.index('except socket.timeout:') < (_recv.index('except OSError')
                                            if 'except OSError' in _recv else 1 << 30))

# ── a rendezvous out of the box ────────────────────────────────────────────────────────
# A phone that only works in the same room as the computer is not the thing anybody
# asked for, and an address nobody can be expected to paste in is not a default. An
# install that has never chosen one follows the shipped address.
_fresh = tempfile.mkdtemp()
_cfg = relay_client.config(_fresh)
ok("a fresh install has somewhere to meet",
   _cfg.get('url') == relay_client.DEFAULT_URL, _cfg.get('url'))
ok("the default is a wss address", str(_cfg.get('url')).startswith('wss://'))
ok("but it is NOT attached until somebody asks", _cfg.get('on') is None, _cfg)

# Filled in rather than written down, so a default that moves reaches installs that
# never chose for themselves - and never overwrites one that did.
relay_client.save_config(_fresh, url='wss://propio.example')
ok("an owner's own address wins",
   relay_client.config(_fresh)['url'] == 'wss://propio.example')

# Pairing is the moment the owner says they want a phone, so that is when it attaches.
# An owner who said no stays no: this is a default, not an override.
_said_no = tempfile.mkdtemp()
relay_client.save_config(_said_no, on=False)
ok("'off' survives, because it was a decision",
   relay_client.config(_said_no).get('on') is False)
_remote_src = io.open(os.path.join(ROOT, 'src', 'wizard', 'remote.py'),
                      encoding='utf-8').read()
ok("pairing only turns it on when nothing was decided",
   'rc.get("on") is None' in _remote_src, 'the guard is missing')
shutil.rmtree(_fresh, ignore_errors=True)
shutil.rmtree(_said_no, ignore_errors=True)

# ── getting past whatever sits in front of the rendezvous ──────────────────────────────
# A relay may well be behind a CDN. Cloudflare answers a WebSocket handshake with no
# User-Agent with 530 and a body of 'error code: 1010' - its browser-integrity check -
# and the only symptom at either end is a rendezvous that will not take the connection,
# with nothing said about why.
_rc = io.open(os.path.join(ROOT, 'src', 'wizard', 'relay_client.py'),
              encoding='utf-8').read()
ok("the computer names itself to the rendezvous",
   'USER_AGENT' in _rc and 'User-Agent: %s' in _rc)
_dart_relay = os.path.join(ROOT, 'app', 'lib', 'relay.dart')
if os.path.exists(_dart_relay):
    _dr = io.open(_dart_relay, encoding='utf-8').read()
    ok("and so does the phone", 'User-Agent: Olivaw' in _dr)

# A stale address is not an impostor. An address written down on one network can belong
# to something else entirely on another - a tether address is routable again the moment
# the phone is on mobile data - and reporting that as 'your computer presented a
# different certificate' accuses the wrong machine AND hides the rendezvous.
_link = os.path.join(ROOT, 'app', 'lib', 'link.dart')
if os.path.exists(_link):
    _lk = io.open(_link, encoding='utf-8').read()
    _reach = _lk[_lk.index('Future<Map<String, dynamic>> reach'):]
    _reach = _reach[:_reach.index('Future<bool> rediscover')]
    ok("a handshake failure on a direct address falls back",
       'rediscover()' in _reach.split('on HandshakeException')[1])
    ok("and only gives up when there is no rendezvous to try",
       _reach.split('on HandshakeException')[1].count('if (!hasRelay) rethrow;') == 1)
    # Sweeping 254 carrier addresses on a metered connection, for a machine that cannot
    # be there.
    ok("the sweep stays on private networks", 'bool _private(String ip)' in _lk)

# ── voice notes ────────────────────────────────────────────────────────────────────────
# A recording says the same kinds of things a typed message does, so it needs the same
# permission and no more. The microphone itself is the phone's to guard.
ok("a voice note needs 'hablar'", pairing.allowed("talk/voice", ["ver", "hablar"]))
ok("a voice note is refused with only 'ver'", not pairing.allowed("talk/voice", ["ver"]))
ok("whether transcription exists is readable",
   pairing.allowed("talk/voice_status", ["ver"]))

# The transcriber lives in its own virtualenv and may simply not be on a machine. That
# is a thing to SAY, not a traceback: the route has to answer either way.
from wizard import talk as _talk  # noqa: E402
_st = _talk.stt_status()
ok("stt_status always answers", _st.get("ok") is True, _st)
ok("stt_status says ready or says why",
   _st.get("ready") is True or bool(_st.get("detail")), _st)
_saved_venvs = list(_talk._STT_VENVS)
try:
    _talk._STT_VENVS[:] = [os.path.join(SAND, "no-such-python.exe")]
    ok("with no transcriber it is not ready", _talk.stt_status().get("ready") is False)
    _r = _talk.transcribe(name="x.m4a", data_b64="AAAA", install_dir=SAND)
    ok("a voice note without a transcriber fails politely",
       _r.get("ok") is False and bool(_r.get("detail")), _r)
finally:
    _talk._STT_VENVS[:] = _saved_venvs

# Attachments ride on the same permission as the words they come with.
ok("attaching needs 'hablar'", pairing.allowed("talk/attach", ["ver", "hablar"]))
ok("attaching is refused with only 'ver'", not pairing.allowed("talk/attach", ["ver"]))

# ── what the app asks Android for ──────────────────────────────────────────────────────
_KT = os.path.join(ROOT, "app", "android", "app", "src", "main", "kotlin",
                   "com", "olivaw", "olivaw", "MainActivity.kt")
if os.path.exists(_KT):
    _kt = io.open(_KT, encoding="utf-8").read()
    # A message is text written by a model. Handing any scheme it writes to
    # startActivity would let a reply open anything on the phone that answers to it.
    ok("only http(s) links are opened",
       'startsWith("http://")' in _kt and 'startsWith("https://")' in _kt)
    ok("the microphone is checked before recording",
       "checkSelfPermission" in _kt and "RECORD_AUDIO" in _kt)

# ── the app's own manifest ─────────────────────────────────────────────────────────────
# Flutter's template grants INTERNET in the DEBUG manifest only. A release build of a
# network client then installs, opens, and fails every connection with nothing to show
# for it - found by building the APK, which is too late and too slow to find it again.
_MANIFEST = os.path.join(ROOT, "app", "android", "app", "src", "main",
                         "AndroidManifest.xml")
if os.path.exists(_MANIFEST):
    _man = io.open(_MANIFEST, encoding="utf-8").read()
    for _perm in ("INTERNET", "ACCESS_NETWORK_STATE", "ACCESS_WIFI_STATE",
                  # Voice notes. Asked for at the moment the owner first presses the
                  # microphone, never at install.
                  "RECORD_AUDIO"):
        ok("the release manifest asks for %s" % _perm,
           "android.permission.%s" % _perm in _man)
    # Everything else stays out. Files are chosen through the system picker, which hands
    # over one document and needs no storage permission at all; a camera, contacts or a
    # location would each be a thing this app could do and has no reason to.
    for _no in ("CAMERA", "ACCESS_FINE_LOCATION", "READ_CONTACTS",
                "READ_EXTERNAL_STORAGE"):
        ok("the app does not ask for %s" % _no,
           "android.permission.%s" % _no not in _man)

_GRADLE = os.path.join(ROOT, "app", "android", "gradle.properties")
if os.path.exists(_GRADLE):
    _gp = io.open(_GRADLE, encoding="utf-8").read()
    # The template's -Xmx8G is larger than the free memory on the machine this is built
    # on, and Gradle's daemon is killed mid-build with a message about disappearing.
    # The SETTING, not the comment above it that quotes the number being avoided:
    # searching the whole file found the explanation and called the fix a failure.
    _args = [l for l in _gp.splitlines()
             if l.strip().startswith("org.gradle.jvmargs")]
    _xmx = re.search(r"-Xmx(\d+)([gGmM])", _args[0]) if _args else None
    _mb = (int(_xmx.group(1)) * 1024 if _xmx.group(2) in "gG"
           else int(_xmx.group(1))) if _xmx else 0
    ok("the gradle heap is one a small machine can actually give", 0 < _mb <= 4096, _mb)

# ── tidy up ────────────────────────────────────────────────────────────────────────────
relay_client.stop()
remote.stop()
try:
    relay_proc.terminate()
except Exception:  # noqa: BLE001
    pass
try:
    srv.close()
except Exception:  # noqa: BLE001
    pass
shutil.rmtree(SAND, ignore_errors=True)

print("\n%d passed, %d failed" % (passed, failed))
sys.exit(1 if failed else 0)
