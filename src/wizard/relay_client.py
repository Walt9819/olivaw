# -*- coding: utf-8 -*-
"""Being reachable from outside the house, without opening anything.

A home router gives this machine no address the internet can dial, and asking the owner
to forward a port is both beyond "no terminal, nothing to install" and the exact thing
the security rule is there to prevent. So nothing is opened: this *dials out* to a
rendezvous and keeps the socket, and the phone dials the same rendezvous. Two outbound
connections meeting in the middle is how every chat app crosses a router, and it is the
only shape that works behind carrier NAT as well.

The part worth being careful about is what that rendezvous can see, and the answer here
is **nothing**. It is not a proxy that terminates the connection and makes a fresh one;
it is a pipe that carries bytes it cannot read. The phone performs TLS against *this*
machine's certificate - the same self-signed one it pinned when it paired on the wifi -
and those TLS records travel through the relay as opaque payload. The relay has no key,
no certificate of ours, and no way to acquire one: swapping in its own would change the
fingerprint, and the phone pinned the fingerprint.

That means the relay can be anyone's and it is still safe to use. It can drop traffic or
count bytes; it cannot read a message, see an agent's name, or drive a browser. Which is
what makes "where do we host it" a question about cost and uptime instead of trust.

One WebSocket carries every connection, multiplexed, because a phone browsing an agent's
Chrome has the stream open and still wants to send API calls:

    [1 byte kind][4 bytes stream][payload]     kind: 1 open, 2 data, 3 close

Kinds are tiny on purpose - this frame is in front of every JPEG.
"""

import base64
import json
import os
import socket
import ssl
import struct
import threading
import time
from urllib.parse import urlparse

OPEN, DATA, CLOSE = 1, 2, 3
MAX_STREAMS = 16
CHUNK = 32 * 1024
BACKOFF_MAX = 300

_state = {"on": False, "connected": False, "detail": "", "since": 0, "url": ""}
_thread = None
_stop = threading.Event()
_log = lambda *a: None       # noqa: E731


def log(msg):
    try:
        _log("relay: %s" % msg)
    except Exception:  # noqa: BLE001
        pass


class WSClient(object):
    """The client half of RFC 6455 over TLS, with binary frames.

    browser_label._WS speaks ws:// to a process on this same machine and only handles
    text; this one crosses the internet, so it needs TLS, binary, and fragments
    reassembled - a 40KB JPEG is routinely split by whatever is in the middle.
    """

    def __init__(self, url, timeout=20.0):
        u = urlparse(url)
        secure = u.scheme == "wss"
        port = u.port or (443 if secure else 80)
        self.sock = socket.create_connection((u.hostname, port), timeout=timeout)
        if secure:
            # A public relay has an ordinary public certificate, so this is the one place
            # in Olivaw that verifies the normal way.
            ctx = ssl.create_default_context()
            self.sock = ctx.wrap_socket(self.sock, server_hostname=u.hostname)
        self.sock.settimeout(timeout)
        self.lock = threading.Lock()
        key = base64.b64encode(os.urandom(16)).decode()
        path = (u.path or "/") + (("?" + u.query) if u.query else "")
        # A User-Agent, because a rendezvous may well sit behind a CDN that refuses a
        # request without one. Cloudflare answers a bare handshake with 530 and a body
        # of "error code: 1010" - its browser-integrity check - and the only symptom at
        # this end is a relay that will not take the attachment, with nothing said about
        # why. Named rather than browser-shaped: the point is to HAVE one.
        self.sock.sendall((
            "GET %s HTTP/1.1\r\nHost: %s\r\nUpgrade: websocket\r\n"
            "Connection: Upgrade\r\nSec-WebSocket-Key: %s\r\n"
            "User-Agent: %s\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n"
            % (path, u.netloc, key, USER_AGENT)).encode())
        buf = b""
        while b"\r\n\r\n" not in buf:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise OSError("the relay closed the handshake")
            buf += chunk
            if len(buf) > 65536:
                raise OSError("handshake too long")
        head = buf.split(b"\r\n", 1)[0]
        if b" 101 " not in head:
            raise OSError("the relay refused: %s" % head.decode("latin-1")[:120])

    def send(self, payload, op=0x2):
        mask = os.urandom(4)
        n = len(payload)
        if n < 126:
            head = struct.pack("!BB", 0x80 | op, 0x80 | n)
        elif n < 65536:
            head = struct.pack("!BBH", 0x80 | op, 0x80 | 126, n)
        else:
            head = struct.pack("!BBQ", 0x80 | op, 0x80 | 127, n)
        body = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        with self.lock:
            self.sock.sendall(head + mask + body)

    def _exact(self, n):
        out = b""
        while len(out) < n:
            chunk = self.sock.recv(n - len(out))
            if not chunk:
                raise OSError("closed")
            out += chunk
        return out

    def recv(self):
        """One complete message, fragments joined. None for a control frame."""
        parts = []
        quiet = 0
        while True:
            # A rendezvous with nobody on the other end of it is SILENT, and silence is
            # the normal state: the phone is in a pocket. The socket timeout is a
            # heartbeat interval, not a death. Treating it as one made this machine
            # attach, drop twenty seconds later, and back off - forever, without ever
            # being reachable.
            #
            # Only at a frame boundary. Once a frame has started, a timeout means the
            # far side stopped mid-message, which IS broken.
            try:
                b1, b2 = self._exact(2)
            except socket.timeout:
                quiet += 1
                if quiet > QUIET_PINGS:
                    raise OSError('the relay stopped answering')
                # Ours to send: it keeps the path open through whatever NAT and proxies
                # are between here and there, and proves the far side is still there.
                self.send(b'', op=0x9)
                continue
            quiet = 0
            fin, op = b1 & 0x80, b1 & 0x0F
            n = b2 & 0x7F
            if n == 126:
                n = struct.unpack("!H", self._exact(2))[0]
            elif n == 127:
                n = struct.unpack("!Q", self._exact(8))[0]
            if n > 8 << 20:
                raise OSError("frame too large")
            data = self._exact(n) if n else b""
            if op == 0x8:
                raise OSError("the relay closed the connection")
            if op == 0x9:
                self.send(data, op=0xA)
                continue
            if op == 0xA:
                continue
            parts.append(data)
            if fin:
                return b"".join(parts)

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass


class _Link(object):
    """One live attachment to the relay: the WebSocket plus the local sockets it feeds."""

    def __init__(self, ws, local_port):
        self.ws = ws
        self.local_port = local_port
        self.streams = {}
        self.lock = threading.Lock()

    def _frame(self, kind, sid, payload=b""):
        return struct.pack("!BI", kind, sid) + payload

    def open_stream(self, sid):
        with self.lock:
            if len(self.streams) >= MAX_STREAMS:
                self.ws.send(self._frame(CLOSE, sid))
                return
        try:
            s = socket.create_connection(("127.0.0.1", self.local_port), timeout=10)
        except OSError as e:
            log("could not reach the local door: %s" % e)
            self.ws.send(self._frame(CLOSE, sid))
            return
        s.settimeout(None)
        with self.lock:
            self.streams[sid] = s
        t = threading.Thread(target=self._drain, args=(sid, s),
                             name="olivaw-relay-%d" % sid)
        t.daemon = True
        t.start()

    def _drain(self, sid, s):
        """Everything this machine answers goes back up the pipe, as it arrives."""
        try:
            while True:
                data = s.recv(CHUNK)
                if not data:
                    break
                self.ws.send(self._frame(DATA, sid, data))
        except Exception:  # noqa: BLE001
            pass
        finally:
            self.close_stream(sid, tell=True)

    def feed(self, sid, data):
        with self.lock:
            s = self.streams.get(sid)
        if not s:
            return
        try:
            s.sendall(data)
        except Exception:  # noqa: BLE001
            self.close_stream(sid, tell=True)

    def close_stream(self, sid, tell=False):
        with self.lock:
            s = self.streams.pop(sid, None)
        if s:
            try:
                s.close()
            except Exception:  # noqa: BLE001
                pass
        if tell:
            try:
                self.ws.send(self._frame(CLOSE, sid))
            except Exception:  # noqa: BLE001
                pass

    def shutdown(self):
        for sid in list(self.streams):
            self.close_stream(sid)
        self.ws.close()


def _serve_once(url, room, key, local_port):
    ws = WSClient("%s?role=host&room=%s&key=%s" % (url.rstrip("/") + "/attach", room, key))
    link = _Link(ws, local_port)
    _state.update({"connected": True, "detail": "", "since": time.time()})
    log("attached to the rendezvous as %s" % room[:8])
    try:
        while not _stop.is_set():
            msg = ws.recv()
            if msg is None or len(msg) < 5:
                continue
            kind, sid = struct.unpack("!BI", msg[:5])
            if kind == OPEN:
                link.open_stream(sid)
            elif kind == DATA:
                link.feed(sid, msg[5:])
            elif kind == CLOSE:
                link.close_stream(sid)
    finally:
        link.shutdown()
        _state.update({"connected": False})


def _loop(url, room, key, local_port):
    """Reconnect forever, backing off, because a laptop sleeps and wifi drops.

    Capped at five minutes: a relay that is down for an afternoon should not be retried
    every second, and a laptop that wakes up should not wait an hour to be reachable.
    """
    wait = 2
    while not _stop.is_set():
        try:
            _serve_once(url, room, key, local_port)
            wait = 2
        except Exception as e:  # noqa: BLE001
            _state.update({"connected": False, "detail": str(e)[:160]})
            log("not attached (%s); retrying in %ds" % (str(e)[:80], wait))
        for _ in range(wait):
            if _stop.is_set():
                return
            time.sleep(1)
        wait = min(BACKOFF_MAX, wait * 2)


# ── configuration ──────────────────────────────────────────────────────────────────────

CONF = "relay.json"


# How many quiet heartbeats before an attachment is considered dead. Four of them at
# the socket timeout is over a minute of silence with no pong, which is long enough to
# ride out a slow network and short enough to reconnect before anybody notices.
QUIET_PINGS = 4

# What this machine calls itself to the rendezvous. Named rather than browser-shaped:
# the point is to HAVE one, because a CDN in front of the relay may refuse a request
# without it, not to pretend to be a browser.
USER_AGENT = "Olivaw/1.0 (+https://github.com/Walt9819/olivaw)"

# The rendezvous an install uses when its owner has not named another one.
#
# It is the Worker in relay/worker/, running on the free tier. Pointing every install
# at it is only acceptable because of what it is: a byte pipe. The phone does TLS
# against the COMPUTER's certificate and those records cross this address as noise, so
# whoever runs it - including us - sees that two machines met and how many bytes went
# through, and cannot see a message, a name, or a screen.
#
# An owner who would rather not meet on somebody else's address can put their own here
# from the console; relay/OPTIONS.md lists what else works. Running out of free quota
# fails the attachment rather than charging anybody, which is the right failure.
DEFAULT_URL = "wss://olivaw-relay.olivaw.workers.dev"


def config(install_dir):
    try:
        with open(os.path.join(install_dir, CONF), "r", encoding="utf-8") as fh:
            got = json.load(fh)
        if not isinstance(got, dict):
            got = {}
    except Exception:  # noqa: BLE001
        got = {}
    # Filled in rather than written down, so an install that never chose an address
    # follows the default if the default ever moves. An owner who named one keeps it.
    if not got.get("url"):
        got["url"] = DEFAULT_URL
    return got


def save_config(install_dir, url="", room="", key="", on=None):
    got = config(install_dir)
    if url:
        got["url"] = url.strip().rstrip("/")
    if room:
        got["room"] = room
    if key:
        got["key"] = key
    if on is not None:
        got["on"] = bool(on)
    got.setdefault("room", base64.urlsafe_b64encode(os.urandom(15)).decode().rstrip("="))
    got.setdefault("key", base64.urlsafe_b64encode(os.urandom(24)).decode().rstrip("="))
    p = os.path.join(install_dir, CONF)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(got, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, p)
    return got


def state(install_dir):
    got = config(install_dir)
    return {"on": bool(got.get("on")), "url": got.get("url", ""),
            "room": got.get("room", ""), "connected": bool(_state.get("connected")),
            "detail": _state.get("detail", ""),
            "since": int(_state.get("since", 0))}


def start(install_dir, local_port, logger=None):
    """Keep this machine attached, if the owner asked for it. Idempotent per tick."""
    global _thread, _log
    if logger:
        _log = logger
    got = config(install_dir)
    want = bool(got.get("on")) and bool(got.get("url"))
    alive = bool(_thread and _thread.is_alive())
    if alive and not want:
        _stop.set()
        _thread = None
        log("detaching from the rendezvous")
        return {"on": False}
    # An attachment to the WRONG address is worse than none: it reports itself as
    # connected while the phone cannot be met anywhere. So a live thread only counts if
    # it is attached to the address and room the owner currently has configured -
    # otherwise it is stopped and replaced. Without this, changing the rendezvous in
    # the console did nothing until the next restart, and said it was connected
    # throughout.
    if alive and (_state.get("url") != got["url"]
                  or _state.get("room") != got["room"]):
        _stop.set()
        _thread = None
        alive = False
        log("the rendezvous changed; attaching to the new one")
    if alive or not want:
        return {"on": want, "connected": bool(_state.get("connected"))}
    _stop.clear()
    _state.update({"on": True, "url": got["url"], "room": got["room"]})
    _thread = threading.Thread(target=_loop, name="olivaw-relay",
                               args=(got["url"], got["room"], got["key"], local_port))
    _thread.daemon = True
    _thread.start()
    return {"on": True}


def stop():
    global _thread
    _stop.set()
    _thread = None
    _state.update({"on": False, "connected": False})
