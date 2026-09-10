r"""The brain's login expires. Say so before it bites, and make fixing it one click.

Why this exists
---------------
Both brains sign in with a subscription rather than an API key, and both of those sessions
expire. When one does, nothing announces it: the agent simply stops answering, on every
channel at once, and the owner is left with a bot that has gone quiet. The failure looks
identical to "the computer is off", "the internet is down" and "I broke something" - and
the actual repair is fifteen seconds of clicking.

So this module answers three questions the console had no way to ask:

  * is the brain signed in **right now**;
  * **when** does that stop being true;
  * and can we get it back without the owner opening a terminal.

What is read, and what is deliberately not
------------------------------------------
Claude Code keeps its session in ``~/.claude/.credentials.json``. Two fields there matter::

    claudeAiOauth.expiresAt              the ACCESS token - hours, refreshed automatically
    claudeAiOauth.refreshTokenExpiresAt  the REFRESH token - weeks, and the real deadline

The access token expiring is a non-event: the CLI renews it silently. The refresh token
expiring is the one that makes the owner log in again, so that is the date this reports.
Reporting the wrong one would cry wolf every few hours.

**Only those two timestamps are read.** The tokens sitting beside them are never read, never
returned, never logged. A status that leaked a credential to make a nicer dashboard would be
a bad trade at any price, and this file is imported by an HTTP server.

Codex is different: its CLI owns the answer (``codex login status``) and its on-disk shape
is not something this machine could verify, since Codex is not installed here. So Codex is
asked, not parsed, and any file-based expiry is best-effort and clearly marked as such -
guessing a date and being wrong is worse than saying "I do not know when".

Nothing here is trusted absolutely
----------------------------------
A CLI can report a healthy session that still fails on the next real request. The only
proof is a real turn through the bridge, which is what ``verify()`` runs after a login -
the same check the setup wizard uses. A green light here means "should work"; a green light
from verify() means "did work, just now".
"""

import io
import json
import os
import time

from .procutil import which

# How long before the deadline the console starts saying something. Long enough to act on
# without being nagged: the refresh token lasts weeks, so a few days is a gentle nudge, not
# an alarm.
WARN_DAYS = 5

STATES = ("ok", "expiring", "expired", "signed_out", "not_installed", "unknown")


def _home():
    return os.path.expanduser("~")


def _epoch(value):
    """A timestamp in seconds from a field that may be seconds or milliseconds."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    if v <= 0:
        return None
    return v / 1000.0 if v > 1e12 else v


# ── Claude Code ──────────────────────────────────────────────────────────────

def claude_credentials_path():
    return os.path.join(_home(), ".claude", ".credentials.json")


def claude_expiry():
    """When Claude Code's session really ends, from its own credential store.

    Returns {access, refresh} as epoch seconds, either possibly None. ONLY the two
    expiry timestamps are read out of that file; the tokens beside them are not touched.
    """
    out = {"access": None, "refresh": None, "source": ""}
    path = claude_credentials_path()
    try:
        with io.open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return out
    if not isinstance(data, dict):
        return out
    oauth = data.get("claudeAiOauth")
    if not isinstance(oauth, dict):
        return out
    out["access"] = _epoch(oauth.get("expiresAt"))
    out["refresh"] = _epoch(oauth.get("refreshTokenExpiresAt"))
    out["source"] = path if (out["access"] or out["refresh"]) else ""
    return out


def claude_status(exe=None):
    """Ask the Claude CLI. It answers in JSON, so this parses rather than greps.

    `claude auth status` prints {"loggedIn": true, "authMethod": ..., "email": ...}. The
    older substring check ("not logged in" in the blob) survives as a fallback for builds
    that print prose, but a JSON answer is believed over a guess about words.
    """
    from .procutil import run
    path = exe or which("claude")
    if not path:
        return {"found": False, "signed_in": False, "detail": "Claude Code no está instalado."}
    r = run([path, "auth", "status"], timeout=40)
    blob = ((r.get("out") or "") + "\n" + (r.get("err") or "")).strip()
    info = {}
    try:
        start = blob.index("{")
        info = json.loads(blob[start:blob.rindex("}") + 1])
    except (ValueError, IndexError):
        info = {}
    if isinstance(info, dict) and "loggedIn" in info:
        return {"found": True, "signed_in": bool(info.get("loggedIn")),
                "account": str(info.get("email") or ""),
                "plan": str(info.get("subscriptionType") or ""),
                "method": str(info.get("authMethod") or ""),
                "detail": ("Sesión de Claude activa." if info.get("loggedIn")
                           else "Claude Code no tiene sesión iniciada.")}
    low = blob.lower()
    signed = bool(r.get("ok")) and not any(w in low for w in
                                           ("not logged", "not signed", "no auth", "logged out"))
    return {"found": True, "signed_in": signed,
            "detail": ("Sesión de Claude activa." if signed
                       else "Claude Code no tiene sesión iniciada.")}


# ── Codex ────────────────────────────────────────────────────────────────────

def codex_home():
    env = (os.environ.get("CODEX_HOME") or "").strip()
    return env or os.path.join(_home(), ".codex")


def codex_expiry():
    """Best effort, and labelled as such.

    Codex is not installed on the machine this was written on, so its auth file's shape
    could not be verified. Rather than invent a field name, this looks for any epoch-shaped
    value under an expiry-ish key and reports `certain: False`. A dashboard that states a
    wrong date confidently is worse than one that admits it does not know.
    """
    out = {"refresh": None, "certain": False, "source": ""}
    path = os.path.join(codex_home(), "auth.json")
    try:
        with io.open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return out
    best = None

    def walk(node):
        nonlocal best
        if isinstance(node, dict):
            for k, v in node.items():
                if isinstance(v, (dict, list)):
                    walk(v)
                elif any(w in k.lower() for w in ("expire", "expiry", "exp_at", "expires")):
                    ts = _epoch(v)
                    if ts and (best is None or ts > best):
                        best = ts
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(data)
    if best:
        # `certain` means "a date was actually read out of a file", which is what decides
        # whether the dashboard may show one at all. It is NOT a claim that the field name
        # is the right one - that caveat is the docstring's, and the reason this looks for
        # any expiry-shaped key rather than one it made up. Leaving this False while
        # returning a date, as an earlier version did, made the flag a lie the caller could
        # not detect: every consumer had to re-derive it from `refresh` anyway.
        out.update(refresh=best, certain=True, source=path)
    return out


def codex_status():
    try:
        import codex_engine
    except Exception:  # noqa: BLE001
        return {"found": False, "signed_in": False,
                "detail": "Esta versión de Olivaw no trae el motor de Codex."}
    st = codex_engine.login_status()
    return {"found": bool(st.get("found")), "signed_in": bool(st.get("signed_in")),
            "detail": st.get("detail", "")}


# ── the verdict ──────────────────────────────────────────────────────────────

def _days(ts, now=None):
    if not ts:
        return None
    return (ts - (now or time.time())) / 86400.0


def status(engine=None, install_dir=None, now=None):
    """Is this brain's login good, and for how much longer.

    Returns a dict with `state` in STATES, a human `detail`, and `needs_login` - the single
    flag the dashboard keys its alert off, so the decision lives here rather than being
    re-derived by a browser that has less to go on.
    """
    from . import rescue
    engine = (engine or rescue.configured_engine(install_dir) or "claude").strip().lower()
    label = rescue.engine_label(engine)
    now = now or time.time()

    if engine == "codex":
        live = codex_status()
        exp = codex_expiry()
        days = _days(exp.get("refresh"), now)
        certain = bool(exp.get("refresh"))
    else:
        live = claude_status()
        exp = claude_expiry()
        days = _days(exp.get("refresh"), now)
        certain = bool(exp.get("refresh"))

    out = {
        "engine": engine, "label": label,
        "found": bool(live.get("found")),
        "signed_in": bool(live.get("signed_in")),
        "account": live.get("account", ""),
        "plan": live.get("plan", ""),
        "expires_at": exp.get("refresh"),
        "expires_in_days": None if days is None else round(days, 2),
        "expiry_known": certain,
        "warn_days": WARN_DAYS,
        # The access token is reported for the diagnostics panel only. It is NOT a reason to
        # alarm anyone: it lasts hours and the CLI renews it by itself, so treating it as an
        # expiry would put a red banner on a perfectly healthy machine every morning.
        "access_expires_at": exp.get("access"),
    }

    if not live.get("found"):
        out.update(state="not_installed", needs_login=False,
                   detail="%s no está instalado en este equipo." % label)
        return out
    if not live.get("signed_in"):
        out.update(state="signed_out", needs_login=True,
                   detail="%s ha cerrado la sesión. Tu agente no puede pensar hasta que "
                          "vuelvas a entrar." % label)
        return out
    if days is not None and days <= 0:
        out.update(state="expired", needs_login=True,
                   detail="La sesión de %s caducó. Tu agente no puede pensar hasta que "
                          "vuelvas a entrar." % label)
        return out
    if days is not None and days <= WARN_DAYS:
        out.update(state="expiring", needs_login=False,
                   detail="La sesión de %s caduca %s. Puedes renovarla ahora y evitar que "
                          "tu agente se quede mudo." % (label, _when(days)))
        return out
    out.update(state="ok", needs_login=False,
               detail=("%s tiene sesión%s%s." % (
                   label,
                   (" de %s" % live["account"]) if live.get("account") else "",
                   (", caduca %s" % _when(days)) if days is not None else "")))
    return out


def _when(days):
    """A deadline in words. Nobody reads '4.87 days'."""
    if days is None:
        return ""
    if days < 0:
        return "ya caducó"
    if days < 1 / 24.0:
        return "en menos de una hora"
    if days < 1:
        return "hoy"
    if days < 2:
        return "mañana"
    return "en %d días" % int(days)


# ── fixing it ────────────────────────────────────────────────────────────────

def login(engine=None, install_dir=None, paths=None):
    """Start the brain's own sign-in. Opens ITS flow; we never handle a credential.

    Both CLIs open a browser and complete the exchange themselves, so Olivaw's part is to
    launch the right one and then watch for it to finish. Deliberately so: an installer that
    asked for the password itself would be the wrong shape, and the standing rule here is
    that Olivaw never touches a credential it does not have to.
    """
    from . import providers, rescue
    engine = (engine or rescue.configured_engine(install_dir) or "claude").strip().lower()
    for p in providers.all_providers():
        if p.engine == engine and p.status == "ready":
            res = p.login(dict(paths or {}))
            res.setdefault("engine", engine)
            res.setdefault("detail",
                           "Abrí la ventana de inicio de sesión. Termínala y vuelve aquí.")
            return res
    return {"ok": False, "engine": engine,
            "detail": "No sé cómo iniciar sesión con ese cerebro."}


def verify(engine=None, install_dir=None, base_url=None, deep=True):
    """After a login: is it really fixed?

    Two levels, and the difference matters. The CLI saying "signed in" means the file on
    disk looks right. A real turn through the bridge means a request was actually accepted -
    which is the thing the owner cares about and the only claim worth making. So the deep
    check runs the same end-to-end test the setup wizard uses, and its verdict wins.
    """
    from . import checks, rescue
    st = status(engine, install_dir)
    out = {"session": st, "engine": st["engine"], "label": st["label"]}
    if st["state"] in ("signed_out", "expired", "not_installed"):
        out.update(ok=False, tested=False, detail=st["detail"])
        return out
    if not deep:
        out.update(ok=True, tested=False,
                   detail="La sesión de %s está activa." % st["label"])
        return out
    url = base_url or _bridge_url(install_dir)
    if not url:
        out.update(ok=True, tested=False,
                   detail="La sesión está activa. No pude probar el puente porque este "
                          "equipo no tiene uno configurado.")
        return out
    t = checks.test_brain(url, timeout=180, brain=st["label"], engine=st["engine"])
    out["test"] = t
    out["tested"] = True
    out["ok"] = bool(t.get("ok"))
    out["detail"] = ("Listo: %s tiene sesión y tu agente volvió a responder."
                     % st["label"] if t.get("ok") else t.get("detail", ""))
    return out


def _bridge_url(install_dir=None):
    from . import rescue
    try:
        with io.open(os.path.join(install_dir or rescue.INSTALL_DIR,
                                  "updater.config.json"), encoding="utf-8") as fh:
            return (json.load(fh) or {}).get("bridge_url") or ""
    except (OSError, ValueError):
        return "http://127.0.0.1:8790"
