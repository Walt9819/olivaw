r"""The owner's passwords, kept where the agent can use them and never read them.

An agent drives its own Chrome, and Chrome logs out. Google expires a session, a bank
signs you out nightly, a platform rotates a token - and then the agent is in front of a
login form with no way past it. Until now the only ways through were the owner typing it
herself, or the worst habit this product could teach: pasting a password into the chat,
which sends it to the model provider, into their logs, and into a transcript that lives
on disk for ever.

So: the agent ORCHESTRATES the login and never learns the secret. It can see the page,
decide that this is the password box, and say "fill the password here". The bytes travel
from this store into Chrome's input field over the DevTools socket, inside this machine.
They never enter the agent's context, never appear in argv (where another process could
read them from the process list), never reach a log, and never cross the network.

What is kept where:

  * **Nothing secret in the metadata file.** `credentials.json` holds the platform, the
    label, the login page, which fields exist, when each was last used. That file can be
    read, copied into a bug report, or shown on screen without losing anything.
  * **The values go to the operating system's own keystore**, which is the one place on a
    laptop that is already built for this: DPAPI on Windows, the Keychain on macOS,
    libsecret on Linux. No key of ours on disk, no cipher of ours to get wrong.
  * **If there is no keystore, nothing is stored.** A fallback that wrote the password to
    a file "for now" would be the whole problem, shipped.

What this protects against, said plainly, because a security promise that is vaguer than
the threat is not a promise:

  * the model provider, and anyone with the transcript: the secret is never in it;
  * another user of this computer, and a stolen copy of the install folder: the values are
    sealed to this Windows account / this login keychain;
  * an agent that goes wrong, or is talked into going wrong by a web page it is reading:
    it can ASK for a fill and the fill is recorded, but it cannot read the value back,
    cannot use another agent's keys, and cannot use one more often than a person would.

What it does not protect against: an agent that has a shell on this machine runs as the
same user as this process, and the same user is who the keystore answers to. Protecting
against that needs a secret that is not on the machine at all - see `lock()` below, which
takes a passphrase the owner types and keeps only in the supervisor's memory.
"""

import base64
import hashlib
import hmac
import io
import json
import math
import os
import platform
import struct
import subprocess
import threading
import time

try:
    from . import vault_lock
except ImportError:  # running from src/ rather than as a package
    from wizard import vault_lock

STORE = "credentials.json"
SEALED = "credentials.sealed"
AUDIT = "credentials-audit.log"

# How often one agent may spend one credential. A login is a thing a person does once and
# then not again for weeks; a loop that tries forty times in a minute is not logging in,
# it is locking the account out - and that is also what a prompt-injected agent would do.
USES_PER_HOUR = 12

# The fields an entry can hold. `username` is a field like any other so that an owner who
# considers the account name itself private can have it typed rather than shown.
FIELDS = ("username", "password", "totp")

_lock = threading.RLock()

# The service name under which macOS and Linux keep these. Per field, per agent, per
# platform, so one entry is one thing to grant, revoke or audit.
SERVICE = "olivaw-credentials"


# ── where the values actually live ────────────────────────────────────────────────────

def keeper(install_dir=None):
    """Which keystore this machine has, or None.

    Decided by asking, not by assuming: a Linux box without libsecret and a Mac with the
    Keychain disabled both exist.

    The operating system's own store comes first wherever there is one - it needs no
    passphrase, survives a reboot, and is the thing already protecting everything else
    the owner has saved. A passphrase is the answer for the machines that have none,
    and a Linux server with no desktop is exactly that machine.
    """
    system = platform.system()
    if system == "Windows":
        try:
            import ctypes  # noqa: F401
            ctypes.windll.crypt32  # noqa: B018 - raises if it is not there
            return "dpapi"
        except Exception:  # noqa: BLE001
            pass
    elif system == "Darwin":
        if _which("security"):
            return "keychain"
    elif _which("secret-tool"):
        return "libsecret"
    if install_dir is not None and vault_lock.configured(install_dir):
        return "passphrase"
    return None


def can_set_passphrase(install_dir):
    """True when a passphrase is the way forward here: no OS store, none set yet."""
    return keeper(install_dir) is None


def _which(name):
    from shutil import which
    return which(name)


def keeper_label(install_dir=None):
    return {"dpapi": "Windows (DPAPI)",
            "keychain": "Llavero de macOS",
            "libsecret": "libsecret",
            "passphrase": u"contraseña maestra de este equipo"}.get(
                keeper(install_dir), "")


class NoKeeper(Exception):
    """This machine has nowhere safe to put a password, so we will not put it anywhere."""


# ── Windows: DPAPI, through ctypes, because it is in the OS already ──────────────────

def _dpapi(data, entropy, protect):
    import ctypes
    import ctypes.wintypes as wt

    class BLOB(ctypes.Structure):
        _fields_ = [("cbData", wt.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    def blob_of(raw):
        buf = ctypes.create_string_buffer(raw, len(raw))
        return BLOB(len(raw), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), buf

    src, _keep1 = blob_of(data)
    ent, _keep2 = blob_of(entropy)
    out = BLOB()
    fn = (ctypes.windll.crypt32.CryptProtectData if protect
          else ctypes.windll.crypt32.CryptUnprotectData)
    # CRYPTPROTECT_UI_FORBIDDEN: this runs in a service with no desktop. A prompt here
    # would hang the supervisor rather than ask anybody anything.
    ok = fn(ctypes.byref(src), None, ctypes.byref(ent), None, None, 0x1,
            ctypes.byref(out))
    if not ok:
        raise OSError("DPAPI refused (%d)" % ctypes.GetLastError())
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(out.pbData)


def _entropy(key, extra=b""):
    """Binds a sealed value to its own slot.

    Without this, a sealed blob is just "something this Windows account can open", and
    moving one entry's blob into another's row in the file would hand the agent a
    different password than the one it asked for. With it, the slot is part of the
    ciphertext.
    """
    return b"olivaw-vault-v1|" + key.encode("utf-8") + b"|" + extra


# ── the files ─────────────────────────────────────────────────────────────────────────

def _path(install_dir, name):
    return os.path.join(install_dir or ".", name)


def _read_json(path, empty):
    try:
        with io.open(path, encoding="utf-8") as fh:
            got = json.load(fh)
        return got if isinstance(got, dict) else dict(empty)
    except Exception:  # noqa: BLE001
        return dict(empty)


def _write_json(path, data):
    # Named for this write, and the same care as the device store: the console and the
    # door are two processes, and a shared '.tmp' is something for them to fight over.
    tmp = "%s.%d.%d.tmp" % (path, os.getpid(), threading.get_ident())
    try:
        with io.open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
        _only_owner(tmp)
        for attempt in range(6):
            try:
                os.replace(tmp, path)
                break
            except PermissionError:
                if attempt == 5:
                    raise
                time.sleep(0.02 * (attempt + 1))
    except Exception:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def _only_owner(path):
    """As private as the filesystem will make it.

    On POSIX that is 0600. On Windows the install lives under the owner's own
    %LOCALAPPDATA%, which is already hers; the sealed values are sealed to her account
    besides, so a copied file is a copied brick.
    """
    try:
        if os.name != "nt":
            os.chmod(path, 0o600)
    except OSError:
        pass


def _key(agent, platform_id, field):
    return "%s/%s/%s" % (_slug(agent), _slug(platform_id), field)


def _slug(text):
    out = "".join(c for c in str(text or "").strip().lower()
                  if c.isalnum() or c in "-_.")
    return out or "default"


# ── sealing one value ─────────────────────────────────────────────────────────────────

class Locked(Exception):
    """There is a passphrase on this machine and nobody has typed it yet."""


def _sealed_path(install_dir):
    return _path(install_dir, SEALED)


def _passphrase_key(install_dir):
    got = vault_lock.key()
    if got is None:
        raise Locked()
    return got


def _seal(install_dir, key, value, extra=b""):
    kind = keeper(install_dir)
    if kind is None:
        raise NoKeeper()
    if kind == "passphrase":
        master = _passphrase_key(install_dir)
        blob = vault_lock.seal(master, value, key.encode("utf-8"))
        sealed = _read_json(_sealed_path(install_dir), {"version": 1, "values": {}})
        sealed.setdefault("values", {})[key] = blob
        _write_json(_sealed_path(install_dir), sealed)
        return
    if kind == "dpapi":
        blob = _dpapi(value.encode("utf-8"), _entropy(key, extra), True)
        sealed = _read_json(_path(install_dir, SEALED), {"version": 1, "values": {}})
        sealed.setdefault("values", {})[key] = base64.b64encode(blob).decode("ascii")
        _write_json(_path(install_dir, SEALED), sealed)
        return
    if kind == "keychain":
        # -U updates in place; -w takes the value as an argument, which is visible in
        # `ps` for the instant the command runs, so it goes on stdin instead.
        subprocess.run(["security", "add-generic-password", "-U", "-s", SERVICE,
                        "-a", key, "-w"], input=value, text=True, check=True,
                       capture_output=True)
        return
    subprocess.run(["secret-tool", "store", "--label=Olivaw", "service", SERVICE,
                    "account", key], input=value, text=True, check=True,
                   capture_output=True)


def _unseal(install_dir, key, extra=b""):
    kind = keeper(install_dir)
    if kind is None:
        raise NoKeeper()
    if kind == "passphrase":
        master = _passphrase_key(install_dir)
        sealed = _read_json(_sealed_path(install_dir), {"version": 1, "values": {}})
        blob = (sealed.get("values") or {}).get(key)
        if not isinstance(blob, dict):
            return None
        return vault_lock.unseal(master, blob, key.encode("utf-8")).decode("utf-8")
    if kind == "dpapi":
        sealed = _read_json(_path(install_dir, SEALED), {"version": 1, "values": {}})
        raw = (sealed.get("values") or {}).get(key)
        if not raw:
            return None
        return _dpapi(base64.b64decode(raw), _entropy(key, extra), False).decode("utf-8")
    if kind == "keychain":
        r = subprocess.run(["security", "find-generic-password", "-s", SERVICE,
                            "-a", key, "-w"], text=True, capture_output=True)
        return r.stdout.rstrip("\n") if r.returncode == 0 else None
    r = subprocess.run(["secret-tool", "lookup", "service", SERVICE, "account", key],
                       text=True, capture_output=True)
    return r.stdout if r.returncode == 0 and r.stdout else None


def _unseal_forget(install_dir, key):
    kind = keeper(install_dir)
    if kind == "passphrase":
        sealed = _read_json(_sealed_path(install_dir), {"version": 1, "values": {}})
        if (sealed.get("values") or {}).pop(key, None) is not None:
            _write_json(_sealed_path(install_dir), sealed)
        return
    if kind == "dpapi":
        sealed = _read_json(_path(install_dir, SEALED), {"version": 1, "values": {}})
        if (sealed.get("values") or {}).pop(key, None) is not None:
            _write_json(_path(install_dir, SEALED), sealed)
        return
    if kind == "keychain":
        subprocess.run(["security", "delete-generic-password", "-s", SERVICE,
                        "-a", key], capture_output=True)
        return
    if kind == "libsecret":
        subprocess.run(["secret-tool", "clear", "service", SERVICE, "account", key],
                       capture_output=True)


# ── the metadata, which is the part that may be looked at ────────────────────────────

def _store(install_dir):
    return _read_json(_path(install_dir, STORE), {"version": 1, "entries": {}})


def _save_store(install_dir, data):
    _write_json(_path(install_dir, STORE), data)


def entry_key(agent, platform_id):
    return "%s/%s" % (_slug(agent), _slug(platform_id))


def entries(install_dir, agent=None):
    """Every credential, or one agent's - metadata only, never a value.

    This is what the console draws and what an agent is allowed to see: enough to know
    that a key exists, what it opens and when it was last used.
    """
    got = (_store(install_dir).get("entries") or {})
    out = []
    for key, row in sorted(got.items()):
        if not isinstance(row, dict):
            continue
        if agent is not None and not key.startswith(_slug(agent) + "/"):
            continue
        out.append({
            "key": key,
            "agent": key.split("/", 1)[0],
            "platform": row.get("platform", key.split("/", 1)[-1]),
            "label": row.get("label", ""),
            "url": row.get("url", ""),
            "username": "" if row.get("secret_username") else row.get("username", ""),
            "secret_username": bool(row.get("secret_username")),
            "fields": list(row.get("fields") or []),
            "confirm": bool(row.get("confirm")),
            "created": int(row.get("created") or 0),
            "last_used": int(row.get("last_used") or 0),
            "uses": int(row.get("uses") or 0),
            "note": row.get("note", ""),
            "allowed_until": int(row.get("allow_until") or 0),
        })
    return out


def put(install_dir, agent, platform_id, values, label="", url="", username="",
        secret_username=False, confirm=False, note=""):
    """Add or update one credential. `values` is {field: secret}, and secrets only.

    Passing a field with an empty value LEAVES that field as it was, so the console can
    send back a form the owner only half filled in without quietly wiping the password
    she did not retype.
    """
    with _lock:
        if keeper(install_dir) is None:
            raise NoKeeper()
        key = entry_key(agent, platform_id)
        data = _store(install_dir)
        row = dict((data.get("entries") or {}).get(key) or {})
        fields = set(row.get("fields") or [])
        for field, value in (values or {}).items():
            if field not in FIELDS:
                raise ValueError("no such field: %s" % field)
            if value is None or value == "":
                continue
            _seal(install_dir, _key(agent, platform_id, field), value)
            fields.add(field)
        if secret_username and username:
            _seal(install_dir, _key(agent, platform_id, "username"), username)
            fields.add("username")
        row.update({
            "platform": _slug(platform_id),
            "label": label or row.get("label") or _slug(platform_id),
            "url": url or row.get("url", ""),
            "username": "" if secret_username else (username or row.get("username", "")),
            "secret_username": bool(secret_username),
            "fields": sorted(fields),
            "confirm": bool(confirm),
            "note": note or row.get("note", ""),
            "created": int(row.get("created") or time.time()),
            "last_used": int(row.get("last_used") or 0),
            "uses": int(row.get("uses") or 0),
        })
        data.setdefault("entries", {})[key] = row
        _save_store(install_dir, data)
        _audit(install_dir, agent, platform_id, "saved", ",".join(sorted(fields)), True)
        return {"ok": True, "key": key, "fields": sorted(fields)}


def forget(install_dir, agent, platform_id):
    """Remove a credential and its values. Nothing is left in the keystore."""
    with _lock:
        key = entry_key(agent, platform_id)
        data = _store(install_dir)
        row = (data.get("entries") or {}).pop(key, None)
        if row is None:
            return {"ok": False, "detail": "No hay ninguna llave así."}
        for field in (row.get("fields") or []):
            _unseal_forget(install_dir, _key(agent, platform_id, field))
        _save_store(install_dir, data)
        _audit(install_dir, agent, platform_id, "deleted", "", True)
        return {"ok": True}


# ── spending one ──────────────────────────────────────────────────────────────────────

# How long an owner's "go ahead" lasts, and how many times it can be spent. One use,
# a few minutes: the agent asks because it is in front of the form NOW, and an
# allowance that outlived the moment would quietly turn "ask me every time" into "ask
# me once, ever".
ALLOW_MINUTES = 5


def allow_once(install_dir, agent, platform_id, minutes=ALLOW_MINUTES):
    """The owner says yes to the next use of one key.

    Without this the 'ask me first' switch was a switch that only said no: the agent
    was told to ask, and there was nothing for the owner to press when it did.
    """
    with _lock:
        key = entry_key(agent, platform_id)
        data = _store(install_dir)
        row = (data.get("entries") or {}).get(key)
        if not isinstance(row, dict):
            return {"ok": False, "detail": "No hay ninguna llave as\u00ed."}
        row["allow_until"] = int(time.time() + max(1, int(minutes)) * 60)
        data["entries"][key] = row
        _save_store(install_dir, data)
        _audit(install_dir, agent, platform_id, "allowed",
               "%d min" % int(minutes), True)
        return {"ok": True, "until": row["allow_until"]}


class Denied(Exception):
    """Said no, and why. The reason is for the agent and for the audit line."""


def _recent_uses(row, now):
    return [t for t in (row.get("recent") or []) if now - t < 3600]


def take(install_dir, agent, platform_id, field, approved=False):
    """The value, for a caller that is about to type it into a browser and nothing else.

    Deliberately not called `get`: there is no route, no console button and no tool
    command that returns a secret to anybody. The only caller is the filler in this
    install, and what it does with the value is put it in an input box.
    """
    with _lock:
        key = entry_key(agent, platform_id)
        data = _store(install_dir)
        row = (data.get("entries") or {}).get(key)
        if not isinstance(row, dict):
            raise Denied(u"Este agente no tiene una llave para «%s»." % platform_id)
        if field not in (row.get("fields") or []):
            raise Denied(u"Esa llave no guarda «%s»." % field)
        allowed = approved or int(row.get("allow_until") or 0) > int(time.time())
        if row.get("confirm") and not allowed:
            raise Denied(u"Esta llave pide permiso de la dueña cada vez. Pídeselo "
                         u"—ella lo da en la consola, en «Llaves y contraseñas»— y "
                         u"vuelve a intentarlo.")
        now = int(time.time())
        recent = _recent_uses(row, now)
        if len(recent) >= USES_PER_HOUR:
            raise Denied(u"Esa llave ya se usó %d veces en la última hora; "
                         u"algo no está saliendo bien. Pregúntale a la dueña."
                         % len(recent))
        try:
            value = _unseal(install_dir, _key(agent, platform_id, field))
        except Locked:
            raise Denied(u"Las llaves de este equipo están cerradas con contraseña y "
                         u"nadie la ha abierto todavía. Pídele a la dueña que las "
                         u"abra.")
        if value is None:
            raise Denied(u"La llave está registrada pero su valor no está en el "
                         u"llavero de este equipo.")
        # Spent: one yes is one use, not a window somebody forgot to close.
        row.pop("allow_until", None)
        row["recent"] = recent + [now]
        row["last_used"] = now
        row["uses"] = int(row.get("uses") or 0) + 1
        data["entries"][key] = row
        _save_store(install_dir, data)
        return value


def note_fill(install_dir, agent, platform_id, field, where, ok, detail=""):
    _audit(install_dir, agent, platform_id, "filled:" + field, where, ok, detail)


def reseal_all(install_dir, old_key, new_key):
    """Every value, opened with the old key and closed with the new one.

    Handed to vault_lock.change_passphrase, which owns the crypto and not the file. A
    half-finished walk here would leave values nobody can open, so the new file is
    written only once every one of them has been re-sealed in memory.
    """
    sealed = _read_json(_sealed_path(install_dir), {"version": 1, "values": {}})
    values = sealed.get("values") or {}
    fresh = {}
    for key, blob in values.items():
        raw = vault_lock.unseal(old_key, blob, key.encode("utf-8"))
        fresh[key] = vault_lock.seal(new_key, raw, key.encode("utf-8"))
    sealed["values"] = fresh
    _write_json(_sealed_path(install_dir), sealed)


def lock_state(install_dir):
    """What the console needs to draw: whether there is a passphrase, and whether it
    has been typed."""
    st = vault_lock.state(install_dir)
    st["can_set"] = can_set_passphrase(install_dir)
    return st


# ── one-time codes ────────────────────────────────────────────────────────────────────

def totp(seed, when=None, digits=6, step=30):
    """RFC 6238, which is thirty lines and in the standard library already.

    Worth having rather than sending the owner a push every time: an agent that can get
    past a password wall and then stops at the six digits has not got past anything.
    """
    key = base64.b32decode(_tidy_seed(seed), casefold=True)
    counter = int((time.time() if when is None else when) // step)
    mac = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    at = mac[-1] & 0x0F
    code = struct.unpack(">I", mac[at:at + 4])[0] & 0x7FFFFFFF
    return str(code % (10 ** digits)).zfill(digits)


def _tidy_seed(seed):
    """A seed as people actually paste it: spaces, lower case, no padding."""
    flat = "".join(str(seed or "").split()).upper()
    return flat + "=" * (-len(flat) % 8)


def seconds_left(step=30, when=None):
    """How long the code on screen stays good for. Never 0: a code with no seconds
    left is one nobody would type, and the honest answer at that instant is that the
    next one is a moment away."""
    now = time.time() if when is None else when
    return int(math.ceil(step - (now % step))) or step


# ── the record of what was spent ──────────────────────────────────────────────────────

def _audit(install_dir, agent, platform_id, what, where, ok, detail=""):
    """One line per use. Never the value - that is the whole point of the line.

    Kept because "the agent can use my passwords" is only acceptable if the owner can
    see, afterwards, every time it did.
    """
    line = u"%s\t%s\t%s\t%s\t%s\t%s\t%s\n" % (
        time.strftime("%Y-%m-%dT%H:%M:%S"), _slug(agent), _slug(platform_id),
        what, "ok" if ok else "no", (where or "")[:200], (detail or "")[:200])
    try:
        path = _path(install_dir, AUDIT)
        with io.open(path, "a", encoding="utf-8") as fh:
            fh.write(line)
        _only_owner(path)
    except OSError:
        pass


def audit(install_dir, limit=50, agent=None):
    """The last uses, newest first, for the console to show."""
    try:
        with io.open(_path(install_dir, AUDIT), encoding="utf-8") as fh:
            rows = fh.read().splitlines()
    except OSError:
        return []
    out = []
    for raw in reversed(rows):
        parts = raw.split("\t")
        if len(parts) < 6:
            continue
        if agent is not None and parts[1] != _slug(agent):
            continue
        out.append({"when": parts[0], "agent": parts[1], "platform": parts[2],
                    "what": parts[3], "ok": parts[4] == "ok", "where": parts[5],
                    "detail": parts[6] if len(parts) > 6 else ""})
        if len(out) >= limit:
            break
    return out
