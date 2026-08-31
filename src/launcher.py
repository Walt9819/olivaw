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
try:
    from wizard import wa_setup as _wa
except Exception:  # noqa: BLE001
    _wa = None


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


def engine_mismatch(cfg):
    """The running bridge's brain vs the configured one. Returns (live, wanted) or None.

    Only reports a mismatch when the bridge actually TELLS us its engine: a bridge too old to
    report one must not be restarted in a loop - the updater replaces it soon enough.
    """
    want = ((cfg.get("env") or {}).get("OLIVAW_ENGINE") or "claude").strip().lower() or "claude"
    st = bridge_status(cfg)
    if not st:
        return None
    live = st.get("engine")
    if not live:
        return None
    live = str(live).strip().lower()
    return None if live == want else (live, want)


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
    # An extra agent inherits the default agent's brain unless it names its own.
    if agent.get("engine"):
        env["OLIVAW_ENGINE"] = agent["engine"]
        if agent["engine"] == "codex" and not env.get("OLIVAW_CODEX"):
            found = shutil.which("codex.cmd") or shutil.which("codex.exe") or shutil.which("codex")
            if found:
                env["OLIVAW_CODEX"] = found
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


# ── restart policy, shared by EVERY supervised child ─────────────────────────
# A child that dies within seconds is refusing to start, not crashing under load, and
# retrying it on the next 15-second tick simply repeats the refusal. That is how a single
# misconfigured agent turned into 56 process launches in fourteen minutes: `gateway run`
# exits 1 immediately when another gateway already owns the profile, and the supervisor
# obligingly started it again, forever.
#
# The lesson is not "special-case the gateway" - every keep-alive here could do the same,
# so all of them go through this instead. A child that actually ran resets the counter; one
# that never came up gets progressively more room, and says why once per attempt.
MIN_LIFETIME = 20         # under this, it never really started
BACKOFF_CAP = 900         # 15 minutes


def _backoff(fails):
    """Seconds to wait after `fails` consecutive immediate exits: 1, 2, 4, 8, 15 min."""
    return min(60 * (2 ** min(max(fails, 1) - 1, 4)), BACKOFF_CAP)


# Kept under its old name so existing callers/tests keep working.
_gw_backoff = _backoff


def _rs(holder, key):
    """Restart bookkeeping for one supervised child, created on first use."""
    return holder.setdefault(key, {"started_at": 0.0, "fails": 0, "retry_at": 0.0})


def _may_start(rs):
    return time.time() >= rs.get("retry_at", 0.0)


def _started(rs):
    rs["started_at"] = time.time()
    return rs


def _died(rs, label):
    """Record a death and decide how long to wait. Call once per death, not per loop."""
    lived = time.time() - rs.get("started_at", 0.0)
    if lived >= MIN_LIFETIME:
        rs["fails"] = 0
        rs["retry_at"] = 0.0
        return
    rs["fails"] = rs.get("fails", 0) + 1
    wait = _backoff(rs["fails"])
    rs["retry_at"] = time.time() + wait
    log(f"{label}: exited after {lived:.0f}s (attempt {rs['fails']}); "
        f"not retrying for {int(wait / 60)} min")


def _start_gateway(agent, ent):
    """Run this agent's Hermes gateway under OUR supervision (survives reboot with us).

    Two things stop this from becoming a hot loop, both learned the hard way. `gateway run`
    REFUSES when a gateway already owns that profile - it prints "Gateway already running
    (PID ...)" and exits 1 immediately - so a plain "child is dead, start it again" retried
    every 15 seconds, forever, spawning a process each time. So: ask Hermes first and leave
    a healthy gateway alone, and when a start does fail, back off instead of hammering.

    Returns the Popen, or None when there is nothing to start right now.
    """
    if not _hctl or not agent.get("gateway_enabled"):
        return None
    rs = _rs(ent, "gw_rs")
    if not _may_start(rs):
        return None
    now = time.time()

    slug, profile = agent.get("slug"), agent.get("profile")
    try:
        running = _hctl.gateway_status(profile=profile).get("running")
    except Exception:  # noqa: BLE001
        running = False
    if running:
        # Somebody else owns it - Hermes' own service, or a gateway started by the wizard.
        # A second one would only fight it for the same Telegram poll.
        if not ent.get("gw_external"):
            log(f"agent '{slug}': its gateway is already running outside our supervision; "
                f"leaving it alone")
            ent["gw_external"] = True
        rs["retry_at"] = now + 60          # re-check occasionally, cheaply
        return None
    ent["gw_external"] = False

    base = _hctl._base(profile)
    if not base:
        return None
    cmd = base + ["gateway", "run", "--external-supervisor", "-q"]
    try:
        log(f"starting gateway for agent '{slug}'")
        child = subprocess.Popen(cmd, cwd=INSTALL_DIR)
    except Exception as e:  # noqa: BLE001
        log(f"gateway start failed for '{slug}': {e}")
        rs["retry_at"] = now + 60
        return None
    _started(rs)
    return child


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
        brs = _rs(ent, "bridge_rs")
        child = ent.get("child")
        if child is not None and child.poll() is not None:
            _died(brs, f"agent '{slug}' bridge")
            ent["child"] = None
        if not ent.get("child") and _may_start(brs):
            log(f"starting bridge for agent '{slug}' on port {agent['port']}")
            ent["child"] = start_bridge(acfg)
            _started(brs)
        # gateway keep-alive (only if the agent has a channel configured)
        if agent.get("gateway_enabled"):
            gw = ent.get("gw")
            if gw is not None and gw.poll() is not None:
                _died(_rs(ent, "gw_rs"), f"agent '{slug}' gateway")
                ent["gw"] = None
            if not ent.get("gw"):
                ent["gw"] = _start_gateway(agent, ent)
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
_adopted = set()


def start_bridge(cfg):
    """Start the bridge, or adopt one that is already serving.

    Spawning a second bridge on a taken port produces a process that dies instantly, which the
    supervisor then treats as healthy. If something is already answering, leave it alone."""
    if bridge_status(cfg):
        url = cfg.get("bridge_url", "")
        if url not in _adopted:
            _adopted.add(url)
            log(f"a bridge is already serving on {url}; adopting it instead of starting a second")
        return None
    _adopted.discard(cfg.get("bridge_url", ""))
    env = dict(os.environ)
    env.update(cfg.get("env") or {})
    cmd = list(cfg["bridge_cmd"])
    # Resolve a relative script path against the install dir.
    if len(cmd) >= 2 and not os.path.isabs(cmd[1]):
        cmd[1] = os.path.join(INSTALL_DIR, cmd[1])
    script = cmd[1] if len(cmd) >= 2 else ""
    if script and not os.path.isfile(script):
        # e.g. a swap that went wrong: say so instead of spawning something that cannot run.
        log(f"cannot start the bridge: {script} does not exist")
        return None
    log(f"starting bridge: {cmd}")
    return subprocess.Popen(cmd, cwd=cfg.get("bridge_cwd", INSTALL_DIR), env=env)


def _free_bridge_port(cfg):
    """Kill whatever still holds the bridge port (an orphan from a previous generation).

    Only used when we are about to run NEW code there: otherwise the old process keeps serving
    and the update silently has no effect."""
    url = cfg.get("bridge_url") or ""
    m = re.search(r":(\d+)", url)
    if not m:
        return
    port = m.group(1)
    try:
        out = subprocess.run(["netstat", "-ano"], capture_output=True, text=True,
                             timeout=30).stdout
    except Exception:  # noqa: BLE001
        return
    pids = set()
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 5 and parts[0].upper() == "TCP" and parts[1].endswith(":" + port) \
                and parts[3].upper() == "LISTENING" and parts[4].isdigit():
            pids.add(parts[4])
    for pid in pids:
        log(f"freeing port {port}: stopping leftover process {pid}")
        try:
            subprocess.run(["taskkill", "/F", "/PID", pid], capture_output=True, timeout=30)
        except Exception:  # noqa: BLE001
            pass


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
# Without these, an installation is not an installation.
REQUIRED_FILES = ("launcher.py", "claude_bridge.py", "codex_engine.py",
                  os.path.join("wizard", "wizard_server.py"))


def _replace_dir(new_dir, dest):
    """Lay `new_dir` over `dest`, file by file.

    NOT rmtree-then-move. On Windows the running bridge holds src/bridge.log open, and that
    makes deleting *and* renaming the directory fail - the old code's
    rmtree(..., ignore_errors=True) swallowed exactly that failure and then move() nested the
    whole new tree at src/src, leaving the install with no launcher.py and no bridge. Copying
    over the top is immune to open handles and cannot nest.

    Files that the new release no longer ships stay behind deliberately: they are inert, and
    pruning would risk deleting live state (session map, cached images, logs)."""
    if not os.path.isdir(dest):
        shutil.move(new_dir, dest)
        return
    for root, _dirs, files in os.walk(new_dir):
        rel = os.path.relpath(root, new_dir)
        target = dest if rel == "." else os.path.join(dest, rel)
        os.makedirs(target, exist_ok=True)
        for name in files:
            shutil.copy2(os.path.join(root, name), os.path.join(target, name))
    shutil.rmtree(new_dir, ignore_errors=True)


def _verify_install(where=None):
    """Missing pieces after a swap mean the install is broken; say which."""
    base = where or SRC_DIR
    missing = [f for f in REQUIRED_FILES if not os.path.isfile(os.path.join(base, f))]
    if os.path.isdir(os.path.join(base, "src")):
        missing.append("(a nested src/src directory should not exist)")
    return missing


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
    # An orphan from an earlier supervisor would keep serving the OLD code on that port and
    # would answer the health check below, hiding a failed update. Take the port back.
    if bridge_status(cfg):
        _free_bridge_port(cfg)
        time.sleep(1.5)

    # back up current code for rollback
    if os.path.isdir(BACKUP_DIR):
        shutil.rmtree(BACKUP_DIR, ignore_errors=True)
    os.makedirs(BACKUP_DIR, exist_ok=True)
    shutil.copytree(SRC_DIR, os.path.join(BACKUP_DIR, "src"))
    shutil.copy2(VERSION_FILE, os.path.join(BACKUP_DIR, "VERSION")) if os.path.isfile(VERSION_FILE) else None
    old_version = read_version()

    try:
        # Live state inside src/ (session map, logs) survives by construction: the new tree is
        # copied over the old one, so files the release does not ship are left untouched.
        _replace_dir(new_src, SRC_DIR)
        shutil.move(new_ver, VERSION_FILE)
        # refresh templates (defaults only — never the user's live config/CLAUDE.md)
        new_tpl = os.path.join(STAGING_DIR, "templates")
        if os.path.isdir(new_tpl):
            _replace_dir(new_tpl, os.path.join(INSTALL_DIR, "templates"))
        missing = _verify_install()
        if missing:
            raise RuntimeError("swap left the install incomplete: %s" % ", ".join(missing))
        _run_migrations(cfg, os.path.join(STAGING_DIR, "manifest.json"))

        state["child"] = start_bridge(cfg)
        # The bridge WE started has to be the one that answers. Before this, an orphan bridge
        # from an earlier supervisor could vouch for an install that had just been destroyed.
        if state["child"] is not None and state["child"].poll() is not None:
            raise RuntimeError("the new bridge exited immediately")
        if not wait_healthy(cfg, want_version=ver, timeout=40):
            raise RuntimeError("new bridge did not become healthy")
        _reconcile_extras(cfg, state)   # bring every extra agent back up on the new code
    except Exception as e:
        log(f"update failed ({e}); rolling back to v{old_version}")
        stop_bridge(state.get("child")); state["child"] = None
        _stop_extras(state)
        try:
            _replace_dir(os.path.join(BACKUP_DIR, "src"), SRC_DIR)
        except Exception as re_:  # noqa: BLE001
            log(f"ROLLBACK FAILED ({re_}); the backup is still at {BACKUP_DIR}")
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


def _ensure_whatsapp():
    """Keep the WhatsApp delivery-receipt patch in place.

    `hermes update` git-pulls over its own checkout and takes the patch with it, which
    would silently return the agent to guessing whether messages were sent. The check is
    two stat() calls when nothing moved, so it is cheap enough to run on every update
    cycle; it only speaks up when it actually changed something or cannot proceed.
    """
    if not _wa:
        return
    try:
        r = _wa.ensure()
    except Exception as e:  # noqa: BLE001
        log(f"whatsapp: receipt patch check failed: {e}")
        return
    patch, skill = r.get("patch", {}), r.get("skill", {})
    if patch.get("changed"):
        log(f"whatsapp: re-applied the delivery-receipt patch to {patch.get('path')}")
    elif patch.get("state") == "anchors_moved":
        log("whatsapp: Hermes' bridge changed shape; the receipt patch needs review "
            "- deliveries cannot be confirmed until then")
    if skill.get("changed"):
        log(f"whatsapp: installed the client-handling skill at {skill.get('path')}")


def main():
    cfg = load_config()
    log(f"supervisor up. install={INSTALL_DIR} version={read_version()} "
        f"repo={cfg.get('repo')} auto_update={cfg.get('auto_update')}")
    _ensure_app_shortcut()
    if _registry:
        # Same repair from the other side: whichever process starts first fixes it.
        try:
            adopted = _registry.reconcile(log=log)
            if adopted:
                log(f"adopted {len(adopted)} agent(s) that were registered next to the code")
        except Exception as e:  # noqa: BLE001
            log(f"could not reconcile the agent registry: {e}")
    state = {"child": start_bridge(cfg), "launcher_changed": False, "extra": {}}
    _reconcile_extras(cfg, state)
    _ensure_whatsapp()
    last_check = 0.0
    while True:
        try:
            # re-read config each loop so a config written AFTER we started (e.g. by the
            # onboarding wizard) is picked up without needing a restart.
            cfg = load_config()
            poll = max(300, int(cfg.get("poll_minutes", 45)) * 60)
            # keep-alive: the question is "is the bridge serving?", not "is our handle
            # alive?" - an adopted bridge has no handle, and a dead handle whose port still
            # answers means someone else is serving.
            mrs = _rs(state, "main_rs")
            if state["child"] is not None and state["child"].poll() is not None:
                _died(mrs, "bridge")
                state["child"] = None
            ours_dead = not state["child"]
            if ours_dead and not bridge_status(cfg) and _may_start(mrs):
                log("bridge not answering; (re)starting")
                state["child"] = start_bridge(cfg)
                _started(mrs)
            else:
                # The owner changed the brain in the wizard: swap the running bridge for one on
                # the new engine. Only when idle - a restart mid-turn loses that turn.
                swap = engine_mismatch(cfg)
                if swap and is_idle(cfg):
                    live, want = swap
                    log(f"engine changed in config ({live} -> {want}); restarting the bridge")
                    try:
                        if state["child"]:
                            state["child"].terminate()
                            state["child"].wait(timeout=20)
                    except Exception as e:  # noqa: BLE001
                        log(f"could not stop the old bridge cleanly ({e})")
                    _free_bridge_port(cfg)
                    state["child"] = start_bridge(cfg)
                elif swap:
                    log(f"engine change to {swap[1]} pending: waiting for the agent to go idle")
            # keep-alive + pick up newly-created / removed extra agents each loop
            _reconcile_extras(cfg, state)
            # periodic update check
            if time.time() - last_check >= poll:
                last_check = time.time()
                maybe_update(cfg, state)
                # after any update - ours or Hermes' - make sure WhatsApp can still
                # prove a delivery.
                _ensure_whatsapp()
        except Exception as e:
            log(f"supervisor loop error: {e}")
        time.sleep(15)


if __name__ == "__main__":
    main()
