r"""Talk to your own agent from the browser, not only from Telegram.

Why this needed a real answer rather than a text box
----------------------------------------------------
Olivaw already has a bridge on 127.0.0.1:8790 that speaks OpenAI's chat API, and pointing a
chat box at it would have been two hours' work. It would also have been a lie: that bridge
is the *brain*, reached before Hermes exists. It has no persona, no memory, no skills, no
tools, no conversation. The owner would be talking to a raw model that shares nothing with
the agent they know from Telegram, and every difference would look like a bug.

Hermes ships the right door: the ``api_server`` platform, an OpenAI-compatible HTTP server
that runs a full agent turn - persona, memory, skills, tools, sessions - exactly as a
Telegram message does. So "talk from the UI" means enabling that platform and proxying to
it, and what the owner types reaches the same agent by the same path as everything else.

The security model, which is Hermes' own and worth stating
----------------------------------------------------------
This endpoint dispatches terminal-capable agent work, so it is not a casual thing to open:

* it binds **127.0.0.1 only** (Hermes' ``DEFAULT_HOST``), so nothing off this machine can
  reach it;
* Hermes **refuses to start it without a strong ``API_SERVER_KEY``** - it rejects
  placeholders and anything under 16 characters, with the reasoning written into its own
  source: a guessable key here is remote code execution. We generate 64 hex characters;
* the key lives in the profile's ``.env`` and **never reaches the browser**. The page talks
  to the wizard, which is itself loopback-bound and requires its own one-time token; the
  wizard adds the bearer header server-side.

That is why this module proxies instead of handing the page a key and a port.

One deliberate product choice
-----------------------------
A conversation started here is its OWN conversation, not a continuation of the Telegram
thread. Hermes keys sessions per platform and chat, so joining them would mean forging a
Telegram session id - and it would put whatever the owner types in the browser into the
history the agent replays on Telegram. Separate is both simpler and the honest shape; the
UI says so rather than letting the owner guess.
"""

import io
import json
import os
import re
import secrets
import socket
import urllib.error
import urllib.parse
import urllib.request

from . import hermes_ctl

PLATFORM = "api_server"
KEY_ENV = "API_SERVER_KEY"
DEFAULT_PORT = 8642          # Hermes' own default
PORT_SPAN = 40
HOST = "127.0.0.1"
KEY_BYTES = 32               # 64 hex chars, far above Hermes' 16-char floor

_SESSION_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")


def hermes_home():
    env = os.environ.get("HERMES_HOME")
    if env:
        return env
    local = os.environ.get("LOCALAPPDATA")
    if local and os.path.isdir(os.path.join(local, "hermes")):
        return os.path.join(local, "hermes")
    return os.path.join(os.path.expanduser("~"), ".hermes")


def profile_home(profile=None):
    if not profile or profile == "default":
        return hermes_home()
    return os.path.join(hermes_home(), "profiles", profile)


def _read_env(path):
    out = {}
    try:
        with io.open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    out[k.strip()] = v.strip().strip('"').strip("'")
    except OSError:
        pass
    return out


def env_of(profile=None, hermes=None):
    path = ""
    try:
        path = hermes_ctl.env_path(hermes, profile)
    except Exception:  # noqa: BLE001
        pass
    return _read_env(path or os.path.join(profile_home(profile), ".env"))


def _port_free(port):
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind((HOST, port))
        return True
    except OSError:
        return False


def _configured_port(profile=None, hermes=None):
    """The port this profile's api_server is set to, if any."""
    raw = hermes_ctl.config_get("platforms.%s.extra.port" % PLATFORM, hermes, profile)
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        env = env_of(profile, hermes).get("API_SERVER_PORT", "")
        try:
            return int(env)
        except (TypeError, ValueError):
            return None


def _taken_ports(hermes=None, install_dir=None):
    """Ports already handed to another agent's api_server, so two never collide."""
    taken = set()
    try:
        from . import agents_registry
        profiles = [None] + [a.get("profile") or a.get("slug")
                             for a in agents_registry.list_agents(install_dir)]
    except Exception:  # noqa: BLE001
        profiles = [None]
    for prof in profiles:
        p = _configured_port(prof, hermes)
        if p:
            taken.add(p)
    return taken


def pick_port(profile=None, hermes=None, install_dir=None):
    """This profile's port if it has one, else the first free one nobody claims."""
    mine = _configured_port(profile, hermes)
    if mine:
        return mine
    taken = _taken_ports(hermes, install_dir)
    for port in range(DEFAULT_PORT, DEFAULT_PORT + PORT_SPAN):
        if port not in taken and _port_free(port):
            return port
    return None


def _http(url, key, data=None, method=None, timeout=25):
    """One request to the agent's own API server. Returns (ok, parsed_or_text, status).

    The bearer key is added HERE and nowhere the browser can see. Never logged, never
    echoed into a result, and never placed in a URL.
    """
    body = None
    headers = {"Accept": "application/json", "User-Agent": "olivaw-wizard"}
    if key:
        headers["Authorization"] = "Bearer " + key
    if data is not None:
        body = json.dumps(data).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8", "replace")
            try:
                return True, json.loads(raw), r.status
            except json.JSONDecodeError:
                return True, raw, r.status
    except urllib.error.HTTPError as e:
        raw = ""
        try:
            raw = e.read().decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            pass
        try:
            return False, json.loads(raw), e.code
        except Exception:  # noqa: BLE001
            return False, raw or str(e), e.code
    except Exception as e:  # noqa: BLE001
        return False, str(e), 0


def base_url(profile=None, hermes=None):
    port = _configured_port(profile, hermes) or DEFAULT_PORT
    return "http://%s:%d" % (HOST, port)


# ── turning it on ────────────────────────────────────────────────────────────

def enable(profile=None, hermes=None, install_dir=None, log=None):
    """Enable the agent's own API server, on loopback, with a strong generated key.

    Idempotent: an existing key is REUSED rather than rotated. Rotating it on every call
    would invalidate any client the owner has pointed at their agent, and would do so
    silently, from a button whose label says nothing about keys.
    """
    port = pick_port(profile, hermes, install_dir)
    if port is None:
        return {"ok": False, "detail": "No encontré un puerto libre para hablar con el agente."}
    key = env_of(profile, hermes).get(KEY_ENV, "").strip()
    fresh = False
    if len(key) < 32:            # covers absent, placeholder and anything Hermes would reject
        key = secrets.token_hex(KEY_BYTES)
        fresh = True
    steps = []

    def step(name, res):
        steps.append({"name": name, "ok": bool(res.get("ok")), "detail": res.get("detail", "")})

    if fresh:
        step("clave", hermes_ctl.set_env_vars({KEY_ENV: key}, hermes, profile))
    step("puerto", hermes_ctl.config_set("platforms.%s.extra.port" % PLATFORM, str(port),
                                         hermes, profile))
    # Loopback is written explicitly rather than left to Hermes' default: the default is
    # right today, and a config file that SAYS 127.0.0.1 is the difference between "we are
    # sure" and "we assume" for something that dispatches terminal-capable work.
    step("host", hermes_ctl.config_set("platforms.%s.extra.host" % PLATFORM, HOST,
                                       hermes, profile))
    step("activar", hermes_ctl.config_set("platforms.%s.enabled" % PLATFORM, "true",
                                          hermes, profile))
    ok = all(s["ok"] for s in steps)
    if log and ok:
        log("talk: %s can be reached at %s:%d (key %s)"
            % (profile or "default", HOST, port, "generated" if fresh else "kept"))
    return {"ok": ok, "port": port, "steps": steps, "key_generated": fresh,
            "detail": ("Listo. Falta reiniciar el agente para que empiece a escuchar."
                       if ok else "No pude configurarlo del todo.")}


def disable(profile=None, hermes=None, log=None):
    """Turn it off. The key is LEFT in place so turning it back on keeps the same one."""
    r = hermes_ctl.config_set("platforms.%s.enabled" % PLATFORM, "false", hermes, profile)
    if log and r.get("ok"):
        log("talk: %s no longer listens for browser chat" % (profile or "default"))
    return {"ok": bool(r.get("ok")), "detail": r.get("detail", "")}


def status(profile=None, hermes=None, install_dir=None):
    """Configured? Listening? Answering? Three different questions, three answers.

    A UI that collapses them says "not available" for a gateway that simply has not
    restarted yet, which is the state right after the owner enables it.
    """
    key = env_of(profile, hermes).get(KEY_ENV, "").strip()
    enabled = (hermes_ctl.config_get("platforms.%s.enabled" % PLATFORM, hermes, profile)
               or "").strip().lower() in ("true", "1", "yes", "on")
    port = _configured_port(profile, hermes)
    out = {"ok": True, "enabled": enabled, "configured": bool(key) and bool(port),
           "port": port, "has_key": bool(key), "reachable": False, "ready": False}
    if not (enabled and key and port):
        out["detail"] = ("Aún no está activado. Actívalo y podrás escribirle desde aquí, "
                         "igual que por Telegram." if not enabled else
                         "Falta configurarlo del todo.")
        return out
    ok, data, code = _http("http://%s:%d/health" % (HOST, port), key, timeout=5)
    out["reachable"] = bool(ok)
    if not ok and code == 0:
        out["detail"] = ("Está configurado pero el agente todavía no escucha. Se activa al "
                         "reiniciarse, normalmente en menos de un minuto.")
        return out
    if code == 401:
        # Only reachable if the running gateway holds a DIFFERENT key than the .env we just
        # read - i.e. it started before the key was written. Naming that beats "unauthorized".
        out["detail"] = ("El agente escucha pero con otra clave: arrancó antes del último "
                         "cambio. Se arregla en cuanto se reinicie.")
        return out
    out["ready"] = bool(ok)
    out["detail"] = ("Puedes escribirle desde aquí." if ok else
                     "Responde algo inesperado en %s:%d." % (HOST, port))
    if isinstance(data, dict) and data.get("model"):
        out["model"] = data["model"]
    return out


# ── talking ──────────────────────────────────────────────────────────────────

def _require(profile, hermes):
    key = env_of(profile, hermes).get(KEY_ENV, "").strip()
    port = _configured_port(profile, hermes)
    if not key or not port:
        return None, None, {"ok": False,
                            "detail": "Todavía no está activado para este agente."}
    return key, port, None


def _safe_session(sid):
    return bool(sid) and _SESSION_RE.match(str(sid)) is not None


def sessions(profile=None, hermes=None, limit=20):
    key, port, err = _require(profile, hermes)
    if err:
        return err
    ok, data, code = _http("http://%s:%d/api/sessions?limit=%d" % (HOST, port, int(limit)), key)
    if not ok:
        return {"ok": False, "detail": _explain(code, data)}
    rows = data.get("data") if isinstance(data, dict) else data
    return {"ok": True, "sessions": rows if isinstance(rows, list) else []}


def create(profile=None, hermes=None, title=""):
    key, port, err = _require(profile, hermes)
    if err:
        return err
    url = "http://%s:%d/api/sessions" % (HOST, port)
    payload = {"title": title[:120]} if title else {}
    ok, data, code = _http(url, key, data=payload, method="POST")
    # Hermes requires session titles to be UNIQUE and answers 400 when one is reused. A
    # title is a nicety here - the conversation works perfectly without one - so losing the
    # title beats losing the conversation. Learned from the live server: the second call
    # with the same title failed while the first had already created a session.
    if not ok and payload and code == 400 and "already in use" in str(data):
        ok, data, code = _http(url, key, data={}, method="POST")
    if not ok:
        return {"ok": False, "detail": _explain(code, data)}
    sid = _session_id(data)
    return {"ok": bool(sid), "session_id": sid, "session": (data or {}).get("session") or data,
            "detail": "" if sid else
                      "El agente creó la conversación pero no devolvió un identificador."}


def _session_id(data):
    """The id out of a create/read response.

    Hermes nests it: ``{"object": "hermes.session", "session": {"id": ...}}``. Read off the
    live server rather than guessed - the first version looked for a top-level ``id``,
    which parsed fine, found nothing, and reported "ok: False, detail: None". The flat
    forms are kept as fallbacks in case the shape moves again.
    """
    if not isinstance(data, dict):
        return ""
    inner = data.get("session")
    if isinstance(inner, dict) and inner.get("id"):
        return str(inner["id"])
    return str(data.get("id") or data.get("session_id") or "")


def history(profile=None, hermes=None, session_id=""):
    if not _safe_session(session_id):
        return {"ok": False, "detail": "Conversación no válida."}
    key, port, err = _require(profile, hermes)
    if err:
        return err
    ok, data, code = _http("http://%s:%d/api/sessions/%s/messages"
                           % (HOST, port, urllib.parse.quote(session_id, safe="")), key)
    if not ok:
        return {"ok": False, "detail": _explain(code, data)}
    rows = data.get("data") if isinstance(data, dict) else data
    return {"ok": True, "messages": rows if isinstance(rows, list) else []}


def send(profile=None, hermes=None, session_id="", text="", timeout=600):
    """One agent turn. Long timeout on purpose - a real turn can use tools for minutes."""
    if not _safe_session(session_id):
        return {"ok": False, "detail": "Conversación no válida."}
    if not (text or "").strip():
        return {"ok": False, "detail": "Escribe algo primero."}
    key, port, err = _require(profile, hermes)
    if err:
        return err
    ok, data, code = _http(
        "http://%s:%d/api/sessions/%s/chat" % (HOST, port,
                                               urllib.parse.quote(session_id, safe="")),
        key, data={"message": text}, method="POST", timeout=timeout)
    if not ok:
        return {"ok": False, "detail": _explain(code, data)}
    msg = ((data or {}).get("message") or {}).get("content", "")
    return {"ok": True, "reply": msg, "session_id": (data or {}).get("session_id") or session_id,
            "usage": (data or {}).get("usage")}


def _explain(code, data):
    """Turn an HTTP failure into something the owner can act on."""
    if code == 0:
        return ("El agente dejó de escuchar. Suele ser un reinicio en curso; espera unos "
                "segundos y vuelve a intentar.")
    if code == 401:
        return ("El agente arrancó con otra clave. Se arregla al reiniciarse; si insiste, "
                "vuelve a activarlo aquí.")
    if code == 404:
        return "Esa conversación ya no existe."
    detail = ""
    if isinstance(data, dict):
        detail = str(((data.get("error") or {}) if isinstance(data.get("error"), dict)
                      else {}).get("message") or data.get("detail") or "")
    elif isinstance(data, str):
        detail = data[:200]
    return ("El agente respondió con un error (%s)%s"
            % (code, (": " + detail) if detail else "."))
