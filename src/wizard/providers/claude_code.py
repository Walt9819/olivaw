"""Claude Code provider — the default, fully-supported brain."""

import os

from ..procutil import run, which
from .base import ProviderInfo


def _check(paths):
    """Is the Claude Code CLI installed and runnable?"""
    path = paths.get("claude") or which("claude")
    if not path:
        return {"ok": False, "found": False, "path": "",
                "detail": "No encontramos el comando 'claude'. Instálalo con el botón."}
    r = run([path, "--version"], timeout=25)
    if r["ok"]:
        return {"ok": True, "found": True, "path": path,
                "version": r["out"].splitlines()[0] if r["out"] else "",
                "detail": "Claude Code está instalado."}
    # present but errored (often: not logged in / bad shim)
    return {"ok": False, "found": True, "path": path,
            "detail": "Encontramos 'claude' pero no respondió. ¿Iniciaste sesión? "
                      "Abre una terminal y ejecuta: claude"}


def _install(paths):
    """Best-effort: install the CLI via npm (needs Node)."""
    npm = which("npm")
    if not npm:
        return {"ok": False, "detail": "Necesitas Node.js primero (nodejs.org), "
                                       "luego reintenta."}
    r = run([npm, "install", "-g", "@anthropic-ai/claude-code"], timeout=240)
    if r["ok"]:
        return {"ok": True, "detail": "Claude Code instalado. Ahora inicia sesión: "
                                      "abre una terminal y ejecuta 'claude'."}
    return {"ok": False, "detail": "No se pudo instalar automáticamente. "
                                   "Detalle: " + (r["err"] or r["out"])[:400]}


def _bridge_env(paths):
    env = {}
    if paths.get("claude"):
        env["CLAUDE_BRIDGE_CLAUDE"] = paths["claude"]
    if paths.get("workspace"):
        env["CLAUDE_BRIDGE_WORKSPACE"] = paths["workspace"]
    return env


INFO = ProviderInfo(
    id="claude-code",
    label="Claude Code",
    status="ready",
    tagline="El cerebro recomendado. Usa tu suscripción de Claude — sin claves de API.",
    paid_note="Necesitas una cuenta de pago de Claude (plan Pro o Max). "
              "Con ella, tu agente piensa con los modelos de Anthropic sin costo por API.",
    download_url="https://claude.com/product/claude-code",
    help_url="https://docs.claude.com/en/docs/claude-code/overview",
    login_hint="Se abrirá una ventana para conectar tu cuenta; cuando termine, vuelve aquí.",
    steps=[
        {"title": "Ya lo instalamos por ti",
         "body": "Claude Code quedó instalado durante la instalación. No tienes que hacer nada."},
        {"title": "Conecta tu cuenta (un clic)",
         "body": "Pulsa «Iniciar sesión en Claude» abajo y sigue los pasos en la ventana que "
                 "se abre. Solo se hace una vez."},
    ],
    check_fn=_check,
    install_fn=_install,
    bridge_env_fn=_bridge_env,
)
