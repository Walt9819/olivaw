r"""A passphrase, for the machines that have no keystore of their own.

Windows has DPAPI, a Mac has the Keychain, a Linux desktop has libsecret. A Linux server
has none of them - no keyring daemon, often no desktop at all - and that is exactly the
kind of machine an agent runs on unattended. Refusing to keep passwords there is honest
but useless, and writing them to a file would be the thing this whole feature exists to
avoid.

So: the owner sets a passphrase. The key is derived from it and lives ONLY in this
process's memory; on disk there is a salt, the parameters, a verifier, and ciphertext.
A stolen copy of the folder is a brick without the passphrase, and - unlike every other
keeper here - so is the machine itself until somebody unlocks it. That is the cost, and
it buys the one property the OS keystores cannot: an agent with a shell on this box
cannot read the vault either, because the key is not anywhere it can reach.

**What does the encrypting.** In order of preference:

  * `cryptography`'s AES-GCM, when the machine happens to have it installed;
  * failing that, a construction out of the standard library: SHA-256 in counter mode
    for the keystream, HMAC-SHA256 over the ciphertext, encrypt-then-MAC, with the two
    keys derived separately from the master. Hand-written cipher code deserves
    suspicion, so this one is as small and as conservative as it can be - no modes to
    get wrong, no padding, a fresh random nonce per write, a constant-time compare, and
    the slot's name mixed into the MAC so a blob cannot be moved from one entry to
    another. It is only reached on a machine that offered nothing better.

The algorithm is written into every blob, so a machine that gains `cryptography` later
still reads what it wrote before.
"""

import base64
import hashlib
import hmac
import io
import json
import os
import struct
import threading
import time

LOCK = "credentials.lock"

# 64 MB and about a third of a second on the machine this was written on. Enough that
# guessing a passphrase offline is expensive; little enough that unlocking after a
# reboot is not an event. The numbers are written into the file, so they can be raised
# later without stranding what is already sealed.
KDF = {"kdf": "scrypt", "n": 1 << 16, "r": 8, "p": 1}
MAXMEM = 256 * 1024 * 1024

# How long a key stays held after it was last used. A workday, not a week: the owner
# unlocks once in the morning, and a machine left alone overnight locks itself.
HOLD_SECONDS = 12 * 3600

try:  # pragma: no cover - depends on the machine, both paths are tested
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    HAVE_AESGCM = True
except Exception:  # noqa: BLE001
    AESGCM = None
    HAVE_AESGCM = False

_held = {"key": None, "until": 0.0}
_lock = threading.RLock()


class Locked(Exception):
    """There is a passphrase, and nobody has typed it on this machine yet."""


class Tampered(Exception):
    """The ciphertext does not match its MAC. Not a wrong passphrase - a changed file."""


# ── keys ──────────────────────────────────────────────────────────────────────────────

def _sub(master, label):
    """A key for one job, from the master. Separate jobs must never share one."""
    return hmac.new(master, b"olivaw-vault|" + label, hashlib.sha256).digest()


def derive(passphrase, salt, params=None):
    p = dict(KDF, **(params or {}))
    return hashlib.scrypt(passphrase.encode("utf-8"), salt=salt,
                          n=int(p["n"]), r=int(p["r"]), p=int(p["p"]),
                          dklen=32, maxmem=MAXMEM)


# ── the cipher ────────────────────────────────────────────────────────────────────────

def _keystream(key, nonce, length):
    """SHA-256 in counter mode.

    Each block is the hash of (key, nonce, counter), so blocks are independent and the
    stream never repeats for a nonce that is used once. The key here is already a
    derived subkey, never the master.
    """
    out = bytearray()
    counter = 0
    while len(out) < length:
        out += hashlib.sha256(key + nonce + struct.pack(">I", counter)).digest()
        counter += 1
    return bytes(out[:length])


def seal(master, data, slot=b""):
    """Encrypt one value. `slot` is bound in, so a blob is only valid where it was put."""
    if isinstance(data, str):
        data = data.encode("utf-8")
    if HAVE_AESGCM:
        nonce = os.urandom(12)
        box = AESGCM(_sub(master, b"aes")).encrypt(nonce, data, slot)
        return {"v": 1, "alg": "aes-gcm", "n": _b64(nonce), "c": _b64(box)}
    nonce = os.urandom(16)
    cipher = bytes(a ^ b for a, b in
                   zip(data, _keystream(_sub(master, b"enc"), nonce, len(data))))
    tag = hmac.new(_sub(master, b"mac"),
                   b"sha256-ctr|" + slot + b"|" + nonce + cipher,
                   hashlib.sha256).digest()
    return {"v": 1, "alg": "sha256-ctr", "n": _b64(nonce), "c": _b64(cipher),
            "t": _b64(tag)}


def unseal(master, blob, slot=b""):
    """Decrypt one value, or refuse. Never returns something half-verified."""
    alg = (blob or {}).get("alg")
    nonce = _unb64(blob.get("n", ""))
    cipher = _unb64(blob.get("c", ""))
    if alg == "aes-gcm":
        if not HAVE_AESGCM:
            raise Tampered("este equipo ya no tiene con qué abrir eso")
        try:
            return AESGCM(_sub(master, b"aes")).decrypt(nonce, cipher, slot)
        except Exception:  # noqa: BLE001
            raise Tampered("no abre")
    if alg != "sha256-ctr":
        raise Tampered("formato desconocido")
    want = hmac.new(_sub(master, b"mac"),
                    b"sha256-ctr|" + slot + b"|" + nonce + cipher,
                    hashlib.sha256).digest()
    # Checked BEFORE a single byte is decrypted, and in constant time: a MAC compared
    # with == leaks where it stopped matching, and a plaintext produced before the check
    # is a plaintext somebody can steer.
    if not hmac.compare_digest(want, _unb64(blob.get("t", ""))):
        raise Tampered("no abre")
    return bytes(a ^ b for a, b in
                 zip(cipher, _keystream(_sub(master, b"enc"), nonce, len(cipher))))


def _b64(raw):
    return base64.b64encode(raw).decode("ascii")


def _unb64(text):
    try:
        return base64.b64decode(text or "")
    except Exception:  # noqa: BLE001
        return b""


# ── the lock file ─────────────────────────────────────────────────────────────────────

def _path(install_dir):
    return os.path.join(install_dir or ".", LOCK)


def configured(install_dir):
    """Has a passphrase been set on this machine?"""
    return os.path.isfile(_path(install_dir))


def _read(install_dir):
    try:
        with io.open(_path(install_dir), encoding="utf-8") as fh:
            got = json.load(fh)
        return got if isinstance(got, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def set_passphrase(install_dir, passphrase):
    """Choose one, and hold the key it makes.

    Refuses to be the second passphrase for a vault that already has one: changing it
    means re-sealing every value, which is `change_passphrase`.
    """
    if not passphrase or len(passphrase) < 8:
        return {"ok": False, "detail":
                u"Elige una contraseña de al menos 8 caracteres."}
    if configured(install_dir):
        return {"ok": False, "detail": u"Ya hay una contraseña para este equipo."}
    salt = os.urandom(16)
    master = derive(passphrase, salt)
    data = dict(KDF, salt=_b64(salt), v=1,
                verify=_b64(_sub(master, b"verify")))
    tmp = "%s.%d.tmp" % (_path(install_dir), os.getpid())
    with io.open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
    try:
        if os.name != "nt":
            os.chmod(tmp, 0o600)
    except OSError:
        pass
    os.replace(tmp, _path(install_dir))
    _hold(master)
    return {"ok": True}


def unlock(install_dir, passphrase):
    """Type it once; the key stays in this process and never touches the disk."""
    got = _read(install_dir)
    if not got:
        return {"ok": False, "detail": u"Este equipo no tiene contraseña de llaves."}
    master = derive(passphrase, _unb64(got.get("salt", "")), got)
    if not hmac.compare_digest(_sub(master, b"verify"),
                               _unb64(got.get("verify", ""))):
        return {"ok": False, "detail": u"Esa no es la contraseña."}
    _hold(master)
    return {"ok": True, "until": _held["until"]}


def change_passphrase(install_dir, old, new, reseal):
    """A new passphrase, and every value re-sealed under it.

    `reseal` is handed the old and new keys and does the walk over the store, because
    the vault owns the file and this module owns the crypto.
    """
    if not new or len(new) < 8:
        return {"ok": False, "detail":
                u"Elige una contraseña de al menos 8 caracteres."}
    got = _read(install_dir)
    if not got:
        return {"ok": False, "detail": u"Este equipo no tiene contraseña de llaves."}
    old_key = derive(old, _unb64(got.get("salt", "")), got)
    if not hmac.compare_digest(_sub(old_key, b"verify"),
                               _unb64(got.get("verify", ""))):
        return {"ok": False, "detail": u"La contraseña actual no es esa."}
    salt = os.urandom(16)
    new_key = derive(new, salt)
    reseal(old_key, new_key)
    data = dict(KDF, salt=_b64(salt), v=1, verify=_b64(_sub(new_key, b"verify")))
    with io.open(_path(install_dir), "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
    _hold(new_key)
    return {"ok": True}


# ── holding the key ───────────────────────────────────────────────────────────────────

def _hold(master):
    with _lock:
        _held["key"] = master
        _held["until"] = time.time() + HOLD_SECONDS


def key():
    """The key, or None when nobody has unlocked (or it has been long enough)."""
    with _lock:
        if _held["key"] is None:
            return None
        if time.time() > _held["until"]:
            _held["key"] = None
            return None
        # Using it is what keeps it: a machine in use does not re-ask every twelve
        # hours in the middle of the afternoon.
        _held["until"] = time.time() + HOLD_SECONDS
        return _held["key"]


def forget():
    """Lock it now - the owner leaving her desk, or a test tidying up."""
    with _lock:
        _held["key"] = None
        _held["until"] = 0.0


def state(install_dir):
    with _lock:
        held = _held["key"] is not None and time.time() <= _held["until"]
        return {"configured": configured(install_dir), "unlocked": held,
                "until": int(_held["until"]) if held else 0,
                "cipher": "aes-gcm" if HAVE_AESGCM else "sha256-ctr"}
