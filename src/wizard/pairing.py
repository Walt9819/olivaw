# -*- coding: utf-8 -*-
"""Which phones may talk to this computer, and what each one is allowed to ask for.

The owner's Telegram channel is almost unrestricted, because it is the owner. A phone is
a different thing: it is a contact point that can be lost in a taxi. So this file is
written around one rule - *a paired phone must not be able to compromise the machine,
the agents, or the way they work* - and it keeps that rule in three ways.

**Default deny.** A request names an API route; a route grants nothing unless it appears
in ``ROUTES`` below with the permission it needs. A route added to the console next month
is unreachable from a phone until somebody puts it in that table on purpose. The opposite
arrangement - a deny-list - fails open every time somebody adds a feature, which is the
only failure mode that matters here.

**Powers, not an on/off switch.** Pairing hands out the useful things (see them, talk to
them, their routines, start and stop). The two that could actually hurt stay off until
the owner turns them on from the desktop, per phone:

* ``navegador`` - driving the agent's Chrome. That window is signed into the owner's
  Google account; whoever drives it is the owner as far as every site is concerned.
* ``config`` - the brain, the vault, the channels, deleting an agent. The settings that
  decide what Olivaw *is*.

**The token is never stored.** Only its SHA-256, so a readable devices.json is not a key
to the house, and comparisons are constant-time.
"""

import hashlib
import hmac
import json
import os
import re
import secrets
import time

STORE = "devices.json"
MAX_DEVICES = 24
NAME_MAX = 32
PIN_TRIES = 5                 # typed-code attempts before the offer is burned
OFFER_SECONDS = 180           # a pairing offer on screen lives three minutes
TOKEN_BYTES = 32

# What a freshly paired phone can do. Deliberately the useful half.
DEFAULT_CAPS = ("ver", "hablar", "tareas", "operar")
ALL_CAPS = ("ver", "hablar", "tareas", "operar", "navegador", "config")

CAP_LABEL = {
    "ver": u"Ver los agentes y cómo están",
    "hablar": u"Hablar con ellos",
    "tareas": u"Ver y pausar sus rutinas",
    "operar": u"Encender y apagar agentes",
    "navegador": u"Ver y manejar su Chrome",
    "config": u"Cambiar su configuración",
}

# ── the route table ────────────────────────────────────────────────────────────────────
# route -> capability it needs. A route that is not here cannot be reached from a phone,
# whatever it is called and whoever added it.
ROUTES = {
    # looking
    # How to reach this machine from outside the house. Answered by remote.py, not
    # by the console - and readable with nothing but "ver", because a device that
    # already holds a token learns nothing from it that it could not learn by
    # connecting.
    "net/home": "ver",
    "state": "ver",
    "agents/list": "ver",
    "hermes/status": "ver",
    "connections/status": "ver",
    "update/status": "ver",
    "session/status": "ver",
    "browser/status": "ver",
    "intercom/status": "ver",
    "talk/status": "ver",
    "proposals/list": "ver",
    "cron/list": "ver",
    # talking
    "talk/sessions": "hablar",
    "talk/history": "hablar",
    "talk/new": "hablar",
    "talk/send": "hablar",
    "talk/file": "hablar",
    "talk/attach": "hablar",
    # A voice note is a message like any other: the same permission, because it says the
    # same kinds of things. The microphone is the phone's to guard, not ours.
    "talk/voice": "hablar",
    "talk/voice_status": "ver",
    "intercom/thread": "hablar",
    # routines, and answering what an agent asked permission for
    "cron/flags": "tareas",
    "proposals/decide": "tareas",
    # running them
    "agent/action": "operar",
    # the two that are off until the owner says otherwise
    "agent/rename": "config",
    "avatar/set": "config",
    "policy/save": "config",
    "teams/assign": "config",
    "channel/": "config",
    "numbers/update": "config",
    "update/apply": "config",
}

# Routes a phone may never reach even with every permission on, because they exist to
# change what the machine itself is. The desktop console is where those live.
NEVER = ("phone/", "provider/", "telegram/", "obsidian/", "rescue/", "workspace/",
         "selfcare/",
         "images/", "session/login", "session/verify", "numbers/add", "numbers/remove",
         "numbers/main", "check", "teams/apply", "agent/delete")

_NAME_RE = re.compile(r"^[\w .\-()À-ɏ]{1,%d}$" % NAME_MAX, re.UNICODE)


def allowed(route, caps):
    """May a device holding `caps` call `route`? Default deny, in that order."""
    route = (route or "").strip("/")
    for bad in NEVER:
        if route == bad.strip("/") or (bad.endswith("/") and route.startswith(bad)):
            return False
    need = ROUTES.get(route)
    if need is None:
        for key, cap in ROUTES.items():
            if key.endswith("/") and route.startswith(key):
                need = cap
                break
    if need is None:
        return False
    return need in caps


# ── the store ──────────────────────────────────────────────────────────────────────────

def _path(install_dir):
    return os.path.join(install_dir, STORE)


def _load(install_dir):
    try:
        with open(_path(install_dir), "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:  # noqa: BLE001
        data = {}
    if not isinstance(data, dict):
        data = {}
    data.setdefault("devices", [])
    data.setdefault("enabled", False)
    return data


def _save(install_dir, data):
    p = _path(install_dir)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, p)
    try:
        if os.name != "nt":
            os.chmod(p, 0o600)
    except Exception:  # noqa: BLE001
        pass


def _hash(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def clean_name(name, fallback=u"Teléfono"):
    name = (name or "").strip()[:NAME_MAX]
    if not name or not _NAME_RE.match(name):
        return fallback
    return name


# ── pairing offers ─────────────────────────────────────────────────────────────────────
# On disk, briefly, because the two halves of pairing are in different processes: the
# console shows the code, and the supervisor is what the phone actually connects to. An
# offer in memory would be visible to whichever of them created it and to nothing else.
#
# So it is a file that is deliberately short-lived - three minutes, deleted the moment it
# is claimed, and ignored once it has expired even if deleting it failed. It sits beside
# devices.json, which is already the most sensitive file here.

OFFER_STORE = "pair-offer.json"


def _offer_path(install_dir):
    return os.path.join(install_dir, OFFER_STORE)


def _read_offer(install_dir):
    try:
        with open(_offer_path(install_dir), "r", encoding="utf-8") as fh:
            got = json.load(fh)
    except Exception:  # noqa: BLE001
        return {}
    if not isinstance(got, dict) or got.get("expires", 0) <= time.time():
        return {}
    return got


def _write_offer(install_dir, got):
    p = _offer_path(install_dir)
    if not got:
        try:
            os.remove(p)
        except OSError:
            pass
        return
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(got, fh)
    os.replace(tmp, p)
    try:
        if os.name != "nt":
            os.chmod(p, 0o600)
    except Exception:  # noqa: BLE001
        pass


def offer(install_dir, caps=None):
    """Open a pairing window. Returns what the QR should carry.

    Two ways in, one offer: the QR carries 160 bits nobody can guess, and the six digits
    under it exist for the day the camera will not focus. The digits are guessable, so
    they get five attempts and then the whole offer dies - including the QR.
    """
    data = _load(install_dir)
    if len(data["devices"]) >= MAX_DEVICES:
        return {"ok": False, "detail": u"Ya hay demasiados dispositivos enlazados."}
    secret = secrets.token_urlsafe(20)
    pin = "".join(secrets.choice("0123456789") for _ in range(6))
    got = {"secret": secret, "pin": pin, "tries": 0,
           "expires": time.time() + OFFER_SECONDS,
           "caps": list(caps or DEFAULT_CAPS)}
    _write_offer(install_dir, got)
    return {"ok": True, "secret": secret, "pin": pin,
            "expires_in": OFFER_SECONDS, "caps": got["caps"]}


def offer_open(install_dir=None):
    return bool(_read_offer(install_dir or "."))


def cancel_offer(install_dir=None):
    _write_offer(install_dir or ".", None)


def claim(install_dir, secret="", pin="", name="", platform=""):
    """A phone presents the QR secret or the typed code. Mint it a token, once."""
    live = _read_offer(install_dir)
    if not live:
        _write_offer(install_dir, None)
        return {"ok": False, "detail": u"No hay ningún enlace abierto en el ordenador."}
    ok = False
    if secret and hmac.compare_digest(str(secret), live["secret"]):
        ok = True
    elif pin:
        live["tries"] = int(live.get("tries", 0)) + 1
        _write_offer(install_dir, live)
        if hmac.compare_digest(str(pin), live["pin"]):
            ok = True
        elif live["tries"] >= PIN_TRIES:
            _write_offer(install_dir, None)
            return {"ok": False, "detail": u"Demasiados intentos. Vuelve a abrir el enlace."}
    if not ok:
        return {"ok": False, "detail": u"Ese código no es el que está en pantalla."}

    caps = list(live.get("caps") or DEFAULT_CAPS)
    _write_offer(install_dir, None)             # single use, whatever happens next
    data = _load(install_dir)
    if len(data["devices"]) >= MAX_DEVICES:
        return {"ok": False, "detail": u"Ya hay demasiados dispositivos enlazados."}
    token = secrets.token_urlsafe(TOKEN_BYTES)
    dev = {"id": secrets.token_hex(8),
           "name": clean_name(name),
           "platform": (platform or "")[:24],
           "token": _hash(token),
           "caps": [c for c in caps if c in ALL_CAPS],
           "created": int(time.time()),
           "last_seen": int(time.time())}
    data["devices"].append(dev)
    data["enabled"] = True
    _save(install_dir, data)
    return {"ok": True, "token": token, "device_id": dev["id"],
            "name": dev["name"], "caps": dev["caps"]}


# ── using a token ──────────────────────────────────────────────────────────────────────

def device_for(install_dir, token):
    """The device this bearer token belongs to, or None. Constant-time throughout.

    Every stored hash is compared even after a match, so the time this takes does not
    say how far down the list the answer was.
    """
    if not token:
        return None
    want = _hash(token)
    data = _load(install_dir)
    if not data.get("enabled"):
        return None
    found = None
    for dev in data["devices"]:
        if hmac.compare_digest(str(dev.get("token", "")), want):
            found = dev
    return found


def touch(install_dir, device_id):
    data = _load(install_dir)
    for dev in data["devices"]:
        if dev.get("id") == device_id:
            dev["last_seen"] = int(time.time())
            _save(install_dir, data)
            return


def devices(install_dir):
    """The list the console shows. Never includes the token hashes."""
    data = _load(install_dir)
    out = []
    for dev in data["devices"]:
        out.append({"id": dev.get("id"), "name": dev.get("name"),
                    "platform": dev.get("platform", ""),
                    "caps": dev.get("caps", []),
                    "created": dev.get("created", 0),
                    "last_seen": dev.get("last_seen", 0)})
    out.sort(key=lambda d: d.get("last_seen", 0), reverse=True)
    return {"enabled": bool(data.get("enabled")), "devices": out,
            "caps": list(ALL_CAPS), "labels": dict(CAP_LABEL)}


def revoke(install_dir, device_id):
    data = _load(install_dir)
    before = len(data["devices"])
    data["devices"] = [d for d in data["devices"] if d.get("id") != device_id]
    if len(data["devices"]) == before:
        return {"ok": False, "detail": u"Ese dispositivo ya no estaba."}
    _save(install_dir, data)
    return {"ok": True, "left": len(data["devices"])}


def set_caps(install_dir, device_id, caps):
    """Change what one phone may do. 'ver' is not optional - a device that cannot look
    at anything is a device that should be revoked instead."""
    want = {c for c in (caps or []) if c in ALL_CAPS} | {"ver"}
    data = _load(install_dir)
    for dev in data["devices"]:
        if dev.get("id") == device_id:
            dev["caps"] = [c for c in ALL_CAPS if c in want]
            _save(install_dir, data)
            return {"ok": True, "caps": dev["caps"]}
    return {"ok": False, "detail": u"Ese dispositivo ya no estaba."}


def rename(install_dir, device_id, name):
    data = _load(install_dir)
    for dev in data["devices"]:
        if dev.get("id") == device_id:
            dev["name"] = clean_name(name, dev.get("name") or u"Teléfono")
            _save(install_dir, data)
            return {"ok": True, "name": dev["name"]}
    return {"ok": False, "detail": u"Ese dispositivo ya no estaba."}


def set_enabled(install_dir, on):
    """The master switch. Off means the remote door is shut for every device at once,
    without forgetting any of them - the owner gets them all back by turning it on."""
    data = _load(install_dir)
    data["enabled"] = bool(on)
    _save(install_dir, data)
    return {"ok": True, "enabled": data["enabled"]}


def enabled(install_dir):
    return bool(_load(install_dir).get("enabled"))
