r"""The owner's passwords: what the agent can do with them, and what it cannot.

This is the suite for a feature whose whole value is a set of "cannot"s, so most of what
is below is an attempt to do something and a check that it was refused.

The four that matter most, in order:

  * **The value is never on disk in the clear.** Every file the vault writes is read back
    and searched for the password. A feature that encrypts the password and then leaves a
    copy in an audit line has encrypted nothing.
  * **The agent cannot read it back.** There is no `get`; `take` exists for the filler and
    is tested here for the refusals it makes - wrong agent, wrong field, too often, not
    approved - because those refusals ARE the access control.
  * **It goes into the right site or nowhere.** A credential saved for one host is offered
    to a page on another, and must be refused BEFORE it is unsealed: the ordering is the
    test, not just the outcome. This is the phishing case, which is the one an agent
    reading web pages will actually meet.
  * **A password goes in a password box.** A login form that asks for it in a visible
    field is not a login form.

Run: python tools/test_vault.py
"""

import io
import os
import shutil
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from wizard import vault as V          # noqa: E402
from wizard import vault_fill as F     # noqa: E402

PASSED, FAILED = [], []
SECRET = u"correcto-caballo-grapa-7!"
SEED = u"JBSWY3DPEHPK3PXP"


def ok(name, cond, extra=""):
    (PASSED if cond else FAILED).append(name)
    print(("  ok   " if cond else "  FAIL ") + name +
          (("\n       " + str(extra)) if (extra and not cond) else ""))


def section(t):
    print("\n=== %s ===" % t)


SAND = tempfile.mkdtemp(prefix="olivaw-vault-")


# A keeper of our own, so the suite runs the same on a build machine with no DPAPI and
# no Keychain as it does on the owner's laptop. It is deliberately NOT encryption: it
# stands in for the operating system's store, and the tests below never treat what it
# holds as protected - they check that the value is not in the vault's OWN files.
_FAKE = {}


def _fake_seal(install_dir, key, value, extra=b""):
    _FAKE[key] = value


def _fake_unseal(install_dir, key, extra=b""):
    return _FAKE.get(key)


def _fake_forget(install_dir, key):
    _FAKE.pop(key, None)


REAL_KEEPER = V.keeper()
if REAL_KEEPER is None:
    V.keeper = lambda install_dir=None: "fake"
    V._seal, V._unseal, V._unseal_forget = _fake_seal, _fake_unseal, _fake_forget
    print("(no OS keystore here - standing one in)")
else:
    print("(keystore: %s)" % V.keeper_label())


# ── keeping one ───────────────────────────────────────────────────────────────────────
section("a key goes in")

res = V.put(SAND, "heraldo", "google", {"password": SECRET, "totp": SEED},
            label=u"Google de Heraldo", url="https://accounts.google.com/",
            username="heraldo@igalenus.com")
ok("saving one says what it now holds",
   res["ok"] and res["fields"] == ["password", "totp"], res)

rows = V.entries(SAND, "heraldo")
ok("and it is listed, with the account but never the value",
   len(rows) == 1 and rows[0]["username"] == "heraldo@igalenus.com"
   and SECRET not in str(rows), rows)

ok("the value comes back to the one caller that may have it",
   V.take(SAND, "heraldo", "google", "password") == SECRET)


# ── and is not written down anywhere ─────────────────────────────────────────────────
section("nothing readable is left on disk")

leaked = []
for name in sorted(os.listdir(SAND)):
    path = os.path.join(SAND, name)
    if not os.path.isfile(path):
        continue
    try:
        text = io.open(path, "rb").read().decode("utf-8", "replace")
    except OSError:
        continue
    if SECRET in text or SEED in text:
        leaked.append(name)
ok("no file the vault writes contains the password or the seed", not leaked, leaked)

if REAL_KEEPER == "dpapi":
    sealed = io.open(os.path.join(SAND, V.SEALED), encoding="utf-8").read()
    ok("what IS on disk is a sealed blob", "values" in sealed and len(sealed) > 80)
    # Sealed to this slot, not merely to this account: moving a blob into another row
    # must not hand that row's reader a different password.
    import base64
    blob = base64.b64decode(
        __import__("json").loads(sealed)["values"]["heraldo/google/password"])
    moved = False
    try:
        V._dpapi(blob, V._entropy("heraldo/google/totp"), False)
        moved = True
    except OSError:
        pass
    ok("and a blob moved to another field does not open", not moved)


# ── who may spend it ─────────────────────────────────────────────────────────────────
section("what the agent cannot do")


def denied(what, fn):
    try:
        fn()
        ok(what, False, "it was allowed")
    except V.Denied as e:
        ok(what, True)
        return str(e)
    except Exception as e:  # noqa: BLE001
        ok(what, False, "wrong error: %r" % e)
    return ""


why = denied("another agent's key is not this agent's to use",
             lambda: V.take(SAND, "forja", "google", "password"))
# The exact refusal, not just any refusal: the scope is checked in two places - here,
# and again by the keystore key, which has the agent in it. Pinning the sentence keeps
# the first layer honest instead of letting the second one cover for it.
ok("and the refusal is about whose key it is, not about a missing value",
   u"no tiene una llave para" in why, why)

denied("a field the key does not hold is refused",
       lambda: V.take(SAND, "heraldo", "google", "username"))
denied("a platform nobody saved is refused",
       lambda: V.take(SAND, "heraldo", "banco", "password"))

# The rate limit is not about abuse alone: twelve failed logins in an hour is how an
# account gets locked, and a loop is the likeliest cause.
for _ in range(V.USES_PER_HOUR - 1):
    V.take(SAND, "heraldo", "google", "password")
why = denied("one key cannot be spent all afternoon",
             lambda: V.take(SAND, "heraldo", "google", "password"))
ok("and it says how many times, so the agent can report it", "12" in why, why)

# Yesterday's uses are not today's problem.
data = V._store(SAND)
row = data["entries"]["heraldo/google"]
row["recent"] = [int(time.time()) - 7200] * 20
V._save_store(SAND, data)
ok("an hour later it works again",
   V.take(SAND, "heraldo", "google", "password") == SECRET)


# ── the ones the owner wants to be asked about ───────────────────────────────────────
section("a key that asks first")

V.put(SAND, "forja", "banco", {"password": "otra-cosa"}, url="https://banco.es/",
      confirm=True)
why = denied("a key marked 'ask me' is refused without an answer",
             lambda: V.take(SAND, "forja", "banco", "password"))
ok("and the refusal tells the agent what to do about it",
   u"permiso" in why or u"ped" in why.lower(), why)
ok("with the owner's yes, it works",
   V.take(SAND, "forja", "banco", "password", approved=True) == "otra-cosa")

# And the yes she actually gives, which is a button in the console rather than a flag
# passed in code. Without it the switch only ever said no.
V.allow_once(SAND, "forja", "banco")
ok("the owner's go-ahead lets the next use through",
   V.take(SAND, "forja", "banco", "password") == "otra-cosa")
denied("and it is spent by that one use, not left open",
       lambda: V.take(SAND, "forja", "banco", "password"))
# Aged rather than asked for in the past: allow_once clamps to at least a minute, on
# purpose, so the way an allowance dies is the clock.
V.allow_once(SAND, "forja", "banco")
_d = V._store(SAND)
_d["entries"]["forja/banco"]["allow_until"] = int(time.time()) - 1
V._save_store(SAND, _d)
denied("a go-ahead that has run out is no go-ahead",
       lambda: V.take(SAND, "forja", "banco", "password"))
ok("and the console can see one that is live",
   (V.allow_once(SAND, "forja", "banco") or {}).get("ok") and
   [e for e in V.entries(SAND, "forja") if e["platform"] == "banco"][0]["allowed_until"] > 0)
V.take(SAND, "forja", "banco", "password")


# ── changing and removing ────────────────────────────────────────────────────────────
section("changing one")

V.put(SAND, "heraldo", "google", {"password": ""}, label=u"Google (nuevo nombre)")
ok("saving with an empty box leaves the password alone",
   V.take(SAND, "heraldo", "google", "password") == SECRET)
ok("but the rest of the row is updated",
   V.entries(SAND, "heraldo")[0]["label"] == u"Google (nuevo nombre)")

V.put(SAND, "analecta", "x", {"password": "p"}, username="secreta@x.com",
      secret_username=True)
shown = [r for r in V.entries(SAND, "analecta")][0]
ok("an account the owner wants hidden is not shown either",
   shown["username"] == "" and shown["secret_username"] is True, shown)
ok("and is typed from the keystore like anything else",
   V.take(SAND, "analecta", "x", "username") == "secreta@x.com")

V.forget(SAND, "heraldo", "google")
ok("forgetting one takes the row away", not V.entries(SAND, "heraldo"))
denied("and the value with it",
       lambda: V.take(SAND, "heraldo", "google", "password"))


# ── the six digits ───────────────────────────────────────────────────────────────────
section("one-time codes")

# RFC 6238's own vectors, which is the only way to know this is the same algorithm the
# site is checking against. The seed there is "12345678901234567890" in base32.
VEC_SEED = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"
for when, expect in ((59, "94287082"), (1111111109, "07081804"),
                     (1234567890, "89005924"), (2000000000, "69279037")):
    got = V.totp(VEC_SEED, when=when, digits=8)
    ok("RFC 6238 vector at t=%d" % when, got == expect, got)

ok("a seed pasted with spaces and lower case still works",
   V.totp("jbswy3dp ehpk3pxp", when=59) == V.totp(SEED, when=59))
ok("the code is six digits by default",
   len(V.totp(SEED)) == 6 and V.totp(SEED).isdigit())
ok("and it says how long this one lasts", 1 <= V.seconds_left() <= 30)


# ── the record ───────────────────────────────────────────────────────────────────────
section("what gets written down")

rows = V.audit(SAND, limit=50)
ok("every save and every refusal is in the log", len(rows) >= 3, len(rows))
log_text = io.open(os.path.join(SAND, V.AUDIT), encoding="utf-8").read()
ok("and the log does not contain a single value",
   SECRET not in log_text and SEED not in log_text and "otra-cosa" not in log_text)
ok("it is per agent, so one agent's page is its own",
   all(r["agent"] == "forja" for r in V.audit(SAND, agent="forja")))


# ── the site it belongs to ───────────────────────────────────────────────────────────
section("a key only works on its own site")

for saved, page, want in (
        ("https://accounts.google.com/", "https://accounts.google.com/signin", True),
        ("https://google.com/", "https://accounts.google.com/x", True),
        ("https://accounts.google.com/", "https://google.com/", False),
        ("https://google.com/", "https://google.com.phish.tld/", False),
        ("https://google.com/", "https://notgoogle.com/", False),
        ("https://google.com/", "", False),
        ("https://banco.es/", "http://1.2.3.4/", False)):
    ok("%s on %s" % (F.host_of(saved) or "-", F.host_of(page) or "nothing"),
       F.same_site(saved, page) is want)


# ── and the filler obeys it before it unseals anything ───────────────────────────────
section("the filler")

V.put(SAND, "heraldo", "google", {"password": SECRET}, url="https://accounts.google.com/")

took = []
real_take = V.take
V.take = lambda *a, **k: (took.append(a) or real_take(*a, **k))


class FakeWS(object):
    """A DevTools socket that says what the test wants it to say."""
    sent = []

    def __init__(self, found):
        self.found = found

    def send(self, text):
        FakeWS.sent.append(text)

    def recv(self):
        import json as _json
        msg = _json.loads(FakeWS.sent[-1])
        if msg["method"] == "Runtime.evaluate":
            return _json.dumps({"id": msg["id"],
                                "result": {"result": {"value": self.found}}})
        return _json.dumps({"id": msg["id"], "result": {}})

    def close(self):
        pass


# Asked for a tab that is not open. Found against a real browser: the chooser fell back
# to the frontmost page, which is how a password gets typed into whatever happened to be
# in front of the agent.
_pages = [{"type": "page", "url": "https://accounts.google.com/",
           "webSocketDebuggerUrl": "ws://127.0.0.1:1/x"}]
F._targets = lambda port: list(_pages)
try:
    F._page(9222, "about:blank")
    ok("asking for a tab that is not open is refused", False, "it picked another")
except F.Refused as e:
    ok("asking for a tab that is not open is refused", True)
    ok("and names the page it could not find", "about:blank" in str(e), str(e))
ok("while asking for one that is open finds it",
   F._page(9222, "accounts.google")["url"].startswith("https://accounts.google"))
ok("and asking for nothing in particular takes the frontmost",
   F._page(9222, "")["url"] == _pages[0]["url"])


def run_fill(page_url, found, field="password"):
    FakeWS.sent = []
    F.browser_setup.port_for = lambda profile=None, **k: 9222
    F._page = lambda port, want="": {"url": page_url,
                                     "webSocketDebuggerUrl": "ws://127.0.0.1:1/x"}
    F.browser_label._WS = lambda url, timeout=0: FakeWS(found)
    return F.fill(SAND, "heraldo", "google", field)


took[:] = []
try:
    run_fill("https://accounts.evil-login.tld/", {"ok": True, "type": "password"})
    ok("a page on another site is refused", False, "it filled")
except F.Refused as e:
    ok("a page on another site is refused", True)
    ok("and says which site it expected", "google.com" in str(e), str(e))
ok("and the key is not even taken out of the keystore for it", not took, took)

took[:] = []
try:
    run_fill("https://accounts.google.com/signin", {"ok": True, "type": "text"})
    ok("a password into a visible box is refused", False, "it filled")
except F.Refused:
    ok("a password into a visible box is refused", True)
ok("and that one is not unsealed either", not took, took)

try:
    run_fill("https://accounts.google.com/signin", {"ok": False, "why": "no-field"})
    ok("a page with nowhere to type is refused", False, "it filled")
except F.Refused:
    ok("a page with nowhere to type is refused", True)

got = run_fill("https://accounts.google.com/signin",
               {"ok": True, "type": "password", "name": "Passwd"})
ok("the right page and the right box is filled", got["ok"] and got["field"] == "password")
typed = [m for m in FakeWS.sent if "Input.insertText" in m]
ok("the value goes in one insertText, not key by key", len(typed) == 1, len(typed))
ok("and it is the value", SECRET in typed[0])
ok("the answer to the caller never contains it", SECRET not in str(got), got)
last = V.audit(SAND, limit=1)[0]
ok("the use is written down with the page", last["what"] == "filled:password"
   and "accounts.google.com" in last["where"], last)
ok("and the line has no value in it", SECRET not in str(last))

V.take = real_take


# ── the tool the agent actually runs ───────────────────────────────
# Every agent on a machine runs the same scripts as the same user, so `--agent heraldo`
# is a request and not an identity. The identity comes from the workspace the process is
# standing in, which is the one thing that differs between them - and a flag that
# disagrees with it is worth refusing out loud, because the only reason to pass a name
# that is not yours is to use keys that are not yours.
section("which agent is asking")

sys.path.insert(0, os.path.join(ROOT, "src", "tools"))
import credentials as CLI  # noqa: E402

_ws_heraldo = os.path.join(SAND, "ws", "heraldo")
_ws_forja = os.path.join(SAND, "ws", "forja")
for _d in (_ws_heraldo, _ws_forja):
    os.makedirs(os.path.join(_d, "notas"), exist_ok=True)

CLI.install_dir = lambda: SAND
CLI.agents_registry.list_agents = lambda install_dir=None: [
    {"slug": "heraldo", "workspace": _ws_heraldo},
    {"slug": "forja", "workspace": _ws_forja},
]

_was = os.getcwd()
try:
    os.chdir(_ws_heraldo)
    ok("the agent is read from the workspace it runs in", CLI.whoami("") == "heraldo")
    os.chdir(os.path.join(_ws_heraldo, "notas"))
    ok("including from a folder inside it", CLI.whoami("") == "heraldo")
    ok("a flag that agrees is fine", CLI.whoami("heraldo") == "heraldo")
    _said = ""
    try:
        CLI.whoami("forja")
        ok("a flag naming somebody else is refused", False, "it was allowed")
    except SystemExit as e:
        _said = str(e)
        ok("a flag naming somebody else is refused", True)
    ok("and the refusal names both", "heraldo" in _said and "forja" in _said, _said)

    # ── standing somewhere else ──────────────────────────────────────────────────
    # A turn cds. The agent's process starts in its workspace, and the first time it
    # looks at a file anywhere else it is no longer standing there - at which point
    # the old check matched nothing and quietly answered "default", handing an agent
    # the MAIN agent's keys. Measured on a live install: in its own workspace Heraldo
    # saw none of its keys, and one folder away it saw the owner's two.
    #
    # The supervisor passes each agent its workspace as CLAUDE_BRIDGE_WORKSPACE and
    # the bridge starts the turn there. That says where the agent lives rather than
    # where its shell is standing, and no cd can change it.
    _elsewhere = os.path.join(SAND, "nowhere")
    os.makedirs(_elsewhere, exist_ok=True)
    os.chdir(_elsewhere)
    os.environ["CLAUDE_BRIDGE_WORKSPACE"] = _ws_heraldo
    try:
        ok("an agent that has cd-ed away is still itself",
           CLI.whoami("") == "heraldo")
        # The hole: with nothing matching, a stated --agent used to be honoured, so
        # the one case an agent can arrange for itself was the one that let it name
        # somebody else.
        try:
            CLI.whoami("default")
            ok("and it still may not claim to be the main agent", False, "allowed")
        except SystemExit:
            ok("and it still may not claim to be the main agent", True)
    finally:
        os.environ.pop("CLAUDE_BRIDGE_WORKSPACE", None)

    # With no workspace to go on at all - somebody running this by hand - the main
    # agent is the answer, but a flag still cannot turn that into somebody else.
    os.chdir(_elsewhere)
    ok("outside everything, it is the main agent", CLI.whoami("") == "default")
    try:
        CLI.whoami("heraldo")
        ok("and a flag cannot make it another agent", False, "allowed")
    except SystemExit:
        ok("and a flag cannot make it another agent", True)
    os.chdir(_ws_heraldo)

    # Nothing this tool prints may contain a value - there is no command that returns
    # one, and `list` is the one that comes closest.
    import contextlib
    V.put(SAND, "heraldo", "google", {"password": SECRET, "totp": SEED},
          url="https://accounts.google.com/", username="heraldo@igalenus.com")
    _out = io.StringIO()
    with contextlib.redirect_stdout(_out):
        CLI.main(["list"])
    _text = _out.getvalue()
    ok("list says the key is there", "google" in _text and "heraldo@igalenus.com" in _text)
    ok("and prints neither the password nor the seed",
       SECRET not in _text and SEED not in _text, _text[:120])
    ok("it names where the owner should put a new one",
       "consola" in _text.lower() or "Llaves" in _text, _text[:200])

    _out = io.StringIO()
    with contextlib.redirect_stdout(_out):
        _code = CLI.main(["fill", "banco", "password"])
    ok("asking for a key that is not there fails, with a sentence",
       _code == 1 and u"llave" in _out.getvalue(), _out.getvalue()[:120])
finally:
    os.chdir(_was)


# ── the other two systems ───────────────────────────────────────
# This suite runs on one machine and the product runs on three. A keychain command with
# a wrong flag fails the same way as "no keychain here", so the shapes are pinned: what
# is run, and - the part that matters - that the password goes in on STDIN and never as
# an argument, where every other process on the machine could read it out of the
# process list.
section("macOS and Linux, from here")

_ran = []


class _Fake(object):
    def __init__(self, out="", code=0):
        self.stdout, self.returncode, self.stderr = out, code, ""


def _fake_run(cmd, **kw):
    _ran.append((list(cmd), kw.get("input")))
    if cmd[0] == "security" and "find-generic-password" in cmd:
        return _Fake(SECRET + "\n")
    if cmd[0] == "secret-tool" and cmd[1] == "lookup":
        return _Fake(SECRET)
    return _Fake()


_saved_run, _saved_which = V.subprocess.run, V._which
_saved_platform = V.platform.system
try:
    V.subprocess.run = _fake_run
    V._which = lambda name: "/usr/bin/" + name

    # ── macOS ──────────────────────────────────────────────────────────────────
    V.platform.system = lambda: "Darwin"
    ok("a Mac is spotted by its keychain tool", V.keeper() == "keychain")
    _ran[:] = []
    V._seal(SAND, "heraldo/google/password", SECRET)
    cmd, sent = _ran[-1]
    ok("macOS: it is written with security(1)",
       cmd[:2] == ["security", "add-generic-password"], cmd)
    ok("and updates in place rather than piling up copies", "-U" in cmd, cmd)
    ok("and the password goes in on stdin, never in the command line",
       sent == SECRET and SECRET not in " ".join(cmd), cmd)
    ok("and the slot is this agent's, this site's, this field's",
       "heraldo/google/password" in cmd, cmd)
    ok("macOS: reading it back asks for the same slot",
       V._unseal(SAND, "heraldo/google/password") == SECRET, _ran[-1][0])
    _ran[:] = []
    V._unseal_forget(SAND, "heraldo/google/password")
    ok("macOS: forgetting one deletes it from the keychain",
       "delete-generic-password" in _ran[-1][0], _ran[-1][0])

    # ── Linux ──────────────────────────────────────────────────────────────────
    V.platform.system = lambda: "Linux"
    ok("a Linux box is spotted by secret-tool", V.keeper() == "libsecret")
    _ran[:] = []
    V._seal(SAND, "forja/banco/password", SECRET)
    cmd, sent = _ran[-1]
    ok("Linux: it is written with secret-tool",
       cmd[:2] == ["secret-tool", "store"], cmd)
    ok("and the password goes in on stdin there too",
       sent == SECRET and SECRET not in " ".join(cmd), cmd)
    ok("Linux: reading it back returns exactly what went in",
       V._unseal(SAND, "forja/banco/password") == SECRET)
    _ran[:] = []
    V._unseal_forget(SAND, "forja/banco/password")
    ok("Linux: forgetting one clears it", "clear" in _ran[-1][0], _ran[-1][0])

    # ── and a Linux box without it ─────────────────────────────────────────────
    V._which = lambda name: None
    ok("a Linux box with no keyring says so rather than guessing",
       V.keeper() is None)
    V.platform.system = lambda: "Darwin"
    ok("and so does a Mac whose security tool is missing", V.keeper() is None)
finally:
    V.subprocess.run, V._which = _saved_run, _saved_which
    V.platform.system = _saved_platform

ok("and this machine is still itself afterwards", V.keeper() == REAL_KEEPER or
   REAL_KEEPER is None)


def _read_sealed(install_dir):
    try:
        with io.open(os.path.join(install_dir, V.SEALED), encoding="utf-8") as fh:
            import json as _json
            return (_json.load(fh) or {}).get("values") or {}
    except Exception:  # noqa: BLE001
        return {}


# ── a machine with no keystore of its own ───────────────────────────
# A Linux server has no keyring daemon and often no desktop, and that is exactly the
# machine an agent runs on unattended. There, the owner sets a passphrase: the key is
# derived from it, lives in this process and nowhere else, and the disk gets a salt, a
# verifier and ciphertext.
#
# This is the only cipher code in the product that is ours rather than the operating
# system's, so it is tested as such: every refusal it should make, and the identity it
# should keep.
from wizard import vault_lock as L  # noqa: E402

section("a passphrase instead")

_bare = tempfile.mkdtemp(prefix="olivaw-bare-")
_saved_sys, _saved_which2 = V.platform.system, V._which
V.platform.system = lambda: "Linux"
V._which = lambda name: None       # no secret-tool, no keyring, nothing

try:
    ok("a bare machine says it has nowhere - until one is set",
       V.keeper(_bare) is None and V.can_set_passphrase(_bare) is True)
    ok("a short passphrase is refused",
       L.set_passphrase(_bare, "corta")["ok"] is False)
    ok("setting one works", L.set_passphrase(_bare, "una frase larga de prueba")["ok"])
    ok("and the machine now has a keeper", V.keeper(_bare) == "passphrase")
    ok("setting a second one is refused rather than losing the first",
       L.set_passphrase(_bare, "otra frase larga aun")["ok"] is False)

    V.put(_bare, "forja", "panel", {"password": SECRET},
          url="https://panel.example/")
    ok("a key saved under it comes back",
       V.take(_bare, "forja", "panel", "password") == SECRET)

    _blob = io.open(os.path.join(_bare, V.SEALED), encoding="utf-8").read()
    ok("and nothing readable is on the disk", SECRET not in _blob)
    ok("the file says which cipher wrote it, so a machine that gains a better one "
       "can still read this", '"alg"' in _blob)

    # ── the cipher itself ──────────────────────────────────────────────────────
    _key = os.urandom(32)
    for _n in (0, 1, 31, 32, 33, 1000):
        _data = os.urandom(_n)
        _box = L.seal(_key, _data, b"slot")
        ok("sealing and opening %d bytes gives back the same bytes" % _n,
           L.unseal(_key, _box, b"slot") == _data)

    # Key separation, asserted on the construction rather than on behaviour: using
    # one key for the stream cipher and for the MAC is a real weakness and a
    # perfectly consistent one - everything still seals and opens, and nothing
    # observable changes. The only way to catch it is to look.
    _m = os.urandom(32)
    ok("each job gets a key of its own from the master",
       len({L._sub(_m, b"enc"), L._sub(_m, b"mac"), L._sub(_m, b"verify"),
            L._sub(_m, b"aes")}) == 4)
    ok("and the master itself is never used as one of them",
       _m not in (L._sub(_m, b"enc"), L._sub(_m, b"mac")))

    _box = L.seal(_key, b"hola", b"slot")
    ok("two seals of the same thing are not the same ciphertext",
       L.seal(_key, b"hola", b"slot")["c"] != _box["c"])

    def _broken(what, change):
        bad = dict(_box)
        bad.update(change)
        try:
            L.unseal(_key, bad, b"slot")
            ok(what, False, "it opened")
        except L.Tampered:
            ok(what, True)

    import base64 as _b64mod
    _ct = bytearray(_b64mod.b64decode(_box["c"]))
    _ct[0] ^= 1
    _broken("a changed ciphertext does not open",
            {"c": _b64mod.b64encode(bytes(_ct)).decode()})
    if "t" in _box:
        _tag = bytearray(_b64mod.b64decode(_box["t"]))
        _tag[0] ^= 1
        _broken("a changed tag does not open",
                {"t": _b64mod.b64encode(bytes(_tag)).decode()})
    _broken("and neither does one from an unknown algorithm", {"alg": "rot13"})

    try:
        L.unseal(_key, _box, b"otro-slot")
        ok("a blob moved to another slot does not open", False, "it opened")
    except L.Tampered:
        ok("a blob moved to another slot does not open", True)
    try:
        L.unseal(os.urandom(32), _box, b"slot")
        ok("and another key does not open it either", False, "it opened")
    except L.Tampered:
        ok("and another key does not open it either", True)

    # ── locking ────────────────────────────────────────────────────────────────
    L.forget()
    ok("after a reboot the vault is closed", L.state(_bare)["unlocked"] is False)
    _why = denied("and an agent is told so, rather than given an error",
                  lambda: V.take(_bare, "forja", "panel", "password"))
    ok("in words it can repeat to the owner", u"contrase\u00f1a" in _why, _why)
    ok("the wrong passphrase does not open it",
       L.unlock(_bare, "no es esta")["ok"] is False)
    ok("the right one does", L.unlock(_bare, "una frase larga de prueba")["ok"])
    ok("and the keys work again",
       V.take(_bare, "forja", "panel", "password") == SECRET)

    # Held while it is being used, and not for ever: the owner unlocks in the morning
    # and a machine left alone overnight closes itself.
    ok("the key is held for hours, not minutes", L.HOLD_SECONDS >= 3600)
    L._held["until"] = time.time() - 1
    ok("and a held key that has aged out is gone", L.key() is None)
    L.unlock(_bare, "una frase larga de prueba")

    # ── changing it ────────────────────────────────────────────────────────────
    _old_blob = dict((_read_sealed(_bare) or {}).get("forja/panel/password") or {})
    ok("changing it needs the current one",
       L.change_passphrase(_bare, "la que no es", "otra frase larga aqui",
                           lambda a, b: None)["ok"] is False)
    ok("changing it works",
       L.change_passphrase(_bare, "una frase larga de prueba",
                           "otra frase larga aqui",
                           lambda a, b: V.reseal_all(_bare, a, b))["ok"])
    ok("every value was re-sealed under the new one",
       V.take(_bare, "forja", "panel", "password") == SECRET)
    ok("and the old ciphertext is not what is on disk any more",
       (_read_sealed(_bare) or {}).get("forja/panel/password") != _old_blob)
    L.forget()
    ok("the old passphrase no longer opens anything",
       L.unlock(_bare, "una frase larga de prueba")["ok"] is False)
    ok("the new one does", L.unlock(_bare, "otra frase larga aqui")["ok"])

    # ── and it never takes precedence over the real thing ──────────────────────
    V._which = lambda name: "/usr/bin/" + name
    ok("a machine that HAS a keyring uses it, passphrase or not",
       V.keeper(_bare) == "libsecret")
finally:
    V.platform.system, V._which = _saved_sys, _saved_which2
    L.forget()
    shutil.rmtree(_bare, ignore_errors=True)


# ── a machine with nowhere to keep them ──────────────────────────────────────────────
section("a machine with no keystore")

saved_keeper = V.keeper
V.keeper = lambda install_dir=None: None
try:
    V.put(SAND, "heraldo", "nope", {"password": "x"})
    ok("refuses to store rather than falling back to a file", False, "it stored it")
except V.NoKeeper:
    ok("refuses to store rather than falling back to a file", True)
V.keeper = saved_keeper

shutil.rmtree(SAND, ignore_errors=True)

print("\n%d passed, %d failed" % (len(PASSED), len(FAILED)))
for f in FAILED:
    print("  - " + f)
sys.exit(1 if FAILED else 0)
