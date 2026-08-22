#!/usr/bin/env python3
"""
Hermes Bridge supervisor + auto-updater.

This is what the OS auto-starts at login (NOT the bridge directly). It:
  1. starts and watches the bridge, restarting it if it ever exits (keep-alive);
  2. periodically checks GitHub Releases for a newer version;
  3. applies an update ONLY when the bridge is idle (no turn in flight and quiet for a
     while), or in a nightly low-use window — so it never interrupts a conversation;
  4. verifies the download's SHA-256, swaps files atomically, health-checks, and rolls
     back automatically if the new version fails to come up;
  5. notifies the user on Telegram ("updated to vX — what's new: …").

Only CODE is updated (the `src/` tree + VERSION). User config, secrets (.env), the
customized CLAUDE.md and the DB are never touched. Stdlib only — no pip installs.

Config lives in `updater.config.json` in the install dir (one level above this file);
the installer writes it. See load_config() for the schema.
"""
import datetime
import hashlib
import json
import os
import re
import shutil
import ssl
import subprocess
import sys
import time
import urllib.parse
import urllib.request
import zipfile

SELF = os.path.abspath(__file__)
SRC_DIR = os.path.dirname(SELF)                 # .../install/src
INSTALL_DIR = os.path.dirname(SRC_DIR)          # .../install
CONFIG_PATH = os.path.join(INSTALL_DIR, "updater.config.json")
VERSION_FILE = os.path.join(INSTALL_DIR, "VERSION")
BACKUP_DIR = os.path.join(INSTALL_DIR, ".backup")
STAGING_DIR = os.path.join(INSTALL_DIR, ".staging")
LOG_FILE = os.path.join(INSTALL_DIR, "launcher.log")
_SSL = ssl.create_default_context()

# Additional agents (Phase B) live in agents.json and each get their own bridge on
# their own port. Guarded import so the single-agent path never breaks if the wizard
# package is somehow absent.
sys.path.insert(0, SRC_DIR)
try:
    from wizard import agents_registry as _registry
    from wizard import hermes_ctl as _hctl
except Exception:  # noqa: BLE001
    _registry = None
    _hctl = None


# The update source is PINNED into the distributed code. A mutable `repo` in updater.config.json
# must never be able to repoint the auto-updater at an attacker-controlled repo (that would turn
# any one-time config write into persistent, reboot-surviving RCE). Env override exists only so a
# fork can rebuild with its own repo; it is NOT read from the runtime config.
PINNED_REPO = os.environ.get("OLIVAW_REPO", "Walt9819/olivaw").strip()


_warned = set()          # keys already logged, so per-loop conditions log once
LOG_MAX_BYTES = 2 * 1024 * 1024


def _rotate_log():
    """Keep launcher.log bounded (one .1 backup). Without this a repeating per-loop
    message can grow the file to megabytes."""
    try:
        if os.path.exists(LOG_FILE) and os.path.getsize(LOG_FILE) > LOG_MAX_BYTES:
            bak = LOG_FILE + ".1"
            if os.path.exists(bak):
                os.remove(bak)
            os.replace(LOG_FILE, bak)
    except Exception:
        pass


def log(msg):
    line = f"{datetime.datetime.now().isoformat(timespec='seconds')} {msg}"
    print(line, flush=True)
    _rotate_log()
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except Exception:
        pass


def load_config():
    """updater.config.json schema (all but `repo` optional, with sane defaults):
      {
        "repo": "owner/name",                 # GitHub repo to pull releases from
        "bridge_cmd": ["<python>", "src/claude_bridge.py"],  # how to launch the bridge
        "bridge_cwd": "<abs path>",           # working dir for the bridge (defaults INSTALL_DIR)
        "bridge_url": "http://127.0.0.1:8790",
        "env": { "CLAUDE_BRIDGE_CLAUDE": "...", "CLAUDE_BRIDGE_WORKSPACE": "..." },
        "telegram_bot_token": "...",          # to notify the user after an update
        "telegram_chat_id": "...",            # the user's chat id
        "maintainer_chat_id": "...",          # (optional) you — gets failure alerts
        "poll_minutes": 45,
        "idle_seconds": 300,                  # "quiet for this long" == idle
        "nightly_hour": 4,                    # local hour for the fallback window
        "lang": "es",                         # notification language: es | en
        "auto_update": true
      }
    """
    cfg = {}
    try:
        with open(CONFIG_PATH, encoding="utf-8") as fh:
            cfg = json.load(fh)
        _warned.discard("missing")
    except FileNotFoundError:
        # load_config() runs every supervisor loop (15s), so logging this unconditionally
        # grew launcher.log by megabytes. Say it once per state change.
        if "missing" not in _warned:
            log(f"no {CONFIG_PATH}; running in supervise-only mode (no auto-update). "
                f"Finish the wizard (Aplicar y activar) to enable updates.")
            _warned.add("missing")
    except Exception as e:
        key = "bad:%s" % e
        if key not in _warned:
            log(f"bad config ({e}); supervise-only mode")
            _warned.clear()
            _warned.add(key)
    cfg.setdefault("bridge_cmd", [sys.executable, os.path.join("src", "claude_bridge.py")])
    cfg.setdefault("bridge_cwd", INSTALL_DIR)
    cfg.setdefault("bridge_url", "http://127.0.0.1:8790")
    cfg.setdefault("poll_minutes", 45)
    cfg.setdefault("idle_seconds", 300)
    cfg.setdefault("nightly_hour", 4)
    cfg.setdefault("lang", "es")
    cfg.setdefault("auto_update", True)
    return cfg


# ── version helpers ──────────────────────────────────────────────────────────
def read_version():
    try:
        with open(VERSION_FILE, encoding="utf-8") as fh:
            return fh.read().strip()
    except Exception:
        return "0.0.0"


def vtuple(s):
    parts = (s or "0").lstrip("v").split(".")
    out = []
    for p in parts[:3]:
        try:
            out.append(int("".join(ch for ch in p if ch.isdigit()) or 0))
        except Exception:
            out.append(0)
    while len(out) < 3:
        out.append(0)
    return tuple(out)


# ── telegram notify ──────────────────────────────────────────────────────────
def notify(cfg, text, maintainer=False):
    tok = cfg.get("telegram_bot_token")
    chat = cfg.get("maintainer_chat_id") if maintainer else cfg.get("telegram_chat_id")
    if not tok or not chat:
        return
    try:
        data = urllib.parse.urlencode({"chat_id": chat, "text": text}).encode()
        urllib.request.urlopen(
            f"https://api.telegram.org/bot{tok}/sendMessage", data=data,
            timeout=20, context=_SSL)
    except Exception as e:
        log(f"notify failed: {e}")


# ── github release discovery ─────────────────────────────────────────────────
def latest_release(repo):
    url = f"https://api.github.com/repos/{repo}/releases/latest"
    req = urllib.request.Request(url, headers={
        "User-Agent": "hermes-bridge-updater",
        "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=30, context=_SSL) as r:
        d = json.loads(r.read())
    tag = (d.get("tag_name") or "").lstrip("v")
    assets = {a["name"]: a["browser_download_url"] for a in d.get("assets", [])}
    zip_name = next((n for n in assets if n.endswith(".zip")), None)
    return {"version": tag, "changelog": d.get("body") or "",
            "zip_name": zip_name,
            "zip_url": assets.get(zip_name) if zip_name else None,
            "sha_url": assets.get((zip_name or "") + ".sha256")}


def _download(url, dest):
    req = urllib.request.Request(url, headers={"User-Agent": "hermes-bridge-updater"})
    with urllib.request.urlopen(req, timeout=120, context=_SSL) as r, open(dest, "wb") as f:
        shutil.copyfileobj(r, f)


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ── idle detection ───────────────────────────────────────────────────────────
def bridge_status(cfg):
    try:
        with urllib.request.urlopen(cfg["bridge_url"] + "/status", timeout=8) as r:
            return json.loads(r.read())
    except Exception:
        return None


def is_idle(cfg):
    st = bridge_status(cfg)
    if st is None:
        return True  # bridge not answering = nothing in flight = safe to swap
    if st.get("inflight", 0) > 0:
        return False
    idle = st.get("idle_seconds")
    return idle is None or idle >= cfg.get("idle_seconds", 300)


def in_nightly_window(cfg):
    return datetime.datetime.now().hour == int(cfg.get("nightly_hour", 4))


# ── additional agents (multi-agent) ──────────────────────────────────────────
def _load_extra_agents():
    if not _registry:
        return []
    try:
        return _registry.list_agents(INSTALL_DIR)
    except Exception as e:  # noqa: BLE001
        log(f"agents.json read failed: {e}")
        return []


def _agent_cfg(agent, base_cfg):
    """Build a bridge cfg for one extra agent (own port + workspace + optional Claude login)."""
    py = (base_cfg.get("bridge_cmd") or [sys.executable])[0]
    env = dict(base_cfg.get("env") or {})
    if agent.get("workspace"):
        env["CLAUDE_BRIDGE_WORKSPACE"] = agent["workspace"]
    if agent.get("claude_config_dir"):
        env["CLAUDE_CONFIG_DIR"] = agent["claude_config_dir"]
    port = int(agent["port"])
    return {
        "slug": agent.get("slug"),
        "bridge_cmd": [py, os.path.join("src", "claude_bridge.py"), "--port", str(port)],
        "bridge_cwd": INSTALL_DIR,
        "bridge_url": "http://127.0.0.1:%d" % port,
        "env": env,
        "idle_seconds": base_cfg.get("idle_seconds", 300),
        "nightly_hour": base_cfg.get("nightly_hour", 4),
    }


def _start_gateway(agent):
    """Run this agent's Hermes gateway under OUR supervision (survives reboot with us).
    Uses the per-profile wrapper: `<slug> gateway run --external-supervisor`. Returns the
    Popen, or None if the agent has no channel configured yet / wrapper unavailable."""
    if not _hctl or not agent.get("gateway_enabled"):
        return None
    base = _hctl._base(agent.get("profile"))
    if not base:
        return None
    cmd = base + ["gateway", "run", "--external-supervisor", "-q"]
    try:
        log(f"starting gateway for agent '{agent['slug']}'")
        return subprocess.Popen(cmd, cwd=INSTALL_DIR)
    except Exception as e:  # noqa: BLE001
        log(f"gateway start failed for '{agent.get('slug')}': {e}")
        return None


def _reconcile_extras(base_cfg, state):
    """Start/keep-alive a bridge AND gateway per registered extra agent; stop removed ones."""
    extras = state.setdefault("extra", {})
    want = {a["slug"]: a for a in _load_extra_agents()
            if a.get("slug") and a.get("port") and a.get("enabled", True)}
    for slug in list(extras):
        if slug not in want:
            log(f"agent '{slug}' removed/paused; stopping its bridge + gateway")
            stop_bridge(extras[slug].get("child"))
            stop_bridge(extras[slug].get("gw"))
            extras.pop(slug, None)
    for slug, agent in want.items():
        acfg = _agent_cfg(agent, base_cfg)
        ent = extras.setdefault(slug, {"cfg": acfg, "child": None, "gw": None})
        ent["cfg"] = acfg
        ent["agent"] = agent
        if not ent.get("child") or ent["child"].poll() is not None:
            log(f"starting bridge for agent '{slug}' on port {agent['port']}")
            ent["child"] = start_bridge(acfg)
        # gateway keep-alive (only if the agent has a channel configured)
        if agent.get("gateway_enabled"):
            if not ent.get("gw") or ent["gw"].poll() is not None:
                ent["gw"] = _start_gateway(agent)
        elif ent.get("gw"):
            stop_bridge(ent["gw"]); ent["gw"] = None
    return extras


def _stop_extras(state):
    for ent in state.get("extra", {}).values():
        stop_bridge(ent.get("child"))
        stop_bridge(ent.get("gw"))


def _all_idle(cfg, state):
    """Safe to swap SHARED code only when the primary AND every extra agent are idle."""
    if not is_idle(cfg):
        return False
    return all(is_idle(ent["cfg"]) for ent in state.get("extra", {}).values())


def _any_mid_turn(cfg, state):
    for c in [cfg] + [ent["cfg"] for ent in state.get("extra", {}).values()]:
        st = bridge_status(c)
        if st and st.get("inflight", 0) > 0:
            return True
    return False


# ── bridge process management ────────────────────────────────────────────────
def start_bridge(cfg):
    env = dict(os.environ)
    env.update(cfg.get("env") or {})
    cmd = list(cfg["bridge_cmd"])
    # Resolve a relative script path against the install dir.
    if len(cmd) >= 2 and not os.path.isabs(cmd[1]):
        cmd[1] = os.path.join(INSTALL_DIR, cmd[1])
    log(f"starting bridge: {cmd}")
    return subprocess.Popen(cmd, cwd=cfg.get("bridge_cwd", INSTALL_DIR), env=env)


def stop_bridge(child, timeout=15):
    if not child or child.poll() is not None:
        return
    try:
        child.terminate()
        for _ in range(timeout * 2):
            if child.poll() is not None:
                return
            time.sleep(0.5)
        child.kill()
    except Exception as e:
        log(f"stop_bridge: {e}")


def wait_healthy(cfg, want_version=None, timeout=30):
    deadline = time.time() + timeout
    while time.time() < deadline:
        st = bridge_status(cfg)
        if st is not None and (want_version is None or st.get("version") == want_version):
            return True
        time.sleep(1)
    return False


# ── the update itself ────────────────────────────────────────────────────────
def apply_update(cfg, state, rel):
    """Download, verify, swap, health-check, roll back on failure. Returns True on
    a successful version change (caller may re-exec if the launcher itself changed)."""
    ver = rel["version"]
    log(f"applying update -> v{ver}")
    if os.path.isdir(STAGING_DIR):
        shutil.rmtree(STAGING_DIR, ignore_errors=True)
    os.makedirs(STAGING_DIR, exist_ok=True)
    zip_path = os.path.join(STAGING_DIR, rel["zip_name"])
    _download(rel["zip_url"], zip_path)

    # integrity: verify against the published .sha256 (mandatory — never run unverified code)
    if not rel.get("sha_url"):
        log("no .sha256 asset on the release; refusing to apply (integrity unverifiable)")
        notify(cfg, f"⚠️ Update v{ver} skipped: missing checksum.", maintainer=True)
        return False
    sha_path = zip_path + ".sha256"
    _download(rel["sha_url"], sha_path)
    expected = open(sha_path, encoding="utf-8").read().split()[0].strip().lower()
    actual = _sha256(zip_path)
    if actual != expected:
        log(f"checksum mismatch (expected {expected[:12]}…, got {actual[:12]}…); aborting")
        notify(cfg, f"⚠️ Update v{ver} aborted: checksum mismatch.", maintainer=True)
        return False

    with zipfile.ZipFile(zip_path) as z:
        z.extractall(STAGING_DIR)
    new_src = os.path.join(STAGING_DIR, "src")
    new_ver = os.path.join(STAGING_DIR, "VERSION")
    if not os.path.isdir(new_src) or not os.path.isfile(new_ver):
        log("staging missing src/ or VERSION; aborting"); return False

    launcher_changed = _sha256(os.path.join(new_src, "launcher.py")) != _sha256(SELF) \
        if os.path.isfile(os.path.join(new_src, "launcher.py")) else False

    # stop ALL bridges (primary + every extra agent) so the shared src/ swap is safe
    stop_bridge(state.get("child"))
    state["child"] = None
    _stop_extras(state)

    # back up current code for rollback
    if os.path.isdir(BACKUP_DIR):
        shutil.rmtree(BACKUP_DIR, ignore_errors=True)
    os.makedirs(BACKUP_DIR, exist_ok=True)
    shutil.copytree(SRC_DIR, os.path.join(BACKUP_DIR, "src"))
    shutil.copy2(VERSION_FILE, os.path.join(BACKUP_DIR, "VERSION")) if os.path.isfile(VERSION_FILE) else None
    old_version = read_version()

    try:
        shutil.rmtree(SRC_DIR, ignore_errors=True)
        shutil.move(new_src, SRC_DIR)
        shutil.move(new_ver, VERSION_FILE)
        # refresh templates (defaults only — never the user's live config/CLAUDE.md)
        new_tpl = os.path.join(STAGING_DIR, "templates")
        if os.path.isdir(new_tpl):
            dst_tpl = os.path.join(INSTALL_DIR, "templates")
            shutil.rmtree(dst_tpl, ignore_errors=True)
            shutil.move(new_tpl, dst_tpl)
        _run_migrations(cfg, os.path.join(STAGING_DIR, "manifest.json"))

        state["child"] = start_bridge(cfg)
        if not wait_healthy(cfg, want_version=ver, timeout=40):
            raise RuntimeError("new bridge did not become healthy")
        _reconcile_extras(cfg, state)   # bring every extra agent back up on the new code
    except Exception as e:
        log(f"update failed ({e}); rolling back to v{old_version}")
        stop_bridge(state.get("child")); state["child"] = None
        _stop_extras(state)
        shutil.rmtree(SRC_DIR, ignore_errors=True)
        shutil.move(os.path.join(BACKUP_DIR, "src"), SRC_DIR)
        if os.path.isfile(os.path.join(BACKUP_DIR, "VERSION")):
            shutil.copy2(os.path.join(BACKUP_DIR, "VERSION"), VERSION_FILE)
        state["child"] = start_bridge(cfg)
        wait_healthy(cfg, timeout=30)
        _reconcile_extras(cfg, state)
        notify(cfg, f"⚠️ Update to v{ver} failed; rolled back to v{old_version}. ({e})",
               maintainer=True)
        return False
    finally:
        shutil.rmtree(STAGING_DIR, ignore_errors=True)

    log(f"update ok: v{old_version} -> v{ver}")
    notify(cfg, _friendly_note(cfg, ver, rel.get("changelog", "")))
    state["launcher_changed"] = launcher_changed
    return True


def _run_migrations(cfg, manifest_path):
    """Apply idempotent config migrations declared in the release manifest.
    Currently supports {"type":"note","text":...} (logged). Extend as needed
    (e.g. config_set) — kept conservative so most updates are pure code."""
    try:
        with open(manifest_path, encoding="utf-8") as fh:
            man = json.load(fh)
    except Exception:
        return
    for m in man.get("migrations", []) or []:
        if m.get("type") == "note":
            log("migration note: " + str(m.get("text")))


def _friendly_note(cfg, ver, changelog):
    cl = (changelog or "").strip()
    cl = ("\n\n" + cl[:600]) if cl else ""
    if cfg.get("lang") == "en":
        return f"🔄 Your assistant updated to v{ver}.{cl}"
    return f"🔄 Tu asistente se actualizó a la versión {ver}.{cl}"


def maybe_update(cfg, state):
    if not cfg.get("auto_update"):
        return
    # Ignore any `repo` from the (mutable) config; always update from the pinned repo.
    cfg_repo = (cfg.get("repo") or "").strip()
    if cfg_repo and cfg_repo != PINNED_REPO:
        log(f"config repo '{cfg_repo}' != pinned '{PINNED_REPO}'; using pinned source")
    try:
        rel = latest_release(PINNED_REPO)
    except Exception as e:
        log(f"release check failed: {e}"); return
    if not rel.get("version") or not rel.get("zip_url"):
        return
    cur = read_version()
    if vtuple(rel["version"]) <= vtuple(cur):
        return
    log(f"update available: v{cur} -> v{rel['version']}")
    # Gate on idle across ALL agents (shared code), unless in the nightly fallback window;
    # never mid-turn on any agent.
    if _any_mid_turn(cfg, state) or (not _all_idle(cfg, state) and not in_nightly_window(cfg)):
        log("deferring update: an agent is busy / not idle yet")
        return
    if apply_update(cfg, state, rel) and state.get("launcher_changed"):
        log("launcher.py changed; restarting supervisor")
        stop_bridge(state.get("child"))
        _stop_extras(state)
        # If the restart cannot happen, DO NOT leave the machine with nothing running: the code
        # is already updated, so keep serving under this (older) supervisor instead.
        if not respawn_self():
            state["child"] = start_bridge(cfg)
            _reconcile_extras(cfg, state)


def respawn_self():
    """Hand over to the NEW launcher.py. Returns False if it could not be done.

    os.execv is the obvious way and it is WRONG on Windows: the CRT builds the child's command
    line by joining argv WITHOUT quoting, so an interpreter path containing a space (the default
    "C:\\Program Files\\Python312\\pythonw.exe") makes the child treat the second half of that
    path as its script name and exit immediately - silently, because pythonw.exe has no console
    to report it on. The result was the worst possible failure: every update that touched
    launcher.py killed the supervisor AND the bridge, so the agent went dark until the next
    login, with nothing in the log after "re-exec'ing".

    subprocess.Popen quotes properly, and DETACHED_PROCESS means the new supervisor is not tied
    to this one's console or lifetime."""
    if os.name == "nt":
        DETACHED_PROCESS, CREATE_NO_WINDOW = 0x00000008, 0x08000000
        try:
            subprocess.Popen([sys.executable, SELF], cwd=INSTALL_DIR, close_fds=True,
                             creationflags=DETACHED_PROCESS | CREATE_NO_WINDOW)
        except Exception as e:  # noqa: BLE001
            log(f"could not restart the supervisor ({e}); staying up with the old launcher")
            return False
        log("new supervisor started; this one is exiting")
        os._exit(0)     # the log write above already flushed; skip interpreter teardown
    try:
        os.execv(sys.executable, [sys.executable, SELF])
    except Exception as e:  # noqa: BLE001
        log(f"could not restart the supervisor ({e}); staying up with the old launcher")
        return False
    return True


def _vbs_needs_write(path):
    """True if the .vbs is missing or points at an interpreter that no longer exists."""
    if not os.path.isfile(path):
        return True
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            cur = fh.read()
        m = re.search(r'""([^"]+?python[w]?\.exe)""', cur)
        return not (m and os.path.isfile(m.group(1)))
    except Exception:  # noqa: BLE001
        return True


def _write_launcher_vbs(path, pyw, wiz, sos=False):
    """Write a windowless launcher for the wizard UI (optionally straight to the console)."""
    what = "help console" if sos else "setup/help UI"
    args = ' ""--sos""' if sos else ""
    body = [
        "' Opens the Olivaw %s (no console). Auto-repaired by the supervisor." % what,
        'Set s = CreateObject("Wscript.Shell")',
        's.CurrentDirectory = "%s"' % INSTALL_DIR,
        's.Run """%s"" ""%s""%s", 0, False' % (pyw, wiz, args),
    ]
    with open(path, "w", encoding="ascii", errors="replace") as fh:
        fh.write("\n".join(body) + "\n")


def _start_menu_dir():
    return os.path.join(os.environ.get("APPDATA", ""), "Microsoft", "Windows",
                        "Start Menu", "Programs")


def _make_lnk(lnk, target, description):
    """Create a .lnk if it is missing (WScript can write shortcuts; no pywin32 needed)."""
    if not lnk or os.path.exists(lnk) or not os.path.isdir(os.path.dirname(lnk)):
        return
    mk = os.path.join(INSTALL_DIR, "_mklnk.vbs")
    script = [
        'Set s = CreateObject("WScript.Shell")',
        'Set l = s.CreateShortcut("%s")' % lnk,
        'l.TargetPath = "%s"' % target,
        'l.WorkingDirectory = "%s"' % INSTALL_DIR,
        'l.Description = "%s"' % description,
        "l.Save",
    ]
    try:
        with open(mk, "w", encoding="ascii", errors="replace") as fh:
            fh.write("\n".join(script) + "\n")
        subprocess.run(["wscript.exe", "//B", mk], timeout=30)
        log("created the shortcut %s" % os.path.basename(lnk))
    except Exception as e:  # noqa: BLE001
        log("could not create %s: %s" % (os.path.basename(lnk), e))
    finally:
        try:
            os.remove(mk)
        except Exception:  # noqa: BLE001
            pass


def _ensure_app_shortcut():
    """Keep the "Olivaw" icon working forever, with no manual maintenance.

    The icon points at Olivaw.vbs -> <python> <install>/src/wizard/wizard_server.py. An update
    only swaps src/, so the icon normally needs no attention at all. The one fragile part is the
    interpreter path baked into the .vbs: a uv-managed Python can be pruned or replaced, which
    would break the icon even though Olivaw itself is fine. So on each supervisor start we check
    that path and, if the interpreter is gone, rewrite the .vbs with the interpreter we are
    actually running under. Best-effort: a failure here is irrelevant to the agent and must
    never stop the supervisor.
    """
    if os.name != "nt":
        return
    try:
        vbs = os.path.join(INSTALL_DIR, "Olivaw.vbs")
        wiz = os.path.join(INSTALL_DIR, "src", "wizard", "wizard_server.py")
        if not os.path.isfile(wiz):
            return
        pyw = sys.executable.replace("python.exe", "pythonw.exe")
        if not os.path.isfile(pyw):
            pyw = sys.executable
        needs = True
        if os.path.isfile(vbs):
            try:
                with open(vbs, encoding="utf-8", errors="replace") as fh:
                    cur = fh.read()
                m = re.search(r'""([^"]+?python[w]?\.exe)""', cur)
                needs = not (m and os.path.isfile(m.group(1)))
            except Exception:
                needs = True
        if needs:
            _write_launcher_vbs(vbs, pyw, wiz)
            log("repaired the app-shortcut launcher (interpreter path had changed)")

        # A dedicated SOS launcher: straight to the help console, no setup flow in the way.
        sos_vbs = os.path.join(INSTALL_DIR, "Olivaw-SOS.vbs")
        if _vbs_needs_write(sos_vbs):
            _write_launcher_vbs(sos_vbs, pyw, wiz, sos=True)
        _make_lnk(os.path.join(_start_menu_dir(), "Olivaw SOS.lnk"), sos_vbs,
                  "Hablar con Claude sobre Olivaw (ayuda directa)")

        # Recreate the desktop shortcut if the user lost it (WScript can make .lnk files).
        desktop = os.path.join(os.path.expanduser("~"), "Desktop")
        lnk = os.path.join(desktop, "Olivaw.lnk")
        if os.path.isdir(desktop) and not os.path.exists(lnk):
            mk = os.path.join(INSTALL_DIR, "_mklnk.vbs")
            script = [
                'Set s = CreateObject("WScript.Shell")',
                'Set l = s.CreateShortcut("%s")' % lnk,
                'l.TargetPath = "%s"' % vbs,
                'l.WorkingDirectory = "%s"' % INSTALL_DIR,
                'l.Description = "Abrir la configuracion / ayuda de Olivaw"',
                "l.Save",
            ]
            with open(mk, "w", encoding="ascii", errors="replace") as fh:
                fh.write("\n".join(script) + "\n")
            subprocess.run(["wscript.exe", "//B", mk], timeout=30)
            try:
                os.remove(mk)
            except Exception:
                pass
            if os.path.exists(lnk):
                log("recreated the Olivaw desktop shortcut")
    except Exception as e:
        log(f"shortcut check skipped: {e}")


def main():
    cfg = load_config()
    log(f"supervisor up. install={INSTALL_DIR} version={read_version()} "
        f"repo={cfg.get('repo')} auto_update={cfg.get('auto_update')}")
    _ensure_app_shortcut()
    state = {"child": start_bridge(cfg), "launcher_changed": False, "extra": {}}
    _reconcile_extras(cfg, state)
    last_check = 0.0
    while True:
        try:
            # re-read config each loop so a config written AFTER we started (e.g. by the
            # onboarding wizard) is picked up without needing a restart.
            cfg = load_config()
            poll = max(300, int(cfg.get("poll_minutes", 45)) * 60)
            # keep-alive: restart the primary bridge if it died
            if not state["child"] or state["child"].poll() is not None:
                log("bridge not running; (re)starting")
                state["child"] = start_bridge(cfg)
            # keep-alive + pick up newly-created / removed extra agents each loop
            _reconcile_extras(cfg, state)
            # periodic update check
            if time.time() - last_check >= poll:
                last_check = time.time()
                maybe_update(cfg, state)
        except Exception as e:
            log(f"supervisor loop error: {e}")
        time.sleep(15)


if __name__ == "__main__":
    main()
