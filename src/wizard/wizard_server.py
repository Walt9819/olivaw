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
from urllib.parse import parse_qs, urlparse

# allow running as a script (python src/wizard/wizard_server.py) or as a module
if __package__ in (None, ""):
    _HERE = os.path.dirname(os.path.abspath(__file__))
    sys.path.insert(0, os.path.dirname(_HERE))
    # And take THIS directory back off. Python puts a script's own directory first on
    # sys.path, which here is src/wizard - a directory that contains numbers.py. From
    # then on stdlib `numbers` resolves to this package's module, which cannot be
    # imported outside its package, so every library that imports `numbers` fails. The
    # failure is silent where it matters: Pillow swallows a plugin that will not import,
    # registers no PNG decoder, and simply stops being able to open images - which is how
    # an uploaded avatar came to be stored exactly as it arrived, original container and
    # all, instead of being decoded and re-encoded by us. (The same shadow is why
    # importing this file printed a warning about _decimal being initialised twice.)
    # Nothing imports a wizard module bare - they are all `from . import x` or
    # `from wizard import x` - so removing it costs nothing.
    sys.path[:] = [d for d in sys.path if os.path.abspath(d or ".") != _HERE]
    from wizard import updates as updates_mod
    from wizard import (agent_info, agents_registry, avatars, browser_setup,
                        channels, checks,
                        config_writer, browser_label, connections, context_policy,
                        cron_patch, cronjobs, hermes_ctl, image_setup, numbers, obsidian,
                        pairing, proposals, providers, relay_client, remote, renaming,
                        rescue, selfcare, session_health, talk, talk_access, telegram_health,
                        console_one, usage, vault,
                        telegram_setup, usecases)
    from wizard import workspace as wsdir   # aliased: `workspace` is a local
                                            # variable name elsewhere in this file
    from wizard.procutil import http_json, which
else:
    from . import updates as updates_mod
    from . import (agent_info, agents_registry, avatars, browser_setup,
                   channels, checks,
                   config_writer, browser_label, connections, context_policy, cron_patch,
                   cronjobs, hermes_ctl, image_setup, numbers, obsidian, pairing,
                   proposals, providers, relay_client, remote, renaming, rescue,
                   selfcare, session_health, talk, talk_access, telegram_health, telegram_setup,
                   console_one, usage, vault,
                   usecases)
    from . import workspace as wsdir        # aliased: see above
    from .procutil import http_json, which

# AFTER the bootstrap above, never before it: this file runs as a script from the Olivaw
# shortcut, and `src` is only importable once that sys.path.insert has run. An import up
# with the stdlib ones died with ModuleNotFoundError - and because the shortcut launches it
# with pythonw.exe, which has no console, the failure was completely silent: clicking the
# icon simply did nothing.
from winspawn import CREATE_NEW_PROCESS_GROUP, quiet     # noqa: E402
import intercom                                          # noqa: E402
import teams                                             # noqa: E402
import teams_advisor                                     # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))          # .../src/wizard
SRC_DIR = os.path.dirname(HERE)                            # .../src
CODE_ROOT = os.path.dirname(SRC_DIR)                       # the copy of Olivaw running now

# Where this MACHINE's Olivaw lives, which is not necessarily where this code lives. The
# wizard can be started from a source checkout while the supervisor that actually reads
# agents.json and launches each agent's bridge runs from the installed copy. Writing agent
# state next to the code in that situation registers an agent nothing ever reads: its
# Telegram bot answers and its brain never starts, so the owner gets silence from a setup
# that said it succeeded.
INSTALL_DIR = agents_registry.install_root()
RUNNING_FROM_COPY = os.path.abspath(CODE_ROOT) != os.path.abspath(INSTALL_DIR)

WEB_DIR = os.path.join(HERE, "web")                        # always the running code's UI
# The throwaway "probar el cerebro" bridge is this code's job; the supervisor is the
# install's, so starting the wrong launcher would leave two supervisors competing.
BRIDGE_PY = os.path.join(SRC_DIR, "claude_bridge.py")
_INSTALLED_LAUNCHER = os.path.join(INSTALL_DIR, "src", "launcher.py")
LAUNCHER_PY = (_INSTALLED_LAUNCHER if os.path.isfile(_INSTALLED_LAUNCHER)
               else os.path.join(SRC_DIR, "launcher.py"))
TEST_PORT = 8788
TEST_URL = "http://127.0.0.1:%d" % TEST_PORT

TOKEN = secrets.token_urlsafe(24)

_RUNNING = None


def running_version():
    """The version of the code this process is actually executing.

    Read once and kept: VERSION on disk is rewritten by an update while this process
    goes on running the modules it imported at start. The point of telling another
    console our version is to say what we ARE, not what the folder now says.
    """
    global _RUNNING
    if _RUNNING is None:
        try:
            here = os.path.dirname(os.path.dirname(os.path.dirname(
                os.path.abspath(__file__))))
            with open(os.path.join(here, "VERSION"), encoding="utf-8") as fh:
                _RUNNING = fh.read().strip() or "?"
        except OSError:
            _RUNNING = "?"
    return _RUNNING
MAX_BODY_BYTES = 40 * 1024 * 1024   # see do_POST: the chat's attachments set this floor
_test_bridge = None  # Popen of the throwaway bridge used by "probar el cerebro"

import re as _re
_SLUG_RE = _re.compile(r"^[a-z0-9]{1,24}$")


def _default_engine():
    """The brain an agent runs on unless it names its own.

    The same rule launcher.py applies when it starts a bridge, read from the same
    file - so a number labelled 'codex' was produced by a codex process and not by
    whatever a session row happened to call itself.
    """
    try:
        with open(os.path.join(INSTALL_DIR, "updater.config.json"),
                  encoding="utf-8") as fh:
            cfg = json.load(fh)
        got = ((cfg.get("env") or {}).get("OLIVAW_ENGINE") or "").strip().lower()
        return got or "claude"
    except Exception:  # noqa: BLE001
        return "claude"


def _target_profile(body, allow_new=False):
    """WHICH agent this request is for — decided here, not by whatever the page sent.

    Every channel mutation used to read body["profile"] verbatim and hand it to
    `hermes --profile` and to a path join. A name for an agent that does not exist would
    then be created on the spot: the QR pairs, the session lands on disk, the call returns
    success, and the gateway that is actually running never sees any of it. That is the
    profile mismatch that made a configured WhatsApp bot answer nobody.

    Raises ValueError, which do_POST turns into a plain 400 with the reason.
    """
    return agents_registry.resolve_profile(body.get("profile"), INSTALL_DIR,
                                           allow_new=allow_new)


def _dashboard(fast=False):
    """The machine at a glance: the brain's session, every agent, and the update state.

    One call rather than the page assembling six, because the dashboard is the first thing
    the owner sees and six round trips is six chances to paint half a picture. `fast` skips
    what leaves the machine so it can render immediately and refine after.
    """
    hp = which("hermes")
    agents = agents_snapshot()
    rows = [agents["default"]] + list(agents.get("extra") or [])
    out = []
    for a in rows:
        prof = None if a.get("is_default") else (a.get("profile") or a.get("slug"))
        try:
            snap = connections.snapshot(prof, hp, INSTALL_DIR, port=a.get("port"), fast=fast)
        except Exception as e:  # noqa: BLE001
            snap = {"ok": False, "channels": [], "detail": str(e)}
        out.append({
            "slug": a.get("slug"), "name": a.get("name") or a.get("slug"),
            "is_default": bool(a.get("is_default")),
            "gateway_running": a.get("gateway_running"),
            "bridge_up": a.get("bridge_up"),
            "missing_profile": bool(a.get("missing_profile")),
            "port": a.get("port"),
            "channels": snap.get("channels") or [],
        })
    return {
        "ok": True,
        # The session is machine-wide: the brain is shared, so one expired login silences
        # every agent at once. That is why it leads the dashboard rather than sitting in
        # one agent's page.
        "session": session_health.status(install_dir=INSTALL_DIR),
        "agents": out,
        "update": updates_mod.status(INSTALL_DIR),
        "supervisor": updates_mod.supervisor(INSTALL_DIR),
        "fast": bool(fast),
    }


def _port_for_profile(profile):
    """The brain-bridge port of one agent, so a status call can report its bridge too."""
    if not profile or profile == "default":
        return agents_registry.BASE_PORT
    for a in agents_registry.list_agents(INSTALL_DIR):
        if (a.get("profile") or a.get("slug")) == profile:
            try:
                return int(a.get("port"))
            except (TypeError, ValueError):
                return None
    return None


def _queue_gateway_reload(profile):
    """A number only exists once the gateway has restarted and read the new platform list.

    Queued rather than done here: restarting mid-conversation would drop whatever the agent
    is in the middle of, and the supervisor already knows how to wait for idle.
    """
    try:
        context_policy.mark_pending(profile or None)
        return {"ok": True, "queued": True,
                "detail": "El número quedará activo en cuanto el agente esté libre "
                          "(normalmente menos de un minuto)."}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "detail": str(e)}


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
    # One name per agent across the whole console. The owner can say what the other agents
    # should call this one ("Chalenus"), and a sidebar that went on saying "Agente
    # principal" would leave her reading two names for the same agent on one screen - the
    # exact confusion that override exists to end.
    names = intercom.config().get("names") or {}
    # Give every agent its colour before anyone draws a face with it. A READ must not
    # hand out a slot (browser_label.slot_for), so without this every agent that has
    # never had its browser opened honestly answers "slot 0" - and the sidebar showed
    # seven faces in the same blue, which is precisely what the colours exist to stop.
    # Assigning for agents that actually exist is the case assign_all is for.
    try:
        browser_label.assign_all(
            ["default"] + [a.get("slug") for a in agents_registry.list_agents(INSTALL_DIR)],
            INSTALL_DIR)
    except Exception:  # noqa: BLE001
        pass
    # Asking about the agents AT THE SAME TIME, not one after another. Each of these
    # answers costs a `hermes` CLI launch - about three and a half seconds, nearly all of
    # it Python starting up - and there are three per agent. Serially that is seventy
    # seconds on a seven-agent machine, which is what the phone's first screen used to
    # spend showing a spinner. They do not depend on each other, so they wait together.
    def _about(slug):
        """(gateway_running, owners, missing_profile) for one agent."""
        if not hp:
            return (None, [], False)
        if slug == "default":
            return (hermes_ctl.gateway_status(hp).get("running"), owners, False)
        if not hermes_ctl.profile_exists(slug, hp):
            return (None, [], True)   # code/profile deleted -> restore candidate
        return (hermes_ctl.gateway_status(hp, profile=slug).get("running"),
                hermes_ctl.pairing_list(hp, profile=slug).get("approved", []), False)

    _rows = agents_registry.list_agents(INSTALL_DIR)
    _slugs = ["default"] + [a.get("slug") for a in _rows]
    _facts = {}
    try:
        import concurrent.futures
        # Bounded: one `hermes` process per worker, and this runs on a laptop that is
        # also running the agents themselves.
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(8, len(_slugs))) as ex:
            for slug, got in zip(_slugs, ex.map(_about, _slugs)):
                _facts[slug] = got
    except Exception:  # noqa: BLE001
        # A pool that will not start is not a reason to show no agents.
        for slug in _slugs:
            try:
                _facts[slug] = _about(slug)
            except Exception:  # noqa: BLE001
                _facts[slug] = (None, [], False)

    _d_running, _d_owners, _ = _facts.get("default", (None, owners, False))
    default = {
        "slug": "default", "name": names.get("default") or "Agente principal",
        "profile": "default",
        "port": agents_registry.BASE_PORT, "workspace": _default_workspace(),
        "is_default": True,
        "gateway_running": _d_running,
        "bridge_up": _bridge_up(agents_registry.BASE_PORT),
        "owners": _d_owners,
    }
    # The face rides with the row rather than in a call of its own: the sidebar draws
    # every agent on every repaint, and a second round trip per agent would make the
    # list arrive in pieces. It is four short keys, not the image.
    default["look"] = avatars.look("default", default["name"], INSTALL_DIR)
    # Which team each one is in, from the same read for all of them. It rides with
    # the row for the reason the face does: a list that draws every agent should not
    # need a second call per agent to say the one thing that groups them.
    try:
        _tstatus, _tdata = teams.read(INSTALL_DIR)
    except Exception:  # noqa: BLE001
        _tdata = None

    def _team_badge(slug):
        """{id, name, icon} for the team this agent is in, or None."""
        if not _tdata:
            return None
        try:
            g = teams.team_of(_tdata, slug)
        except Exception:  # noqa: BLE001
            return None
        if not g:
            return None
        return {"id": g.get("id", ""), "name": g.get("name", ""),
                "icon": g.get("icon", "")}

    default["team"] = _team_badge("default")
    extra = []
    for a in _rows:
        slug = a.get("slug")
        row = dict(a)
        row["is_default"] = False
        if names.get(slug):
            row["name"] = names[slug]
        row["bridge_up"] = _bridge_up(a.get("port", 0)) if a.get("port") else False
        running, approved, missing = _facts.get(slug, (None, [], False))
        row["gateway_running"] = running
        if missing:
            row["missing_profile"] = True
        else:
            row["owners"] = approved
        # Left on this thread on purpose: look() may assign and write a face, and the
        # point of the pool above is the waiting, not the bookkeeping.
        row["look"] = avatars.look(slug, row.get("name") or slug, INSTALL_DIR)
        row["team"] = _team_badge(slug)
        extra.append(row)
    return {"default": default, "extra": extra}


def _configured_engine(profile=None, port=None):
    """Which brain this machine is set up for.

    Delegates to rescue.configured_engine so the wizard and the help console can never
    disagree about the brain. This used to be a second, worse copy: it read one key in
    updater.config.json and answered "claude" for everything else, which is every install
    written before that key existed and every extra agent (whose engine lives in
    agents.json). See rescue.configured_engine for what it consults now.
    """
    try:
        return rescue.configured_engine(INSTALL_DIR, profile=profile, port=port)
    except Exception:  # noqa: BLE001
        return "claude"


def _provider_for_engine(engine):
    for p in providers.all_providers():
        if p.engine == engine and p.status == "ready":
            return p.id
    return providers.DEFAULT_ID


def initial_state():
    claude = which("claude")
    workspace = _default_workspace()
    return {
        "agents": agents_snapshot(),
        "lang": "es",
        # Shown in the sidebar. An owner who cannot see a version number cannot tell an
        # install that updated itself from one that has been stuck for a month, which is
        # exactly the doubt that made us go looking for a broken updater.
        "version": updates_mod.current_version(INSTALL_DIR),
        "defaults": {
            "install_dir": INSTALL_DIR,
            "workspace": workspace,
            "python": sys.executable,
            "claude": claude or "",
            "codex": which("codex") or "",
            "node": which("node") or "",
            "hermes": which("hermes") or "",
            "hermes_config": _find_hermes_config(),
            "repo": _existing_repo() or "Walt9819/olivaw",
        },
        "providers": providers.public_list(),
        # What this machine is set up for: the wizard opens on it, so the choice made in the
        # installer is not asked again.
        "default_provider": _provider_for_engine(_configured_engine()),
        # What we recommend, which is a different thing and keeps the badge honest.
        "recommended_provider": providers.DEFAULT_ID,
        "usecases": usecases.public_list(),
        "smtp_providers": channels.SMTP_PROVIDERS,
        "image_options": channels.IMAGE_OPTIONS,
        "google_presets": channels.GOOGLE_PRESETS,
    }


def _cli_paths(body=None):
    """Everything an adapter might need to find its CLI. Both keys are always present, so the
    front-end does not need to know which one a given provider cares about."""
    body = body or {}
    return {"claude": body.get("claude") or which("claude") or "",
            "codex": body.get("codex") or which("codex") or "",
            "workspace": body.get("workspace") or ""}


def _provider(body=None):
    body = body or {}
    return (providers.get(body.get("provider", providers.DEFAULT_ID))
            or providers.get(providers.DEFAULT_ID))


# ── test bridge (for "probar el cerebro") ────────────────────────────────────
def _port_open(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.4)
        return s.connect_ex(("127.0.0.1", port)) == 0


def ensure_test_bridge(provider_env, workspace):
    """Start a throwaway bridge on TEST_PORT if not already up. Returns base url.

    `provider_env` comes from the selected provider's adapter, so the test exercises the brain
    the owner actually picked. A test bridge already running a DIFFERENT engine is retired first
    — otherwise switching provider mid-wizard would keep testing the previous one.
    """
    global _test_bridge
    want = (provider_env or {}).get("OLIVAW_ENGINE", "claude")
    ok, data, _s = http_json(TEST_URL + "/health", timeout=3)
    if ok:
        if not isinstance(data, dict) or data.get("engine", "claude") == want:
            return TEST_URL
        try:
            if _test_bridge:
                _test_bridge.terminate()
                _test_bridge.wait(timeout=10)
        except Exception:  # noqa: BLE001
            pass
        for _ in range(20):
            if not http_json(TEST_URL + "/health", timeout=1)[0]:
                break
            time.sleep(0.5)
    env = os.environ.copy()
    env.update({k: str(v) for k, v in (provider_env or {}).items()})
    if workspace:
        env["CLAUDE_BRIDGE_WORKSPACE"] = workspace
        os.makedirs(workspace, exist_ok=True)
    _test_bridge = subprocess.Popen(
        [sys.executable, BRIDGE_PY, "--port", str(TEST_PORT)],
        **quiet(env=env, cwd=SRC_DIR,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
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
    # The heartbeat is the accurate signal: a bridge answering on 8790 can be an ORPHAN
    # left by a dead supervisor, still serving turns while nothing supervises or updates
    # it — and refusing to start on that evidence is how a machine ends up never updating.
    # The /health check stays as the fallback for installs older than the heartbeat.
    sup = updates_mod.supervisor(INSTALL_DIR)
    if sup["running"]:
        return {"ok": True, "detail": "El supervisor ya está corriendo; aplicará la "
                                      "nueva configuración en unos segundos."}
    if not sup["known"] and http_json("http://127.0.0.1:8790/health", timeout=3)[0]:
        return {"ok": True, "detail": "El supervisor ya está corriendo; aplicará la "
                                      "nueva configuración en unos segundos."}
    if not os.path.exists(LAUNCHER_PY):
        return {"ok": False, "detail": "No se encontró launcher.py."}
    try:
        if os.name == "nt":
            pyw = sys.executable.replace("python.exe", "pythonw.exe")
            exe = pyw if os.path.exists(pyw) else sys.executable
            # NOT detached: DETACHED_PROCESS makes Windows ignore CREATE_NO_WINDOW, and
            # a console child of a detached process opens its own visible console.
            subprocess.Popen([exe, LAUNCHER_PY], **quiet(
                cwd=INSTALL_DIR, creationflags=CREATE_NEW_PROCESS_GROUP,
                close_fds=True))
        else:
            subprocess.Popen([sys.executable, LAUNCHER_PY], **quiet(
                cwd=INSTALL_DIR, start_new_session=True,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
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

    def _avatar(self):
        """GET /api/avatar?slug=X&t=TOKEN&v=N - the one image route in this server.

        A GET because it is an <img src>, and the only reason that is acceptable is that
        the page itself is already opened with ?t= (see _json's Referrer-Policy note).
        Same two gates as every POST - loopback Host, and the token - plus:

        * the bytes are re-sniffed in avatars.read() and a file that no longer looks like
          an image is not served at all;
        * nosniff, because this is the owner's token-bearing origin and a mislabelled
          response that the browser decided was HTML would run there;
        * ?v= changes whenever the picture does, so it is cached hard and still never
          shows the previous face.
        """
        if not self._local_host():
            self.send_error(403)
            return
        q = parse_qs(urlparse(self.path).query)
        if not secrets.compare_digest((q.get("t") or [""])[0], TOKEN):
            self.send_error(403)
            return
        got = avatars.read((q.get("slug") or [""])[0], INSTALL_DIR)
        if not got:
            self.send_error(404)
            return
        data, mime = got
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'none'; sandbox")
        self.send_header("Cache-Control", "private, max-age=86400")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        # The one thing a console answers without a token, and the only thing worth
        # answering: which app this is and which code it is RUNNING. Another console
        # starting up reads it to decide whether to replace this one or simply hand
        # the owner its address.
        if path == "/who":
            return self._json({"app": "olivaw-console",
                               "version": running_version(), "pid": os.getpid()})
        if path == "/api/avatar":
            self._avatar()
        elif path == "/" or path.startswith("/web") or path in ("/app.css", "/app.js"):
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
        # Until the chat could carry files every request here was a few hundred bytes, so
        # nothing bounded this read: one Content-Length was one allocation of that size.
        # An upload is the first body worth a limit, and 40MB leaves room for the biggest
        # attachment talk.py accepts (25MB) once base64 has added its third.
        if length > MAX_BODY_BYTES:
            self._json({"ok": False,
                        "detail": "Eso es demasiado grande para mandarlo de una vez."}, 413)
            return
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw.decode("utf-8") or "{}")
        except Exception:  # noqa: BLE001
            body = {}
        try:
            self._json(self.dispatch(self.path, body))
        except (ValueError, agents_registry.Conflict) as e:
            # A rejected target or a colliding registry row: the request named something
            # this machine does not have, or would have made two agents share one
            # resource. Say which, plainly - a 500 "error interno" here reads as a bug in
            # Olivaw and sends the owner looking in the wrong place.
            self._json({"ok": False, "detail": str(e)}, 400)
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

        # A name is carried in four places at once - see wizard/renaming.py. Renaming
        # from here is the only way all four move together, which is why the console no
        # longer has a box that edits just one of them.
        if route == "agent/rename":
            return self._agent_rename(body)

        # The face. Three shapes: a picture she uploaded, an emoji, or initials in the
        # agent's own colour (which is the colour its Chrome window wears).
        if route == "avatar/get":
            return avatars.picture((body.get("slug") or "").strip().lower(),
                                   INSTALL_DIR)

        if route == "avatar/set":
            slug = (body.get("slug") or "").strip().lower()
            if body.get("data"):
                res = avatars.set_picture(slug, body.get("data", ""), INSTALL_DIR)
            elif body.get("clear"):
                res = avatars.clear(slug, INSTALL_DIR)
            else:
                res = avatars.set_mark(slug, emoji=body.get("emoji", ""),
                                       color=body.get("color", ""),
                                       install_dir=INSTALL_DIR)
            # Deliberately NOT the whole agents snapshot: see _agent_rename.
            return res

        # ── the phone ──────────────────────────────────────────────────────
        # This whole block is desktop-only. pairing.NEVER keeps every one of these
        # unreachable from a phone, because a contact point that can grant itself
        # permissions, pair a second device or move the rendezvous is not a contact
        # point, it is a second owner.
        if route == "phone/status":
            st = remote.state(INSTALL_DIR)
            st["relay"] = relay_client.state(INSTALL_DIR)
            return {"ok": True, **st}

        if route == "phone/enable":
            on = bool(body.get("on"))
            pairing.set_enabled(INSTALL_DIR, on)
            if not on:
                pairing.cancel_offer(INSTALL_DIR)
                remote.stop()
            else:
                remote.start(INSTALL_DIR)
            return {"ok": True, **remote.state(INSTALL_DIR)}

        if route == "phone/offer":
            res = pairing.offer(INSTALL_DIR, caps=body.get("caps"))
            if res.get("ok"):
                # Open the door now rather than on the supervisor's next tick: the
                # owner is looking at a code that is only good for three minutes.
                remote.start(INSTALL_DIR)
                st = remote.state(INSTALL_DIR)
                res.update({"addresses": st.get("addresses", []),
                            "port": st.get("port"), "code": st.get("code", ""),
                            "fingerprint": st.get("fingerprint", ""),
                            "running": st.get("running"),
                            "firewall": st.get("firewall", {})})
            return res

        if route == "phone/cancel":
            pairing.cancel_offer(INSTALL_DIR)
            return {"ok": True}

        if route == "phone/revoke":
            return pairing.revoke(INSTALL_DIR, body.get("id", ""))

        if route == "phone/caps":
            return pairing.set_caps(INSTALL_DIR, body.get("id", ""), body.get("caps"))

        if route == "phone/rename":
            return pairing.rename(INSTALL_DIR, body.get("id", ""), body.get("name", ""))

        if route == "phone/firewall":
            return remote.open_firewall()

        if route == "phone/relay":
            relay_client.save_config(INSTALL_DIR, url=body.get("url", ""),
                                     on=body.get("on"))
            relay_client.start(INSTALL_DIR, remote.PORT)
            return {"ok": True, **relay_client.state(INSTALL_DIR)}

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
            p = _provider(body)
            if not p:
                return {"ok": False, "detail": "proveedor desconocido"}
            return p.check(_cli_paths(body))

        if route == "provider/install":
            p = _provider(body)
            if not p:
                return {"ok": False, "detail": "proveedor desconocido"}
            return p.install(_cli_paths(body))

        if route == "provider/login":
            return _provider(body).login(_cli_paths(body))

        if route == "provider/login-status":
            return _provider(body).login_status(_cli_paths(body))

        if route == "test-brain":
            p = _provider(body)
            paths = _cli_paths(body)
            ws = body.get("workspace") or os.path.join(
                os.path.expanduser("~"), "hermes-workspace")
            cli = paths.get(p.cli_key) or ""
            if not cli:
                return {"ok": False,
                        "detail": "No encontramos %s. Complétalo en el paso anterior."
                                  % (p.cli_label or p.label)}
            paths["workspace"] = ws
            base = ensure_test_bridge(p.bridge_env(paths), ws)
            # `engine` lets the check prove the bridge on that port is the brain just
            # chosen, and ask the RIGHT CLI about its session before blaming a login.
            return checks.test_brain(base, brain=(p.cli_label or p.label),
                                     engine=p.engine)

        if route == "telegram/health":
            return telegram_health.check(_target_profile(body), which("hermes"))

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

        # Every scheduled routine on the machine, and how loudly each one speaks. Read
        # from Hermes' own jobs.json rather than from `hermes cron list`, because the
        # printed form has nowhere to put an answer back.
        # How much work each agent and each team has done. Read from Hermes' own
        # session rows, which it keeps the same way whichever brain is behind the
        # bridge - so this counts Claude Code and Codex without asking either.
        if route == "stats/usage":
            try:
                _tstat, _tdat = teams.read(INSTALL_DIR)
            except Exception:  # noqa: BLE001
                _tdat = None
            return usage.report(intercom.roster(INSTALL_DIR), _tdat,
                                which("hermes"), INSTALL_DIR,
                                days=body.get("days") or usage.DEFAULT_DAYS,
                                default_engine=_default_engine())

        if route == "cron/list":
            st = cronjobs.state(roster=intercom.roster(INSTALL_DIR),
                                hermes_exe=which("hermes"))
            # Asked about ONE agent, answer about one agent.
            #
            # The console asks about the machine and gets {agents: [...]}, which is
            # what it draws. The phone asks about the agent whose screen is open and
            # got the same thing - a shape with no "jobs" in it at all - so it read
            # an empty list and drew an empty routines tab for every agent, forever,
            # on a machine with seventeen jobs. It never errored; there was simply
            # nothing where it looked.
            #
            # Only when a profile is named, so the console is untouched. It also
            # stops a phone that asked about one agent being sent the job names of
            # the other six.
            want = (body or {}).get("profile")
            if want is None:
                return st
            # 'default' internally is None, and in the roster it is the string
            # 'default'. Comparing one to the other quietly matched nothing, which
            # is how the agent with sixteen jobs came back with none.
            want = _target_profile(body) or "default"
            for a in (st.get("agents") or []):
                if (a.get("profile") or a.get("slug")) == want:
                    return {"ok": True, "jobs": a.get("jobs") or [],
                            "agent": {k: a.get(k) for k in ("slug", "name", "profile")},
                            "detail": a.get("detail", ""),
                            "patch": st.get("patch")}
            return {"ok": True, "jobs": [], "detail": "",
                    "agent": {"slug": want, "profile": want}}

        # Two booleans on one job. `_target_profile` decides WHICH agent's jobs.json is
        # opened - a profile name straight from the page would be a path join into
        # whatever the browser felt like naming.
        if route == "cron/flags":
            res = cronjobs.set_flags(_target_profile(body), body.get("id", ""),
                                     clean=body.get("clean"),
                                     only_on_error=body.get("only_on_error"))
            # The switches are inert until Hermes' scheduler knows to read them, and the
            # owner has just told us she wants one. Applying it here rather than waiting
            # for the supervisor's next sweep is what makes the checkbox mean something
            # before she looks away from the screen.
            if res.get("ok"):
                try:
                    res["patch"] = cron_patch.ensure(hermes_exe=which("hermes"))
                except Exception as e:  # noqa: BLE001
                    res["patch"] = {"ok": False, "state": "error", "detail": str(e)}
            return res

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

        # Where the agent's own files live. Asked during creation rather than left to an
        # advanced fold, because it decides what gets backed up and what a sync client
        # will fight over.
        if route == "workspace/suggest":
            return {"ok": True,
                    "path": wsdir.suggest(body.get("agent_name", "")),
                    "legacy": wsdir.legacy_default(),
                    "picker": wsdir.picker_available()}
        if route == "workspace/inspect":
            return wsdir.inspect(body.get("path", ""), install_dir=INSTALL_DIR)
        if route == "workspace/pick":
            return wsdir.pick(body.get("start", ""))

        # How long a conversation lives before it is summarised or started over. The single
        # biggest driver of what an agent costs to run, and Hermes' own default is the
        # expensive one, so it is a visible setting rather than a buried preference.
        if route == "policy/get":
            return self._policy_get(_target_profile(body))
        if route == "policy/save":
            return self._policy_save(body)

        # Which browser the agent drives. Turning this on opens a real window on the
        # owner's screen, so it is a button she presses, never something we decide.
        if route == "images/status":
            return image_setup.status(_target_profile(body), install_dir=INSTALL_DIR)

        # The brain's LOGIN, which expires. Nothing used to announce that: the agent just
        # went quiet on every channel at once, and the repair is fifteen seconds of
        # clicking. See wizard/session_health.py.
        if route == "session/status":
            return session_health.status(install_dir=INSTALL_DIR)
        if route == "session/login":
            # Launches the brain's OWN sign-in (it opens the browser and completes the
            # exchange itself). Olivaw never sees a credential.
            return session_health.login(install_dir=INSTALL_DIR, paths=_cli_paths(body))
        if route == "session/verify":
            # After the owner finishes in the browser. `deep` runs a real turn through the
            # bridge, which is the only evidence worth reporting as "it works again".
            return session_health.verify(install_dir=INSTALL_DIR,
                                         deep=body.get("deep", True) is not False)

        # Everything the dashboard shows, in one call.
        if route == "dashboard":
            return _dashboard(bool(body.get("fast")))

        # How this agent is reachable, in one call. `fast` answers from local state only so
        # a panel paints immediately; the same route without it measures Telegram against
        # Telegram. See wizard/connections.py.
        if route == "connections/status":
            prof = _target_profile(body)
            return connections.snapshot(prof, which("hermes"), INSTALL_DIR,
                                        port=_port_for_profile(prof),
                                        fast=bool(body.get("fast")))

        # Talking to the agent from this screen, through Hermes' own api_server platform -
        # so it is the SAME agent, with its persona, memory and skills, and not the raw
        # brain behind the bridge. wizard/talk.py explains why that distinction matters.
        # What this agent IS. One round trip, because the screen that asks is on a
        # phone and three would be three waits.
        if route == "agent/info":
            prof = _target_profile(body)
            st = talk.status(prof, which("hermes"), INSTALL_DIR)
            st.update(talk_access.flags(INSTALL_DIR, prof, st.get("enabled")))
            return {"ok": True,
                    "purpose": agent_info.purpose(prof, INSTALL_DIR),
                    "skills": agent_info.skills(prof),
                    "peers": agent_info.peers(prof, INSTALL_DIR,
                                              intercom.roster(INSTALL_DIR)),
                    "talk": st}
        if route == "agent/skill":
            return agent_info.skill_body(_target_profile(body),
                                         body.get("id", ""))
        if route == "agent/purpose":
            return agent_info.save_purpose(_target_profile(body),
                                           body.get("markdown", ""), INSTALL_DIR)
        # ── the keys the owner leaves for an agent ─────────────────────────────
        #
        # Only here, and deliberately: these routes are not in the phone's table at
        # all. A password is typed on the machine that will use it, into a page served
        # from 127.0.0.1, and never travels - not over the rendezvous, not over the
        # local network, not through a conversation. The phone can see that a key
        # EXISTS (through the agent, if it asks) and nothing more.
        if route == "creds/list":
            prof = _target_profile(body) or "default"
            try:
                return {"ok": True, "agent": prof,
                        "keeper": vault.keeper(INSTALL_DIR),
                        "keeper_label": vault.keeper_label(INSTALL_DIR),
                        "lock": vault.lock_state(INSTALL_DIR),
                        "credentials": vault.entries(INSTALL_DIR, prof),
                        "uses_per_hour": vault.USES_PER_HOUR,
                        "audit": vault.audit(INSTALL_DIR, limit=12, agent=prof)}
            except Exception as e:  # noqa: BLE001
                # Answered rather than raised: a 500 here reaches the owner as "this
                # computer cannot keep passwords", which is a different and much more
                # worrying sentence than "something went wrong, try again".
                return {"ok": False,
                        "detail": u"No pude leer las llaves: %s" % str(e)[:160]}
        if route == "creds/save":
            prof = _target_profile(body) or "default"
            platform_id = (body.get("platform") or "").strip()
            if not platform_id:
                return {"ok": False, "detail": "Falta de qué sitio es la llave."}
            try:
                res = vault.put(
                    INSTALL_DIR, prof, platform_id,
                    {"password": body.get("password") or "",
                     "totp": body.get("totp") or ""},
                    label=(body.get("label") or "").strip(),
                    url=(body.get("url") or "").strip(),
                    username=(body.get("username") or "").strip(),
                    secret_username=bool(body.get("secret_username")),
                    confirm=bool(body.get("confirm")),
                    note=(body.get("note") or "").strip())
            except vault.NoKeeper:
                return {"ok": False, "detail":
                        "Este equipo no tiene un llavero donde guardarla "
                        "(DPAPI en Windows, Llavero en Mac, libsecret en Linux). "
                        "No la guardo en un archivo suelto."}
            except ValueError as e:
                return {"ok": False, "detail": str(e)}
            res["credentials"] = vault.entries(INSTALL_DIR, prof)
            return res
        # A machine with no keystore of its own - a Linux server, most often - keeps
        # them under a passphrase instead. The key lives in this process and nowhere
        # else, so these three are the whole of its lifecycle: choose one, type it
        # after a reboot, and put it away.
        if route == "creds/passphrase":
            which_one = (body.get("do") or "set").strip()
            if which_one == "change":
                return vault.vault_lock.change_passphrase(
                    INSTALL_DIR, body.get("old", ""), body.get("new", ""),
                    lambda old, new: vault.reseal_all(INSTALL_DIR, old, new))
            return vault.vault_lock.set_passphrase(INSTALL_DIR,
                                                   body.get("new", ""))
        if route == "creds/unlock":
            res = vault.vault_lock.unlock(INSTALL_DIR, body.get("passphrase", ""))
            res["lock"] = vault.lock_state(INSTALL_DIR)
            return res
        if route == "creds/lock":
            vault.vault_lock.forget()
            return {"ok": True, "lock": vault.lock_state(INSTALL_DIR)}
        if route == "creds/allow":
            prof = _target_profile(body) or "default"
            res = vault.allow_once(INSTALL_DIR, prof,
                                   (body.get("platform") or "").strip())
            res["credentials"] = vault.entries(INSTALL_DIR, prof)
            return res
        if route == "creds/forget":
            prof = _target_profile(body) or "default"
            res = vault.forget(INSTALL_DIR, prof, (body.get("platform") or "").strip())
            res["credentials"] = vault.entries(INSTALL_DIR, prof)
            return res
        if route == "talk/status":
            prof = _target_profile(body)
            # The phone asks a smaller question, and asks it before every conversation
            # it opens.
            #
            # `status()` answers three - configured, listening, answering - and costs
            # about six seconds to do it: two subprocesses, a file, and an HTTP call to
            # a gateway that may be busy thinking. The phone reads two booleans out of
            # all that, and both of them are in a small file here (or, failing that, in
            # one cached config read). The console still asks the full question,
            # because "it is on but not listening yet" is exactly what it has to be
            # able to say.
            if body.get("quick"):
                decided = talk_access.decided_for_phone(INSTALL_DIR, prof)
                on = (talk.enabled_flag(prof, which("hermes"))
                      if decided is None else decided)
                out = {"ok": True, "quick": True}
                if decided is None:
                    out["enabled"] = on
                out.update(talk_access.flags(INSTALL_DIR, prof, on))
                return out
            st = talk.status(prof, which("hermes"), INSTALL_DIR)
            # Two answers, not one. The gateway underneath is what carries a
            # conversation; whether this computer and whether a phone may HAVE one
            # are separate questions, and collapsing them is what made the console
            # switch quietly decide what the phone could reach.
            st.update(talk_access.flags(INSTALL_DIR, prof, st.get("enabled")))
            return st
        if route == "talk/access":
            prof = _target_profile(body)
            # The switch itself, not the examination: this needs to know whether the
            # gateway is ON, which is one cached config read, and `status()` would
            # spend six seconds also finding out whether it is answering.
            gateway_on = talk.enabled_flag(prof, which("hermes"))
            where = (body.get("where") or "").strip()
            on = bool(body.get("on"))
            try:
                flags, want = talk_access.set_flag(INSTALL_DIR, prof, where, on,
                                                   gateway_on)
            except ValueError:
                return {"ok": False, "detail": "No sé de qué switch me hablas."}
            res = {"ok": True}
            # The gateway follows the pair: on while either wants it, off when
            # neither does. A flag the transport cannot carry is a switch that lies.
            if want and not gateway_on:
                res = talk.enable(prof, which("hermes"), INSTALL_DIR)
                if res.get("ok"):
                    res["restart"] = _queue_gateway_reload(prof)
            elif not want and gateway_on:
                res = talk.disable(prof, which("hermes"))
            res.update(flags)
            return res
        if route == "talk/enable":
            prof = _target_profile(body)
            res = talk.enable(prof, which("hermes"), INSTALL_DIR)
            if res.get("ok"):
                res["restart"] = _queue_gateway_reload(prof)
                # Turning the gateway on by the old route is still an answer to both
                # questions for an agent nobody has answered them for.
                talk_access.set_flag(INSTALL_DIR, prof, "desktop", True, True)
            return res
        if route == "talk/disable":
            prof = _target_profile(body)
            talk_access.set_flag(INSTALL_DIR, prof, "desktop", False,
                                 talk.status(prof, which("hermes"),
                                             INSTALL_DIR).get("enabled"))
            if talk_access.flags(INSTALL_DIR, prof, False)["phone"]:
                # Still wanted on a phone, so the gateway stays up; only the
                # console stops offering its chat.
                return {"ok": True, "detail": "Apagado aquí; sigue encendido en el teléfono."}
            return talk.disable(prof, which("hermes"))
        if route == "talk/sessions":
            return talk.sessions(_target_profile(body), which("hermes"),
                                 limit=int(body.get("limit") or 20),
                                 offset=int(body.get("offset") or 0))
        # Finding a conversation by what was said in it. An agent a month old has
        # hundreds, and scrolling is not a way to find anything.
        if route == "talk/search":
            return talk.search(_target_profile(body), which("hermes"),
                               query=body.get("query", ""),
                               limit=int(body.get("limit") or 25),
                               offset=int(body.get("offset") or 0),
                               deep=body.get("deep") is not False)
        if route == "talk/new":
            return talk.create(_target_profile(body), which("hermes"),
                               title=body.get("title", ""))
        if route == "talk/history":
            # No limit means the whole conversation, which is what the desktop asks
            # for - it is reading a file on its own disk. The phone asks for a page.
            before = body.get("before")
            return talk.history(_target_profile(body), which("hermes"),
                                session_id=body.get("session_id", ""),
                                install_dir=INSTALL_DIR,
                                limit=int(body.get("limit") or 0),
                                before=None if before is None else int(before))
        if route == "talk/send":
            return talk.send(_target_profile(body), which("hermes"),
                             session_id=body.get("session_id", ""),
                             text=body.get("text", ""),
                             files=body.get("files") or [])
        # Attaching and downloading are two routes rather than fields on talk/send because
        # they are two different moments: a file is uploaded while the owner is still
        # writing, and downloaded long after the answer arrived. Neither takes a path -
        # `attach` mints an id, `file` spends one. See wizard/talk.py.
        # A voice note: kept as a file in the agent's folder AND read out into words,
        # because a transcription is a reading of what was said, not a replacement.
        if route == "talk/voice":
            return talk.transcribe(name=body.get("name", ""),
                                   data_b64=body.get("data", ""),
                                   profile=_target_profile(body),
                                   install_dir=INSTALL_DIR,
                                   lang=(body.get("lang") or "es"))
        if route == "talk/voice_status":
            return talk.stt_status()
        if route == "talk/attach":
            return talk.save_upload(name=body.get("name", ""),
                                    data_b64=body.get("data", ""),
                                    profile=_target_profile(body),
                                    install_dir=INSTALL_DIR)
        if route == "talk/file":
            return talk.file_bytes(body.get("id", ""))

        # Several WhatsApp numbers on ONE agent - same persona, same instructions, several
        # business lines. See wizard/numbers.py for why this is a plugin-registered
        # platform per number rather than a second agent.
        if route == "numbers/status":
            return numbers.status(_target_profile(body), which("hermes"))
        if route == "numbers/add":
            prof = _target_profile(body)
            res = numbers.add(body.get("label", ""), prof,
                              allowed_users=body.get("allowed_users", ""),
                              main=bool(body.get("main")))
            if res.get("ok"):
                # Configure it NOW, not at the next supervisor pass: the owner is about to
                # be shown a QR for it, and a number Hermes has not been told about has no
                # bridge to produce one.
                res["applied"] = numbers.apply(prof, which("hermes"))
                res["restart"] = _queue_gateway_reload(prof)
            return res
        if route == "numbers/update":
            prof = _target_profile(body)
            res = numbers.update(body.get("slug", ""), prof,
                                 label=body.get("label"),
                                 allowed_users=body.get("allowed_users"),
                                 main=body.get("main"))
            if res.get("ok"):
                res["applied"] = numbers.apply(prof, which("hermes"))
            return res
        if route == "numbers/main":
            return numbers.set_main(body.get("slug", ""), _target_profile(body))
        if route == "numbers/remove":
            prof = _target_profile(body)
            # The paired session is kept unless the owner explicitly says otherwise:
            # deleting it unlinks the phone, and re-pairing costs them a QR hunt.
            res = numbers.remove(body.get("slug", ""), prof,
                                 drop_session=bool(body.get("drop_session")))
            if res.get("ok"):
                res["restart"] = _queue_gateway_reload(prof)
            return res

        # Updating. The supervisor is the only process that may swap src/ (it holds the
        # bridge handles), so the UI reads its state file and drops a request; see
        # wizard/updates.py. "check" forces a fresh look at GitHub for the button that
        # says so, instead of the cached one every panel load uses.
        if route == "update/status":
            return updates_mod.status(INSTALL_DIR)
        if route == "update/check":
            return updates_mod.status(INSTALL_DIR, force=True)
        if route == "update/apply":
            # A request nobody reads is a button that lies. If the supervisor is not
            # running, start it first - and that is also the repair for the machine this
            # whole feature exists for: one whose supervisor never came up at login.
            sup = updates_mod.supervisor(INSTALL_DIR)
            started = None
            if not sup["running"]:
                started = start_supervisor()
            res = updates_mod.request(INSTALL_DIR)
            if started is not None:
                res["supervisor_started"] = bool(started.get("ok"))
                res["detail"] = ("Encendí el supervisor y se lo pedí; tarda unos segundos."
                                 if started.get("ok") else
                                 "No pude encender el supervisor: %s" % started.get("detail", ""))
                if not started.get("ok"):
                    res["ok"] = False
            return res

        if route == "browser/status":
            # The panel shows each agent's window colour, and a colour is only decided
            # when somebody asks for the whole roster at once. The supervisor does this
            # on its first pass; doing it here too means a console opened before that
            # still shows seven different colours instead of seven identical ones.
            try:
                browser_label.assign_all(
                    [a["slug"] for a in intercom.roster(INSTALL_DIR)], INSTALL_DIR)
            except Exception:  # noqa: BLE001
                pass
            return browser_setup.status(_target_profile(body))
        if route == "browser/enable":
            # The name rides along so the window can say whose it is. With one window per
            # agent, two blank Chromes on screen are otherwise indistinguishable.
            return browser_setup.enable(_target_profile(body),
                                        name=(body.get("name") or "").strip())
        if route == "browser/delegation":
            return browser_setup.delegation_status()
        if route == "browser/delegation-test":
            return browser_setup.delegation_check()
        if route == "browser/disable":
            return browser_setup.disable(_target_profile(body))

        # Agents talking to each other. The owner gets the switch because this is a
        # contact point she did not open herself: something OTHER than her can now make
        # her agent answer.
        if route == "intercom/status":
            return intercom.status(install_dir=INSTALL_DIR)
        if route == "intercom/save":
            return self._intercom_save(body)
        if route == "intercom/thread":
            text = intercom.transcript(body.get("thread") or "")
            return {"ok": bool(text), "text": text or "No encontré esa conversación."}

        # The team map. Every one of these answers with the WHOLE state, so the page
        # repaints from a single source instead of patching its own copy - the map is a
        # permission, and a stale picture of a permission is the one thing it must not be.
        if route in ("teams/adopt", "teams/link", "teams/unlink", "teams/card",
                     "teams/decide", "teams/name", "teams/apply",
                     "teams/group", "teams/ungroup", "teams/assign"):
            return self._teams(route[len("teams/"):], body)

        # Asking the main agent for a proposal is the one team route that does NOT write,
        # and must not pretend to: it answers with the proposal alone, and the console
        # sends back whatever the owner ticked as a separate teams/apply.
        if route == "teams/suggest":
            return teams_advisor.suggest(roster=intercom.roster(INSTALL_DIR),
                                         install_dir=INSTALL_DIR,
                                         focus=body.get("focus", ""))

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

    def _teams(self, what, body):
        """Edit the map, then hand back the state the owner should now be looking at.

        ValueError from teams.py is the owner's own mistake spelled out in Spanish
        ("No había un enlace entre X e Y"), and dispatch() already turns it into a 400 with
        that text - so these are deliberately not caught here. A 500 "error interno" would
        send her looking for a bug in Olivaw instead of reading the sentence.
        """
        roster = intercom.roster(INSTALL_DIR)
        wrote, extra = {"ok": True}, {}

        if what == "adopt":
            wrote = teams.adopt(roster, INSTALL_DIR)
        elif what == "unlink":
            wrote = teams.remove_link(body.get("from", ""), body.get("to", ""),
                                      install_dir=INSTALL_DIR)
        elif what == "decide":
            wrote = teams.decide_request(body.get("from", ""), body.get("to", ""),
                                         accept=bool(body.get("accept")),
                                         both=bool(body.get("both")),
                                         install_dir=INSTALL_DIR)
        elif what == "card":
            wrote = teams.set_card(body.get("slug", ""), role=body.get("role"),
                                   description=body.get("description"),
                                   never=body.get("never"), roster=roster,
                                   install_dir=INSTALL_DIR)
            if wrote.get("adopted"):
                extra["adopted"] = True
        elif what == "link":
            # An empty map is the one state where a link cannot be written, so adopt first
            # rather than refusing: the owner asked for this pair, and on a legacy machine
            # that means she also wants the connections she already had written down.
            status, _ = teams.read(INSTALL_DIR)
            if status == teams.LEGACY:
                res = teams.adopt(roster, INSTALL_DIR)
                if not res.get("ok"):
                    return dict(res, ok=False)
                extra["adopted"] = True
            hours = body.get("hours")
            wrote = teams.set_link(
                body.get("from", ""), body.get("to", ""),
                why=body.get("why"), why_back=body.get("why_back"),
                both=body.get("both"),
                max_turns=body.get("max_turns"), hourly_limit=body.get("hourly_limit"),
                hours=(hours if isinstance(hours, dict) else
                       (None if hours is None else {})),
                enabled=body.get("enabled"), direction=bool(body.get("direction")),
                install_dir=INSTALL_DIR)
        elif what == "group":
            # Create or rename. `roster` travels so a legacy machine can adopt its live
            # mesh in the same write - see teams.set_team for why that is not optional.
            # `icon` is passed through only when the caller mentioned it: None means
            # "leave it alone" and "" means "take it off", and renaming a team must
            # not quietly drop its mark.
            wrote = teams.set_team(body.get("id") or None, name=body.get("name", ""),
                                   roster=roster, install_dir=INSTALL_DIR,
                                   icon=body.get("icon"))
            if wrote.get("adopted"):
                extra["adopted"] = True
            # NOT "team": that key already holds the entire map this reply repaints the
            # page from, and overwriting it with a slug made the panel report one agent
            # on a machine with five.
            extra["team_id"] = wrote.get("id", "")
        elif what == "ungroup":
            wrote = teams.remove_team(body.get("id", ""), install_dir=INSTALL_DIR)
        elif what == "assign":
            # An empty team is "out of all of them", which is a real answer and not a
            # missing one: an agent is allowed to belong nowhere.
            wrote = teams.assign(body.get("slug", ""), body.get("team", ""),
                                 roster=roster, install_dir=INSTALL_DIR)
            if wrote.get("adopted"):
                extra["adopted"] = True
        elif what == "name":
            # How the OTHER agents refer to this one. The main agent calls itself Chalenus
            # in its own memory while every skill on the machine called it "Agente
            # principal", because its display name only ever came from a HOME_CHANNEL_NAME
            # line in a .env that nothing in Olivaw could write. So the owner can type it.
            wrote = self._team_name(body)
        elif what == "apply":
            wrote = teams_advisor.apply_proposal(
                {"cards": body.get("cards") or [], "links": body.get("links") or []},
                roster, install_dir=INSTALL_DIR, replace=bool(body.get("replace")))
            if wrote.get("adopted"):
                extra["adopted"] = True
            extra["applied"] = {"cards": wrote.get("cards", 0),
                                "links": wrote.get("links", 0),
                                "removed": wrote.get("removed", 0)}

        # The map is baked into every agent's skill, so a change here is only half applied
        # until each one has been rewritten. Doing it now - rather than waiting for the
        # supervisor's next sweep - is what makes the owner's edit take effect while she is
        # still looking at the screen.
        touched = []
        if wrote.get("ok"):
            try:
                for r in intercom.ensure_all(install_dir=INSTALL_DIR):
                    if r.get("changed"):
                        prof = r.get("profile") or "default"
                        touched.append(prof)
                        _queue_gateway_reload(None if prof == "default" else prof)
            except Exception as e:  # noqa: BLE001
                extra["skill_warning"] = str(e)

        st = intercom.status(install_dir=INSTALL_DIR)
        st.update(extra)
        st["ok"] = wrote.get("ok", False)
        st["retaught"] = touched
        if not wrote.get("ok"):
            st["detail"] = wrote.get("detail", "No pude guardar.")
        return st

    def _team_connect(self, slug, pick):
        """Wire a just-created agent into the team, from the choice made while creating it.

        The complaint this answers, in the owner's words: "when created they where not
        connected to any other". An agent born after the map was drawn joins it with no
        links, hears from nobody, and nothing says so - it looks exactly like an agent that
        was meant to work alone. Asking during setup is the only moment the answer is
        obvious.

        `asked` is what makes this safe to ship. A front-end that never showed the question
        does not send it, and then this does nothing at all: no map is written, no legacy
        machine is quietly converted. Only an owner who saw the checkboxes changes anything.
        """
        if not pick.get("asked") or not slug:
            return {"ok": True, "skipped": True}
        roster = intercom.roster(INSTALL_DIR)
        slugs = set(a["slug"] for a in roster)
        if slug not in slugs:
            return {"ok": False, "detail": "El agente todavía no aparece en el equipo."}
        want = []
        for s in (pick.get("connect") or []):
            s = (s or "").strip().lower()
            if s in slugs and s != slug and s not in want:
                want.append(s)

        status, _ = teams.read(INSTALL_DIR)
        if status == teams.BROKEN:
            return {"ok": False, "detail": "El mapa del equipo no se puede leer, así que "
                                           "no toqué las conexiones."}
        adopted = False
        if status == teams.LEGACY:
            # Writing the map freezes today's all-to-all mesh for the agents that already
            # existed - byte-identical behaviour for them - and then the new agent gets
            # only what the owner ticked. Nothing that worked this morning stops working;
            # the only agent whose reach is decided here is the one being created.
            res = teams.adopt(roster, INSTALL_DIR)
            if not res.get("ok"):
                return dict(res, ok=False)
            adopted = True
            for other in slugs:
                if other == slug:
                    continue
                try:
                    teams.remove_link(slug, other, install_dir=INSTALL_DIR)
                except ValueError:
                    pass

        made = []
        for other in want:
            teams.set_link(slug, other, both=True, enabled=True, install_dir=INSTALL_DIR)
            made.append(other)

        touched = []
        try:
            for r in intercom.ensure_all(install_dir=INSTALL_DIR):
                if r.get("changed"):
                    prof = r.get("profile") or "default"
                    touched.append(prof)
                    _queue_gateway_reload(None if prof == "default" else prof)
        except Exception as e:  # noqa: BLE001
            return {"ok": True, "linked": made, "adopted": adopted,
                    "skill_warning": str(e)}
        return {"ok": True, "linked": made, "adopted": adopted, "retaught": touched}

    def _team_name(self, body):
        """The name the other agents use for one agent, stored in intercom.json.

        Not the .env: HOME_CHANNEL_NAME belongs to a channel's own config and changing it
        needs that gateway restarted. This is the name used for the roster, the map and
        every generated skill, so a rewrite is enough - and the owner gets to say
        "Chalenus" instead of being told what her main agent is called.
        """
        slug = (body.get("slug") or "").strip().lower()
        if not teams.SLUG_RE.match(slug):
            raise ValueError("«%s» no es un agente válido." % slug)
        if not intercom.find(slug, INSTALL_DIR):
            raise ValueError("En este equipo no hay ningún agente «%s»." % slug)
        name = teams._clip(body.get("name"), 40)
        names = dict(intercom.config().get("names") or {})
        if name:
            names[slug] = name
        else:
            names.pop(slug, None)          # empty box = go back to the detected name
        return intercom.save_config({"names": names})

    def _agent_rename(self, body):
        """Rename, then make the machine act on it while she is still looking.

        Three things follow a rename and none of them is cosmetic: every other agent's
        skill names this one, so the mesh is rewritten; the gateway holds the old name in
        its environment, so it is reloaded; and the console repaints from the snapshot
        this returns rather than from what it hoped happened.
        """
        slug = (body.get("slug") or "").strip().lower()
        ws = ""
        if slug == "default":
            ws = _default_workspace()
        res = renaming.rename(slug, body.get("name", ""), install_dir=INSTALL_DIR,
                              hermes=which("hermes"), workspace=ws)
        try:
            for r in intercom.ensure_all(install_dir=INSTALL_DIR):
                if r.get("changed"):
                    prof = r.get("profile") or "default"
                    _queue_gateway_reload(None if prof == "default" else prof)
        except Exception as e:  # noqa: BLE001
            res.setdefault("failed", []).append("avisar a los dem\u00e1s (%s)" % e)
        # The .env only reaches a running gateway on a restart, so the agent keeps
        # signing with the old name until this lands. Queued, not awaited: it takes
        # seconds and she should not watch a spinner for it.
        _queue_gateway_reload(None if res.get("is_default") else res.get("profile"))
        # The reply carries the new name and nothing else. Returning agents_snapshot()
        # here looked tidier and made every rename wait for a full inventory of the
        # machine - gateway status per profile, a pairing list per profile, a probe per
        # bridge port - which on a slow CLI is tens of seconds of spinner for a write
        # that already finished. The console patches the agent it just renamed and picks
        # the rest up on its next ordinary refresh.
        return res

    def _intercom_save(self, body):
        """Write the owner's limits, then report the state she should actually see."""
        updates = {}
        if "enabled" in body:
            updates["enabled"] = bool(body.get("enabled"))
        for key in ("max_turns", "hourly_limit", "timeout"):
            if body.get(key) not in (None, ""):
                try:
                    updates[key] = int(body[key])
                except (TypeError, ValueError):
                    pass
        wrote = intercom.save_config(updates)
        st = intercom.status(install_dir=INSTALL_DIR)
        st["ok"] = wrote.get("ok", False)          # keep the WRITE's verdict, not the read
        if not wrote.get("ok"):
            st["detail"] = wrote.get("detail", "No pude guardar.")
        return st

    def _apply(self, body):
        agent = body.get("agent") or {}
        mode = agent.get("mode", "default")
        provider_id = body.get("provider", providers.DEFAULT_ID)
        p = providers.get(provider_id)
        claude = body.get("claude") or which("claude")
        # No workspace here on purpose: write_all() sets CLAUDE_BRIDGE_WORKSPACE from the
        # agent's own workspace, which is only decided further down.
        provider_env = p.bridge_env({"claude": claude,
                                     "codex": body.get("codex") or which("codex") or ""}) if p else {}
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
                           "engine": (p.engine if p else "claude"),
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
            "engine": (p.engine if p else "claude"), "provider": provider_id,
        }
        # Refuse to write a token Telegram has already rejected. It was structurally valid the
        # last time it was checked, and BotFather revokes the previous token whenever a new one is
        # generated - so "it validated five minutes ago" is not the same as "it works".
        if body.get("token"):
            pre = telegram_health.check(profile or "default", hp, token=body["token"])
            if pre.get("state") == "token_rejected":
                return {"ok": False, "detail": pre["detail"], "telegram": pre}

        res = config_writer.write_all(cfg)

        # And then confirm it really connected, instead of declaring success because the files
        # were written. This is the check whose absence turned a revoked token into an agent that
        # simply never answered.
        if body.get("token"):
            res["telegram"] = telegram_health.wait_for_connection(profile or "default", hp, 30)
            if not res["telegram"].get("ok"):
                res.setdefault("warnings", []).append(res["telegram"]["detail"])
        # Changing the brain only takes effect when the bridge restarts. The supervisor does that
        # by itself on its next check (it compares /status.engine with the config), but the owner
        # should be told rather than left wondering why answers still come from the old one.
        try:
            live = (checks.check_bridge(cfg["bridge_url"] if "bridge_url" in cfg
                                        else "http://127.0.0.1:%d" % port) or {})
            res["engine"] = cfg.get("engine", "claude")
            if live.get("running") and live.get("engine") and \
                    live["engine"] != cfg.get("engine", "claude"):
                res["engine_switch_pending"] = True
                res.setdefault("warnings", []).append(
                    "El cerebro cambió a %s. El puente se reinicia solo en cuanto tu agente esté "
                    "en reposo (menos de un minuto normalmente)." % cfg.get("engine", "claude"))
        except Exception:  # noqa: BLE001
            pass

        # Register the extra agent so the supervisor starts/keeps its bridge.
        if provisioned:
            agents_registry.upsert(provisioned, INSTALL_DIR)
            res["agent"] = provisioned
            # The registry row has to exist before the map can name it: teams.set_link
            # writes a slug, but intercom.roster is what decides that slug is real.
            res["team"] = self._team_connect(slug, body.get("team") or {})
        elif mode == "reconfigure" and slug != "default":
            rec = agents_registry.get(slug, INSTALL_DIR) or {"slug": slug}
            rec.update({"profile": profile, "port": port, "workspace": workspace,
                        "claude_config_dir": claude_config_dir, "enabled": True,
                        "engine": (p.engine if p else "claude"),
                        "gateway_enabled": bool(body.get("token")) or rec.get("gateway_enabled", False)})
            if body.get("bot_username"):
                rec["bot_username"] = body["bot_username"]
            agents_registry.upsert(rec, INSTALL_DIR)
            res["agent"] = rec
            res["team"] = self._team_connect(slug, body.get("team") or {})
        res["port"] = port
        return res

    def _channel(self, sub, body):
        profile = _target_profile(body)          # None -> default agent (bare hermes)
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
            return channels.whatsapp_qr(profile, body.get("number", ""))
        if sub == "whatsapp-save":
            return channels.whatsapp_save(profile, body.get("allowed_users", ""),
                                          body.get("home_channel", ""))
        if sub == "escalation-get":
            return channels.escalation_get(profile)
        if sub == "escalation-save":
            return channels.escalation_save(profile,
                                            enabled=body.get("enabled", True),
                                            reasons=body.get("reasons") or [],
                                            custom=body.get("custom") or [])
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

    # ── conversation lifetime ───────────────────────────────────────────────
    def _policy_get(self, profile):
        st = context_policy.read(profile=profile)
        return {"ok": True, "presets": context_policy.PRESETS,
                "limits": {k: list(v) for k, v in context_policy.LIMITS.items()},
                "modes": list(context_policy.MODES),
                "defaults": context_policy.DEFAULTS, **st}

    def _policy_save(self, body):
        profile = _target_profile(body)
        preset = body.get("preset") or ""
        if preset and preset != "personalizado":
            wanted = context_policy.preset_policy(preset)
        else:
            wanted = dict(context_policy.read(profile=profile)["policy"])
            wanted.update({k: v for k, v in (body.get("policy") or {}).items()
                           if k in context_policy.DEFAULTS})
        res = context_policy.apply(wanted, profile=profile)
        # Written but not in effect is the failure mode that looks like success: the gateway
        # reads session_reset once, at boot, so without this the panel says "guardado" and
        # the agent goes on with the old policy until something else restarts it.
        activation = None
        if res["ok"] and res.get("written"):
            activation = context_policy.activate(profile=profile)
            if not activation.get("restarted"):
                res["detail"] += " " + activation.get("detail", "")
        # Report the file as it now reads, without letting a successful re-read paper over a
        # failed write: `ok` stays the write's own verdict.
        out = dict(context_policy.read(profile=profile))
        out.update(res)
        out["activation"] = activation
        return out

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
            try:
                avatars.forget(slug, INSTALL_DIR)
            except Exception:  # noqa: BLE001
                pass
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
    # Repair a split registry before serving: an agent registered next to the code is
    # invisible to the supervisor, which is why it can answer on Telegram and never think.
    try:
        adopted = agents_registry.reconcile(log=lambda m: print("  " + m))
        if adopted:
            print("  (se recuperaron agentes registrados en la copia del código)")
    except Exception as e:  # noqa: BLE001
        print("  aviso: no se pudo revisar el registro de agentes: %s" % e)
    # One console at a time. A second one started beside the first used to take
    # another port and serve whatever Python it had loaded that day - which is how an
    # owner ends up looking at today's page talking to Tuesday's routes.
    what, where = console_one.take_over(INSTALL_DIR, running_version())
    if what == "open":
        print("\n  Ya hay una consola abierta. Te llevo a esa.\n")
        try:
            webbrowser.open(where)
        except Exception:  # noqa: BLE001
            print("   ", where)
        return

    port = _free_port()
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    console_one.write(INSTALL_DIR, port, running_version())
    atexit.register(lambda: console_one.clear(INSTALL_DIR))
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
