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


def test_brain(base_url, timeout=120, brain="Claude Code"):
    """
    The headline test: send a real chat completion through the bridge and confirm the brain
    answers. Proves provider → bridge → response end-to-end.

    `brain` is only for the message shown when it fails — telling a Codex user to log into
    Claude Code is how a working setup gets reported as broken.
    """
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
        detail = data if isinstance(data, str) else str(data)
        return {"ok": False,
                "detail": "El cerebro no respondió. ¿Iniciaste sesión en %s? "
                          "Detalle: %s" % (brain, detail[:300])}
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
