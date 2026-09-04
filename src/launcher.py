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
from winspawn import CREATE_NEW_PROCESS_GROUP, quiet    # noqa: E402 (needs the path)
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
try:
    from wizard import context_policy as _ctxpol
except Exception:  # noqa: BLE001
    _ctxpol = None
try:
    from wizard import browser_setup as _browser
except Exception:  # noqa: BLE001
    _browser = None
try:
    from wizard import image_setup as _images
except Exception:  # noqa: BLE001
    _images = None
try:
    import intercom as _intercom
except Exception:  # noqa: BLE001
    _intercom = None


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
            # This used to say "no auto-update", which is not what the code does: the
            # defaults below turn auto_update ON, and with no bridge answering the idle
            # gate is open, so a config-less install updates itself perfectly well. What
            # it CANNOT do is tell the owner about it - that needs the Telegram token the
            # wizard writes. Saying otherwise sent us looking for a broken updater.
            log(f"no {CONFIG_PATH}: updates still apply (defaults), but there is no "
                f"Telegram token yet to announce them. Finish the wizard to get notices.")
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


def rest_window(cfg):
    """(from_hour, to_hour) of the hours the owner considers rest time.

    ``update_from_hour`` / ``update_until_hour`` were already being written into
    updater.config.json on at least one machine and read by NOTHING, so the window the
    owner picked was silently ignored and the fallback stayed one hour wide.

    Absent those keys, the window is derived from ``nightly_hour`` and is three hours long
    rather than one. That is deliberately a superset of the old behaviour: an install that
    used to get its update at 04:00 still does, and one whose machine happens to be busy
    or asleep for that single hour now has two more chances before the night is over.
    """
    a, b = cfg.get("update_from_hour"), cfg.get("update_until_hour")
    if a is None or b is None:
        n = int(cfg.get("nightly_hour", 4)) % 24
        return n, (n + 3) % 24
    try:
        a, b = int(a) % 24, int(b)
    except (TypeError, ValueError):
        n = int(cfg.get("nightly_hour", 4)) % 24
        return n, (n + 3) % 24
    # "until 24" is how a person writes "to the end of the day"; 24 % 24 == 0 says the
    # same thing to the comparison below, since a > b then wraps past midnight.
    return a, b % 24


def in_rest_window(cfg, now=None):
    """Is it rest time right now? Handles a window that wraps midnight (22 -> 6)."""
    h = (now or datetime.datetime.now()).hour
    a, b = rest_window(cfg)
    if a == b:
        return True                    # from == until: the owner said "whenever"
    return (a <= h < b) if a < b else (h >= a or h < b)


# Kept for anything still calling the old name; the semantics are now the window's.
def in_nightly_window(cfg):
    return in_rest_window(cfg)


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
        # Not cheap, whatever the old comment here said: one `gateway status` is cmd.exe ->
        # hermes.exe -> python -> python -> wmic x2, ~2.5s. A healthy gateway owned by
        # somebody else does not need watching every minute; five is plenty, and the
        # keep-alive for a gateway that IS ours stays on the fast path below.
        rs["retry_at"] = now + 300
        return None
    ent["gw_external"] = False

    base = _hctl._base(profile)
    if not base:
        return None
    cmd = base + ["gateway", "run", "--external-supervisor", "-q"]
    try:
        log(f"starting gateway for agent '{slug}'")
        child = subprocess.Popen(cmd, **quiet(cwd=INSTALL_DIR))
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
            # "No child of ours" is not the same as "not running". start_bridge() ADOPTS a
            # port that is already serving and returns None - so without this check the
            # branch below fires every 15 seconds forever, logging a start that never
            # happens. The backoff cannot catch it either: there is no child to die.
            if bridge_status(acfg):
                if not ent.get("bridge_external"):
                    log(f"agent '{slug}': a bridge is already serving port "
                        f"{agent['port']}; adopting it")
                    ent["bridge_external"] = True
                brs["retry_at"] = time.time() + 60      # re-check occasionally
            else:
                ent["bridge_external"] = False
                log(f"starting bridge for agent '{slug}' on port {agent['port']}")
                ent["child"] = start_bridge(acfg)
                if ent["child"]:
                    _started(brs)
                else:
                    # Refused to start (missing script, taken port). Do not hammer it.
                    brs["retry_at"] = time.time() + 60
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
    return subprocess.Popen(cmd, **quiet(cwd=cfg.get("bridge_cwd", INSTALL_DIR), env=env))


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
        out = subprocess.run(["netstat", "-ano"],
                             **quiet(capture_output=True, text=True, timeout=30)).stdout
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
            subprocess.run(["taskkill", "/F", "/PID", pid],
                           **quiet(capture_output=True, timeout=30))
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


# ── what the UI is allowed to know, and to ask for ───────────────────────────
# The supervisor owns updating: it holds the bridge handles, so it is the only process that
# can stop them, swap the shared src/ and bring them back. The wizard runs separately and
# must not do any of that behind its back. So the two talk through two small files in the
# install root (which an update never touches, since only src/ and templates/ are swapped):
#
#   update.state.json   written here after every check - what the UI shows
#   update.request      written by the UI - "the owner pressed the button, go now"
#
# The request controls only WHEN. What gets installed is still the pinned repo's latest
# release, still verified against its published SHA-256, so a stray request cannot point
# the machine at other code. And anything able to write this file can already write src/
# directly, so it grants no authority that wasn't there.
STATE_PATH = os.path.join(INSTALL_DIR, "update.state.json")
REQUEST_PATH = os.path.join(INSTALL_DIR, "update.request")
RESULT_PATH = os.path.join(INSTALL_DIR, "update.result.json")
# Rewritten every loop (~15s). Without it nothing outside this process can tell a
# supervisor that is running from one that died at login, and the difference decides
# whether an "update now" button does anything at all: the request file is only ever read
# from here. A bridge answering on 8790 is NOT the same evidence - it can be an orphan
# from a previous supervisor, still serving while nothing supervises or updates it.
HEARTBEAT_PATH = os.path.join(INSTALL_DIR, "supervisor.alive")


def _write_json(path, data):
    try:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, ensure_ascii=False)
        os.replace(tmp, path)
    except Exception as e:  # noqa: BLE001 - telling the UI is never worth a crash
        log(f"could not write {os.path.basename(path)}: {e}")


def publish_state(cfg, rel=None, error="", deferred=""):
    """Leave the UI a snapshot of where updating stands."""
    a, b = rest_window(cfg)
    cur = read_version()
    latest = (rel or {}).get("version") or ""
    _write_json(STATE_PATH, {
        "checked_at": time.time(),
        "current": cur,
        "latest": latest,
        "available": bool(latest) and vtuple(latest) > vtuple(cur),
        "changelog": (rel or {}).get("changelog") or "",
        "auto_update": bool(cfg.get("auto_update")),
        "rest_from": a, "rest_until": b,
        "in_rest_window": in_rest_window(cfg),
        "poll_minutes": int(cfg.get("poll_minutes", 45)),
        "error": error,
        "deferred": deferred,
    })


def beat(cfg):
    """Say "still here", cheaply. One line, overwritten in place."""
    try:
        with open(HEARTBEAT_PATH, "w", encoding="utf-8") as fh:
            json.dump({"pid": os.getpid(), "ts": time.time(),
                       "version": read_version(),
                       "auto_update": bool(cfg.get("auto_update"))}, fh)
    except Exception:  # noqa: BLE001 - a heartbeat that fails must not stop the loop
        pass


def take_request():
    """True if the owner asked for an update through the UI. Consumes the request."""
    try:
        if not os.path.isfile(REQUEST_PATH):
            return False
        os.remove(REQUEST_PATH)
        return True
    except OSError:
        return False


def maybe_update(cfg, state, forced=False):
    """Check, and update if allowed. `forced` = the owner pressed the button in the UI."""
    if not cfg.get("auto_update") and not forced:
        publish_state(cfg, deferred="auto_update off")
        return
    # Ignore any `repo` from the (mutable) config; always update from the pinned repo.
    cfg_repo = (cfg.get("repo") or "").strip()
    if cfg_repo and cfg_repo != PINNED_REPO:
        log(f"config repo '{cfg_repo}' != pinned '{PINNED_REPO}'; using pinned source")
    try:
        rel = latest_release(PINNED_REPO)
    except Exception as e:
        log(f"release check failed: {e}")
        publish_state(cfg, error=str(e)[:200])
        if forced:
            _write_json(RESULT_PATH, {"ok": False, "ts": time.time(),
                                      "detail": "No pude consultar GitHub: %s" % str(e)[:150]})
        return
    if not rel.get("version") or not rel.get("zip_url"):
        publish_state(cfg, error="la release no trae un .zip")
        return
    cur = read_version()
    if vtuple(rel["version"]) <= vtuple(cur):
        publish_state(cfg, rel)
        if forced:
            _write_json(RESULT_PATH, {"ok": True, "ts": time.time(), "from": cur, "to": cur,
                                      "detail": "Ya estás en la última versión (%s)." % cur})
        return
    log(f"update available: v{cur} -> v{rel['version']}")
    # Gate on idle across ALL agents (shared code), unless in the rest-hours fallback
    # window; never mid-turn on any agent, not even when the owner asked for it - the turn
    # in flight is somebody's message, and it would be lost.
    if _any_mid_turn(cfg, state):
        log("deferring update: an agent is mid-turn")
        publish_state(cfg, rel, deferred="mid-turn")
        if forced:
            _write_json(RESULT_PATH, {"ok": False, "ts": time.time(), "busy": True,
                                      "detail": "Un agente está contestando ahora mismo. "
                                                "Vuelve a intentarlo en un minuto."})
        return
    if not forced and not _all_idle(cfg, state) and not in_rest_window(cfg):
        log("deferring update: an agent is busy / not idle yet")
        publish_state(cfg, rel, deferred="not idle")
        return
    ok = apply_update(cfg, state, rel)
    publish_state(cfg, None if ok else rel)
    if forced:
        _write_json(RESULT_PATH, {
            "ok": bool(ok), "ts": time.time(), "from": cur,
            "to": rel["version"] if ok else cur,
            "detail": ("Actualizado a la versión %s." % rel["version"]) if ok else
                      ("No se pudo instalar la %s; sigues en la %s (mira launcher.log)."
                       % (rel["version"], cur))})
    if ok and state.get("launcher_changed"):
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

    subprocess.Popen quotes properly. Its own process group keeps the new supervisor free of
    this one's Ctrl-C/lifetime, and quiet() keeps it (and anything it starts) off the owner's
    screen - see src/winspawn.py for why DETACHED_PROCESS is the wrong flag for that."""
    if os.name == "nt":
        try:
            subprocess.Popen([sys.executable, SELF], **quiet(
                cwd=INSTALL_DIR, close_fds=True,
                creationflags=CREATE_NEW_PROCESS_GROUP))
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


def _desktop_dir():
    """Where the Desktop REALLY is.

    `~/Desktop` is a guess, and it is wrong on any machine where OneDrive backs up the
    desktop - the folder becomes `~/OneDrive/Desktop` and `~/Desktop` does not exist at
    all (verified here). The old guess meant the self-repair below silently did nothing
    on exactly the machines whose desktop is most likely to lose a file to a sync.
    Windows keeps the answer in the shell-folder registry; ask it.
    """
    if os.name == "nt":
        for key in ("Shell Folders", "User Shell Folders"):
            try:
                import winreg
                path = r"Software\Microsoft\Windows\CurrentVersion\Explorer\%s" % key
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as k:
                    val = os.path.expandvars(winreg.QueryValueEx(k, "Desktop")[0])
                if val and os.path.isdir(val):
                    return val
            except Exception:  # noqa: BLE001 - fall through to the guess
                pass
    return os.path.join(os.path.expanduser("~"), "Desktop")


def _start_menu_dir():
    return os.path.join(os.environ.get("APPDATA", ""), "Microsoft", "Windows",
                        "Start Menu", "Programs")


def app_icon():
    """The Olivaw icon, or "" when this copy of the code predates it.

    It lives inside src/, so an update replaces it along with everything else and the
    path never changes.
    """
    ico = os.path.join(SRC_DIR, "assets", "olivaw.ico")
    return ico if os.path.isfile(ico) else ""


def _run_vbs(script, name):
    """Write a throwaway .vbs, run it windowless, delete it. Returns True if it ran."""
    mk = os.path.join(INSTALL_DIR, name)
    try:
        with open(mk, "w", encoding="ascii", errors="replace") as fh:
            fh.write("\n".join(script) + "\n")
        subprocess.run(["wscript.exe", "//B", mk], **quiet(timeout=30))
        return True
    except Exception as e:  # noqa: BLE001
        log("could not run %s: %s" % (name, e))
        return False
    finally:
        try:
            os.remove(mk)
        except Exception:  # noqa: BLE001
            pass


def _make_lnk(lnk, target, description, icon=""):
    """Create a .lnk if it is missing (WScript can write shortcuts; no pywin32 needed)."""
    if not lnk or os.path.exists(lnk) or not os.path.isdir(os.path.dirname(lnk)):
        return
    script = [
        'Set s = CreateObject("WScript.Shell")',
        'Set l = s.CreateShortcut("%s")' % lnk,
        'l.TargetPath = "%s"' % target,
        'l.WorkingDirectory = "%s"' % INSTALL_DIR,
        'l.Description = "%s"' % description,
    ]
    if icon:
        script.append('l.IconLocation = "%s,0"' % icon)
    script.append("l.Save")
    if _run_vbs(script, "_mklnk.vbs"):
        log("created the shortcut %s" % os.path.basename(lnk))


def _ensure_lnk_icon(lnk, icon):
    """Give an EXISTING shortcut our icon, once.

    Shortcuts made before there was an icon point at a .vbs, so Windows shows the generic
    Windows Script Host page - which is also the icon of a file type Microsoft is
    retiring. _make_lnk() cannot fix those: it only ever creates a missing shortcut.
    The VBS only saves when our .ico is not already the one referenced, so this is a
    no-op on every boot after the first.
    """
    if not (lnk and icon and os.path.isfile(lnk) and os.path.isfile(icon)):
        return
    _run_vbs([
        'Set s = CreateObject("WScript.Shell")',
        'Set l = s.CreateShortcut("%s")' % lnk,
        'If InStr(LCase(l.IconLocation), LCase("%s")) = 0 Then' % icon,
        '  l.IconLocation = "%s,0"' % icon,
        '  l.Save',
        'End If',
    ], "_mkico.vbs")


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

        ico = app_icon()

        # A dedicated SOS launcher: straight to the help console, no setup flow in the way.
        sos_vbs = os.path.join(INSTALL_DIR, "Olivaw-SOS.vbs")
        if _vbs_needs_write(sos_vbs):
            _write_launcher_vbs(sos_vbs, pyw, wiz, sos=True)
        start_menu = _start_menu_dir()
        sos_lnk = os.path.join(start_menu, "Olivaw SOS.lnk")
        _make_lnk(sos_lnk, sos_vbs, "Hablar con Claude sobre Olivaw (ayuda directa)", ico)

        # Recreate the desktop shortcut if the user lost it (WScript can make .lnk files).
        desktop = _desktop_dir()
        lnk = os.path.join(desktop, "Olivaw.lnk")
        _make_lnk(lnk, vbs, "Abrir la configuracion / ayuda de Olivaw", ico)

        # Shortcuts that already exist were made before there was an icon: give them one.
        for existing in (lnk, sos_lnk, os.path.join(start_menu, "Olivaw.lnk")):
            _ensure_lnk_icon(existing, ico)
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


def _ensure_context_policy():
    """Give every agent a conversation that ends, once.

    Hermes starts a profile on "never restart the conversation, summarise at half the
    window". Against the 1M window Olivaw advertises that is ~500k tokens of thread
    resent on every turn, and it is why an agent created here could empty its owner's
    quota in an afternoon while the owner's own agent - configured by hand, long ago -
    ran all week on the same allowance.

    Runs at startup, before the keep-alive loop, so a gateway restart here cannot race
    the supervisor's own gateway supervision. Writes nothing to a profile that already
    has a policy: "never restart" is a legitimate choice and must survive this.
    """
    if not _ctxpol:
        return
    try:
        results = _ctxpol.ensure_all(agents=_load_extra_agents(), log=log)
    except Exception as e:  # noqa: BLE001
        log(f"context policy: check failed: {e}")
        return
    for r in results:
        if r.get("changed"):
            log(f"context policy: {r['profile']} had none - {r.get('summary', '')}")
        elif r.get("reason") == "failed":
            log(f"context policy: could not configure {r['profile']}: {r.get('detail', '')}")
        if (r.get("skill") or {}).get("changed"):
            log(f"context policy: taught {r['profile']} how to change it "
                f"({r['skill'].get('path')})")


def _skill_needs_reload(profile, what):
    """A skill written to disk is NOT a skill the running agent can see.

    Hermes builds its skill index into the system prompt and caches it in-process, keyed on
    the skills directory and the toolset - with no mtime and no TTL. A gateway that was
    already up when the skill landed keeps the index it built at boot, for as long as it
    runs. The file is there, `skills_list` would find it, and the model is never told.

    That is what made "the new skills do not work" true while every check passed: a fresh
    Python process reading the same directory sees them immediately, which is exactly what
    a verification script is. Only the long-lived gateway is stale.

    So the same handoff the conversation policy uses: leave a note, and let the supervisor
    restart that profile's gateway once the agent is idle.
    """
    if not _ctxpol:
        return
    try:
        _ctxpol.mark_pending(None if profile == "default" else profile)
        log(f"{what}: {profile} needs a gateway restart to see it; queued for when idle")
    except Exception as e:  # noqa: BLE001
        log(f"{what}: could not queue a reload for {profile}: {e}")


def _ensure_browser_skill():
    """Tell every agent it can browse. Never changes which browser it drives.

    An agent that believes it cannot browse does not browse - and this one told its owner
    exactly that, because he asked about Claude Code's Chrome extension and it answered
    about the extension instead of about itself. The twelve browser tools were in its
    catalog the whole time. Installing the skill is the fix; switching to a visible browser
    opens a window on someone's screen and stays a deliberate choice in the wizard.
    """
    if not _browser:
        return
    try:
        results = _browser.ensure_all(agents=_load_extra_agents(), log=log)
    except Exception as e:  # noqa: BLE001
        log(f"browser: skill check failed: {e}")
        return
    for r in results:
        if r.get("changed"):
            log(f"browser: taught {r['profile']} that it can browse ({r.get('path')})")
            _skill_needs_reload(r["profile"], "browser")
        elif not r.get("ok"):
            log(f"browser: could not teach {r['profile']}: {r.get('detail', '')}")


def _ensure_image_skill():
    """Teach the free image route to every agent that has no image tool of its own.

    A Claude-Code brain cannot draw. Hermes can, but only after the owner has chosen a
    provider and pasted an API key - so in practice a fresh agent says it cannot make
    images, and that is the end of it. Gemini through the agent's own browser costs
    nothing and needs one login, so the agent should at least know to offer it.
    """
    if not _images:
        return
    try:
        results = _images.ensure_all(agents=_load_extra_agents(), log=log,
                                     install_dir=INSTALL_DIR)
    except Exception as e:  # noqa: BLE001
        log(f"images: skill check failed: {e}")
        return
    for r in results:
        if r.get("changed"):
            log(f"images: taught {r['profile']} the free Gemini route ({r.get('path')})")
            _skill_needs_reload(r["profile"], "images")
        elif r.get("reason") == "codex-builtin":
            log(f"images: {r['profile']} runs on Codex - it generates images itself")
        elif not r.get("ok"):
            log(f"images: could not teach {r['profile']}: {r.get('detail', '')}")


def _ensure_intercom_skill():
    """Tell every agent who its colleagues are, and how to ask them something.

    Rewritten whenever the roster changes: the skill NAMES the other agents, so an agent
    added today has to appear in the skill the others already have. Nothing here enables
    anything the owner has not enabled - it only teaches.
    """
    if not _intercom:
        return
    try:
        results = _intercom.ensure_all(log=log, install_dir=INSTALL_DIR)
    except Exception as e:  # noqa: BLE001
        log(f"intercom: skill check failed: {e}")
        return
    for r in results:
        if r.get("changed"):
            log(f"intercom: taught {r['profile']} who else is on this machine "
                f"({r.get('path')})")
            _skill_needs_reload(r["profile"], "intercom")
        elif not r.get("ok"):
            log(f"intercom: could not teach {r['profile']}: {r.get('detail', '')}")


def _activate_pending_policy(cfg, state):
    """Restart a gateway whose conversation policy changed, once that agent is idle.

    The agent can change its own policy, but it cannot restart its own gateway: doing so
    kills the turn it is answering, so the owner's question disappears instead of being
    answered. It leaves a note; this collects it at a moment when nothing is in flight -
    the same rule the engine swap follows.

    Bounded on purpose. A gateway that will not come back is retried three times, five
    minutes apart, and then left alone: a restart attempted on every 15-second tick is the
    exact shape of the respawn storm this supervisor already learned to avoid.
    """
    if not _ctxpol:
        return
    try:
        waiting = _ctxpol.pending()
    except Exception:  # noqa: BLE001
        return
    for key in waiting:
        target = cfg if key == "default" else None
        prof = None if key == "default" else key
        if prof:
            for slug, ent in state.get("extra", {}).items():
                if slug == key or (ent.get("agent") or {}).get("profile") == key:
                    target = ent.get("cfg")
                    break
        if target is not None and not is_idle(target):
            continue          # mid-turn: try again on a later tick
        try:
            res = _ctxpol.activate(profile=prof, log=log)
        except Exception as e:  # noqa: BLE001
            res = {"ok": False, "detail": str(e)}
        if res.get("ok"):
            _ctxpol.clear_pending(prof)
            log(f"context policy: {key} activated ("
                f"{'gateway restarted' if res.get('restarted') else 'gateway was down'})")
        else:
            tries = _ctxpol.note_activation_failure(prof)
            log(f"context policy: could not activate {key} "
                f"(attempt {tries}/{_ctxpol.MAX_ACTIVATION_TRIES}): {res.get('detail', '')}")


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
    _ensure_context_policy()
    _ensure_browser_skill()
    _ensure_image_skill()
    _ensure_intercom_skill()
    last_check = 0.0
    while True:
        try:
            # re-read config each loop so a config written AFTER we started (e.g. by the
            # onboarding wizard) is picked up without needing a restart.
            cfg = load_config()
            beat(cfg)
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
            # an agent that changed its own conversation policy is waiting for us to make
            # it real; do it while it is not answering anyone
            _activate_pending_policy(cfg, state)
            # The owner pressed "update now" in the UI. Checked every loop, not on the
            # poll interval, so the button feels like a button (~15s) instead of like a
            # setting that might take three quarters of an hour to do anything.
            asked = take_request()
            if asked:
                log("update requested from the UI; checking now")
            if asked or time.time() - last_check >= poll:
                last_check = time.time()
                maybe_update(cfg, state, forced=asked)
                # after any update - ours or Hermes' - make sure WhatsApp can still
                # prove a delivery.
                _ensure_whatsapp()
        except Exception as e:
            log(f"supervisor loop error: {e}")
        time.sleep(15)


if __name__ == "__main__":
    main()
