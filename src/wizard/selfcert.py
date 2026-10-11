# -*- coding: utf-8 -*-
"""A TLS certificate this machine makes for itself, with nothing installed.

The phone talks to this computer over TLS. That needs a certificate, and the two usual
ways to get one are both closed here:

* a public CA (Let's Encrypt and friends) would need a public domain name pointing at a
  house with a changing IP, and a port open to the internet - the exact thing the
  security rule forbids;
* ``pip install cryptography`` would need pip, and Olivaw runs on whatever Python the
  owner already has (see launcher.py: "Stdlib only - no pip installs").

So the computer signs its own, here, out of ``secrets`` and ``pow()``. That is normally a
bad idea because nobody can tell a self-signed certificate from an impostor's - but we
are not asking anybody to. The fingerprint of this exact certificate travels to the phone
inside the pairing QR, over the screen, and the phone from then on accepts *that one
certificate and no other*. That is stricter than the public CA system, not weaker: a CA
vouches for a name, and anyone who fools the CA gets in, whereas pinning vouches for a
key and nothing else will do.

Everything below is plain RFC work:

* RSA key generation - random primes, Miller-Rabin (``secrets`` for the randomness, never
  ``random``);
* DER, the tag-length-value encoding X.509 is written in;
* an X.509 v3 certificate, self-issued, signed with RSASSA-PKCS1-v1_5 over SHA-256.

It is written once, at the install's first pairing, and then read from disk. Generation
costs a few seconds of arithmetic; serving costs nothing.
"""

import base64
import datetime
import hashlib
import os
import secrets

KEY_BITS = 2048
# A pinned certificate is not trusted because it is young, it is trusted because it is
# *that* certificate. Expiry here only exists so a forgotten install eventually stops
# serving; ten years is the shape of a device certificate, not a web one.
VALID_DAYS = 3650
SUBJECT_CN = u"Olivaw"

CERT_NAME = "remote-cert.pem"
KEY_NAME = "remote-key.pem"

# ── DER ────────────────────────────────────────────────────────────────────────────────
# Everything X.509 is made of is a tag byte, a length, and some content. Lengths under
# 128 are one byte; above that it is 0x80|n followed by n big-endian bytes.


def _len(n):
    if n < 0x80:
        return bytes([n])
    raw = n.to_bytes((n.bit_length() + 7) // 8, "big")
    return bytes([0x80 | len(raw)]) + raw


def _tlv(tag, content):
    return bytes([tag]) + _len(len(content)) + content


def _int(n):
    """INTEGER. DER integers are signed, so a value whose top bit is set needs a leading
    zero byte or it would be read as negative - this is the single most common way a
    hand-rolled certificate comes out corrupt."""
    if n == 0:
        return _tlv(0x02, b"\x00")
    raw = n.to_bytes((n.bit_length() + 8) // 8, "big")
    return _tlv(0x02, raw)


def _bitstring(content):
    return _tlv(0x03, b"\x00" + content)        # 0 unused bits


def _octets(content):
    return _tlv(0x04, content)


def _null():
    return b"\x05\x00"


def _oid(dotted):
    parts = [int(p) for p in dotted.split(".")]
    out = bytearray([parts[0] * 40 + parts[1]])
    for p in parts[2:]:
        chunk = bytearray([p & 0x7F])
        p >>= 7
        while p:
            chunk.insert(0, (p & 0x7F) | 0x80)
            p >>= 7
        out += chunk
    return _tlv(0x06, bytes(out))


def _seq(*items):
    return _tlv(0x30, b"".join(items))


def _set(*items):
    return _tlv(0x31, b"".join(items))


def _utf8(s):
    return _tlv(0x0C, s.encode("utf-8"))


def _ia5(s):
    return _tlv(0x16, s.encode("ascii"))


def _utctime(dt):
    # UTCTime is YYMMDDHHMMSSZ and runs out in 2049; certificates past that must use
    # GeneralizedTime. Ten years from today is nowhere near it, and the switch is here
    # so a future reader does not have to discover the rule the hard way.
    if dt.year >= 2050:
        return _tlv(0x18, dt.strftime("%Y%m%d%H%M%SZ").encode("ascii"))
    return _tlv(0x17, dt.strftime("%y%m%d%H%M%SZ").encode("ascii"))


def _ctx(n, content, constructed=True):
    """[n] - a context tag, which is how X.509 marks its optional fields."""
    return _tlv((0xA0 if constructed else 0x80) | n, content)


OID_RSA = "1.2.840.113549.1.1.1"
OID_SHA256_RSA = "1.2.840.113549.1.1.11"
OID_CN = "2.5.4.3"
OID_O = "2.5.4.10"
OID_BASIC_CONSTRAINTS = "2.5.29.19"
OID_KEY_USAGE = "2.5.29.15"
OID_EXT_KEY_USAGE = "2.5.29.37"
OID_SAN = "2.5.29.17"
OID_SERVER_AUTH = "1.3.6.1.5.5.7.3.1"

# The DigestInfo that PKCS#1 v1.5 puts in front of a SHA-256 hash. Fixed bytes, from
# RFC 8017 - building it with _seq would produce exactly this.
SHA256_DIGESTINFO = bytes.fromhex("3031300d060960864801650304020105000420")


# ── RSA ────────────────────────────────────────────────────────────────────────────────

_SMALL_PRIMES = [2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37, 41, 43, 47, 53, 59, 61, 67,
                 71, 73, 79, 83, 89, 97, 101, 103, 107, 109, 113, 127, 131, 137, 139,
                 149, 151, 157, 163, 167, 173, 179, 181, 191, 193, 197, 199, 211, 223,
                 227, 229, 233, 239, 241, 251]


def _probably_prime(n, rounds=40):
    """Miller-Rabin. Trial division first because it rejects ~80% of odd candidates for
    almost nothing, and a Miller-Rabin round on a 1024-bit number is a modexp."""
    if n < 2:
        return False
    for p in _SMALL_PRIMES:
        if n == p:
            return True
        if n % p == 0:
            return False
    d = n - 1
    r = 0
    while d % 2 == 0:
        d //= 2
        r += 1
    for _ in range(rounds):
        a = secrets.randbelow(n - 3) + 2
        x = pow(a, d, n)
        if x == 1 or x == n - 1:
            continue
        for _ in range(r - 1):
            x = x * x % n
            if x == n - 1:
                break
        else:
            return False
    return True


def _prime(bits, e):
    """A random prime of exactly `bits` bits, usable with this public exponent.

    The top two bits are forced so that p*q lands on exactly KEY_BITS bits, and p-1 must
    not be a multiple of e or d would not exist.
    """
    while True:
        n = secrets.randbits(bits) | (3 << (bits - 2)) | 1
        if n % e == 1:
            continue
        if _probably_prime(n):
            return n


def generate_key(bits=KEY_BITS):
    """An RSA key as a dict of the numbers PKCS#1 wants."""
    e = 65537
    half = bits // 2
    while True:
        p = _prime(half, e)
        q = _prime(half, e)
        if p == q:
            continue
        n = p * q
        if n.bit_length() != bits:
            continue
        phi = (p - 1) * (q - 1)
        d = pow(e, -1, phi)          # Python 3.8+: modular inverse
        if d.bit_length() < bits // 2:
            continue                 # a tiny d is attackable (Wiener); start over
        if p < q:
            p, q = q, p
        return {"n": n, "e": e, "d": d, "p": p, "q": q,
                "dp": d % (p - 1), "dq": d % (q - 1), "qinv": pow(q, -1, p)}


def _sign(key, message):
    """RSASSA-PKCS1-v1_5 over SHA-256."""
    k = (key["n"].bit_length() + 7) // 8
    t = SHA256_DIGESTINFO + hashlib.sha256(message).digest()
    # EM = 0x00 || 0x01 || 0xFF... || 0x00 || T
    em = b"\x00\x01" + b"\xff" * (k - len(t) - 3) + b"\x00" + t
    s = pow(int.from_bytes(em, "big"), key["d"], key["n"])
    return s.to_bytes(k, "big")


def key_pem(key):
    """PKCS#1 'RSA PRIVATE KEY'. OpenSSL, and so Python's ssl, reads it directly."""
    der = _seq(_int(0), _int(key["n"]), _int(key["e"]), _int(key["d"]),
               _int(key["p"]), _int(key["q"]), _int(key["dp"]), _int(key["dq"]),
               _int(key["qinv"]))
    return _pem("RSA PRIVATE KEY", der)


def _pem(label, der):
    b64 = base64.b64encode(der).decode("ascii")
    lines = [b64[i:i + 64] for i in range(0, len(b64), 64)]
    return ("-----BEGIN %s-----\n" % label) + "\n".join(lines) + \
           ("\n-----END %s-----\n" % label)


def _name(cn, org):
    return _seq(_set(_seq(_oid(OID_O), _utf8(org))),
                _set(_seq(_oid(OID_CN), _utf8(cn))))


def _san(names, ips):
    """subjectAltName. dNSName is [2], iPAddress is [7] and carries raw bytes.

    The phone does not check any of this - it checks the fingerprint - but a certificate
    with no SAN is rejected outright by some TLS stacks before anyone gets to look at it,
    so it carries the names this machine can actually be reached by.
    """
    items = [_ctx(2, n.encode("ascii"), constructed=False) for n in names]
    for ip in ips:
        try:
            raw = bytes(int(p) for p in ip.split("."))
        except Exception:  # noqa: BLE001
            continue
        if len(raw) == 4:
            items.append(_ctx(7, raw, constructed=False))
    return _tlv(0x30, b"".join(items))


def _extensions(names, ips):
    def ext(oid, critical, value_der):
        parts = [_oid(oid)]
        if critical:
            parts.append(_tlv(0x01, b"\xff"))      # BOOLEAN TRUE
        parts.append(_octets(value_der))
        return _seq(*parts)

    # keyUsage: digitalSignature (bit 0) + keyEncipherment (bit 2) = 0b10100000, with
    # 3 trailing bits unused.
    key_usage = _tlv(0x03, b"\x05" + bytes([0b10100000]))
    return _ctx(3, _seq(
        ext(OID_BASIC_CONSTRAINTS, True, _seq()),              # CA: FALSE
        ext(OID_KEY_USAGE, True, key_usage),
        ext(OID_EXT_KEY_USAGE, False, _seq(_oid(OID_SERVER_AUTH))),
        ext(OID_SAN, False, _san(names, ips)),
    ))


def make_cert(key, cn=SUBJECT_CN, names=("localhost",), ips=("127.0.0.1",),
              days=VALID_DAYS, now=None):
    """A self-signed X.509 v3 certificate for this key. Returns the DER bytes."""
    now = now or datetime.datetime.now(datetime.timezone.utc)
    # Backdated an hour: a phone whose clock is a little behind should not see a
    # certificate from the future on the day it is created.
    not_before = now - datetime.timedelta(hours=1)
    not_after = now + datetime.timedelta(days=days)
    sig_alg = _seq(_oid(OID_SHA256_RSA), _null())
    subject = _name(cn, u"Olivaw")
    spki = _seq(_seq(_oid(OID_RSA), _null()),
                _bitstring(_seq(_int(key["n"]), _int(key["e"]))))
    tbs = _seq(
        _ctx(0, _int(2)),                                   # v3
        _int(int.from_bytes(secrets.token_bytes(16), "big") >> 1 | 1),
        sig_alg,
        subject,                                            # issuer == subject
        _seq(_utctime(not_before), _utctime(not_after)),
        subject,
        spki,
        _extensions(list(names), list(ips)),
    )
    return _seq(tbs, sig_alg, _bitstring(_sign(key, tbs)))


def fingerprint(der):
    """What the QR carries and the phone pins: SHA-256 of the certificate, lowercase hex.

    The same number OpenSSL prints for `-fingerprint -sha256` and the same one Dart's
    X509Certificate exposes, so both ends compute it from the identical bytes.
    """
    return hashlib.sha256(der).hexdigest()


_WORDS = (u"ÁGUILA", u"BOSQUE", u"CANELA", u"DELFÍN", u"ESTAÑO", u"FAROL", u"GAVIOTA",
          u"HIEDRA", u"ÍNDIGO", u"JAZMÍN", u"LADRILLO", u"MANZANA", u"NUBE", u"OLIVO",
          u"PÁJARO", u"QUESO", u"ROBLE", u"SALMÓN", u"TIGRE", u"UVA", u"VIOLETA",
          u"XILÓFONO", u"YUNQUE", u"ZORRO", u"ÁMBAR", u"BRÚJULA", u"CEDRO", u"DUNA",
          u"ESPIGA", u"FÓSFORO", u"GRANATE", u"HORNO")


def short_code(fp):
    """Two words both screens can show, so a person can check they match.

    A fingerprint is 64 hex characters and nobody compares those honestly. The phone
    computes this from the certificate it was actually handed and the console computes it
    from the certificate this machine actually holds - if somebody on the wifi had put
    themselves in the middle, the two would differ, because they would be looking at
    different certificates. Two words out of this list is about ten bits, which is the
    wrong amount to protect a secret and the right amount to catch an impostor who has to
    commit to a certificate *before* finding out which words it has to produce.
    """
    raw = hashlib.sha256(("olivaw-pair:" + (fp or "")).encode("utf-8")).digest()
    n = int.from_bytes(raw[:4], "big")
    return u"%s-%s" % (_WORDS[n % len(_WORDS)],
                       _WORDS[(n // len(_WORDS)) % len(_WORDS)])


def _der_of_pem(text):
    body = "".join(l.strip() for l in text.splitlines()
                   if l.strip() and not l.startswith("-----"))
    return base64.b64decode(body)


def local_ips():
    """Every IPv4 this machine answers on, so the QR can offer the right one.

    No DNS and no outbound packet: a UDP socket "connected" to a public address picks a
    route and names the interface it would leave by, which is the address the phone on
    the same wifi has to dial.
    """
    import socket
    found = []
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 53))       # no packet is sent for UDP connect()
            found.append(s.getsockname()[0])
        finally:
            s.close()
    except Exception:  # noqa: BLE001
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if ip not in found and not ip.startswith("127."):
                found.append(ip)
    except Exception:  # noqa: BLE001
        pass
    return found


def ensure(install_dir, regenerate=False):
    """The certificate for this install, made on first use and then read from disk.

    Returns ``{cert, key, fingerprint, created}``. Generating costs a few seconds of
    prime hunting, so it happens once and never on a request path.
    """
    cert_path = os.path.join(install_dir, CERT_NAME)
    key_path = os.path.join(install_dir, KEY_NAME)
    if not regenerate and os.path.isfile(cert_path) and os.path.isfile(key_path):
        try:
            with open(cert_path, "r", encoding="ascii") as fh:
                der = _der_of_pem(fh.read())
            if der:
                return {"cert": cert_path, "key": key_path,
                        "fingerprint": fingerprint(der), "created": False}
        except Exception:  # noqa: BLE001
            pass                                  # unreadable: make a new one
    key = generate_key()
    ips = local_ips()
    der = make_cert(key, names=["localhost", "olivaw.local"],
                    ips=["127.0.0.1"] + ips)
    tmp_cert, tmp_key = cert_path + ".new", key_path + ".new"
    with open(tmp_cert, "w", encoding="ascii") as fh:
        fh.write(_pem("CERTIFICATE", der))
    with open(tmp_key, "w", encoding="ascii") as fh:
        fh.write(key_pem(key))
    try:
        # The private key is the whole of this machine's identity to the phone. On
        # Windows that means taking the inherited ACL off and granting only this user.
        if os.name == "nt":
            import subprocess

            from winspawn import quiet
            user = os.environ.get("USERNAME", "")
            if user:
                # quiet(): the console runs under pythonw, which has no console of its
                # own, so a bare subprocess here flashes a black window on the owner's
                # screen for the length of one icacls.
                subprocess.run(["icacls", tmp_key, "/inheritance:r",
                                "/grant:r", "%s:(R,W)" % user],
                               **quiet(capture_output=True, timeout=20))
        else:
            os.chmod(tmp_key, 0o600)
    except Exception:  # noqa: BLE001
        pass
    os.replace(tmp_cert, cert_path)
    os.replace(tmp_key, key_path)
    return {"cert": cert_path, "key": key_path, "fingerprint": fingerprint(der),
            "created": True}
