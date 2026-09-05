"""
Live environment checks — the engine behind every "probar" (test) button.

Each function returns a small dict {ok, detail, ...} that the front-end renders
as a green/red pill with a helpful message. Nothing here mutates state.
"""

import os
import sys

from .procutil import http_json, run, which


def check_python():
    return {"ok": True, "found": True, "path": sys.executable,
            "version": "Python %d.%d.%d" % sys.version_info[:3],
            "detail": "Python está listo."}


def check_node():
    path = which("node")
    if not path:
        return {"ok": False, "found": False,
                "detail": "Node.js no está instalado. Descárgalo de nodejs.org."}
    r = run([path, "--version"], timeout=20)
    return {"ok": r["ok"], "found": True, "path": path,
            "version": r["out"],
            "detail": "Node.js está listo." if r["ok"]
                      else "Node.js está pero no respondió."}


def check_hermes():
    path = which("hermes")
    if not path:
        return {"ok": False, "found": False,
                "detail": "No encontramos 'hermes'. Instálalo siguiendo su guía."}
    r = run([path, "--version"], timeout=25)
    ver = r["out"].splitlines()[0] if r["out"] else ""
    # Some builds don't support --version; treat "found & runs" as ok.
    return {"ok": True, "found": True, "path": path, "version": ver,
            "detail": "Hermes está instalado."}


def check_bridge(base_url="http://127.0.0.1:8790"):
    ok, data, status = http_json(base_url.rstrip("/") + "/status", timeout=6)
    if ok and isinstance(data, dict):
        return {"ok": True, "running": True, "version": data.get("version"),
                "inflight": data.get("inflight"),
                # Which brain is actually serving - the wizard compares this with the choice the
                # owner just made, so a pending switch can be reported instead of silently waited on.
                "engine": data.get("engine"),
                "detail": "El puente está corriendo (v%s%s)." % (
                    data.get("version", "?"),
                    ", cerebro: %s" % data["engine"] if data.get("engine") else "")}
    # try /health as a fallback (older bridge without /status)
    ok2, _d, _s = http_json(base_url.rstrip("/") + "/health", timeout=6)
    if ok2:
        return {"ok": True, "running": True, "version": None,
                "detail": "El puente responde (versión antigua, sin /status)."}
    return {"ok": False, "running": False,
            "detail": "El puente no está corriendo todavía (es normal antes de terminar)."}


def _split_url(base_url):
    """(host, port) from a base url, without pulling in urllib for one line of parsing."""
    rest = base_url.split("://", 1)[-1].split("/", 1)[0]
    if ":" in rest:
        host, _, port = rest.rpartition(":")
        try:
            return (host or "127.0.0.1"), int(port)
        except ValueError:
            pass
    return rest or "127.0.0.1", 80


def _port_open(host, port, timeout=3):
    import socket
    try:
        with socket.create_connection((host, port), timeout):
            return True
    except OSError:
        return False


def diagnose_bridge(base_url, want_engine=None):
    """Why the brain is not answering — as a cause, not a guess.

    A refused connection on 127.0.0.1 (WinError 10061) means nothing is listening on that
    port. It says nothing whatsoever about whether anyone is logged in, and reporting it as
    "did you sign in?" sends the owner to re-authenticate a CLI that was never the problem
    while the real fault - a bridge that died, or a port the config and the process disagree
    about - goes unlooked-at. That happened, on a real install.

    Returns {state, detail, ...}. States: `ok`, `bridge_not_listening`,
    `bridge_wrong_engine`, `bridge_unhealthy`.
    """
    host, port = _split_url(base_url)
    if not _port_open(host, port):
        return {"ok": False, "state": "bridge_not_listening", "port": port,
                "detail": "No hay nada escuchando en el puerto %d de este equipo, así que "
                          "el cerebro no puede responder: el proceso puente no está "
                          "arrancado (o está en otro puerto). No es un problema de inicio "
                          "de sesión." % port}
    ok, data, _status = http_json(base_url.rstrip("/") + "/health", timeout=6)
    if not ok or not isinstance(data, dict):
        ok, data, _status = http_json(base_url.rstrip("/") + "/status", timeout=6)
    if not ok or not isinstance(data, dict):
        return {"ok": False, "state": "bridge_unhealthy", "port": port,
                "detail": "Algo está ocupando el puerto %d pero no es el puente de Olivaw "
                          "(no contesta /health). Puede ser otro programa con ese puerto "
                          "tomado." % port}
    engine = data.get("engine") or data.get("backend") or ""
    if want_engine and engine and engine != want_engine:
        return {"ok": False, "state": "bridge_wrong_engine", "port": port, "engine": engine,
                "detail": "El puente del puerto %d está corriendo con «%s», no con «%s». "
                          "Es el agente equivocado, o el cambio de cerebro aún no se ha "
                          "reiniciado." % (port, engine, want_engine)}
    return {"ok": True, "state": "ok", "port": port, "engine": engine}


def _login_state(engine):
    """Ask the brain's own CLI whether it is signed in. Only meaningful once the bridge is up."""
    try:
        if (engine or "").startswith("codex"):
            import codex_engine
            return codex_engine.login_status()
        from .channels import claude_status
        return claude_status()
    except Exception:  # noqa: BLE001
        return {}


def test_brain(base_url, timeout=120, brain="Claude Code", engine=None):
    """
    The headline test: send a real chat completion through the bridge and confirm the brain
    answers. Proves provider → bridge → response end-to-end.

    Failure is CLASSIFIED before it is reported. `brain` is only for the wording — telling a
    Codex user to log into Claude Code is how a working setup gets reported as broken — and
    `engine` ('codex' | 'claude-code') lets the diagnosis check the bridge on that port is
    the brain the owner just chose, and ask the right CLI about its session.
    """
    pre = diagnose_bridge(base_url, want_engine=engine)
    if not pre.get("ok"):
        return dict(pre, brain=brain)
    payload = {
        "model": "claude-code",
        "messages": [
            {"role": "user",
             "content": "Responde EXACTAMENTE con la palabra: LISTO"},
        ],
        "temperature": 0,
        "stream": False,
    }
    ok, data, status = http_json(
        base_url.rstrip("/") + "/v1/chat/completions",
        data=payload, method="POST", timeout=timeout)
    if not ok:
        detail = (data if isinstance(data, str) else str(data))[:300]
        # The bridge answered /health a moment ago, so the port and the process are fine and
        # the fault is downstream: the brain CLI itself. NOW a login question is the right
        # question - and it is asked of the CLI rather than of the owner.
        low = detail.lower()
        if "timed out" in low or "timeout" in low:
            return {"ok": False, "state": "timeout", "brain": brain, "raw": detail,
                    "detail": "El puente está vivo pero %s tardó más de %ds en contestar. "
                              "Suele pasar con la primera respuesta del día; vuelve a "
                              "probar." % (brain, timeout)}
        login = _login_state(engine)
        if login and not login.get("signed_in", True):
            return {"ok": False, "state": "not_authenticated", "brain": brain,
                    "detail": login.get("detail") or
                              "Aún no has iniciado sesión en %s." % brain}
        return {"ok": False, "state": "brain_failed", "brain": brain,
                "detail": "El puente está corriendo, pero %s no completó la respuesta. "
                          "Detalle: %s" % (brain, detail)}
    try:
        text = data["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError):
        return {"ok": False, "detail": "Respuesta inesperada del puente.",
                "raw": str(data)[:300]}
    reply = text.strip()
    good = "LISTO" in reply.upper()
    return {"ok": good, "reply": reply,
            "detail": ("¡El cerebro respondió! Tu agente ya puede pensar."
                       if good else
                       "El cerebro respondió, pero con texto inesperado: " + reply[:120])}
