"""
Phase C — inbound/outbound channels, per agent.

The wizard fully automates what's scriptable (webhook subscriptions, Slack app manifest,
SMTP email) and, for the interactive setups (WhatsApp QR), opens the right Hermes command
in a real terminal with a clear guide. Everything targets the agent's Hermes profile via
its wrapper, so channels for agent B never touch agent A.
"""

import os
import re
import smtplib
import ssl
import subprocess
import sys

from . import hermes_ctl

IS_WIN = os.name == "nt"

# SMTP presets for the popular providers (host / port / security + how to get a password).
SMTP_PROVIDERS = [
    {"id": "gmail", "label": "Gmail", "host": "smtp.gmail.com", "port": 587, "secure": "starttls",
     "note": "Activa la verificación en 2 pasos y crea una «Contraseña de aplicación».",
     "link": "https://myaccount.google.com/apppasswords"},
    {"id": "outlook", "label": "Outlook / Microsoft 365", "host": "smtp.office365.com", "port": 587,
     "secure": "starttls", "note": "Usa tu correo y contraseña; si tienes 2FA, crea una contraseña de aplicación.",
     "link": "https://account.microsoft.com/security"},
    {"id": "yahoo", "label": "Yahoo", "host": "smtp.mail.yahoo.com", "port": 465, "secure": "ssl",
     "note": "Crea una «Contraseña de aplicación» en seguridad de la cuenta.",
     "link": "https://login.yahoo.com/account/security"},
    {"id": "icloud", "label": "iCloud", "host": "smtp.mail.me.com", "port": 587, "secure": "starttls",
     "note": "Crea una «contraseña específica de aplicación» en tu Apple ID.",
     "link": "https://appleid.apple.com"},
    {"id": "other", "label": "Otro (manual)", "host": "", "port": 587, "secure": "starttls",
     "note": "Pide a tu proveedor el host, el puerto y si usa STARTTLS o SSL.", "link": ""},
]


def _profile_cmd_str(profile, subargs):
    """A shell command string that runs `hermes <subargs>` against the right profile."""
    if not profile or profile == "default":
        exe = hermes_ctl.hermes_path() or "hermes"
    else:
        exe = hermes_ctl.wrapper_path(profile)
    return '"%s" %s' % (exe, subargs)


def launch_terminal(profile, subargs, title="Hermes"):
    """Open a visible terminal running a (possibly interactive) hermes subcommand."""
    cmdstr = _profile_cmd_str(profile, subargs)
    try:
        if IS_WIN:
            subprocess.Popen('start "%s" cmd /k %s' % (title, cmdstr), shell=True)
        elif sys.platform == "darwin":
            subprocess.Popen(["osascript", "-e",
                              'tell application "Terminal" to do script "%s"' % cmdstr.replace('"', '\\"')])
        else:
            for term in ("x-terminal-emulator", "gnome-terminal", "konsole", "xterm"):
                if hermes_ctl.which(term) if hasattr(hermes_ctl, "which") else False:
                    subprocess.Popen([term, "-e", "bash", "-lc", cmdstr]); break
            else:
                return {"ok": False, "detail": "Ejecuta en una terminal: %s" % cmdstr, "command": cmdstr}
        return {"ok": True, "detail": "Abrí una terminal. Sigue las instrucciones ahí.",
                "command": cmdstr}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "detail": "No pude abrir la terminal. Ejecuta: %s (%s)" % (cmdstr, e),
                "command": cmdstr}


# ── WhatsApp ─────────────────────────────────────────────────────────────────
def whatsapp_pair(profile=None, cloud=False):
    return launch_terminal(profile, "whatsapp-cloud" if cloud else "whatsapp",
                           title="Hermes WhatsApp")


# ── Slack ────────────────────────────────────────────────────────────────────
def slack_manifest(profile=None, hermes=None):
    r = hermes_ctl._run(["slack", "manifest"], hermes, timeout=40, profile=profile)
    if not r["ok"]:
        return {"ok": False, "detail": (r["err"] or r["out"])[:300]}
    return {"ok": True, "manifest": r["out"]}


def slack_setup(profile=None):
    return launch_terminal(profile, "gateway setup", title="Hermes Slack")


# ── Webhook / Google Chat / generic inbound ────────────────────────────────────
def webhook_add(name, description="", deliver="telegram", prompt="", profile=None, hermes=None):
    args = ["webhook", "subscribe", name]
    if description:
        args += ["--description", description]
    if deliver:
        args += ["--deliver", deliver]
    if prompt:
        args += ["--prompt", prompt]
    r = hermes_ctl._run(args, hermes, timeout=60, profile=profile)
    return {"ok": r["ok"], "detail": (r["out"] or r["err"])[:800]}


def webhook_test(name, profile=None, hermes=None):
    r = hermes_ctl._run(["webhook", "test", name], hermes, timeout=40, profile=profile)
    return {"ok": r["ok"], "detail": (r["out"] or r["err"])[:400]}


# ── Email (SMTP) ────────────────────────────────────────────────────────────────
def email_save(profile, host, port, user, password, from_addr, secure):
    updates = {
        "SMTP_HOST": host, "SMTP_PORT": str(port), "SMTP_USER": user,
        "SMTP_PASS": password, "SMTP_FROM": from_addr or user, "SMTP_SECURE": secure,
    }
    return hermes_ctl.set_env_vars(updates, profile=profile)


def email_test(host, port, user, password, from_addr, to_addr, secure):
    if not (host and user and password and to_addr):
        return {"ok": False, "detail": "Faltan datos: host, usuario, contraseña o destinatario."}
    from email.message import EmailMessage
    msg = EmailMessage()
    msg["From"] = from_addr or user
    msg["To"] = to_addr
    msg["Subject"] = "Prueba de tu agente Hermes"
    msg.set_content("¡Funciona! Tu agente ya puede enviar correos por SMTP. 🎉")
    try:
        ctx = ssl.create_default_context()
        port = int(port)
        if (secure or "").lower() == "ssl" or port == 465:
            with smtplib.SMTP_SSL(host, port, context=ctx, timeout=25) as s:
                s.login(user, password); s.send_message(msg)
        else:
            with smtplib.SMTP(host, port, timeout=25) as s:
                s.ehlo(); s.starttls(context=ctx); s.login(user, password); s.send_message(msg)
        return {"ok": True, "detail": "¡Correo de prueba enviado a %s!" % to_addr}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "detail": "Error SMTP: %s" % e}


# ── generic outbound test (reuses configured platform creds) ────────────────────
def send_test(target, text="Prueba desde el asistente ✅", profile=None, hermes=None):
    return hermes_ctl.send(target, text, hermes, profile)


# ── Capabilities: image/video/vision toolsets ──────────────────────────────────
# Hermes ships these image providers, ALL requiring a paid/keyed API:
#   deepinfra, fal, krea, openai, openai-codex, openrouter, xai
# None is free-no-key, local, or Google. So we surface the honest free paths as
# guidance and drive `hermes setup tools` (which enables the toolset AND captures the
# provider + key correctly). The genuinely-free providers below need a small custom
# Hermes plugin (Pollinations / Gemini / local GPU) — offered separately.
IMAGE_OPTIONS = [
    {"id": "local", "label": "GPU local — Stable Diffusion", "free": True, "ships": False,
     "note": "Gratis, ilimitado y privado: genera en tu propia GPU (igual que tu STT/TTS "
             "local). Requiere instalar un modelo. Necesita un conector local (te lo armo).",
     "link": ""},
    {"id": "gemini", "label": "Google Gemini — capa gratuita", "free": True, "ships": False,
     "note": "Usa tu cuenta de Google: crea una API key GRATIS en Google AI Studio. Buena "
             "calidad y capa gratuita amplia. Necesita un conector (te lo armo).",
     "link": "https://aistudio.google.com/app/apikey"},
    {"id": "pollinations", "label": "Pollinations — gratis, sin cuenta", "free": True, "ships": False,
     "note": "Sin registro ni clave: lo más simple para empezar. Necesita un conector (te lo armo).",
     "link": "https://pollinations.ai"},
    {"id": "openrouter", "label": "OpenRouter — clave gratis", "free": False, "ships": True,
     "note": "Clave gratuita; algunos modelos de imagen son gratis o muy baratos. Ya soportado "
             "por Hermes (elige 'openrouter' en la configuración).",
     "link": "https://openrouter.ai/keys"},
    {"id": "paid", "label": "OpenAI · xAI · fal · DeepInfra — de pago", "free": False, "ships": True,
     "note": "Alta calidad, requieren clave de pago. Ya soportados por Hermes.", "link": ""},
]


def tools_setup(profile=None):
    """Open Hermes' interactive tool configurator (enables image/video/vision toolsets per
    platform AND captures the provider + API key — the correct place for that)."""
    return launch_terminal(profile, "setup tools", title="Hermes - Capacidades")


# ── Conversation memory / history (resume past conversations) ──────────────────
# The agent can search + resume past conversations via Hermes' `session_search` tool and
# the SQLite session store (`hermes sessions browse`). By default `session_search` is only
# in the `cli` toolset, NOT chat platforms — so on Telegram the agent can't recall history.
# NOTE: `hermes config set` mangles list values into a scalar string, so we must NOT use it
# to edit platform_toolsets. The sanctioned enable path is the interactive `setup tools`.
def history_status(profile=None, hermes=None):
    """Report whether session_search is enabled on the common chat platforms."""
    out = {"ok": True, "platforms": {}}
    for plat in ("telegram", "cli", "whatsapp", "slack", "discord"):
        raw = hermes_ctl.config_get("platform_toolsets.%s" % plat, hermes, profile)
        if not raw:
            continue
        out["platforms"][plat] = "session_search" in raw
    out["enabled_telegram"] = out["platforms"].get("telegram", False)
    return out


def history_enable(profile=None):
    """Open Hermes' tool configurator so the user can turn on Session Search for their
    channel (Session Search is item ~16 under each platform). Sanctioned + safe."""
    return launch_terminal(profile, "setup tools", title="Hermes - Memoria de conversaciones")


def sessions_recent(profile=None, hermes=None, limit=10):
    """List recent stored sessions (proof the history exists / is searchable)."""
    r = hermes_ctl._run(["sessions", "list"], hermes, timeout=30, profile=profile)
    return {"ok": r["ok"], "detail": (r["out"] or r["err"])[:1500]}


# ── Connectors (MCP) — the RIGHT place for connectors (Hermes-side, works via the
#    bridge). Claude Code MCP connectors do NOT work here (the bridge disables them). ──
def mcp_catalog(profile=None, hermes=None):
    r = hermes_ctl._run(["mcp", "catalog"], hermes, timeout=40, profile=profile)
    rows = []
    for line in (r["out"] or "").splitlines():
        s = line.strip()
        low = s.lower()
        if (not s or low.startswith(("name", "install", "picker", "catalog", "mcp"))
                or "hermes mcp" in low or set(s) <= set("- ")):
            continue
        cols = re.split(r"\s{2,}", s)
        if len(cols) >= 2 and re.fullmatch(r"[A-Za-z0-9_.-]+", cols[0]):
            rows.append({"name": cols[0], "status": cols[1] if len(cols) > 1 else "",
                         "desc": cols[2] if len(cols) > 2 else ""})
    return {"ok": r["ok"], "servers": rows, "detail": (r["err"] or "")[:200]}


def mcp_list(profile=None, hermes=None):
    r = hermes_ctl._run(["mcp", "list"], hermes, timeout=30, profile=profile)
    return {"ok": r["ok"], "detail": (r["out"] or r["err"])[:800]}


def mcp_install(name, profile=None):
    """Install a catalog MCP server (may require OAuth login -> runs in a terminal)."""
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", name or ""):
        return {"ok": False, "detail": "Nombre de conector invalido."}
    return launch_terminal(profile, "mcp install " + name, title="Hermes MCP: " + name)


def mcp_add(name, url, profile=None):
    """Add a custom remote MCP server by URL (may require OAuth -> runs in a terminal)."""
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", name or "") or not (url or "").startswith("http"):
        return {"ok": False, "detail": "Nombre o URL invalidos."}
    return launch_terminal(profile, 'mcp add %s --url %s' % (name, url), title="Hermes MCP: " + name)
