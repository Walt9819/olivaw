r"""
Is Telegram actually working for this agent — and if not, exactly why.

Written after an install where the wizard reported success and the owner got silence. Hermes had
the token, the gateway started, and Telegram rejected the token as revoked; the gateway exited with
"non-retryable startup conflict". Nothing in Olivaw said so. Finding it meant reading Hermes' own
profile logs, which is precisely what this kit exists to spare people.

Four things can be true at once and each fails differently, so each is checked separately against
the thing that actually decides it:

  * the token in the PROFILE's .env (not the one typed into the wizard ten minutes ago - a token
    can be revoked in between, and BotFather revokes the old one when you generate a new one);
  * whether Telegram itself accepts that token, right now (getMe);
  * whether a webhook is set, because that silently stops polling from ever seeing a message;
  * whether the gateway is running, and what its log says about the last attempt.

The token is used only to ask Telegram about itself. It is never returned, logged or echoed.
"""

import os
import re
import socket
import ssl

from . import hermes_ctl
from .procutil import http_json

API = "https://api.telegram.org/bot{token}/{method}"
API_HOST = "api.telegram.org"

# Proxy settings, by their documented names. Their PRESENCE is the diagnosis; their value can
# carry a username and password, so it is never printed.
_PROXY_VARS = ("HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY",
               "https_proxy", "http_proxy", "all_proxy")


def proxies_configured():
    return sorted({v.upper() for v in _PROXY_VARS if (os.environ.get(v) or "").strip()})


def tls_probe(host=API_HOST, port=443, timeout=8):
    """Did the request fail because the CERTIFICATE was rejected, or because of the network?

    They look identical from urllib - both surface as "could not connect" - and they need
    opposite advice. A corporate proxy that inspects TLS presents its own root certificate;
    Python does not trust it, refuses the handshake, and the request never reaches Telegram.
    Telling that owner "check your internet, then re-check the token" sends them round a loop
    they cannot get out of, because their internet is fine and their token was never seen.

    Runs only after something has already failed, so the ordinary path pays nothing for it.

    Deliberately does NOT fall back to an unverified context, not even to read the
    intercepting certificate for a nicer message: this codebase has no way to turn off
    certificate verification, and that is a property worth being able to test for.
    """
    ctx = ssl.create_default_context()
    try:
        with socket.create_connection((host, port), timeout) as raw:
            with ctx.wrap_socket(raw, server_hostname=host):
                return {"state": "ok"}
    except ssl.SSLCertVerificationError as e:
        return {"state": "tls", "reason": getattr(e, "verify_message", "") or str(e)[:200]}
    except ssl.SSLError as e:
        return {"state": "tls", "reason": str(e)[:200]}
    except socket.gaierror:
        return {"state": "dns"}
    except (socket.timeout, TimeoutError):
        return {"state": "timeout"}
    except OSError as e:
        return {"state": "network", "reason": str(e)[:200]}

# What Hermes writes when each of these things happens. Matching its own words is what lets the
# verdict name a cause instead of "the gateway crashed".
SIGNS = {
    "rejected": re.compile(r"token .{0,40}was rejected|token rejected|Unauthorized", re.I),
    "connected": re.compile(r"Connected to Telegram|polling confirmed healthy", re.I),
    "conflict": re.compile(r"non-retryable startup conflict", re.I),
    "double": re.compile(r"Another gateway instance .{0,40}started during our startup", re.I),
    # Hermes-on-Windows noise. Loud, alarming, and harmless - worth naming so nobody (person or
    # model) spends an afternoon chasing it while the real cause sits two lines above.
    "unix_watchdog": re.compile(r"start_unix_server", re.I),
    "dispatcher_lock": re.compile(r"dispatcher lock|another gateway owns", re.I),
}

_KEYS = ("TELEGRAM_BOT_TOKEN", "TELEGRAM_ALLOWED_USERS", "TELEGRAM_HOME_CHANNEL")


def _read_env(path):
    out = {}
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip()
    except Exception:  # noqa: BLE001
        pass
    return out


def _api(token, method, timeout=20):
    """Returns (ok, data, status). The status matters: 401 is Telegram REFUSING the token, while
    no status at all means we never reached Telegram - and telling someone their token was
    revoked when their wifi is down sends them to BotFather to fix a router.

    Twenty seconds, not fifteen: Telegram holds a request for a bot it cannot serve and then
    answers 502 at about sixteen. At fifteen that verdict arrived as a timeout, which reads
    as "no internet" - on a machine whose other six bots were answering in half a second.
    """
    ok, data, status = http_json(API.format(token=token, method=method), timeout=timeout)
    if isinstance(data, dict) and "ok" in data:
        return data.get("ok", False), data, status
    return ok, {"ok": False, "description": str(data)[:200]}, status


# Shaped like a token, belongs to nobody. Telegram answers 401 "invalid token specified" for
# it in about half a second whenever the API is serving this machine.
_NO_TOKEN = "0:0"


def api_reachable(timeout=10):
    """Is the Telegram API answering THIS computer at all, without using anybody's token?

    The discriminator the diagnosis was missing. When a real token gets 502 or no answer, the
    question is whether the fault is here (network, DNS, an inspecting proxy, a firewall) or
    there. A deliberately invalid bot id settles it: any HTTP status at all means the request
    reached Telegram and came back, so the network is fine and the silence belongs to that one
    bot. No token is sent, so this is safe to run on any failure path.
    """
    _ok, _data, status = http_json(API.format(token=_NO_TOKEN, method="getMe"), timeout=timeout)
    return bool(status)


_LOG_NAMES = ("gateway.log", "gateway-stdio.log", "gateway-exit-diag.log")


def _logs_under(bases):
    out = []
    for b in bases:
        for name in _LOG_NAMES:
            p = os.path.join(b, "logs", name)
            if os.path.isfile(p):
                out.append(p)
    return out


def log_paths(profile=None):
    """Where this profile's gateway writes. Named in the result so a person (or the SOS console)
    can go straight there instead of hunting.

    An agent with logs of its own gets ONLY those. The shared ones used to be appended as a
    fallback and were then read as if they were his: scan_logs ORs its signals across every
    file it is given, so the main agent's "Connected to Telegram" became proof that a silent
    agent had connected, and the main agent's token error became his to fix. The fallback is
    still there for an agent that has never written a log, where the shared file is the only
    account of the start that failed.
    """
    local = os.environ.get("LOCALAPPDATA", "")
    home = os.path.expanduser("~")
    bases = [os.path.join(local, "hermes"), os.path.join(home, ".hermes")]
    if profile and profile != "default":
        own = _logs_under([os.path.join(b, "profiles", profile) for b in bases])
        if own:
            return own
    return _logs_under(bases)


def _tail(path, chars=20000):
    try:
        size = os.path.getsize(path)
        with open(path, encoding="utf-8", errors="replace") as fh:
            if size > chars:
                fh.seek(size - chars)
            return fh.read()
    except Exception:  # noqa: BLE001
        return ""


# A structured gateway line: timestamp, level, logger, message. Anything that does not match
# is a traceback continuation or free text, and does not go on screen.
_LOG_LINE = re.compile(
    r"^(\d{4}-\d\d-\d\d[ T]\d\d:\d\d:\d\d)[,.]?\d* +"
    r"(DEBUG|INFO|WARNING|ERROR|CRITICAL) +([\w.\[\]_-]+): +(.*)$")
_SHOWN = ("WARNING", "ERROR", "CRITICAL")
# Belt and braces: a token can only get into a log through somebody else's mistake, but this
# is the one place a log is shown on a screen, so it is also the place to catch it.
_TOKENISH = re.compile(r"\b\d{6,12}:[A-Za-z0-9_-]{30,}")


def _source(logger):
    """`hermes_plugins.telegram_platform.adapter` -> `Telegram`. The owner is reading this to
    find out which part is unhappy, and the dotted path is an answer for somebody else."""
    low = (logger or "").lower()
    for word, label in (("telegram", "Telegram"), ("whatsapp", "WhatsApp"),
                        ("api_server", "esta pantalla"), ("http", "esta pantalla")):
        if word in low:
            return label
    return "motor" if "gateway" in low else (logger or "").split(".")[-1]


def log_digest(profile=None, limit=40, chars=60000):
    """The last diagnostic lines of this agent's gateway, for reading inside Olivaw.

    Only the levels that are a diagnosis, and only lines whose shape says they came from a
    logger rather than from a message. The owner's conversations pass through this gateway;
    "show me the log" must not become a way to read them back out of a panel, so the filter
    is a whitelist of structure and level, never a blacklist of what looks private.
    """
    out, files = [], []
    for p in log_paths(profile):
        if p.endswith("-exit-diag.log"):      # JSON records of starts and exits, not lines
            continue
        files.append(p)
        for raw in _tail(p, chars).splitlines():
            m = _LOG_LINE.match(raw.strip())
            if not m:
                continue
            ts, level, logger, msg = m.groups()
            if level not in _SHOWN and not SIGNS["connected"].search(msg):
                continue
            out.append({"ts": ts, "level": level, "logger": _source(logger),
                        "text": _TOKENISH.sub("«token»", msg)[:300]})
    out.sort(key=lambda r: r["ts"])
    return {"lines": out[-max(1, limit):], "files": files, "total": len(out)}


def scan_logs(profile=None, chars=20000):
    """What the last gateway start says about Telegram."""
    found = {k: False for k in SIGNS}
    quotes = {}
    for p in log_paths(profile):
        blob = _tail(p, chars)
        for key, rx in SIGNS.items():
            m = rx.search(blob)
            if m:
                found[key] = True
                if key not in quotes:
                    line = blob[max(0, blob.rfind("\n", 0, m.start()) + 1):
                                blob.find("\n", m.end()) if blob.find("\n", m.end()) > 0 else None]
                    quotes[key] = line.strip()[:300]
    return found, quotes


# States where waiting longer cannot help: the answer will not change on its own.
#
# The three telegram_* states are here for a second reason. Each costs a request Telegram
# holds for sixteen seconds before refusing it, so a poll that "waits for it to settle"
# spends half a minute confirming what the first answer already said. A bot Telegram is not
# serving is not a handshake in progress.
TERMINAL = ("token_rejected", "webhook_set", "no_token", "unreachable",
            "unreachable_tls", "unreachable_network",
            "telegram_down", "telegram_busy", "telegram_slow")


def wait_for_connection(profile=None, hermes=None, seconds=30):
    """Poll until Telegram is really connected (or the failure is one that waiting cannot fix).

    A gateway needs a few seconds after a restart to finish its handshake, and the wizard used to
    declare success the instant it had written the config - which is how an owner ended up with a
    green screen and a bot that never answered.
    """
    import time as _time
    deadline = _time.time() + max(1, seconds)
    last = None
    while True:
        last = check(profile, hermes)
        if last.get("ok") or last.get("state") in TERMINAL:
            return last
        if _time.time() >= deadline:
            return last
        _time.sleep(2)


# The advice that must never be given by mistake. BotFather invalidates the old token the
# moment it issues the next one, so "generate a new token" is a one-way door: follow it
# against a bot Telegram is merely unwell about, and the working token is gone and the new
# one meets the identical 502. Only Telegram SAYING NO to the credential justifies it.
_DEAD_TOKEN = (400, 401, 403, 404)


def _refused(res, profile, status, data):
    """Telegram answered, and the answer was not yes. Which kind of no is the whole question."""
    why = (data.get("description") or "") if isinstance(data, dict) else ""
    said = (" (%s)" % why) if why else ""
    if status in _DEAD_TOKEN:
        res.update(state="token_rejected",
                   detail="Telegram RECHAZÓ el token del perfil «%s»%s. Suele pasar cuando el "
                          "token fue revocado (BotFather invalida el anterior al generar uno "
                          "nuevo). Crea uno con /token en @BotFather y pégalo otra vez."
                          % (profile, said))
        return res
    # Everything below is Telegram's own trouble with this bot. The token is not in question,
    # and saying so plainly matters more than the wording of the rest.
    res["api_up"] = api_reachable()
    proof = (" Este equipo sí alcanza a Telegram: una petición de prueba sin token recibe "
             "respuesta al instante, así que no es tu red ni tu cortafuegos."
             if res["api_up"] else "")
    if status == 429:
        res.update(state="telegram_busy",
                   detail="Telegram está limitando las peticiones de este bot%s (error 429), "
                          "así que de momento no contesta. No es el token.%s Espera unos "
                          "minutos y vuelve a comprobar." % (said, proof))
        return res
    if status >= 500:
        res.update(state="telegram_down",
                   detail="Telegram respondió «%s» (error %d) al preguntarle por este bot. Es "
                          "un fallo del lado de Telegram, no de tu token.%s\n\nNO generes un "
                          "token nuevo: al hacerlo el actual deja de servir y el problema "
                          "seguiría igual. Hermes reintenta la conexión solo cada pocos "
                          "minutos; esto suele restablecerse por sí mismo."
                          % (why or "Bad Gateway", status, proof))
        return res
    res.update(state="telegram_refused",
               detail="Telegram contestó con un error %d%s al preguntarle por este bot.%s"
                      % (status, said, proof))
    return res


def check(profile=None, hermes=None, token=None):
    """The verdict. `token` overrides the profile's own (used to pre-flight a token the owner
    just typed, before it is written anywhere)."""
    profile = profile or "default"
    # gateway_running starts as None - UNKNOWN, not stopped. Every branch that returns before
    # the gateway is asked used to hand back a confident False, so a verdict about Telegram's
    # side carried "y su motor está apagado" about an agent that was up and retrying.
    res = {"ok": False, "profile": profile, "state": "unknown", "detail": "",
           "bot": "", "has_token": False, "has_owner": False, "has_home": False,
           "gateway_running": None, "logs": log_paths(profile), "notes": []}

    env_file = ""
    env = {}
    if token:
        res["has_token"] = True
    else:
        env_file = hermes_ctl.env_path(hermes, None if profile == "default" else profile)
        res["env_path"] = env_file
        env = _read_env(env_file) if env_file else {}
        token = env.get("TELEGRAM_BOT_TOKEN", "").strip()
        res["has_token"] = bool(token)
        res["has_owner"] = bool(env.get("TELEGRAM_ALLOWED_USERS", "").strip())
        res["has_home"] = bool(env.get("TELEGRAM_HOME_CHANNEL", "").strip())

    if not token:
        res.update(state="no_token",
                   detail="El perfil «%s» no tiene TELEGRAM_BOT_TOKEN. Pega el token de "
                          "BotFather en el paso de Telegram." % profile)
        return res

    ok, data, status = _api(token, "getMe")
    if not ok and not status:
        # Nothing came back with an HTTP status, so Telegram never answered - and the reason
        # decides the advice. Ask the connection itself instead of guessing.
        probe = tls_probe()
        proxies = proxies_configured()
        res["proxies"] = proxies
        via = (" Este equipo tiene un proxy configurado (%s)." % ", ".join(proxies)) if proxies else ""
        if probe["state"] == "tls":
            res.update(state="unreachable_tls",
                       detail="La conexión con Telegram se cortó al validar el certificado: "
                              "algo entre este equipo y Telegram está inspeccionando el "
                              "tráfico y presenta un certificado que este equipo no "
                              "reconoce.%s\n\nTelegram NO ha visto el token, así que no hay "
                              "nada malo con él. Lo resuelve tu departamento de sistemas, de "
                              "una de dos formas: instalando el certificado raíz de la "
                              "empresa en «Entidades de certificación raíz de confianza» de "
                              "Windows, o excluyendo api.telegram.org de la inspección." % via)
            return res
        if probe["state"] == "dns":
            res.update(state="unreachable_network",
                       detail="Este equipo no pudo siquiera resolver api.telegram.org. Es la "
                              "red o el DNS, no el token.%s" % via)
            return res
        # The handshake worked, so the chain, the DNS and the route are all fine. Ask the API
        # itself, with no token: if it answers, this machine's connection is not the problem
        # and "comprueba tu conexión" would send the owner to repair a working router.
        if api_reachable():
            res["api_up"] = True
            res.update(state="telegram_slow",
                       detail="Telegram aceptó la conexión desde este equipo pero no contestó a "
                              "tiempo al preguntarle por este bot. No es tu red: una petición "
                              "de prueba sin token recibe respuesta al instante. Es Telegram, "
                              "que ahora mismo no está atendiendo a este bot.%s" % via)
            return res
        res.update(state="unreachable",
                   detail="No pude hablar con Telegram desde este equipo (sin conexión, o un "
                          "cortafuegos lo bloquea).%s No sé si el token es bueno; vuelve a "
                          "comprobarlo cuando haya red." % via)
        return res
    if not ok:
        return _refused(res, profile, status or 0, data)
    res["bot"] = (data.get("result") or {}).get("username") or ""

    ok_wh, wh, _st = _api(token, "getWebhookInfo")
    hook = ((wh.get("result") or {}).get("url") or "") if ok_wh and isinstance(wh, dict) else ""
    if hook:
        res.update(state="webhook_set",
                   detail="El bot @%s tiene un webhook configurado (%s), así que Hermes no "
                          "recibirá nada por polling. Quítalo con deleteWebhook."
                          % (res["bot"], hook[:60]))
        return res

    st = hermes_ctl.gateway_status(hermes, None if profile == "default" else profile)
    res["gateway_running"] = bool(st.get("running"))
    found, quotes = scan_logs(profile)
    res["signals"] = found

    if found.get("unix_watchdog"):
        res["notes"].append("El error 'asyncio has no attribute start_unix_server' que aparece "
                            "en el log es un fallo conocido de Hermes en Windows y NO impide "
                            "que funcione: ignóralo al diagnosticar.")
    if found.get("dispatcher_lock"):
        res["notes"].append("El aviso de que otro gateway tiene el 'dispatcher lock' tampoco "
                            "impide Telegram: solo desactiva el despachador en este perfil.")
    if found.get("double"):
        res["notes"].append("Hubo dos arranques del gateway a la vez; Hermes mató uno. Si se "
                            "repite, usa «reiniciar» desde aquí en vez de arrancarlo a mano.")

    if not res["gateway_running"]:
        if found.get("rejected") or found.get("conflict"):
            res.update(state="token_rejected_log",
                       detail="El token es válido ahora, pero el gateway del perfil «%s» no está "
                              "corriendo y su último arranque falló por token rechazado. "
                              "Reinícialo para que tome el token nuevo." % profile)
        else:
            res.update(state="gateway_down",
                       detail="El bot @%s es válido, pero el gateway del perfil «%s» no está "
                              "corriendo, así que nadie lee tus mensajes." % (res["bot"], profile))
        return res

    missing = []
    if not res["has_owner"]:
        missing.append("no hay dueño en TELEGRAM_ALLOWED_USERS (el candado de dueño está abierto)")
    if not res["has_home"]:
        missing.append("no hay canal principal (TELEGRAM_HOME_CHANNEL), así que los avisos "
                       "programados no tienen a dónde llegar")
    if missing:
        res.update(ok=True, state="connected_incomplete",
                   detail="Telegram conectado como @%s en el perfil «%s», pero %s."
                          % (res["bot"], profile, " y ".join(missing)))
        return res

    res.update(ok=True, state="connected",
               detail="Telegram conectado como @%s en el perfil «%s»." % (res["bot"], profile))
    return res
