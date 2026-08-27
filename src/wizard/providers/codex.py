"""Codex provider — OpenAI's CLI as the agent's brain.

Wired against the real thing (codex-cli 0.150.x): install via npm, sign in once with
`codex login`, and the bridge runs `codex exec` as a pure reasoner. The execution details live
in src/codex_engine.py; this file is only what the wizard needs — is it there, is it signed in,
and which env vars the bridge needs to use it.
"""

import os
import sys

from ..procutil import run, which
from .base import ProviderInfo

# codex_engine sits next to the bridge (src/), one source of truth for exe discovery and the
# login check. The wizard runs both as a script and as a package, so make sure src/ is importable.
_SRC = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)
try:
    import codex_engine
except Exception:  # noqa: BLE001 - an old install may not ship it yet
    codex_engine = None


def _exe(paths):
    return (paths or {}).get("codex") or (codex_engine.resolve_exe() if codex_engine
                                          else which("codex")) or ""


def _check(paths):
    if codex_engine is None:
        return {"ok": False, "found": False, "path": "",
                "detail": "Esta versión de Olivaw todavía no trae el motor de Codex. Actualiza."}
    path = _exe(paths)
    if not path:
        return {"ok": False, "found": False, "path": "",
                "detail": "No encontramos el comando 'codex'. Instálalo con el botón."}
    ver = codex_engine.version()
    if ver:
        return {"ok": True, "found": True, "path": path, "version": ver,
                "detail": "Codex está instalado (%s)." % ver}
    return {"ok": False, "found": True, "path": path,
            "detail": "Encontramos 'codex' pero no respondió. Abre una terminal y ejecuta: codex"}


def _install(paths):
    npm = which("npm")
    if not npm:
        return {"ok": False, "detail": "Necesitas Node.js primero (nodejs.org), luego reintenta."}
    r = run([npm, "install", "-g", "@openai/codex"], timeout=600)
    if r["ok"]:
        return {"ok": True, "detail": "Codex instalado. Ahora inicia sesión: pulsa «Iniciar "
                                      "sesión en Codex»."}
    return {"ok": False, "detail": "No se pudo instalar automáticamente. Detalle: "
                                   + (r["err"] or r["out"])[:400]}


def _login(paths):
    from .. import channels          # local import: channels must not depend on providers
    return channels.open_login_terminal([_exe(paths) or "codex", "login"],
                                        title="Iniciar sesion en Codex")


def _login_status(paths):
    if codex_engine is None:
        return {"ok": False, "signed_in": False,
                "detail": "Esta versión de Olivaw todavía no trae el motor de Codex."}
    return codex_engine.login_status()


def _bridge_env(paths):
    """What the bridge needs in its environment to think with Codex."""
    env = {"OLIVAW_ENGINE": "codex"}
    path = _exe(paths)
    if path:
        env["OLIVAW_CODEX"] = path
    if (paths or {}).get("workspace"):
        env["CLAUDE_BRIDGE_WORKSPACE"] = paths["workspace"]
    return env


INFO = ProviderInfo(
    id="codex",
    label="Codex",
    status="ready",
    tagline="Usa Codex de OpenAI como cerebro. Tu suscripción de ChatGPT, sin claves de API.",
    paid_note="Necesitas una cuenta de pago de ChatGPT (Plus, Pro o Business). También funciona "
              "con una API key de OpenAI si prefieres pagar por uso.",
    download_url="https://developers.openai.com/codex/cli",
    help_url="https://developers.openai.com/codex",
    login_hint="Se abrirá una ventana para conectar tu cuenta de ChatGPT; cuando termine, "
               "vuelve aquí.",
    cli_key="codex",
    cli_label="Codex",
    engine="codex",
    steps=[
        {"title": "Instala Codex (un clic)",
         "body": "Pulsa «Instalar Codex» en las opciones avanzadas. Necesita Node.js, que ya "
                 "viene con la instalación de Olivaw."},
        {"title": "Conecta tu cuenta (un clic)",
         "body": "Pulsa «Iniciar sesión en Codex» y sigue los pasos en la ventana que se abre. "
                 "Solo se hace una vez."},
    ],
    check_fn=_check,
    install_fn=_install,
    login_fn=_login,
    login_status_fn=_login_status,
    bridge_env_fn=_bridge_env,
)
