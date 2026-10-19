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

from wizard import (pairing, relay_client, remote, screencast, selfcert,
                    talk_access)  # noqa: E402

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
# The owner's keys, from a phone. Managing them sits with the rest of an agent's
# configuration - and there is no route anywhere that returns a value, so the worst a
# phone with this permission can do is replace a password it cannot read.
for _r in ("creds/list", "creds/save", "creds/forget", "creds/allow"):
    ok("keys need 'config': %s" % _r,
       pairing.allowed(_r, ["ver", "hablar", "config"])
       and not pairing.allowed(_r, ["ver", "hablar", "tareas", "operar"]))
ok("and a phone paired today cannot touch them until she says so",
   not pairing.allowed("creds/save", list(pairing.DEFAULT_CAPS)))
for _r in ("teams/group", "teams/ungroup", "teams/assign"):
    ok("shaping the teams needs 'config': %s" % _r,
       pairing.allowed(_r, ["ver", "config"]) and not pairing.allowed(_r, ["ver"]))

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
# An agent has to be put on the phone before a phone may talk to it, which is a
# separate decision from whether this computer offers a chat. Nothing is decided here
# yet, and with no gateway running the pair inherits 'off' - so the refusal comes
# first, and then the switch.
ok("an agent nobody put on the phone is not reachable from one",
   p.request("POST", "/api/talk/send", {"text": "hola"})[0] == 403)
talk_access.set_flag(SAND, "default", "phone", True, False)
ok("and talk", p.request("POST", "/api/talk/send", {"text": "hola"})[0] == 200)
ok("the switch for this computer is a different one",
   talk_access.flags(SAND, "default", False)["desktop"] is False)

# Turning it off again closes the same door.
talk_access.set_flag(SAND, "default", "phone", False, False)
ok("taking it off the phone takes it off the phone",
   p.request("POST", "/api/talk/send", {"text": "hola"})[0] == 403)
talk_access.set_flag(SAND, "default", "phone", True, False)
ok("but not rename", p.request("POST", "/api/agent/rename", {})[0] == 403)

# A connection that opens and then says nothing.
#
# TLS used to be put on the LISTENING socket, which reads naturally and is wrong:
# ssl wraps each accepted connection with do_handshake_on_connect, so the handshake
# ran INSIDE accept(), on the one thread that accepts. A phone that opened a
# connection and then went through a tunnel held that thread for as long as it
# liked, the backlog filled, and Windows then refuses new connections outright. The
# machine looked like it had no door at all - the rendezvous logged 'could not reach
# the local door' and the phone blamed the certificate - and nothing said otherwise.
#
# Three of them, silent, while a real phone asks a real question.
_mute = []
for _ in range(3):
    try:
        _mute.append(socket.create_connection(("127.0.0.1", PORT), timeout=5))
    except OSError:
        pass
try:
    _code = p.request("POST", "/api/agents/list", {})[0]
except Exception as e:  # noqa: BLE001 - the old code timed out here
    _code = repr(e)
ok("a connection that never speaks TLS does not shut the door", _code == 200, _code)
for _m in _mute:
    try:
        _m.close()
    except OSError:
        pass
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

# Switching an agent on for the phone.
#
# An agent that has never been switched on answers nothing from the phone, and the
# app used to say only 'Todavía no está activado para este agente' - true, and no
# use at all to somebody holding a phone away from the computer the switch is on.
# It offers the switch now, which makes who is allowed to press it a real question.
pairing.set_caps(SAND, dev_id, ["ver", "hablar"])
ok("a phone that was not given config cannot switch an agent on",
   p.request("POST", "/api/talk/enable", {})[0] == 403)
pairing.set_caps(SAND, dev_id, ["ver", "hablar", "config"])
ok("and one that was is let through to the handler",
   p.request("POST", "/api/talk/enable", {})[0] != 403)
ok("the same for switching it back off",
   p.request("POST", "/api/talk/disable", {})[0] != 403)

# And the app does not simply dead-end when it is off.
_main = io.open(os.path.join(ROOT, 'app', 'lib', 'main.dart'), encoding='utf-8').read()
ok("the app has a screen for an agent that is not switched on",
   '_talkOffScreen' in _main and 'talk/access' in _main)
# And it throws the PHONE switch, not the computer's. They are two settings now, and
# an app that flipped the wrong one would turn on a chat window nobody is sitting at.
ok("and the switch it throws is the phone one",
   "'where': 'phone'" in _main)
ok("it offers the switch only to a phone that may press it",
   "pairing.caps.contains('config')" in _main)
ok("and says where the switch is when it may not",
   'H\u00e1blale desde aqu\u00ed' in _main)
# No composer while the agent is off, and none while the app is still finding out
# where the conversation left off - typing into either is a way to lose a message.
ok("with no box to type into while nothing is listening",
   'if (!_talkOff && !_connecting)' in _main)
ok("and a wait that says what it is waiting for, rather than a dead screen",
   'Conectando con el agente' in _main and 'Waiting(' in _main)

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
    # Headers and then nothing: the socket broke between the two (the `except
    # Exception: break` above). That came back as an empty dict and therefore as a
    # red test about whatever the call was for, rather than about the one thing that
    # actually happened - the relay carried half an answer. Same treatment as a
    # reply that never started: say so, and let through_relay try again.
    if h.strip() and (len(b) < (need or 1)):
        raise _EmptyThroughRelay("%s %s (%d of %s bytes)"
                                 % (method, path, len(b), need))
    if not h.strip():
        # Nothing at all came back. On a loaded machine the FIRST tunnel opened
        # after the house attaches does this about one run in five, and it used to
        # surface as an IndexError on an empty split - which says nothing about
        # where it went wrong and buries the rest of the suite.
        raise _EmptyThroughRelay(method + " " + path)
    return int(h.split(b" ")[1]), json.loads(b.decode("utf-8", "replace") or "{}")


class _EmptyThroughRelay(Exception):
    """The relay carried nothing back. Retried once by through_relay below."""


_through_once = through_relay


def through_relay(method, path, body=None, token=""):
    """Retried, and only for a reply that never arrived.

    Not a loop and not a blanket catch: a 403 is an answer and stays an answer. This
    is for the one failure the suite actually sees - a stream opened in the moment
    after the house attaches that gets nothing at all.

    Three attempts rather than two: with one retry the suite still came back red about
    once in five runs, which is worse than a slow test. It is not a flaky ASSERTION -
    nothing about the product is in doubt here - it is waiting for a rendezvous that
    has been alive for a few milliseconds, and the wait grows until it is enough.
    """
    for attempt in range(3):
        try:
            return _through_once(method, path, body, token)
        except _EmptyThroughRelay:
            if attempt == 2:
                raise
            time.sleep(0.5 * (attempt + 1))


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

# ── what the rendezvous is allowed to know, and how many may ask ────────────────

# The room and its key used to ride in the query string, which is the part of a
# request that everything along the way writes down - Cloudflare's own handler span
# records url.full. The house sends headers now; the old place is still read so an
# install that has not updated yet still gets in.
_rc = io.open(os.path.join(ROOT, 'src', 'wizard', 'relay_client.py'),
              encoding='utf-8').read()
ok("the house does not put its room in a URL",
   '?role=host&room=' not in _rc and 'X-Olivaw-Room' in _rc)
ok("nor its key", 'X-Olivaw-Key' in _rc and 'key=%s' not in _rc)
_dart2 = io.open(os.path.join(ROOT, 'app', 'lib', 'relay.dart'), encoding='utf-8').read()
ok("and neither does the phone",
   '?role=guest&room=' not in _dart2 and 'X-Olivaw-Room' in _dart2)

# Both forms reach the same room. The host above attached with headers; this guest
# uses the old query string, and they have to meet.
_old_style = Guest(RELAY_PORT, cfg["room"])
ok("a guest still attaches the old way", _old_style is not None)
try:
    _old_style.sock.close()
except OSError:
    pass


def _attach_raw(headers):
    """Attach with whatever headers are given; return the status line."""
    s = socket.create_connection(("127.0.0.1", RELAY_PORT), timeout=5)
    k = _b64.b64encode(os.urandom(16)).decode()
    s.sendall(("GET /attach HTTP/1.1\r\nHost: r\r\nUpgrade: websocket\r\n"
               "Connection: Upgrade\r\nSec-WebSocket-Key: %s\r\n%s"
               "Sec-WebSocket-Version: 13\r\n\r\n"
               % (k, "".join("%s: %s\r\n" % h for h in headers))).encode())
    head = s.recv(200).split(b"\r\n", 1)[0]
    return s, head


_s, _head = _attach_raw((("X-Olivaw-Role", "guest"), ("X-Olivaw-Room", cfg["room"])))
ok("and a guest attaches the new way, by header", b" 101 " in _head, _head[:60])
try:
    _s.close()
except OSError:
    pass

# Refused, not shortened. Truncating the id that decides WHICH computer you reach
# quietly merges two rooms that share a prefix.
_s, _head = _attach_raw((("X-Olivaw-Role", "guest"), ("X-Olivaw-Room", "z" * 200)))
ok("a room id that is too long is refused, not cut down",
   b" 400 " in _head, _head[:60])
try:
    _s.close()
except OSError:
    pass

# A room holds a household's phones, not a crowd. Whoever knows the id could
# otherwise sit here in numbers and have every frame the house sends copied to each.
_relay_src = io.open(os.path.join(ROOT, 'relay', 'relay.py'), encoding='utf-8').read()
_MAXG = int(re.search(r'MAX_GUESTS = (\d+)', _relay_src).group(1))
_crowd = []
for _ in range(_MAXG + 2):
    try:
        _crowd.append(Guest(RELAY_PORT, cfg["room"]))
    except OSError:
        break
ok("a room takes a household, not a crowd",
   len(_crowd) <= _MAXG, len(_crowd))
ok("and the phone already in it is the one kept",
   through_relay("POST", "/api/agents/list", {}, away)[0] == 200)
for _g in _crowd:
    try:
        _g.sock.close()
    except OSError:
        pass
time.sleep(0.6)

# The Worker says the same things as the relay beside it.
_wk2 = io.open(os.path.join(ROOT, 'relay', 'worker', 'worker.js'), encoding='utf-8').read()
ok("the Worker caps a room too", 'GUEST_MAX' in _wk2 and 'room-full' in _wk2)
# Comments stripped first: the comment explaining what this used to do necessarily
# contains the thing the code must no longer do.
_code2 = re.sub(r'//.*', '', _wk2)
ok("the Worker refuses a long room id rather than cutting it",
   'room.length > ROOM_MAX' in _code2 and '.slice(0, ROOM_MAX)' not in _code2)
ok("the Worker reads the identifiers from headers", 'x-olivaw-' in _wk2)

# And what it writes down. A log that could answer 'who met here' would be the same
# leak as reading the traffic, arriving by a more respectable route.
_said = [L for L in _code2.split('\n') if 'say(' in L and 'function say' not in L]
# The VALUES, not the words: "wrong-key" is a reason, `key` would be the secret. So
# the string literals come out and what is left is only identifiers.
_bare = [re.sub(r'"[^"]*"', '', L) for L in _said]
ok("nothing it logs carries a room or a key",
   bool(_said) and not any(re.search(r'\\b(room|key)\\b', L) for L in _bare),
   _bare)
_toml = io.open(os.path.join(ROOT, 'relay', 'worker', 'wrangler.toml'),
                encoding='utf-8').read()
ok("its own logs are on", '[observability]' in _toml and 'enabled = true' in _toml)
ok("but not the one that would record the URL", 'invocation_logs = false' in _toml)

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

# ── a voice note is still a voice note tomorrow ──────────────────────────
# Hermes refuses `file` parts, so a recording cannot ride inside a turn: send() names
# it in the message instead, as a block of prose the agent can act on. That block was
# the only record of it - so reopening a conversation brought every note back as its
# transcript followed by a Windows path, with no way to hear what was actually said,
# which is the one thing a note has that a transcription does not.
from wizard import talk as talk_mod  # noqa: E402

_vd = tempfile.mkdtemp()
_ad = talk_mod.attach_dir('daneel', _vd)
_note = os.path.join(_ad, 'nota-1.m4a')
io.open(_note, 'wb').write(b'x' * 2048)
_blk = ('Lo que dije.\n\n'
        '[Archivos que te acabo de adjuntar, ya guardados en tu carpeta:\n'
        '- nota-1.m4a (2 KB): ' + _note + '\n'
        '\u00c1brelos con tus herramientas si necesitas lo que hay dentro.]')
_txt, _fs = talk_mod.read_files_in(_blk, install_dir=_vd, profile='daneel')
ok('a stored note comes back as a file, not as a path',
   len(_fs) == 1 and _fs[0]['kind'] == 'audio', _fs)
ok('with a way to fetch it', bool(_fs[0].get('id')))
ok('and the sentence left behind is just the sentence', _txt == 'Lo que dije.', _txt)

# The path in a transcript is text, not a capability.
_out = ('x\n\n[Archivos que te acabo de adjuntar, ya guardados:\n'
        '- win.ini (1 KB): ' + os.path.join('C:' + os.sep, 'Windows', 'win.ini') + '\n]')
_t2, _f2 = talk_mod.read_files_in(_out, install_dir=_vd, profile='daneel')
ok('a path outside the agent folder is named but not fetchable',
   len(_f2) == 1 and _f2[0].get('missing') is True and not _f2[0].get('id'), _f2)

ok('a message with no attachments is left exactly alone',
   talk_mod.read_files_in('solo texto', install_dir=_vd, profile='daneel')
   == ('solo texto', []))
shutil.rmtree(_vd, ignore_errors=True)

# Only the OWNER's turns. An agent that wrote this block into a reply would otherwise
# be choosing paths for a phone to fetch, which is the whole thing talk/file prevents.
# Asked of the code rather than read out of it: the same stored turn is dressed twice,
# once as the owner's and once as the agent's, and only one of them may come back with
# a file attached to it. The block is the one read_files_in is known to understand -
# the test above built it and proved it works.
_vd2 = tempfile.mkdtemp()
_ad2 = talk_mod.attach_dir('daneel', _vd2)
_note2 = os.path.join(_ad2, 'nota-2.m4a')
io.open(_note2, 'wb').write(b'x' * 2048)
_blk2 = ('Lo que dije.\n\n'
         '[Archivos que te acabo de adjuntar, ya guardados en tu carpeta:\n'
         '- nota-2.m4a (2 KB): ' + _note2 + '\n'
         '\u00c1brelos con tus herramientas si necesitas lo que hay dentro.]')
_as_owner = talk_mod._dress({'role': 'user', 'content': _blk2},
                            install_dir=_vd2, profile='daneel')
_as_agent = talk_mod._dress({'role': 'assistant', 'content': _blk2},
                            install_dir=_vd2, profile='daneel')
ok('the owner\'s own attachments come back as files',
   len(_as_owner.get('files') or []) == 1 and _as_owner['content'] == 'Lo que dije.',
   _as_owner)
ok('and only the owner\'s own turns are read that way',
   not _as_agent.get('files') and '[Archivos' in _as_agent['content'], _as_agent)
shutil.rmtree(_vd2, ignore_errors=True)

# ── a refusal the owner can read ─────────────────────────────────────
# Modules here raise ValueError with a sentence written FOR the owner - «Ponle un nombre
# al equipo». The console has always handed those back as they are; this door wrapped
# them in «error interno», which sends her looking for a bug instead of reading it.
class _Cranky(object):
    def dispatch(self, path, body):
        if body.get("boom"):
            raise RuntimeError("algo se rompió de verdad")
        raise ValueError(u"Ponle un nombre al equipo.")


_real_shim = remote._shim
try:
    remote._shim = _Cranky()
    _out, _code = remote.call_api("teams/group", {}, ["ver", "config"])
    ok("a mistake of the owner's comes back as her own sentence",
       _code == 400 and _out.get("detail") == u"Ponle un nombre al equipo.", (_code, _out))
    ok("and is not dressed up as an internal error",
       "interno" not in str(_out.get("detail")), _out)
    _out, _code = remote.call_api("teams/group", {"boom": True}, ["ver", "config"])
    ok("while a real fault is still a 500, and says so",
       _code == 500 and "interno" in _out.get("detail", ""), (_code, _out))
finally:
    remote._shim = _real_shim

# ── what a phone asks before it shows a conversation ───────────────────
# `status()` answers three questions - configured, listening, answering - and costs
# about six seconds to do it: two subprocesses, a file, and an HTTP call to a gateway
# that may be busy thinking. The chat screen asked it before EVERY conversation it
# opened and read two booleans out of the answer, which was most of the wait before
# anything appeared at all. The console still asks the whole question, because "on but
# not listening yet" is precisely what it has to be able to say.
from wizard import wizard_server as _ws  # noqa: E402

_ws.INSTALL_DIR = SAND
_saved_status, _saved_enabled = talk_mod.status, talk_mod.enabled_flag
_saved_which = _ws.which
_asked_full = []


def _loud_status(*a, **k):
    _asked_full.append(a)
    return {"ok": True, "enabled": True, "ready": True, "reachable": True}


def _say(route, body):
    return _ws.Handler.dispatch(None, "/api/" + route, body)


try:
    talk_mod.status = _loud_status
    talk_mod.enabled_flag = lambda *a, **k: True
    _ws.which = lambda name: ""
    talk_access.set_flag(SAND, "default", "phone", True, True)

    _quick = _say("talk/status", {"quick": True})
    ok('the quick answer says whether this phone may talk',
       _quick.get("ok") is True and _quick.get("phone") is True, _quick)
    ok('and gets there without the six-second examination', not _asked_full, _asked_full)

    _full = _say("talk/status", {})
    ok('while the console still asks the whole question',
       len(_asked_full) == 1 and _full.get("ready") is True, (_asked_full, _full))

    # A phone that has been switched off for this agent is told so, and that answer
    # does not need the examination either.
    talk_access.set_flag(SAND, "default", "phone", False, True)
    _off = _say("talk/status", {"quick": True})
    ok('a phone that has been switched off is told so', _off.get("phone") is False, _off)
    ok('and that answer still costs no examination', len(_asked_full) == 1, _asked_full)
    talk_access.set_flag(SAND, "default", "phone", True, True)
finally:
    talk_mod.status, talk_mod.enabled_flag = _saved_status, _saved_enabled
    _ws.which = _saved_which

# ── a conversation arrives a page at a time ─────────────────────────
# A thread of sixty turns measured 157 KB, of which the phone threw away seven eighths
# on arrival - tool calls, reasoning, compaction notes. Now the computer filters and
# windows it, which only works if "a turn" means the same thing on both sides: a page
# of twenty-five rows that comes out as four bubbles is not a page.
_stored = []
for _i in range(30):
    _stored.append({'role': 'user', 'content': 'pregunta %d' % _i})
    _stored.append({'role': 'assistant', 'content': 'respuesta %d' % _i,
                    'tool_calls': None})
    _stored.append({'role': 'assistant', 'content': 'mirando algo',
                    'tool_calls': [{'name': 'read'}]})
    _stored.append({'role': 'tool', 'content': 'resultado'})
_stored.append({'role': 'assistant', 'content': '[CONTEXT COMPACTION] resumen'})
_stored.append({'role': 'user', 'content': '   '})

_saved_msgs = talk_mod._messages_of
talk_mod._messages_of = lambda profile, hermes, sid: (list(_stored), None)
try:
    _all = talk_mod.history(None, None, session_id='s1')
    ok('only what somebody actually said is a turn',
       _all['total'] == 60 and len(_all['messages']) == 60, _all.get('total'))
    ok('a tool call is not a turn, and neither is a compaction note',
       not any('mirando' in m['content'] or 'COMPACTION' in m['content']
               for m in _all['messages']))

    _last = talk_mod.history(None, None, session_id='s1', limit=25)
    ok('a page is the END of the conversation, which is where you left off',
       len(_last['messages']) == 25 and _last['messages'][-1]['content'] == 'respuesta 29',
       _last['messages'][-1]['content'])
    ok('and it says where in the whole thread it begins',
       (_last['start'], _last['end'], _last['has_more']) == (35, 60, True),
       (_last['start'], _last['end'], _last['has_more']))

    _back = talk_mod.history(None, None, session_id='s1', limit=25,
                             before=_last['start'])
    ok('the page before it ends exactly where that one starts',
       _back['end'] == _last['start'] and _back['start'] == 10, _back)
    _joined = [m['content'] for m in _back['messages'] + _last['messages']]
    ok('two pages joined are the conversation, with nothing lost or repeated',
       _joined == [m['content'] for m in _all['messages'][10:]], len(_joined))

    _first = talk_mod.history(None, None, session_id='s1', limit=25, before=10)
    ok('and the first page knows there is nothing before it',
       _first['start'] == 0 and _first['has_more'] is False, _first)

    # Indices count from the START for a reason: the agent may be answering while the
    # owner reads, and a page measured from the end would shift under them.
    _stored.append({'role': 'assistant', 'content': 'y una m\u00e1s'})
    _after = talk_mod.history(None, None, session_id='s1', limit=25,
                              before=_last['start'])
    ok('a turn arriving while you read does not move the page you asked for',
       [m['content'] for m in _after['messages']] ==
       [m['content'] for m in _back['messages']])
    _stored.pop()
finally:
    talk_mod._messages_of = _saved_msgs

# ── and the list of conversations does too ────────────────────────────
_asked = []
_rows_by_offset = {
    0: [{'id': 'a%d' % i, 'title': None, 'preview': 'hola %d' % i,
         'last_active': 2000 - i} for i in range(15)],
    15: [{'id': 'b%d' % i, 'title': None, 'preview': 'adi\u00f3s %d' % i,
          'last_active': 1000 - i} for i in range(15)],
}


def _fake_http(url, key, data=None, method=None, timeout=25):
    _asked.append(url)
    if '/sessions?' in url:
        off = int(url.split('offset=')[1])
        rows = _rows_by_offset.get(off, [])
        return True, {'data': rows, 'has_more': off == 0}, 200
    return True, {'data': []}, 200


_saved_http, _saved_req = talk_mod._http, talk_mod._require
try:
    talk_mod._http = _fake_http
    talk_mod._require = lambda profile, hermes: ('k', 1234, None)
    _p1 = talk_mod.sessions(None, None, limit=15)
    _p2 = talk_mod.sessions(None, None, limit=15, offset=15)
    ok('the list is asked for a page at a time',
       'offset=0' in _asked[0] and 'offset=15' in _asked[1], _asked[:2])
    ok('and the phone is told whether there is another page',
       _p1['has_more'] is True and _p2['has_more'] is False)
    ok('each page is the one that was asked for',
       _p1['sessions'][0]['id'] == 'a0' and _p2['sessions'][0]['id'] == 'b0')
finally:
    talk_mod._http, talk_mod._require = _saved_http, _saved_req

# ── searching inside them, not at their names ──────────────────────
# Hermes rarely names a session, so a search over titles would find nothing. What a
# person remembers is a sentence from the middle of a conversation.
_talks = {
    'uno': [{'role': 'user', 'content': u'Te paso el INFORM\u00c9 anual del viernes'}],
    'dos': [{'role': 'assistant', 'content': u'Nada que ver aqu\u00ed'}],
    'tres': [{'role': 'user', 'content': u'el informe de ventas, sin m\u00e1s'}],
    'cuatro': [{'role': 'assistant', 'content': u'mirando', 'tool_calls': [{'n': 1}]}],
}
_rows2 = [{'id': 'uno', 'title': None, 'preview': 'hola', 'last_active': 300},
          {'id': 'dos', 'title': None, 'preview': 'hola', 'last_active': 400},
          {'id': 'tres', 'title': u'Informe de ventas', 'preview': '', 'last_active': 200},
          {'id': 'cuatro', 'title': None, 'preview': '', 'last_active': 100}]
_opened = []


def _fake_sessions(profile=None, hermes=None, limit=20, offset=0):
    return {'ok': True, 'sessions': _rows2 if not offset else [], 'has_more': False}


def _fake_messages(profile, hermes, sid):
    _opened.append(sid)
    return list(_talks.get(sid, [])), None


_saved_sessions, _saved_msgs2 = talk_mod.sessions, talk_mod._messages_of
_saved_req2 = talk_mod._require
try:
    talk_mod.sessions = _fake_sessions
    talk_mod._messages_of = _fake_messages
    talk_mod._require = lambda profile, hermes: ('k', 1234, None)

    _r = talk_mod.search(None, None, query=u'informe')
    _ids = [h['id'] for h in _r['sessions']]
    ok('a word said in the middle of a conversation finds it', 'uno' in _ids, _ids)
    ok('accents and capitals are not part of the question',
       any(h['id'] == 'uno' and h['snippet']['hit'] == u'INFORM\u00c9'
           for h in _r['sessions']),
       [h.get('snippet') for h in _r['sessions']])
    ok('a title that matches is found without opening the conversation',
       any(h['id'] == 'tres' and h['where'] == 'title' for h in _r['sessions']))
    ok('and "tres" was never opened', 'tres' not in _opened, _opened)
    ok('the ones that say nothing of the sort stay out', 'dos' not in _ids)
    ok('newest first, the same order the list itself is in',
       _ids == sorted(_ids, key=lambda i: -{r['id']: r['last_active']
                                            for r in _rows2}[i]), _ids)

    # The highlight is cut by the computer because it is the side that matched. If the
    # three parts do not rebuild the sentence, the phone is drawing a different one.
    _hit = [h for h in _r['sessions'] if h['id'] == 'uno'][0]['snippet']
    ok('the snippet is the sentence, in three parts',
       (_hit['before'] + _hit['hit'] + _hit['after']).replace(u'\u2026', '')
       in u'Te paso el INFORM\u00c9 anual del viernes',
       _hit)

    _both = talk_mod.search(None, None, query=u'informe ventas')
    ok('two words narrow it rather than widening it',
       [h['id'] for h in _both['sessions']] == ['tres'],
       [h['id'] for h in _both['sessions']])

    _opened[:] = []
    _shallow = talk_mod.search(None, None, query=u'informe', deep=False)
    ok('a shallow search opens nothing', not _opened, _opened)
    ok('and still finds what the titles say',
       [h['id'] for h in _shallow['sessions']] == ['tres'])

    ok('an empty search is not a search for everything',
       talk_mod.search(None, None, query='   ')['sessions'] == [])
finally:
    talk_mod.sessions = _saved_sessions
    talk_mod._messages_of = _saved_msgs2
    talk_mod._require = _saved_req2

# ── and the gateway is located once, not before every request ─────────────
# `env_of` reads a file and `_configured_port` shells out to `hermes config get`:
# together 1.93 seconds, and they were paid before EVERY call a phone made. Opening a
# conversation spent two seconds deciding which port to knock on and twenty
# milliseconds reading the conversation.
_looks = []
_saved_env, _saved_port = talk_mod.env_of, talk_mod._configured_port
try:
    talk_mod.env_of = lambda profile=None, hermes=None: (_looks.append('env')
                                                         or {talk_mod.KEY_ENV: 'k'})
    talk_mod._configured_port = lambda profile=None, hermes=None: (_looks.append('port')
                                                                   or 4321)
    talk_mod._forget_gate()
    for _ in range(20):
        talk_mod._require('ag', None)
    ok('twenty requests ask the config once', _looks.count('port') == 1, _looks)

    talk_mod._forget_gate('ag', None)
    talk_mod._require('ag', None)
    ok('turning the gateway on or off forgets where it was',
       _looks.count('port') == 2, _looks)

    # A port that has gone stale is not a thing to remember: nothing answers on it.
    talk_mod._gate_gone('ag', None, 0)
    talk_mod._require('ag', None)
    ok('a port nothing answers on is looked up again', _looks.count('port') == 3, _looks)

    talk_mod._gate_gone('ag', None, 401)
    talk_mod._require('ag', None)
    ok('but an answer that says no is still an answer, and the port stands',
       _looks.count('port') == 3, _looks)
finally:
    talk_mod.env_of, talk_mod._configured_port = _saved_env, _saved_port
    talk_mod._forget_gate()

# Asking a phone to wait for the whole examination before every message was the other
# half of it: `status()` costs an HTTP call on top, and remote.py called it per request.
ok('the phone door asks the cheap question first',
   'decided_for_phone' in io.open(os.path.join(ROOT, 'src', 'wizard', 'remote.py'),
                                  encoding='utf-8').read())
_acc = os.path.join(SAND, 'access-quick')
os.makedirs(_acc, exist_ok=True)
import wizard.talk_access as _access  # noqa: E402
ok('an agent nobody has decided about has no answer of its own',
   _access.decided_for_phone(_acc, 'nadie') is None)
_access.set_flag(_acc, 'daneel', 'phone', False, True)
ok('and one the owner has switched off says so without asking hermes',
   _access.decided_for_phone(_acc, 'daneel') is False)
_access.set_flag(_acc, 'daneel', 'phone', True, False)
ok('as does one they switched on', _access.decided_for_phone(_acc, 'daneel') is True)

# ── nine requests at once, one devices.json ───────────────────────────
# The door is threaded and every authenticated request stamps the device's last_seen,
# so a screen that asks nine things at once had nine threads doing read-modify-write
# on one file. On Windows they collide three ways - the temp file is open by another
# thread, the rename is denied, or the other thread already renamed it away - and all
# three came out of the handler as a dropped connection. Most of a burst simply
# failed, which from the phone looks like the computer refusing to answer.
_burst = []


def _hammer():
    # _save, not touch: touch is throttled now, so a burst of touches would prove
    # only that the throttle works. The shared resource is the file, and every
    # mutation of the store goes through here.
    try:
        for _ in range(20):
            pairing._save(SAND, pairing._load(SAND))
            pairing.touch(SAND, dev_id)
        _burst.append(True)
    except Exception as e:  # noqa: BLE001
        _burst.append(e)


_threads = [threading.Thread(target=_hammer) for _ in range(9)]
for _t in _threads:
    _t.start()
for _t in _threads:
    _t.join()
ok('nine at once never lose a request between them',
   all(x is True for x in _burst), [x for x in _burst if x is not True][:2])

# And nothing is left lying around: a temp file per write is only an improvement if
# it is also cleaned up.
_litter = [n for n in os.listdir(SAND) if n.startswith('devices.json.') 
           and n.endswith('.tmp')]
ok('and leave no half-written files behind', not _litter, _litter)

# Recording that a phone is alive must never be the reason a request fails. Even a
# store that cannot be written at all is a lost timestamp, not a lost answer.
_real_save = pairing._save
try:
    pairing._save = lambda *a, **k: (_ for _ in ()).throw(OSError('disco lleno'))
    pairing.touch(SAND, dev_id)   # must not raise
    ok('and a store that cannot be written loses a timestamp, not the request', True)
except Exception as e:  # noqa: BLE001
    ok('and a store that cannot be written loses a timestamp, not the request',
       False, e)
finally:
    pairing._save = _real_save

# The stamp is shown to the minute, so writing it nine times a second to record the
# same minute is work nobody asked for - and it was the volume that made the race
# easy to hit in the first place.
ok('a stamp that has not aged is not rewritten', pairing.TOUCH_EVERY >= 30,
   pairing.TOUCH_EVERY)

# ── how much work has been done, and by which brain ─────────────────────
# The counting is Hermes' - it keeps a row per conversation the same way whichever
# brain is behind the bridge - so this never asks a brain anything, and Claude Code
# and Codex need no special case. The ENGINE is a property of the AGENT, read from
# Olivaw's registry, so every number can be labelled with the process that made it.
from wizard import usage as usage_mod  # noqa: E402

_now = time.time()
_rows = [
    {'id': 'a', 'source': 'telegram', 'model': 'claude-code', 'message_count': 10,
     'tool_call_count': 4, 'input_tokens': 1000, 'output_tokens': 100,
     'last_active': _now - 3600},
    {'id': 'b', 'source': 'cron', 'model': 'hermes-agent', 'message_count': 2,
     'tool_call_count': 1, 'input_tokens': 50, 'output_tokens': 5,
     'last_active': _now - 86400},
    # Older than the window: counted by nobody.
    {'id': 'c', 'source': 'cli', 'model': 'claude-code', 'message_count': 99,
     'tool_call_count': 99, 'input_tokens': 999999, 'output_tokens': 9999,
     'last_active': _now - 400 * 86400},
]
_real_sessions = usage_mod.talk.sessions
try:
    usage_mod.talk.sessions = lambda *a, **k: {'ok': True, 'sessions': _rows}
    _u = usage_mod.for_agent(None, days=30, install_dir=SAND)
    ok('a window means a window', _u['sessions'] == 2, _u['sessions'])
    ok('and the counters add up', _u['message_count'] == 12
       and _u['input_tokens'] == 1050, _u)
    ok('where the work came from is kept',
       _u['by_source'] == {'telegram': 1, 'cron': 1}, _u['by_source'])
    # A subscription reports no price, and summing zero would read as free.
    ok('no price is no price, not zero', _u['cost_usd'] == 0)

    _rep = usage_mod.report(
        [{'slug': 'default', 'name': 'Principal'}, {'slug': 'otro', 'name': 'Otro'}],
        {'teams': [{'id': 'eq', 'name': 'Equipo', 'icon': '\U0001f3e5',
                    'members': ['default']}]},
        install_dir=SAND)
    ok('a team is the sum of its members',
       _rep['teams'][0]['message_count'] == 12, _rep['teams'][0])
    ok('and it keeps its mark', _rep['teams'][0]['icon'] == '\U0001f3e5')
    ok('an agent on no team is still counted somewhere',
       _rep['unassigned'] == ['otro'], _rep['unassigned'])
    # Every number carries the brain that produced it, so a team running two of them
    # can be split without either side knowing the other exists.
    ok('every agent is labelled with its brain',
       all(a.get('engine') for a in _rep['agents']), _rep['agents'])
    ok('and the report says whether money was measured at all',
       _rep['costed'] is False)

    # A gateway that is off has NO numbers. Reporting zero would be a measurement
    # nobody took, and it would quietly drag a team's totals down.
    usage_mod.talk.sessions = lambda *a, **k: {'ok': False, 'detail': 'apagado'}
    _off = usage_mod.for_agent('x', days=30, install_dir=SAND)
    ok('an agent that cannot answer has no numbers, not zero numbers',
       _off['known'] is False and _off['sessions'] == 0, _off)
finally:
    usage_mod.talk.sessions = _real_sessions

ok('a phone that may look may see the numbers',
   pairing.allowed('stats/usage', ['ver']))
_m3 = io.open(os.path.join(ROOT, 'app', 'lib', 'main.dart'), encoding='utf-8').read()
ok('and the main panel has the three views',
   "tr('Agentes')" in _m3 and "tr('Equipos')" in _m3 and "tr('Uso')" in _m3)

# ── whose team an agent is on ────────────────────────────────────────
# Seven names say nothing about who works with whom, and that grouping is the owner's
# own. It rides with the roster for the same reason the face does: a list that draws
# every agent should not need a second call per agent to say the one thing that
# groups them.
import teams as teams_mod  # noqa: E402

_td = tempfile.mkdtemp()
teams_mod.set_team(None, name='Pruebas', install_dir=_td,
                   roster=[{'slug': 'default', 'name': 'x'}])
_st, _data = teams_mod.read(_td)
_gid = teams_mod.teams_of(_data)[0]['id']
teams_mod.set_team(_gid, install_dir=_td, icon='🏥')
_st, _data = teams_mod.read(_td)
ok('a team can be given a mark',
   teams_mod.teams_of(_data)[0].get('icon') == '🏥')
# None means 'not mentioned' and '' means 'take it off'. Collapsing the two would
# make renaming a team quietly erase its mark.
teams_mod.set_team(_gid, name='Pruebas 2', install_dir=_td)
_st, _data = teams_mod.read(_td)
ok('and renaming it does not drop the mark',
   teams_mod.teams_of(_data)[0].get('icon') == '🏥')
teams_mod.set_team(_gid, install_dir=_td, icon='')
_st, _data = teams_mod.read(_td)
ok('but clearing it on purpose does',
   teams_mod.teams_of(_data)[0].get('icon') == '')
shutil.rmtree(_td, ignore_errors=True)

_srv2 = io.open(os.path.join(ROOT, 'src', 'wizard', 'wizard_server.py'),
                encoding='utf-8').read()
ok('the roster says which team each agent is on',
   '_team_badge(' in _srv2 and 'row["team"]' in _srv2)
_m2 = io.open(os.path.join(ROOT, 'app', 'lib', 'main.dart'), encoding='utf-8').read()
ok('and the app draws it under the name',
   'class _AgentLine' in _m2 and 'teamIcon' in _m2)

# ── routines, and the shape they come back in ─────────────────────────────
# cron/list answers the console, which asks about the MACHINE and gets
# {agents: [...]}. The phone asks about the agent whose screen is open and got the
# same thing - a shape with no 'jobs' in it - so it read an empty list and drew an
# empty routines tab for every agent, on a machine with seventeen of them. It never
# errored; there was simply nothing where it looked.
_srv = io.open(os.path.join(ROOT, 'src', 'wizard', 'wizard_server.py'),
               encoding='utf-8').read()
_cron = _srv[_srv.index('if route == "cron/list"'):]
_cron = _cron[:_cron.index('if route ==', 40)]
ok('asked about one agent, cron/list answers about one agent',
   '"jobs"' in _cron and 'profile' in _cron)
ok('and the console, which names no profile, still gets the machine',
   'if want is None' in _cron and 'return st' in _cron)
# 'default' is None inside the server and the string 'default' in the roster.
# Comparing one to the other quietly matched nothing, which is how the agent with
# sixteen jobs came back with none.
ok('and the main agent is not lost between its two names',
   'or "default"' in _cron)
_app_main = io.open(os.path.join(ROOT, 'app', 'lib', 'main.dart'),
                    encoding='utf-8').read()
ok('the app always names the agent it is asking about',
   "body: {'profile': widget.agent.slug}" in _app_main)
ok('and still finds the jobs if the computer answers the old way',
   '_jobsIn(' in _app_main and "res['agents']" in _app_main)

# ── the face a phone may look at ───────────────────────────────────────
# The console draws a face from a GET on its own token, which a phone does not have
# and should not be given - so the phone could SET a picture and never see it, which
# reads exactly like the upload having failed.
ok('a phone may look at the face', pairing.allowed('avatar/get', ['ver']))
ok('and still may not change it without config',
   not pairing.allowed('avatar/set', ['ver']))
ok('with config, it may', pairing.allowed('avatar/set', ['ver', 'config']))

from wizard import avatars  # noqa: E402

# And the picture has to turn up wherever the agent does. The roster already says
# WHETHER an agent has one and which version; only the bytes are a second request,
# because seven photos inline would make the first screen a quarter of a megabyte on
# a connection that may be mobile data.
ok('the roster carries what a face is and which version it is at',
   'kind' in avatars.look('daneel', 'Daneel', SAND)
   and 'v' in avatars.look('daneel', 'Daneel', SAND))
ok('the app draws a photo wherever it draws a face',
   'class Faces {' in _app_main and 'ClipOval(' in _app_main)
ok('keyed by version, so a changed picture is a different picture',
   "'${a.slug}@${a.look['v'] ?? 0}'" in _app_main)
# The open chat draws the agent the SCREEN is holding, not the one the roster handed
# it: a rename or a new face has to show in the title straight away.
ok('the roster and the open chat both get one',
   'Face(agent: a, link: _link)' in _app_main
   and 'Face(agent: _agent, size: 30, link: widget.link)' in _app_main)
# A face that did not change on the screen you came from looks exactly like one that
# was not saved.
ok('and the roster is asked again after a change',
   'if (changed == true && mounted) _refresh();' in _app_main)

_facedir = tempfile.mkdtemp()
_noface = avatars.picture('daneel', _facedir)
ok('an agent with no picture says so rather than failing',
   _noface.get('ok') is True and _noface.get('data_b64') == '', _noface)
_badslug = avatars.picture('../../etc', _facedir)
ok('and a slug that is a path is refused', _badslug.get('ok') is False, _badslug)
shutil.rmtree(_facedir, ignore_errors=True)

# A picked file that cannot be read is NOT a picked file that does not exist. The
# two were the same empty list, so choosing a photo Android could not read did
# nothing and said nothing.
_kt = io.open(os.path.join(ROOT, 'app', 'android', 'app', 'src', 'main', 'kotlin',
                           'com', 'olivaw', 'olivaw', 'MainActivity.kt'),
              encoding='utf-8').read()
ok('a pick that could not be read is an error, not an empty hand',
   'out.isEmpty() && uris.isNotEmpty()' in _kt)
ok('and the recording can be paused and picked up again',
   'recordPause' in _kt and 'recordResume' in _kt)
ok('and whatever was playing is paused while the microphone is on',
   'playPause' in _kt and 'playResume' in _kt)

# ── the agent is told it can answer one message in particular ─────────────────
# The phone has been able to DRAW an agent's reply as a reply since the day it could
# draw one - the quote is the whole protocol - and no agent had been told it could
# write one. So the skill teaches a shape and the app parses a shape, and the only
# thing holding them together is that somebody checked.
from wizard import reply_skill  # noqa: E402

_sk = reply_skill.render_skill()
ok('the skill says how to quote', '> **' in _sk and ':**' in _sk)
ok('and says when NOT to', 'no es el formato por defecto' in _sk)

# The example in the skill, parsed by the app's own rule. If either side moves, this
# is what notices.
_ex = [L for L in _sk.split('\n') if L.startswith('> **')]
ok('the skill shows an example to copy', bool(_ex), _sk[:80])
_dart_re = re.search(r"RegExp\(r'(\^>[^']+)'\)", _main)
ok('the app has one rule for reading it', _dart_re is not None)
if _ex and _dart_re:
    # Dart and Python agree on this much of the syntax, which is all it uses.
    _pat = _dart_re.group(1).replace('[ \\t]', '[ \\t]')
    _m = re.match(_pat, _ex[0] + '\n')
    ok('and that rule reads the example the agent is given',
       _m is not None and bool((_m.group(1) or '').strip())
       and bool((_m.group(2) or '').strip()),
       _ex[0])

# Writing it twice must not rewrite the file: the supervisor calls this on a tick,
# and a skill that changes every fifteen seconds restarts a gateway every fifteen
# seconds.
_skhome = tempfile.mkdtemp()
_r1 = reply_skill.install_skill('daneel', home=_skhome)
_r2 = reply_skill.install_skill('daneel', home=_skhome)
ok('the skill installs', _r1.get('changed') is True)
ok('and installing it again changes nothing', _r2.get('changed') is False)
ok('it lands where the agent will look for it',
   os.path.join('profiles', 'daneel', 'skills') in _r1['path'])
shutil.rmtree(_skhome, ignore_errors=True)

# ── every sentence the app can say ───────────────────────────────────
# Spanish IS the key, so a missing row is not a crash - tr() hands back the Spanish
# and the screen looks finished. On a phone set to German it is half translated, and
# the only way anyone finds out is by holding one. Three sentences had already slipped
# through that way before this existed.
_LIT = r"'(?:[^'\\]|\\.)*'"
_lib = os.path.join(ROOT, 'app', 'lib')
_tbl = io.open(os.path.join(_lib, 'strings.dart'), encoding='utf-8').read()
_have = set()
# A key may be spread over adjacent literals on several lines; Dart joins them, so
# reading one line at a time reports keys as missing that are already there.
for _m in re.finditer(r'^  ((?:' + _LIT + r'\s*)+):\s*\(', _tbl, re.M):
    _have.add(''.join(x[1:-1] for x in re.findall(_LIT, _m.group(1))))
_untranslated = []
for _name in sorted(os.listdir(_lib)):
    if not _name.endswith('.dart') or _name == 'strings.dart':
        continue
    _src = io.open(os.path.join(_lib, _name), encoding='utf-8').read()
    for _m in re.finditer(r'\btrp?\(\s*((?:' + _LIT + r'\s*)+)', _src):
        _key = ''.join(x[1:-1] for x in re.findall(_LIT, _m.group(1)))
        if _key and _key not in _have:
            _untranslated.append('%s: %s' % (_name, _key[:60]))
    # And a sentence CHOSEN inside the brackets: `tr(cond ? 'a' : 'b')` matches
    # neither pattern above, so both of its sentences were invisible - the exact
    # failure this check exists to catch, arriving through its own blind spot. The
    # two keys are read out and checked like any other rather than merely reported:
    # a scanner that says 'I cannot read this' is one more thing to learn to ignore.
    # Across lines, because that is where dart format puts the `?` - the first
    # version of this check stopped at the newline and so missed every ternary
    # long enough to wrap, which is all of them. It stops at the first quote
    # instead, so a call that simply STARTS with a sentence is not swept up.
    for _m in re.finditer(r'\btrp?\(\s*[^\']{0,120}\?\s*(' + _LIT +
                          r')\s*:\s*(' + _LIT + r')', _src):
        for _g in (_m.group(1), _m.group(2)):
            _key = _g[1:-1]
            if _key and _key not in _have:
                _untranslated.append('%s (in a choice): %s' % (_name, _key[:60]))
    # And the sentences handed to a widget that translates them later. Reading only
    # tr() call sites missed every one of these, and the loading screens went out
    # speaking Spanish on a German phone - which is exactly what this check exists
    # to prevent, and exactly what it failed to see.
    for _m in re.finditer(r'lines:\s*(?:const\s*)?\[(.*?)\]', _src, re.S):
        for _lit in re.findall(_LIT, _m.group(1)):
            _key = _lit[1:-1]
            if _key and _key not in _have:
                _untranslated.append('%s (lines): %s' % (_name, _key[:60]))
ok('every sentence the app says has English and German',
   not _untranslated, _untranslated[:4])

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
