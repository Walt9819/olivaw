"""
Phase C — inbound/outbound channels, per agent.

The wizard fully automates what's scriptable (webhook subscriptions, Slack app manifest,
SMTP email) and, for the interactive setups (WhatsApp QR), opens the right Hermes command
in a real terminal with a clear guide. Everything targets the agent's Hermes profile via
its wrapper, so channels for agent B never touch agent A.
"""

import os
import re
import secrets
import shlex
import smtplib
import ssl
import subprocess
import sys
import urllib.parse

from . import hermes_ctl
from .procutil import run, which

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


CREATE_NEW_CONSOLE = 0x00000010  # Windows: spawn in its own console window


def _profile_argv(profile, subargs):
    """Argument VECTOR that runs `hermes <subargs>` against the right profile.
    `subargs` is a list. Returns [exe, *subargs] — never a shell string."""
    if not profile or profile == "default":
        exe = hermes_ctl.hermes_path() or "hermes"
    else:
        exe = hermes_ctl.wrapper_path(profile)
    return [exe] + list(subargs)


def _open_terminal(argv, title="Olivaw"):
    """Open a visible terminal running an argument VECTOR (an interactive flow the user must
    finish in a console: Claude login, WhatsApp QR, Hermes setup).

    Security: `argv` is a list and is NEVER concatenated into a shell command on Windows
    (we use cmd /k with a real argv + CREATE_NEW_CONSOLE, shell=False). On macOS/Linux the
    terminal apps require a command string, so each element is shell-quoted with shlex.quote,
    which neutralises metacharacters — so even an attacker-influenced value (e.g. an MCP URL)
    cannot break out of the argument.
    """
    argv = [str(a) for a in argv]
    try:
        if IS_WIN:
            subprocess.Popen(["cmd", "/k"] + argv, creationflags=CREATE_NEW_CONSOLE,
                             close_fds=True)
        elif sys.platform == "darwin":
            quoted = " ".join(shlex.quote(a) for a in argv)
            script = 'tell application "Terminal" to do script "%s"' % quoted.replace("\\", "\\\\").replace('"', '\\"')
            subprocess.Popen(["osascript", "-e", script])
        else:
            quoted = " ".join(shlex.quote(a) for a in argv)
            for term in ("x-terminal-emulator", "gnome-terminal", "konsole", "xterm"):
                if which(term):
                    subprocess.Popen([term, "-e", "bash", "-lc", quoted]); break
            else:
                return {"ok": False, "detail": "Ejecútalo en una terminal.", "command": quoted}
        return {"ok": True, "detail": "Abrí una terminal. Sigue los pasos ahí."}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "detail": "No pude abrir la terminal (%s)." % e}


def launch_terminal(profile, subargs, title="Hermes"):
    """Open a visible terminal running a hermes subcommand (subargs = list) for the profile."""
    return _open_terminal(_profile_argv(profile, subargs), title)


# ── Claude Code sign-in (one click) ────────────────────────────────────────────
def open_login_terminal(argv, title="Olivaw"):
    """Open the interactive sign-in for a brain CLI. Used by the provider adapters, so adding a
    third brain needs no change here."""
    return _open_terminal([str(a) for a in argv], title=title)


def claude_login(claude_path=None):
    """Open a terminal running `claude auth login` so the user signs in with one click
    (the CLI opens the browser OAuth flow). No user input goes into the command."""
    exe = claude_path or which("claude") or "claude"
    return _open_terminal([exe, "auth", "login"], title="Iniciar sesion en Claude")


def claude_status(claude_path=None):
    """Non-interactive check of whether Claude Code is signed in."""
    exe = claude_path or which("claude")
    if not exe:
        return {"ok": False, "signed_in": False, "detail": "Claude Code aún no está instalado."}
    r = run([exe, "auth", "status"], timeout=25)
    blob = (r["out"] + " " + r["err"]).lower()
    signed = r["ok"] and not any(w in blob for w in ("not logged", "not signed", "no auth", "logged out"))
    return {"ok": signed, "signed_in": signed,
            "detail": ("Sesión de Claude activa." if signed
                       else "Aún no has iniciado sesión en Claude. Pulsa «Iniciar sesión».")}


# ── Hermes home / profile paths (for reading adapter logs) ─────────────────────
def _hermes_home(profile=None):
    home = os.environ.get("HERMES_HOME") or ""
    if not home:
        if IS_WIN:
            home = os.path.join(os.environ.get("LOCALAPPDATA", ""), "hermes")
        else:
            home = os.path.join(os.path.expanduser("~"), ".hermes")
    if profile and profile != "default":
        p = os.path.join(home, "profiles", profile)
        if os.path.isdir(p):
            return p
    return home


# ── WhatsApp ─────────────────────────────────────────────────────────────────
# Pairing is a QR the Node bridge prints into its log. Rather than making a non-technical
# user read a terminal, we start the pairing and surface that QR inside the wizard.
def whatsapp_pair(profile=None, cloud=False):
    return launch_terminal(profile, ["whatsapp-cloud" if cloud else "whatsapp"],
                           title="Hermes WhatsApp")


def _whatsapp_logs(profile=None):
    """Candidate Node-bridge logs where the pairing QR is written."""
    base = _hermes_home(profile)
    found = []
    for root, dirs, files in os.walk(base):
        # keep the walk cheap: only descend into whatsapp-ish trees
        depth = root[len(base):].count(os.sep)
        if depth > 3:
            dirs[:] = []
            continue
        dirs[:] = [d for d in dirs
                   if "whatsapp" in d.lower() or "wa" == d.lower() or depth == 0]
        for f in files:
            if f.endswith(".log") and ("whatsapp" in root.lower() or "whatsapp" in f.lower()):
                found.append(os.path.join(root, f))
    return sorted(found, key=lambda p: os.path.getmtime(p) if os.path.exists(p) else 0,
                  reverse=True)[:3]


def whatsapp_qr(profile=None):
    """Return the most recent pairing QR (ASCII) + connection state from the bridge log."""
    logs = _whatsapp_logs(profile)
    if not logs:
        return {"ok": False, "waiting": True,
                "detail": "Aún no veo el proceso de WhatsApp. Pulsa «Conectar WhatsApp» primero."}
    text = ""
    for lg in logs:
        try:
            with open(lg, encoding="utf-8", errors="replace") as fh:
                text = fh.read()[-20000:]
        except Exception:  # noqa: BLE001
            continue
        if text:
            break
    low = text.lower()
    if "status:connected" in low or "connected" in low.split("qr")[-1][:200]:
        return {"ok": True, "connected": True, "detail": "¡WhatsApp conectado!"}
    # QR codes are printed as blocks of block-drawing chars; grab the last such block.
    blocks, cur = [], []
    for line in text.splitlines():
        if sum(ch in "█▀▄ " for ch in line) > max(8, len(line) * 0.8) and line.strip():
            cur.append(line)
        else:
            if len(cur) > 8:
                blocks.append("\n".join(cur))
            cur = []
    if len(cur) > 8:
        blocks.append("\n".join(cur))
    if blocks:
        return {"ok": True, "connected": False, "qr": blocks[-1],
                "detail": "Escanea este código con WhatsApp → Dispositivos vinculados."}
    return {"ok": False, "waiting": True,
            "detail": "El código todavía no aparece. Espera unos segundos y vuelve a intentar."}


def whatsapp_save(profile=None, allowed_users="", home_channel=""):
    """Enable WhatsApp with an OWNER-LOCK. Never leave the channel open to everyone:
    WHATSAPP_ALLOWED_USERS is the allow-list, and we explicitly force ALLOW_ALL off."""
    allowed = ",".join([u.strip() for u in re.split(r"[,\s]+", allowed_users or "") if u.strip()])
    if not allowed:
        return {"ok": False,
                "detail": "Indica al menos tu número (con código de país) para que solo tú "
                          "puedas darle órdenes por WhatsApp."}
    updates = {"WHATSAPP_ENABLED": "1", "WHATSAPP_ALLOWED_USERS": allowed,
               "WHATSAPP_ALLOW_ALL_USERS": "0"}
    if home_channel:
        updates["WHATSAPP_HOME_CHANNEL"] = home_channel.strip()
    return hermes_ctl.set_env_vars(updates, profile=profile)


# ── WhatsApp: when should the owner be pulled in? ────────────────────────────
# WhatsApp is where CLIENTS write, so the interesting question is not "can she use it" but
# "when does a conversation need HER". The taxonomy stays fixed in tools/escalate_owner.py;
# what she chooses here is which of those reach her, plus reasons of her own - and a reason
# of her own is worthless without a description, because that description is the only thing
# that teaches the agent to recognise it.

def escalation_get(profile=None):
    from . import escalation_prefs
    home = _hermes_home(profile)
    prefs = escalation_prefs.load(home=home)
    env = _read_env_file(os.path.join(home, ".env"))
    token = (env.get("TELEGRAM_BOT_TOKEN") or "").strip()
    chat = (env.get("TELEGRAM_HOME_CHANNEL")
            or (env.get("TELEGRAM_ALLOWED_USERS") or "").split(",")[0]).strip()
    return {
        "ok": True,
        "catalog": escalation_prefs.catalog(),
        "prefs": prefs,
        # The alert goes out over Telegram, so say plainly when that is not set up yet
        # rather than letting her tick twelve boxes that can never fire.
        "telegram_ready": bool(token and chat),
        "telegram_detail": ("" if (token and chat) else
                            "Configura Telegram arriba: los avisos se envían por ahí."),
        "max_custom": escalation_prefs.MAX_CUSTOM,
    }


def escalation_save(profile=None, enabled=True, reasons=None, custom=None):
    from . import escalation_prefs
    return escalation_prefs.save(enabled=bool(enabled), reasons=reasons or [],
                                 custom=custom or [], home=_hermes_home(profile))


def _read_env_file(path):
    out = {}
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip().strip('"').strip("'")
    except OSError:
        pass
    return out


# ── Slack ────────────────────────────────────────────────────────────────────
def slack_manifest(profile=None, hermes=None):
    r = hermes_ctl._run(["slack", "manifest"], hermes, timeout=40, profile=profile)
    if not r["ok"]:
        return {"ok": False, "detail": (r["err"] or r["out"])[:300]}
    return {"ok": True, "manifest": r["out"]}


def slack_setup(profile=None):
    return launch_terminal(profile, ["gateway", "setup"], title="Hermes Slack")


# ── Webhook / Google Chat / generic inbound ────────────────────────────────────
_ROUTE_RE = re.compile(r"^[A-Za-z0-9_-]{1,40}$")


def webhook_add(name, description="", deliver="telegram", prompt="", profile=None, hermes=None):
    # Route name must be a safe slug (it becomes part of a URL path and a CLI arg).
    if not _ROUTE_RE.fullmatch(name or ""):
        return {"ok": False, "detail": "El nombre de la ruta debe ser letras/números/guiones (máx 40)."}
    if deliver not in ("telegram", "discord", "slack", "whatsapp", "signal", "log"):
        return {"ok": False, "detail": "Destino de entrega no válido."}
    # Always attach a high-entropy HMAC secret so the endpoint is not world-triggerable.
    secret = secrets.token_hex(24)
    args = ["webhook", "subscribe", name, "--secret", secret]
    if description:
        args += ["--description", description]
    args += ["--deliver", deliver]
    if prompt:
        args += ["--prompt", prompt]
    r = hermes_ctl._run(args, hermes, timeout=60, profile=profile)
    out = {"ok": r["ok"], "detail": (r["out"] or r["err"])[:800]}
    if r["ok"]:
        out["secret"] = secret
        out["detail"] += ("\n\nGuarda este secreto (se pide al llamar el webhook, cabecera HMAC): %s" % secret)
    return out


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


# ── Google Workspace ──────────────────────────────────────────────────────────
# Two native paths, both first-class Hermes platforms:
#   • Gmail / Workspace mail  -> the `email` platform (IMAP in + SMTP out), app password
#   • Google Chat             -> the `google_chat` platform, service-account JSON
# Both get an ALLOW-LIST so a stranger who emails/messages the agent cannot command it.
GOOGLE_PRESETS = {
    "gmail": {"label": "Gmail / Google Workspace", "smtp_host": "smtp.gmail.com",
              "smtp_port": 587, "imap_host": "imap.gmail.com",
              "note": "Activa la verificación en 2 pasos y crea una «Contraseña de aplicación». "
                      "Funciona igual con una cuenta de Google Workspace.",
              "link": "https://myaccount.google.com/apppasswords"},
    "outlook": {"label": "Outlook / Microsoft 365", "smtp_host": "smtp.office365.com",
                "smtp_port": 587, "imap_host": "outlook.office365.com",
                "note": "Usa tu correo y contraseña; con 2FA, crea una contraseña de aplicación.",
                "link": "https://account.microsoft.com/security"},
    "other": {"label": "Otro proveedor", "smtp_host": "", "smtp_port": 587, "imap_host": "",
              "note": "Pide a tu proveedor el servidor SMTP y el IMAP.", "link": ""},
}


def email_platform_save(profile=None, address="", password="", smtp_host="", smtp_port=587,
                        imap_host="", allowed_users="", home_address=""):
    """Configure the NATIVE email platform: the agent can RECEIVE and REPLY to email.
    (Different from the smtp_send.py tool, which only sends.)"""
    if not (address and password and smtp_host and imap_host):
        return {"ok": False, "detail": "Faltan datos: correo, contraseña de aplicación, SMTP e IMAP."}
    allowed = ",".join([u.strip().lower() for u in re.split(r"[,\s]+", allowed_users or "")
                        if u.strip()])
    if not allowed:
        return {"ok": False,
                "detail": "Indica qué direcciones pueden darle órdenes (al menos la tuya). "
                          "Sin esto, cualquiera que escriba a ese buzón podría mandar al agente."}
    updates = {
        "EMAIL_ADDRESS": address.strip(), "EMAIL_PASSWORD": password,
        "EMAIL_SMTP_HOST": smtp_host.strip(), "EMAIL_SMTP_PORT": str(smtp_port or 587),
        "EMAIL_IMAP_HOST": imap_host.strip(), "EMAIL_ALLOWED_USERS": allowed,
        "EMAIL_HOME_ADDRESS": (home_address or address).strip(),
    }
    return hermes_ctl.set_env_vars(updates, profile=profile)


def google_chat_save(profile=None, service_account="", allowed_users="", home_space=""):
    """Configure Google Chat (Workspace). service_account = path to (or inline) SA JSON."""
    if not service_account:
        return {"ok": False, "detail": "Indica la ruta al archivo JSON de la cuenta de servicio."}
    if os.path.sep in service_account and not os.path.exists(service_account):
        return {"ok": False, "detail": "No encuentro ese archivo JSON: %s" % service_account}
    allowed = ",".join([u.strip().lower() for u in re.split(r"[,\s]+", allowed_users or "")
                        if u.strip()])
    if not allowed:
        return {"ok": False,
                "detail": "Indica los correos que pueden darle órdenes (al menos el tuyo)."}
    updates = {"GOOGLE_CHAT_SERVICE_ACCOUNT_JSON": service_account.strip(),
               "GOOGLE_CHAT_ALLOWED_USERS": allowed}
    if home_space:
        updates["GOOGLE_CHAT_HOME_CHANNEL"] = home_space.strip()
    return hermes_ctl.set_env_vars(updates, profile=profile)


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
    return launch_terminal(profile, ["setup", "tools"], title="Hermes - Capacidades")


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
    return launch_terminal(profile, ["setup", "tools"], title="Hermes - Memoria de conversaciones")


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


_MCP_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")


def _valid_mcp_url(url):
    """A real http(s) URL with a host and no control/space characters. Belt-and-suspenders
    on top of the argv-vector launcher (which already prevents shell injection)."""
    try:
        u = urllib.parse.urlparse(url or "")
    except ValueError:
        return False
    if u.scheme not in ("http", "https") or not u.netloc:
        return False
    return not any(c.isspace() or ord(c) < 0x20 for c in url)


def mcp_install(name, profile=None):
    """Install a catalog MCP server (may require OAuth login -> runs in a terminal)."""
    if not _MCP_NAME_RE.fullmatch(name or ""):
        return {"ok": False, "detail": "Nombre de conector inválido."}
    return launch_terminal(profile, ["mcp", "install", name], title="Hermes MCP: " + name)


def mcp_add(name, url, profile=None):
    """Add a custom remote MCP server by URL (may require OAuth -> runs in a terminal)."""
    if not _MCP_NAME_RE.fullmatch(name or ""):
        return {"ok": False, "detail": "Nombre de conector inválido."}
    if not _valid_mcp_url(url):
        return {"ok": False, "detail": "URL inválida (debe ser http(s):// y sin espacios)."}
    return launch_terminal(profile, ["mcp", "add", name, "--url", url], title="Hermes MCP: " + name)
