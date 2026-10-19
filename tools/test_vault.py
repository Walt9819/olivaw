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
    V.keeper = lambda: "fake"
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


# ── a machine with nowhere to keep them ──────────────────────────────────────────────
section("a machine with no keystore")

saved_keeper = V.keeper
V.keeper = lambda: None
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
