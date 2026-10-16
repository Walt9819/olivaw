# -*- coding: utf-8 -*-
"""The door the phone comes in through.

The desktop console (``wizard_server.py``) cannot be that door, for three reasons that
are all deliberate in that file: it binds 127.0.0.1, it refuses any request whose ``Host``
is not loopback, and it only exists while the owner has the console open. A phone needs
the opposite of all three - a real network address, a hostname that is not localhost, and
something listening at four in the morning. The only process that is always up is the
supervisor, ``launcher.py``, so this lives there.

Opening a port on the house wifi is the moment this product could stop being safe, so
every decision here is the cautious one:

* **Off until asked.** Nothing listens until the owner pairs a phone, and the master
  switch in the console shuts it for every device at once.
* **TLS always**, with a certificate this machine makes for itself (``selfcert``) and the
  phone pins by fingerprint from the pairing QR. No CA, no public name, no open inbound
  port needed for either.
* **A bearer token per device**, stored only as a hash, and a permission table where a
  route that nobody listed is a route nobody can call (``pairing.ROUTES``).
* **The console's API is reused, not reimplemented.** One ``dispatch`` means the phone
  and the desktop cannot drift into disagreeing about what "pause this routine" does -
  but the phone reaches it through the filter, never around it.
* **DevTools is never exposed.** Chrome's debugging port stays on loopback. The phone
  gets JPEGs and sends taps; this process is the only thing that ever speaks CDP.

What it does NOT do is solve being away from home: this listens on the local network.
Reaching it from mobile data is ``relay_client.py``, which dials *out* to a rendezvous
and hands it the same TLS bytes - so that relay, wherever it runs and whoever runs it,
only ever sees ciphertext between two ends that already know each other.
"""

import base64
import hashlib
import json
import os
import socket
import ssl
import struct
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from . import pairing, screencast, selfcert

PORT = 8791
MAX_BODY = 12 * 1024 * 1024        # a phone is not where 40MB attachments come from
WS_GUID = b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
IDLE_CLOSE = 15 * 60               # a viewer nobody is looking at
PROTOCOL = 1

_server = None
_lock = threading.Lock()
_log = lambda *a: None             # noqa: E731  - replaced by start()


def log(msg):
    try:
        _log("remote: %s" % msg)
    except Exception:  # noqa: BLE001
        pass


# ── the console's API, reached through the filter ──────────────────────────────────────

_shim = None


def _api():
    """One instance of the console's request handler, with no socket under it.

    ``Handler.dispatch`` is a method, but it only ever reaches for ``self._something``
    helpers - never rfile, wfile or headers - so an instance that never saw a connection
    answers exactly as the desktop one would. Imported lazily and inside a try: this runs
    in the supervisor, and a supervisor that will not start because an unrelated console
    module raised at import time is a worse failure than a phone that cannot connect.
    """
    global _shim
    if _shim is None:
        from . import wizard_server

        class _Shim(wizard_server.Handler):
            def __init__(self):            # noqa: D107 - deliberately not the parent's
                pass                       # BaseHTTPRequestHandler.__init__ serves a
                                           # request; we only want the methods.
        _shim = _Shim()
    return _shim


def call_api(route, body, caps):
    """Answer one API call from a phone, or say why not. Default deny lives here."""
    if not pairing.allowed(route, caps):
        return {"ok": False, "detail": u"Ese permiso no está activado para este teléfono.",
                "denied": route}, 403
    if route == "net/home":
        # Answered here rather than by the console, because it is about the connection
        # itself and the console knows nothing about it. A phone asks on every successful
        # local connection, so a rendezvous configured AFTER a phone was paired still
        # reaches it - while it can still get home to be told.
        try:
            from . import relay_client
            rc = relay_client.config(INSTALL_DIR)
            return {"ok": True, "relay": ({"url": rc["url"], "room": rc.get("room", "")}
                                          if rc.get("on") and rc.get("url") else None),
                    # What this phone may do, as the owner has it set NOW. The phone was
                    # told once, at pairing, and had no way to hear about it again: a
                    # permission granted afterwards never appeared, and one taken away
                    # left the button on screen to be refused when pressed.
                    "caps": list(caps),
                    "addresses": selfcert.local_ips(), "port": PORT}, 200
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "detail": str(e)}, 500
    try:
        return _api().dispatch("/api/" + route, body or {}), 200
    except Exception as e:  # noqa: BLE001
        log("api %s failed: %s" % (route, e))
        return {"ok": False, "detail": u"error interno: %s" % e}, 500


# ── WebSocket, the server half ─────────────────────────────────────────────────────────
# browser_label._WS is the client half (Olivaw -> Chrome). This is the other direction
# and cannot reuse it: a server never masks what it sends and must reject anything a
# client sends unmasked, which is RFC 6455's way of catching a proxy that got confused
# about which end it was.

OP_TEXT, OP_BIN, OP_CLOSE, OP_PING, OP_PONG = 0x1, 0x2, 0x8, 0x9, 0xA


class WSError(Exception):
    pass


class WS(object):
    def __init__(self, sock):
        self.sock = sock
        self.lock = threading.Lock()
        self.closed = False

    @staticmethod
    def accept_key(key):
        return base64.b64encode(
            hashlib.sha1(key.encode("ascii") + WS_GUID).digest()).decode("ascii")

    def _send(self, op, payload):
        n = len(payload)
        if n < 126:
            head = struct.pack("!BB", 0x80 | op, n)
        elif n < 65536:
            head = struct.pack("!BBH", 0x80 | op, 126, n)
        else:
            head = struct.pack("!BBQ", 0x80 | op, 127, n)
        with self.lock:
            if self.closed:
                raise WSError("closed")
            self.sock.sendall(head + payload)

    def send_text(self, obj):
        self._send(OP_TEXT, json.dumps(obj, ensure_ascii=False).encode("utf-8"))

    def send_binary(self, data):
        self._send(OP_BIN, data)

    def _exact(self, n):
        out = b""
        while len(out) < n:
            chunk = self.sock.recv(n - len(out))
            if not chunk:
                raise WSError("closed")
            out += chunk
        return out

    def recv(self):
        """One client frame as text, or None for something we ignore."""
        b1, b2 = self._exact(2)
        op = b1 & 0x0F
        masked = b2 & 0x80
        n = b2 & 0x7F
        if n == 126:
            n = struct.unpack("!H", self._exact(2))[0]
        elif n == 127:
            n = struct.unpack("!Q", self._exact(8))[0]
        if n > 1 << 20:
            raise WSError("frame too large")
        if not masked:
            # A client that does not mask is either broken or not a browser; either way
            # RFC 6455 says the connection ends here.
            raise WSError("client frame was not masked")
        mask = self._exact(4)
        payload = self._exact(n) if n else b""
        data = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        if op == OP_CLOSE:
            raise WSError("client closed")
        if op == OP_PING:
            self._send(OP_PONG, data)
            return None
        if op == OP_TEXT:
            return data.decode("utf-8", "replace")
        return None

    def close(self):
        with self.lock:
            if self.closed:
                return
            self.closed = True
        try:
            self.sock.sendall(struct.pack("!BB", 0x80 | OP_CLOSE, 0))
        except Exception:  # noqa: BLE001
            pass


# ── the HTTP(S) handler ────────────────────────────────────────────────────────────────

class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "Olivaw"
    sys_version = ""

    def log_message(self, *a):          # the supervisor's log is not an access log
        pass

    # Olivaw's own reply, never a page: there is no browser UI on this port, so an
    # error body is JSON and nothing here ever renders HTML from a request.
    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _device(self):
        """The paired device behind this request, or None.

        Bearer in a header, never in the URL: a query string is the part that ends up in
        logs, in Referer and in screenshots.
        """
        auth = self.headers.get("Authorization", "") or ""
        if not auth.startswith("Bearer "):
            return None
        dev = pairing.device_for(INSTALL_DIR, auth[7:].strip())
        if dev:
            pairing.touch(INSTALL_DIR, dev.get("id"))
        return dev

    def _body(self):
        n = int(self.headers.get("Content-Length", 0) or 0)
        if n > MAX_BODY:
            return None
        raw = self.rfile.read(n) if n else b"{}"
        try:
            return json.loads(raw.decode("utf-8") or "{}")
        except Exception:  # noqa: BLE001
            return {}

    # ── routes ─────────────────────────────────────────────────────────────────────
    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/ping":
            # Unauthenticated on purpose and deliberately boring: it says a version and
            # nothing about this machine, its agents or its owner. It is how the phone
            # finds out the address still points here before it offers a token.
            return self._json({"ok": True, "app": "olivaw", "protocol": PROTOCOL,
                               "pairing": pairing.offer_open(INSTALL_DIR)})
        if path == "/ws/browser":
            return self._browser_ws()
        self._json({"ok": False, "detail": "no"}, 404)

    def do_POST(self):
        path = urlparse(self.path).path
        if path == "/pair":
            return self._pair()
        if path.startswith("/api/"):
            return self._api_call(path[len("/api/"):].strip("/"))
        self._json({"ok": False, "detail": "no"}, 404)

    def _pair(self):
        """The only route a device without a token may call, and only while the owner has
        the pairing window open on the desktop."""
        body = self._body() or {}
        res = pairing.claim(INSTALL_DIR,
                            secret=str(body.get("secret") or ""),
                            pin=str(body.get("pin") or ""),
                            name=str(body.get("name") or ""),
                            platform=str(body.get("platform") or ""))
        if res.get("ok"):
            log("paired a device: %s (%s)" % (res.get("name"), res.get("device_id")))
            # How to find this house from outside it. It has to be handed over HERE,
            # during pairing, because by definition the phone cannot ask for it later:
            # the moment it needs the rendezvous is the moment it cannot reach the
            # machine to be told about one. The room id is not a secret worth withholding
            # from a device that already holds a token - a stranger who learns it meets
            # the same pinned TLS and the same permission table.
            try:
                from . import relay_client
                rc = relay_client.config(INSTALL_DIR)
                # Pairing a phone IS the owner saying they want to use one, and a
                # phone that only works in the same room as the computer is not the
                # thing they asked for. So the rendezvous comes on here, at the moment
                # the intent is expressed - not silently at install, which would have
                # every machine dialling out whether or not anybody wanted a phone.
                #
                # Only when nothing has been decided. An owner who turned it OFF stays
                # off; this is a default, not an override.
                if rc.get("on") is None:
                    rc = relay_client.save_config(INSTALL_DIR, on=True)
                    try:
                        relay_client.start(INSTALL_DIR, PORT, logger=log)
                    except Exception:  # noqa: BLE001
                        pass   # the supervisor will attach on its next tick
                if rc.get("on") and rc.get("url"):
                    res["relay"] = {"url": rc["url"], "room": rc.get("room", "")}
            except Exception:  # noqa: BLE001
                pass
        else:
            log("a pairing attempt was refused: %s" % res.get("detail"))
        return self._json(res, 200 if res.get("ok") else 403)

    def _api_call(self, route):
        dev = self._device()
        if not dev:
            return self._json({"ok": False, "detail": u"No autorizado."}, 401)
        body = self._body()
        if body is None:
            return self._json({"ok": False, "detail": u"Demasiado grande."}, 413)
        out, code = call_api(route, body, dev.get("caps") or [])
        self._json(out, code)

    # ── the browser stream ─────────────────────────────────────────────────────────
    def _browser_ws(self):
        q = parse_qs(urlparse(self.path).query)
        dev = self._device()
        if not dev:
            # The WebSocket handshake is a GET, and a GET from a phone app can carry an
            # Authorization header like any other request - so this needs no token in the
            # URL either.
            return self._json({"ok": False, "detail": u"No autorizado."}, 401)
        if "navegador" not in (dev.get("caps") or []):
            return self._json(
                {"ok": False,
                 "detail": u"Este teléfono no tiene permiso para el navegador."}, 403)
        key = self.headers.get("Sec-WebSocket-Key", "")
        if (self.headers.get("Upgrade", "").lower() != "websocket") or not key:
            return self._json({"ok": False, "detail": u"Se esperaba un WebSocket."}, 400)

        agent = (q.get("agent") or ["default"])[0]
        quality = (q.get("q") or [screencast.DEFAULT_QUALITY])[0]
        got = screencast.available(agent)
        if not got.get("ok"):
            return self._json(got, 409)

        self.send_response(101, "Switching Protocols")
        self.send_header("Upgrade", "websocket")
        self.send_header("Connection", "Upgrade")
        self.send_header("Sec-WebSocket-Accept", WS.accept_key(key))
        self.end_headers()
        try:
            self.wfile.flush()
        except Exception:  # noqa: BLE001
            return
        self.close_connection = True
        ws = WS(self.connection)
        _pump(ws, agent, got["port"], quality, dev, (q.get("tab") or [""])[0])


def _pump(ws, agent, port, quality, dev, tab_id):
    """Frames out, commands in, until one end stops.

    The sender is its own thread so a phone that stops reading stalls only itself: the
    reader keeps answering pings and the session still shuts down cleanly.
    """
    who = dev.get("name") or u"un teléfono"
    sess = screencast.Session(port, agent=agent, who=who, quality=quality, log=log)
    if not sess.open(tab_id):
        try:
            ws.send_text({"t": "error", "detail": sess.error})
        finally:
            ws.close()
        return
    log("%s is watching %s's browser on port %d" % (who, agent or "default", port))
    stop = threading.Event()
    last_cmd = [time.time()]

    def send_loop():
        last = None
        last_state = None
        while not stop.is_set():
            sess.wait_frame(timeout=1.0)
            if stop.is_set():
                break
            # An idle page composites nothing, so the stream says nothing. Without this
            # the viewer would be looking at a blank rectangle with no way to tell that
            # from a crash.
            sess.heartbeat()
            state = json.dumps(sess.state(), ensure_ascii=False)
            if state != last_state:
                last_state = state
                try:
                    ws.send_text({"t": "state", **sess.state()})
                except Exception:  # noqa: BLE001
                    break
            frame = sess.frame
            if frame is not None and frame is not last:
                last = frame
                try:
                    ws.send_binary(frame)
                except Exception:  # noqa: BLE001
                    break
            if time.time() - last_cmd[0] > IDLE_CLOSE:
                try:
                    ws.send_text({"t": "bye", "detail": u"Sesión cerrada por inactividad."})
                except Exception:  # noqa: BLE001
                    pass
                break
        stop.set()

    sender = threading.Thread(target=send_loop, name="olivaw-remote-send")
    sender.daemon = True
    sender.start()
    try:
        while not stop.is_set():
            try:
                raw = ws.recv()
            except WSError:
                break
            except Exception:  # noqa: BLE001  - a read timeout is a quiet phone
                continue
            if not raw:
                continue
            last_cmd[0] = time.time()
            try:
                cmd = json.loads(raw)
            except ValueError:
                continue
            _handle(ws, sess, cmd, dev)
    finally:
        stop.set()
        sess.close()
        ws.close()
        log("%s stopped watching %s's browser" % (who, agent or "default"))


def _handle(ws, sess, cmd, dev):
    """One command from the phone. Anything not named here is ignored, silently."""
    c = (cmd.get("c") or "").strip()
    try:
        if c == "arm":
            # Taking the wheel is logged, because it is the one thing a phone can do
            # that the owner might later need to account for on a site that was signed in.
            until = sess.arm(cmd.get("seconds"))
            log("%s took control of %s's browser for %ds"
                % (sess.who, sess.agent or "default", int(until - time.time())))
            ws.send_text({"t": "state", **sess.state()})
        elif c == "disarm":
            sess.disarm()
            ws.send_text({"t": "state", **sess.state()})
        elif c == "tap":
            ws.send_text({"t": "ack", **sess.tap(cmd.get("x", 0), cmd.get("y", 0),
                                                 kind=cmd.get("kind", "click"),
                                                 button=cmd.get("button", "left"),
                                                 clicks=cmd.get("clicks", 1),
                                                 dx=cmd.get("dx", 0),
                                                 dy=cmd.get("dy", 0))})
        elif c == "type":
            ws.send_text({"t": "ack", **sess.type_text(cmd.get("text", ""))})
        elif c == "key":
            ws.send_text({"t": "ack", **sess.key(cmd.get("key", ""))})
        elif c == "nav":
            ws.send_text({"t": "ack", **sess.navigate(cmd.get("url", ""))})
        elif c == "back":
            ws.send_text({"t": "ack", **sess.back()})
        elif c == "refresh":
            sess.refresh()
        elif c == "quality":
            q = cmd.get("q")
            if q in screencast.QUALITY:
                sess.start_stream(q)
                ws.send_text({"t": "state", **sess.state()})
        elif c == "tabs":
            ws.send_text({"t": "tabs", "tabs": screencast.pages(sess.port)})
    except WSError:
        raise
    except Exception as e:  # noqa: BLE001
        log("command %s failed: %s" % (c, e))


# ── the server itself ──────────────────────────────────────────────────────────────────

INSTALL_DIR = ""


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    # A phone on bad mobile data opens and abandons connections; the next bind must not
    # wait for TIME_WAIT to clear or a restart of the supervisor takes a minute.
    allow_reuse_address = True

    def handle_error(self, request, client_address):
        exc = traceback.format_exc().strip().splitlines()[-1]
        # Not a stack trace in the supervisor log: a scanner probing an open port with
        # garbage would otherwise write pages of them.
        log("dropped a connection from %s (%s)" % (client_address[0], exc))


def _port_taken(port):
    """Is something already serving this port on this machine?"""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        return s.connect_ex(("127.0.0.1", int(port))) == 0
    except OSError:
        return False
    finally:
        s.close()


def state(install_dir):
    """What the console shows on the phone page."""
    cert = os.path.join(install_dir, selfcert.CERT_NAME)
    fp = ""
    if os.path.isfile(cert):
        try:
            with open(cert, "r", encoding="ascii") as fh:
                fp = selfcert.fingerprint(selfcert._der_of_pem(fh.read()))
        except Exception:  # noqa: BLE001
            pass
    st = pairing.devices(install_dir)
    st.update({"running": bool(_server) or _port_taken(PORT), "port": PORT,
               "fingerprint": fp, "code": selfcert.short_code(fp) if fp else "",
               "addresses": selfcert.local_ips(), "protocol": PROTOCOL,
               "offer": pairing.offer_open(install_dir),
               "firewall": firewall_state(PORT)})
    return st


def start(install_dir, logger=None, port=PORT):
    """Listen, if the owner has turned this on. Returns what is now true.

    Called on every supervisor tick, so it has to be cheap and idempotent: already
    running and still wanted is the common case and does nothing.
    """
    global _server, _log, INSTALL_DIR
    if logger:
        _log = logger
    INSTALL_DIR = install_dir
    want = pairing.enabled(install_dir) or pairing.offer_open(install_dir)
    with _lock:
        if _server and not want:
            log("turned off; closing the door")
            _stop_locked()
            return {"running": False}
        if _server or not want:
            return {"running": bool(_server), "port": port}
        if _port_taken(port):
            # The supervisor and the console both try to open this door, because the
            # owner pressing "link my phone" should not wait for the supervisor's next
            # tick. Whichever gets there first serves; the other notices and says so
            # instead of logging a bind error every fifteen seconds.
            return {"running": True, "port": port, "elsewhere": True}
        try:
            got = selfcert.ensure(install_dir)
            if got.get("created"):
                log("made this machine a certificate (%s...)" % got["fingerprint"][:16])
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            # TLS 1.2 is the floor; everything that can reach this is an app we wrote.
            ctx.minimum_version = ssl.TLSVersion.TLSv1_2
            ctx.load_cert_chain(got["cert"], got["key"])
            srv = _Server(("0.0.0.0", port), Handler)
            srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
            t = threading.Thread(target=srv.serve_forever, name="olivaw-remote")
            t.daemon = True
            t.start()
            _server = srv
            log("listening on %s:%d (%s)"
                % (", ".join(selfcert.local_ips()) or "0.0.0.0", port,
                   got["fingerprint"][:16]))
            return {"running": True, "port": port, "fingerprint": got["fingerprint"]}
        except Exception as e:  # noqa: BLE001
            log("could not listen on %d: %s" % (port, e))
            return {"running": False, "detail": str(e)}


def _stop_locked():
    global _server
    srv = _server
    _server = None
    if srv:
        try:
            srv.shutdown()
        except Exception:  # noqa: BLE001
            pass
        try:
            srv.server_close()
        except Exception:  # noqa: BLE001
            pass


def stop():
    with _lock:
        _stop_locked()


def running():
    return bool(_server)


# ── the Windows firewall ───────────────────────────────────────────────────────────────

RULE_NAME = "Olivaw (telefono)"


def firewall_state(port=PORT):
    """Is this port allowed in on the private network?

    Windows silently drops the connection when it is not, and a phone showing "no
    responde" while the server log says "listening" is the single most confusing way
    this can fail - so the console asks, and can fix it.
    """
    if os.name != "nt":
        return {"known": False, "open": True}
    import subprocess
    try:
        from winspawn import quiet
        out = subprocess.run(
            ["netsh", "advfirewall", "firewall", "show", "rule",
             "name=%s" % RULE_NAME],
            **quiet(capture_output=True, text=True, timeout=20))
        return {"known": True, "open": out.returncode == 0 and "%d" % port in out.stdout}
    except Exception:  # noqa: BLE001
        return {"known": False, "open": False}


def open_firewall(port=PORT):
    """Ask Windows, with the UAC prompt the owner has to answer, for this one port.

    Scoped to the private profile: the rule says "the wifi at home", not "any network
    this laptop ever joins". A café is a public profile and stays shut.
    """
    if os.name != "nt":
        return {"ok": True, "detail": u"No hace falta en este sistema."}
    import subprocess
    cmd = ('netsh advfirewall firewall delete rule name="%s" ; '
           'netsh advfirewall firewall add rule name="%s" dir=in action=allow '
           'protocol=TCP localport=%d profile=private' % (RULE_NAME, RULE_NAME, port))
    try:
        from winspawn import quiet
        p = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Start-Process powershell -Verb RunAs -Wait -WindowStyle Hidden "
             "-ArgumentList '-NoProfile','-Command','%s'" % cmd.replace("'", "''")],
            **quiet(capture_output=True, text=True, timeout=120))
        if p.returncode != 0:
            return {"ok": False, "detail": u"Windows no aceptó el permiso."}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "detail": str(e)}
    return {"ok": True, **firewall_state(port)}
