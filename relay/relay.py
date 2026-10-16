# -*- coding: utf-8 -*-
"""Olivaw's rendezvous: ~200 lines whose entire job is to not understand anything.

Two devices that both sit behind a router cannot dial each other, so they both dial here
and this introduces them. That is all it does. It does not terminate TLS, hold a key,
parse a request, or know an agent exists:

    phone  --- TLS to the computer's pinned certificate ------------------>  computer
               (carried through here as bytes nobody here can read)

Because the encryption is end to end between the phone and the house, running this is not
a position of trust. Whoever operates it can see that a room is busy and how many bytes
went through; they cannot see a message, a name, or a screen. That is the property that
makes it safe to run on a free tier, on a spare box, or on somebody else's hardware.

It needs no TLS of its own for the same reason - what flows through is already encrypted
end to end, so ``ws://`` costs nothing in confidentiality. Put it behind a reverse proxy
with a certificate if you want the metadata private too.

Run it anywhere with a public address:

    python3 relay.py --port 8080

Nothing is installed, nothing is configured, and there is no database. It keeps rooms in
memory; restarting it drops live connections, and both ends reconnect on their own.
"""

import argparse
import base64
import hashlib
import hmac
import json
import os
import socket
import struct
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

WS_GUID = b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
MAX_FRAME = 8 << 20
MAX_ROOMS = 500
ROOM_IDLE = 30 * 60
ROOM_RE_MAX = 64
# How many phones may be in one room at once; see the note where it is enforced.
MAX_GUESTS = 8

_rooms = {}
_lock = threading.Lock()


def _now():
    return time.time()


class Room(object):
    def __init__(self, key_hash):
        self.key_hash = key_hash
        self.host = None
        self.guests = []
        self.touched = _now()
        self.bytes = 0


def _read_frame(sock):
    """One message off the wire, unmasking if the sender masked it.

    Both ends of this relay are clients, so both mask; the relay never masks what it
    sends. Written out longhand rather than reusing a library because there is no library
    here on purpose - this file must run on a bare machine with nothing installed.
    """
    def exact(n):
        out = b""
        while len(out) < n:
            c = sock.recv(n - len(out))
            if not c:
                raise OSError("closed")
            out += c
        return out

    parts = []
    while True:
        b1, b2 = exact(2)
        fin, op = b1 & 0x80, b1 & 0x0F
        masked = b2 & 0x80
        n = b2 & 0x7F
        if n == 126:
            n = struct.unpack("!H", exact(2))[0]
        elif n == 127:
            n = struct.unpack("!Q", exact(8))[0]
        if n > MAX_FRAME:
            raise OSError("frame too large")
        mask = exact(4) if masked else b""
        body = exact(n) if n else b""
        if masked:
            body = bytes(b ^ mask[i % 4] for i, b in enumerate(body))
        if op == 0x8:
            raise OSError("peer closed")
        if op == 0x9:
            _write_frame(sock, body, op=0xA)
            continue
        if op == 0xA:
            continue
        parts.append(body)
        if fin:
            return b"".join(parts)


_write_lock = {}


def _write_frame(sock, payload, op=0x2):
    n = len(payload)
    if n < 126:
        head = struct.pack("!BB", 0x80 | op, n)
    elif n < 65536:
        head = struct.pack("!BBH", 0x80 | op, 126, n)
    else:
        head = struct.pack("!BBQ", 0x80 | op, 127, n)
    lock = _write_lock.setdefault(id(sock), threading.Lock())
    with lock:
        sock.sendall(head + payload)


# ── the one route ──────────────────────────────────────────────────────────────────────

class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "olivaw-relay"
    sys_version = ""

    def log_message(self, *a):
        pass

    def _json(self, obj, code=200):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u = urlparse(self.path)
        if u.path == "/health":
            with _lock:
                return self._json({"ok": True, "rooms": len(_rooms)})
        if u.path != "/attach":
            return self._json({"ok": False}, 404)
        q = parse_qs(u.query)

        def pick(name):
            """Header first, query string second.

            They moved out of the URL because a URL is the part of a request that
            everything logs, and one of these is the key that proves a machine owns
            its room. The old place is still read, so a house that has not updated
            yet still gets in.
            """
            return (self.headers.get("X-Olivaw-" + name.capitalize())
                    or (q.get(name) or [""])[0]).strip()

        role = pick("role")
        room_id = pick("room")
        key = pick("key")
        # Refused, not shortened: truncating the id that decides WHICH computer you
        # reach quietly merges two rooms that happen to share a prefix.
        if len(room_id) > ROOM_RE_MAX:
            return self._json({"ok": False, "detail": "room id too long"}, 400)
        if role not in ("host", "guest") or not room_id:
            return self._json({"ok": False, "detail": "bad request"}, 400)
        ws_key = self.headers.get("Sec-WebSocket-Key", "")
        if not ws_key or self.headers.get("Upgrade", "").lower() != "websocket":
            return self._json({"ok": False, "detail": "websocket only"}, 400)

        room = self._claim(role, room_id, key)
        if room is None:
            return self._json({"ok": False, "detail": "room refused"}, 403)

        self.send_response(101, "Switching Protocols")
        self.send_header("Upgrade", "websocket")
        self.send_header("Connection", "Upgrade")
        self.send_header("Sec-WebSocket-Accept", base64.b64encode(
            hashlib.sha1(ws_key.encode("ascii") + WS_GUID).digest()).decode())
        self.end_headers()
        self.wfile.flush()
        self.close_connection = True
        sock = self.connection
        sock.settimeout(None)
        self._pump(room, role, room_id, sock)

    def _claim(self, role, room_id, key):
        """A room belongs to whoever registered it first, and only to them.

        The key is never stored, only its hash, and it is compared in constant time -
        the same care the pairing store takes, for the same reason: this file is the one
        that decides whether somebody else's phone can meet your computer.
        """
        want = hashlib.sha256(key.encode("utf-8")).hexdigest() if key else ""
        with _lock:
            self._sweep()
            room = _rooms.get(room_id)
            if role == "host":
                if not key:
                    return None
                if room is None:
                    if len(_rooms) >= MAX_ROOMS:
                        return None
                    room = Room(want)
                    _rooms[room_id] = room
                elif not hmac.compare_digest(room.key_hash, want):
                    return None
                if room.host is not None:
                    try:
                        room.host.close()          # a reconnecting house wins
                    except Exception:  # noqa: BLE001
                        pass
                room.touched = _now()
                return room
            # a guest needs no key: the room id IS the secret, and even a guest who
            # guesses one meets a TLS endpoint that will not talk to it.
            if room is None or room.host is None:
                return None
            # The same cap the Worker has, for the same reason and kept deliberately
            # identical: the id is all that stands between a stranger and an
            # attachment, and without a cap they could sit here in numbers and have
            # every frame the house sends copied to each of them. The NEWCOMER is
            # refused rather than the oldest evicted - evicting would let whoever
            # guessed the id push the owner's own phone out of its room.
            if len(room.guests) >= MAX_GUESTS:
                return None
            room.touched = _now()
            return room

    def _sweep(self):
        for rid in [r for r, m in _rooms.items()
                    if m.host is None and _now() - m.touched > ROOM_IDLE]:
            _rooms.pop(rid, None)

    def _pump(self, room, role, room_id, sock):
        if role == "host":
            room.host = sock
        else:
            with _lock:
                room.guests.append(sock)
        try:
            while True:
                data = _read_frame(sock)
                room.touched = _now()
                room.bytes += len(data)
                if role == "host":
                    targets = list(room.guests)
                else:
                    targets = [room.host] if room.host else []
                for t in targets:
                    try:
                        _write_frame(t, data)
                    except Exception:  # noqa: BLE001
                        pass
        except Exception:  # noqa: BLE001
            pass
        finally:
            with _lock:
                if role == "host" and room.host is sock:
                    room.host = None
                    for g in list(room.guests):
                        try:
                            g.close()
                        except Exception:  # noqa: BLE001
                            pass
                    room.guests = []
                elif sock in room.guests:
                    room.guests.remove(sock)
                _write_lock.pop(id(sock), None)
            try:
                sock.close()
            except Exception:  # noqa: BLE001
                pass


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def handle_error(self, request, client_address):
        pass


def main():
    ap = argparse.ArgumentParser(description="Olivaw rendezvous")
    ap.add_argument("--port", type=int, default=int(os.environ.get("PORT", 8080)))
    ap.add_argument("--host", default="0.0.0.0")
    args = ap.parse_args()
    srv = Server((args.host, args.port), Handler)
    print("olivaw relay on %s:%d" % (args.host, args.port), flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
