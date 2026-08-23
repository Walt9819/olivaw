"""
Hermes Bridge — onboarding wizard server.

A tiny stdlib HTTP server that serves a polished browser wizard and exposes the
"probar" (test) + "apply" actions as JSON endpoints. Zero pip dependencies, so it
ships inside the kit and auto-updates with everything else.

Run:  python -m wizard.wizard_server        (from src/)
  or  python src/wizard/wizard_server.py

It binds 127.0.0.1 on a free port, mints a one-time token, and opens the browser at
http://127.0.0.1:<port>/?t=<token>. All /api/* calls require that token.
"""

import atexit
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# allow running as a script (python src/wizard/wizard_server.py) or as a module
if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from wizard import (agents_registry, channels, checks, config_writer, hermes_ctl,
                        obsidian, proposals, providers, rescue, selfcare, telegram_setup,
                        usecases)
    from wizard.procutil import http_json, which
else:
    from . import (agents_registry, channels, checks, config_writer, hermes_ctl, obsidian,
                   proposals, providers, rescue, selfcare, telegram_setup, usecases)
    from .procutil import http_json, which

HERE = os.path.dirname(os.path.abspath(__file__))          # .../src/wizard
SRC_DIR = os.path.dirname(HERE)                            # .../src
INSTALL_DIR = os.path.dirname(SRC_DIR)                     # folder holding src/, VERSION
WEB_DIR = os.path.join(HERE, "web")
BRIDGE_PY = os.path.join(SRC_DIR, "claude_bridge.py")
LAUNCHER_PY = os.path.join(SRC_DIR, "launcher.py")
TEST_PORT = 8788
TEST_URL = "http://127.0.0.1:%d" % TEST_PORT

TOKEN = secrets.token_urlsafe(24)
_test_bridge = None  # Popen of the throwaway bridge used by "probar el cerebro"

import re as _re
_SLUG_RE = _re.compile(r"^[a-z0-9]{1,24}$")


def _safe_slug(slug):
    """Accept only a strict lowercase-alnum slug (<=24). Rejects path traversal / injection
    for the reconfigure + agent-action paths, which take a slug straight from the request."""
    return bool(slug) and slug != "default" and _SLUG_RE.fullmatch(slug) is not None


def _under_agents(path):
    """True iff `path` resolves inside INSTALL_DIR/agents (containment guard for rmtree/writes)."""
    base = os.path.realpath(os.path.join(INSTALL_DIR, "agents"))
    rp = os.path.realpath(path)
    return rp == base or rp.startswith(base + os.sep)

_CTYPES = {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8",
           ".js": "application/javascript; charset=utf-8",
           ".svg": "image/svg+xml", ".ico": "image/x-icon",
           ".png": "image/png", ".json": "application/json; charset=utf-8"}


# ── discovery / defaults ─────────────────────────────────────────────────────
def _find_hermes_config():
    home = os.path.expanduser("~")
    cands = []
    if os.name == "nt":
        for base in (os.environ.get("LOCALAPPDATA", ""),
                     os.environ.get("APPDATA", ""),
                     os.path.join(home, ".config", "hermes")):
            if base:
                cands.append(os.path.join(base, "hermes", "config.yaml"))
                cands.append(os.path.join(base, "config.yaml"))
    else:
        cands += [os.path.join(home, "Library", "Application Support", "hermes", "config.yaml"),
                  os.path.join(home, ".config", "hermes", "config.yaml"),
                  os.path.join(home, ".local", "share", "hermes", "config.yaml")]
    for c in cands:
        if c and os.path.exists(c):
            return c
    return ""


def _existing_repo():
    p = os.path.join(INSTALL_DIR, "updater.config.json")
    if os.path.exists(p):
        try:
            with open(p, encoding="utf-8") as fh:
                return (json.load(fh) or {}).get("repo", "")
        except Exception:  # noqa: BLE001
            return ""
    return ""


def hermes_snapshot():
    """Detect whether an agent already exists on this machine (native Hermes state)."""
    hp = which("hermes")
    if not hp:
        return {"available": False, "profiles": [], "existing": False}
    profiles = hermes_ctl.profile_list(hp)
    gw = hermes_ctl.gateway_status(hp)
    pairing = hermes_ctl.pairing_list(hp)
    base_url = hermes_ctl.config_get("model.base_url", hp)
    # "existing" = a configured agent is present (profiles beyond none, or model wired)
    existing = bool(profiles) or bool(base_url)
    return {
        "available": True, "path": hp, "existing": existing,
        "profiles": profiles, "active": hermes_ctl.active_profile(hp),
        "gateway_running": gw.get("running"),
        "model_base_url": base_url,
        "owners": pairing.get("approved", []),
    }


def _default_workspace():
    return os.environ.get("CLAUDE_BRIDGE_WORKSPACE",
                          os.path.join(os.path.expanduser("~"), "hermes-workspace"))


def _bridge_up(port):
    return http_json("http://127.0.0.1:%d/status" % int(port), timeout=3)[0]


def agents_snapshot():
    """All agents on this machine: the built-in `default` plus registered extras."""
    hp = which("hermes")
    owners = hermes_ctl.pairing_list(hp).get("approved", []) if hp else []
    default = {
        "slug": "default", "name": "Agente principal", "profile": "default",
        "port": agents_registry.BASE_PORT, "workspace": _default_workspace(),
        "is_default": True,
        "gateway_running": hermes_ctl.gateway_status(hp).get("running") if hp else None,
        "bridge_up": _bridge_up(agents_registry.BASE_PORT),
        "owners": owners,
    }
    extra = []
    for a in agents_registry.list_agents(INSTALL_DIR):
        slug = a.get("slug")
        row = dict(a)
        row["is_default"] = False
        row["bridge_up"] = _bridge_up(a.get("port", 0)) if a.get("port") else False
        if hp and hermes_ctl.profile_exists(slug, hp):
            row["gateway_running"] = hermes_ctl.gateway_status(hp, profile=slug).get("running")
            row["owners"] = hermes_ctl.pairing_list(hp, profile=slug).get("approved", [])
        else:
            row["gateway_running"] = None
            row["missing_profile"] = True     # code/profile deleted -> restore candidate
        extra.append(row)
    return {"default": default, "extra": extra}


def initial_state():
    claude = which("claude")
    workspace = _default_workspace()
    return {
        "agents": agents_snapshot(),
        "lang": "es",
        "defaults": {
            "install_dir": INSTALL_DIR,
            "workspace": workspace,
            "python": sys.executable,
            "claude": claude or "",
            "node": which("node") or "",
            "hermes": which("hermes") or "",
            "hermes_config": _find_hermes_config(),
            "repo": _existing_repo() or "Walt9819/olivaw",
        },
        "providers": providers.public_list(),
        "default_provider": providers.DEFAULT_ID,
        "usecases": usecases.public_list(),
        "smtp_providers": channels.SMTP_PROVIDERS,
        "image_options": channels.IMAGE_OPTIONS,
        "google_presets": channels.GOOGLE_PRESETS,
    }


# ── test bridge (for "probar el cerebro") ────────────────────────────────────
def _port_open(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.4)
        return s.connect_ex(("127.0.0.1", port)) == 0


def ensure_test_bridge(claude_path, workspace):
    """Start a throwaway bridge on TEST_PORT if not already up. Returns base url."""
    global _test_bridge
    ok, _d, _s = http_json(TEST_URL + "/health", timeout=3)
    if ok:
        return TEST_URL
    env = os.environ.copy()
    if claude_path:
        env["CLAUDE_BRIDGE_CLAUDE"] = claude_path
    if workspace:
        env["CLAUDE_BRIDGE_WORKSPACE"] = workspace
        os.makedirs(workspace, exist_ok=True)
    _test_bridge = subprocess.Popen(
        [sys.executable, BRIDGE_PY, "--port", str(TEST_PORT)],
        env=env, cwd=SRC_DIR,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(40):  # up to ~20s for the CLI-backed bridge to bind
        if http_json(TEST_URL + "/health", timeout=2)[0]:
            return TEST_URL
        time.sleep(0.5)
    return TEST_URL  # let the caller's request surface the real error


def stop_test_bridge():
    global _test_bridge
    if _test_bridge and _test_bridge.poll() is None:
        try:
            _test_bridge.terminate()
            _test_bridge.wait(timeout=5)
        except Exception:  # noqa: BLE001
            try:
                _test_bridge.kill()
            except Exception:  # noqa: BLE001
                pass
    _test_bridge = None


atexit.register(stop_test_bridge)


def start_supervisor():
    """Launch the supervisor (launcher.py) detached, so updates run from now on.

    Idempotent: if a supervisor is already managing the bridge (health OK on 8790),
    don't spawn a second one — the running supervisor re-reads config every loop and
    will pick up what the wizard just wrote within seconds. This avoids a double
    launcher on macOS, where launchd may have started one at install time.
    """
    if http_json("http://127.0.0.1:8790/health", timeout=3)[0]:
        return {"ok": True, "detail": "El supervisor ya está corriendo; aplicará la "
                                      "nueva configuración en unos segundos."}
    if not os.path.exists(LAUNCHER_PY):
        return {"ok": False, "detail": "No se encontró launcher.py."}
    try:
        if os.name == "nt":
            pyw = sys.executable.replace("python.exe", "pythonw.exe")
            exe = pyw if os.path.exists(pyw) else sys.executable
            DETACHED = 0x00000008 | 0x00000200  # DETACHED_PROCESS | NEW_PROCESS_GROUP
            subprocess.Popen([exe, LAUNCHER_PY], cwd=INSTALL_DIR,
                             creationflags=DETACHED, close_fds=True)
        else:
            subprocess.Popen([sys.executable, LAUNCHER_PY], cwd=INSTALL_DIR,
                             start_new_session=True,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return {"ok": True, "detail": "Supervisor iniciado."}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "detail": str(e)}


# ── HTTP handler ─────────────────────────────────────────────────────────────
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # quiet
        pass

    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")  # never leak the ?t= token via Referer
        self.end_headers()
        self.wfile.write(body)

    def _static(self, rel):
        rel = rel.split("?", 1)[0].lstrip("/") or "index.html"
        path = os.path.normpath(os.path.join(WEB_DIR, rel))
        # Containment: require the resolved path to sit strictly under WEB_DIR (trailing sep
        # so "/web_evil" can't pass a bare startswith on "/web").
        base = os.path.normpath(WEB_DIR)
        if not (path == base or path.startswith(base + os.sep)) or not os.path.isfile(path):
            self.send_error(404)
            return
        with open(path, "rb") as fh:
            data = fh.read()
        ext = os.path.splitext(path)[1].lower()
        self.send_response(200)
        self.send_header("Content-Type", _CTYPES.get(ext, "application/octet-stream"))
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        self.wfile.write(data)

    def _local_host(self):
        """Reject requests whose Host isn't loopback — kills DNS-rebinding (an attacker page
        resolving its own hostname to 127.0.0.1 sends its hostname in Host, not localhost)."""
        host = (self.headers.get("Host", "") or "").rsplit(":", 1)[0].strip("[]")
        return host in ("127.0.0.1", "localhost", "::1", "")

    def _authed(self):
        tok = self.headers.get("X-Wizard-Token", "")
        return secrets.compare_digest(tok, TOKEN)

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/" or path.startswith("/web") or path in ("/app.css", "/app.js"):
            self._static(self.path if path != "/" else "index.html")
        else:
            self._static(self.path)

    def do_POST(self):
        if not self.path.startswith("/api/"):
            self.send_error(404)
            return
        if not self._local_host():
            self._json({"ok": False, "detail": "host no permitido"}, 403)
            return
        if not self._authed():
            self._json({"ok": False, "detail": "no autorizado"}, 403)
            return
        length = int(self.headers.get("Content-Length", 0) or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw.decode("utf-8") or "{}")
        except Exception:  # noqa: BLE001
            body = {}
        try:
            self._json(self.dispatch(self.path, body))
        except Exception as e:  # noqa: BLE001
            self._json({"ok": False, "detail": "error interno: %s" % e}, 500)

    # ── API router ──────────────────────────────────────────────────────────
    def dispatch(self, path, body):
        route = path[len("/api/"):].strip("/")

        if route == "state":
            return {"ok": True, **initial_state()}

        if route == "hermes/status":
            return {"ok": True, **hermes_snapshot()}

        if route == "agents/list":
            return {"ok": True, **agents_snapshot()}

        if route == "agent/action":
            return self._agent_action(body.get("slug", ""), body.get("action", ""))

        if route == "check":
            what = body.get("what")
            if what == "python":
                return checks.check_python()
            if what == "node":
                return checks.check_node()
            if what == "hermes":
                return checks.check_hermes()
            if what == "bridge":
                return checks.check_bridge(body.get("bridge_url",
                                                    "http://127.0.0.1:8790"))
            return {"ok": False, "detail": "chequeo desconocido"}

        if route == "provider/check":
            p = providers.get(body.get("provider", providers.DEFAULT_ID))
            if not p:
                return {"ok": False, "detail": "proveedor desconocido"}
            return p.check({"claude": body.get("claude") or which("claude")})

        if route == "provider/install":
            p = providers.get(body.get("provider", providers.DEFAULT_ID))
            if not p:
                return {"ok": False, "detail": "proveedor desconocido"}
            return p.install({})

        if route == "provider/login":
            return channels.claude_login(body.get("claude") or which("claude"))

        if route == "provider/login-status":
            return channels.claude_status(body.get("claude") or which("claude"))

        if route == "test-brain":
            claude = body.get("claude") or which("claude")
            ws = body.get("workspace") or os.path.join(
                os.path.expanduser("~"), "hermes-workspace")
            if not claude:
                return {"ok": False,
                        "detail": "No encontramos Claude Code. Complétalo en el paso anterior."}
            base = ensure_test_bridge(claude, ws)
            return checks.test_brain(base)

        if route == "telegram/validate":
            return telegram_setup.validate(body.get("token", ""))

        if route == "telegram/capture":
            return telegram_setup.capture_owner(body.get("token", ""), body.get("code") or None)

        if route == "telegram/brand":
            name = body.get("agent_name") or "Hermes"
            purpose = (body.get("purpose") or "Tu asistente personal.").strip()
            return telegram_setup.brand(
                body.get("token", ""),
                name=name,
                short_desc=purpose[:120],
                description=("Soy %s. %s" % (name, purpose))[:512],
                commands=config_writer.default_bot_commands())

        if route == "telegram/test":
            name = body.get("agent_name") or "tu agente"
            return telegram_setup.test_send(
                body.get("token", ""), body.get("chat_id"),
                "✅ ¡Conexión lista! Soy %s, tu agente. Escríbeme cuando quieras." % name)

        if route == "preview-prompt":
            md = config_writer.build_claude_md(
                body.get("identity", {}), body.get("usecase_ids", []))
            return {"ok": True, "markdown": md}

        if route == "selfcare/status":
            return selfcare.status()

        if route == "selfcare/preview":
            return selfcare.preview(body.get("key", "daily"))

        if route == "selfcare/install":
            return selfcare.install(keys=body.get("keys") or ("daily", "weekly"),
                                    schedules=body.get("schedules") or {},
                                    deliver=body.get("deliver") or None)

        if route == "selfcare/run":
            return selfcare.run_now(body.get("key", "daily"))

        if route == "selfcare/remove":
            return selfcare.remove(keys=body.get("keys") or ("daily", "weekly"))

        # The long-term memory has to be readable by a person, not just writable by the agent.
        if route == "obsidian/status":
            return obsidian.status()

        if route == "obsidian/install":
            return obsidian.install()

        if route == "obsidian/prepare":
            return obsidian.prepare()

        if route == "obsidian/open":
            return obsidian.open_vault()

        # What the agent proposes to build, and the owner's answer - which it reads back and
        # learns from, so a no stays a no.
        if route == "proposals/list":
            return proposals.listing(int(body.get("limit") or 40))

        if route == "proposals/decide":
            return proposals.decide(body.get("id", ""), body.get("state", ""),
                                    body.get("comment", ""))

        if route == "rescue/context":
            return {"ok": True, **rescue.collect_context(INSTALL_DIR, fast=True)}

        if route == "rescue/log":
            return rescue.read_log(body.get("limit", 20), INSTALL_DIR)

        if route == "rescue/conversations":
            return rescue.list_conversations(INSTALL_DIR, body.get("limit", 40))

        if route == "rescue/conversation":
            return rescue.get_conversation(body.get("id", ""), INSTALL_DIR)

        if route == "rescue/delete":
            return rescue.delete_conversation(body.get("id", ""), INSTALL_DIR)

        if route == "rescue/rename":
            return rescue.rename_conversation(body.get("id", ""), body.get("title", ""),
                                              INSTALL_DIR)

        if route == "rescue/start":
            return rescue.start_job(body.get("question", ""),
                                    allow_fix=bool(body.get("allow_fix")),
                                    install_dir=INSTALL_DIR,
                                    conversation_id=body.get("conversation_id") or None)

        if route == "rescue/poll":
            return rescue.poll_job(body.get("job_id", ""), body.get("cursor", 0))

        if route == "rescue/ask":
            return rescue.ask(body.get("question", ""),
                              allow_fix=bool(body.get("allow_fix")),
                              install_dir=INSTALL_DIR,
                              history=body.get("history") or [])

        if route.startswith("channel/"):
            return self._channel(route[len("channel/"):], body)

        if route == "apply":
            return self._apply(body)

        if route == "finish":
            stop_test_bridge()
            res = start_supervisor()
            return res

        if route == "shutdown":
            threading.Timer(0.5, _shutdown).start()
            return {"ok": True}

        return {"ok": False, "detail": "ruta desconocida"}

    def _apply(self, body):
        agent = body.get("agent") or {}
        mode = agent.get("mode", "default")
        provider_id = body.get("provider", providers.DEFAULT_ID)
        p = providers.get(provider_id)
        claude = body.get("claude") or which("claude")
        provider_env = p.bridge_env({"claude": claude}) if p else {}
        identity = dict(body.get("identity", {}))
        identity["owner_id"] = body.get("owner_id", "")
        hp = which("hermes")
        provisioned = None

        if mode == "new":
            name = identity.get("agent_name") or agent.get("name") or "agente"
            existing = [pr["name"] for pr in hermes_ctl.profile_list(hp)] if hp else []
            slug = agents_registry.unique_slug(name, existing, INSTALL_DIR)
            port = agents_registry.next_port(INSTALL_DIR)
            adir = agents_registry.agent_dir(slug, INSTALL_DIR)
            workspace = os.path.join(adir, "workspace")
            claude_config_dir = os.path.join(adir, "claude") if agent.get("isolate_claude") else ""
            if claude_config_dir:
                os.makedirs(claude_config_dir, exist_ok=True)
            # extra-agent gateways are supervised by OUR launcher, not started here
            profile, is_default, gateway_action = slug, False, None
            if hp:
                pc = hermes_ctl.profile_create(slug, description=identity.get("purpose", ""))
                if not pc["ok"]:
                    return {"ok": False, "detail": "No se pudo crear el perfil: " + pc["detail"]}
            provisioned = {"slug": slug, "name": name, "profile": profile, "port": port,
                           "workspace": workspace, "claude_config_dir": claude_config_dir,
                           "enabled": True, "gateway_enabled": bool(body.get("token")),
                           "bot_username": body.get("bot_username", "")}
        elif mode == "reconfigure" and agent.get("slug") and agent.get("slug") != "default":
            slug = agent["slug"]
            if not _safe_slug(slug):
                return {"ok": False, "detail": "Identificador de agente inválido."}
            rec = agents_registry.get(slug, INSTALL_DIR) or {}
            profile, is_default, gateway_action = slug, False, None
            port = int(rec.get("port") or agents_registry.next_port(INSTALL_DIR))
            workspace = rec.get("workspace") or os.path.join(
                agents_registry.agent_dir(slug, INSTALL_DIR), "workspace")
            claude_config_dir = rec.get("claude_config_dir", "")
        else:  # default agent (Walt's existing setup)
            slug, profile, is_default, gateway_action = "default", None, True, "restart"
            port = int(body.get("port", agents_registry.BASE_PORT))
            workspace = body.get("workspace") or _default_workspace()
            claude_config_dir = ""

        cfg = {
            "install_dir": INSTALL_DIR, "workspace": workspace, "python": sys.executable,
            "provider_env": provider_env, "repo": body.get("repo", "Walt9819/olivaw"),
            "telegram_bot_token": body.get("token", ""), "owner_id": body.get("owner_id", ""),
            "chat_id": body.get("chat_id", ""), "maintainer_id": body.get("maintainer_id", ""),
            "lang": body.get("lang", "es"), "identity": identity,
            "usecase_ids": body.get("usecase_ids", []), "tavily_key": body.get("tavily_key", ""),
            "hermes_config_path": body.get("hermes_config") or _find_hermes_config(),
            "hermes": hp, "port": port, "profile": profile, "is_default": is_default,
            "gateway_action": gateway_action,
        }
        res = config_writer.write_all(cfg)

        # Register the extra agent so the supervisor starts/keeps its bridge.
        if provisioned:
            agents_registry.upsert(provisioned, INSTALL_DIR)
            res["agent"] = provisioned
        elif mode == "reconfigure" and slug != "default":
            rec = agents_registry.get(slug, INSTALL_DIR) or {"slug": slug}
            rec.update({"profile": profile, "port": port, "workspace": workspace,
                        "claude_config_dir": claude_config_dir, "enabled": True,
                        "gateway_enabled": bool(body.get("token")) or rec.get("gateway_enabled", False)})
            if body.get("bot_username"):
                rec["bot_username"] = body["bot_username"]
            agents_registry.upsert(rec, INSTALL_DIR)
            res["agent"] = rec
        res["port"] = port
        return res

    def _channel(self, sub, body):
        profile = body.get("profile") or None    # None -> default agent (bare hermes)
        if sub == "whatsapp":
            return channels.whatsapp_pair(profile, cloud=bool(body.get("cloud")))
        if sub == "slack-manifest":
            return channels.slack_manifest(profile)
        if sub == "slack-setup":
            return channels.slack_setup(profile)
        if sub == "webhook-add":
            if not body.get("name"):
                return {"ok": False, "detail": "Falta el nombre de la ruta."}
            return channels.webhook_add(body["name"], body.get("description", ""),
                                        body.get("deliver", "telegram"),
                                        body.get("prompt", ""), profile)
        if sub == "webhook-test":
            return channels.webhook_test(body.get("name", ""), profile)
        if sub == "email-save":
            res = channels.email_save(profile, body.get("host", ""), body.get("port", 587),
                                      body.get("user", ""), body.get("password", ""),
                                      body.get("from_addr", ""), body.get("secure", "starttls"))
            if res.get("ok"):
                ws = body.get("workspace") or _default_workspace()
                smtp = os.path.join(INSTALL_DIR, "src", "tools", "smtp_send.py")
                config_writer.append_email_capability(ws, smtp)
            return res
        if sub == "email-test":
            return channels.email_test(body.get("host", ""), body.get("port", 587),
                                       body.get("user", ""), body.get("password", ""),
                                       body.get("from_addr", ""), body.get("to_addr", ""),
                                       body.get("secure", "starttls"))
        if sub == "send-test":
            return channels.send_test(body.get("target", ""),
                                      body.get("text", "Prueba desde el asistente ✅"), profile)
        if sub == "whatsapp-qr":
            return channels.whatsapp_qr(profile)
        if sub == "whatsapp-save":
            return channels.whatsapp_save(profile, body.get("allowed_users", ""),
                                          body.get("home_channel", ""))
        if sub == "email-platform-save":
            return channels.email_platform_save(
                profile, body.get("address", ""), body.get("password", ""),
                body.get("smtp_host", ""), body.get("smtp_port", 587),
                body.get("imap_host", ""), body.get("allowed_users", ""),
                body.get("home_address", ""))
        if sub == "gchat-save":
            return channels.google_chat_save(profile, body.get("service_account", ""),
                                             body.get("allowed_users", ""),
                                             body.get("home_space", ""))
        if sub == "tools-setup":
            return channels.tools_setup(profile)
        if sub == "history-status":
            return channels.history_status(profile)
        if sub == "history-enable":
            return channels.history_enable(profile)
        if sub == "sessions-recent":
            return channels.sessions_recent(profile)
        if sub == "mcp-catalog":
            return channels.mcp_catalog(profile)
        if sub == "mcp-list":
            return channels.mcp_list(profile)
        if sub == "mcp-install":
            return channels.mcp_install(body.get("name", ""), profile)
        if sub == "mcp-add":
            return channels.mcp_add(body.get("name", ""), body.get("url", ""), profile)
        return {"ok": False, "detail": "canal desconocido"}

    def _agent_action(self, slug, action):
        hp = which("hermes")
        if slug == "default":
            if action in ("stop", "start", "restart"):
                r = hermes_ctl.gateway(action, hp)
                return {"ok": r["ok"], "detail": r["detail"]}
            return {"ok": False, "detail": "Esa acción no aplica al agente principal."}
        if not _safe_slug(slug):
            return {"ok": False, "detail": "Identificador de agente inválido."}
        rec = agents_registry.get(slug, INSTALL_DIR)
        if not rec:
            return {"ok": False, "detail": "Agente no encontrado."}
        if action == "stop":
            rec["enabled"] = False
            agents_registry.upsert(rec, INSTALL_DIR)
            if hp and hermes_ctl.profile_exists(slug, hp):
                hermes_ctl.gateway("stop", hp, profile=slug)
            return {"ok": True, "detail": "Agente pausado; su puente se detiene en segundos."}
        if action in ("start", "restore"):
            ws = rec.get("workspace") or os.path.join(
                agents_registry.agent_dir(slug, INSTALL_DIR), "workspace")
            os.makedirs(ws, exist_ok=True)
            rec["workspace"] = ws
            rec["enabled"] = True
            agents_registry.upsert(rec, INSTALL_DIR)
            if hp and hermes_ctl.profile_exists(slug, hp):
                hermes_ctl.gateway("start", hp, profile=slug)
            return {"ok": True, "detail": "Agente reactivado."}
        if action == "reset":
            if hp:
                hermes_ctl.profile_delete(slug, hp)
            adir = agents_registry.agent_dir(slug, INSTALL_DIR)
            if _under_agents(adir):   # containment guard: never rmtree outside INSTALL_DIR/agents
                try:
                    shutil.rmtree(adir, ignore_errors=True)
                except Exception:  # noqa: BLE001
                    pass
            agents_registry.remove(slug, INSTALL_DIR)
            return {"ok": True, "detail": "Agente eliminado por completo."}
        return {"ok": False, "detail": "Acción desconocida."}


def _shutdown():
    stop_test_bridge()
    os._exit(0)


def _free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def main():
    port = _free_port()
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = "http://127.0.0.1:%d/?t=%s" % (port, TOKEN)
    # `--sos` opens straight on the help console instead of the setup flow, so a shortcut can
    # take a stuck owner to Claude in one click (the bridge being down is exactly when the
    # normal path through Telegram is unavailable).
    sos = "--sos" in sys.argv[1:]
    if sos:
        url += "#rescue"
    print("\n  %s" % ("Ayuda de Olivaw — habla con Claude" if sos
                        else "Asistente de configuración de Hermes"))
    print("  Abre esta dirección en tu navegador si no se abre sola:\n")
    print("   ", url, "\n")
    try:
        webbrowser.open(url)
    except Exception:  # noqa: BLE001
        pass
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop_test_bridge()


if __name__ == "__main__":
    main()
