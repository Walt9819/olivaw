"""
Thin wrappers over the `hermes` CLI — the wizard drives Hermes' own commands instead
of hand-editing its config.

Profile targeting (verified on Windows): `HERMES_PROFILE` does NOT retarget, and there is
no per-call --profile flag. Each profile gets a wrapper script at
`~/.local/bin/<slug>(.bat)` that runs every subcommand against THAT profile. So:
  • default profile  -> bare `hermes ...`
  • profile <slug>   -> `~/.local/bin/<slug>.bat ...`  (created via `hermes profile alias`)
This lets us configure agent B without disturbing agent A (no sticky switching).

Contracts:
  hermes config get/set <dotted.key> [value] | path | env-path
  hermes pairing list | approve <platform> <code> | revoke <platform> <user_id>
  hermes gateway status | start | stop | restart | list
  hermes send --to <target> "<text>"
  hermes profile list | create <name> | delete -y <name> | alias <name> | show <name>

Owner-lock = TELEGRAM_ALLOWED_USERS in the profile's .env (+ the dynamic pairing flow).
"""

import os
import re
import time

from .procutil import run, which

LOCAL_BIN = os.path.join(os.path.expanduser("~"), ".local", "bin")


def hermes_path(explicit=None):
    return explicit or which("hermes")


def available(explicit=None):
    return bool(hermes_path(explicit))


def wrapper_path(profile):
    """Absolute path to a profile's wrapper script (may not exist yet)."""
    name = profile + (".bat" if os.name == "nt" else "")
    return os.path.join(LOCAL_BIN, name)


def _base(profile=None, hermes=None):
    """Command prefix that targets the right profile (or None if unavailable).

    The main agent is named with `-p default` rather than left implicit. A bare `hermes`
    is NOT reliably the main agent: with no flag, Hermes falls back to the sticky
    `active_profile` file in its root (hermes_cli/main.py step 2), so after anyone runs
    `hermes profile use heraldo` every call meant for the main agent would be answered,
    and WRITTEN, by heraldo instead. The wizard uses this to read and set config, so that
    would put the main agent's settings into another agent's profile.

    `-p default` is resolved by get_profile_dir("default") to the root home itself and is
    explicitly exempt from the "profile must exist" check, so it is also correct on a
    fresh machine that has no profiles at all. The same fix, for the same reason, is in
    intercom._base - where this bug was live rather than latent.
    """
    if not profile or profile == "default":
        exe = hermes_path(hermes)
        return [exe, "-p", "default"] if exe else None
    wrap = wrapper_path(profile)
    if not os.path.exists(wrap):
        return None
    return ["cmd", "/c", wrap] if os.name == "nt" else [wrap]


def _run(args, hermes=None, timeout=60, profile=None):
    base = _base(profile, hermes)
    if not base:
        return {"ok": False, "out": "", "err": "hermes/profile no disponible", "code": -1}
    return run(base + list(args), timeout=timeout)


# ── profiles ─────────────────────────────────────────────────────────────────
def profile_list(hermes=None):
    r = _run(["profile", "list"], hermes, timeout=30)
    profiles = []
    if not r["ok"]:
        return profiles
    for line in r["out"].splitlines():
        s = line.strip()
        if not s or set(s) <= set("-─ \t") or s.lower().startswith("profile"):
            continue
        active = s.startswith("◆") or s.startswith("*")
        s = s.lstrip("◆* ").strip()
        cols = re.split(r"\s{2,}", s)
        if not cols or not cols[0]:
            continue
        profiles.append({
            "name": cols[0].strip(),
            "model": cols[1].strip() if len(cols) > 1 else "",
            "gateway": cols[2].strip() if len(cols) > 2 else "",
            "active": active,
        })
    return profiles


def active_profile(hermes=None):
    for p in profile_list(hermes):
        if p["active"]:
            return p["name"]
    return "default"


def profile_exists(name, hermes=None):
    return any(p["name"] == name for p in profile_list(hermes))


def profile_create(name, description=None, clone=False, hermes=None):
    args = ["profile", "create", name, "--no-skills"]
    if clone:
        args.append("--clone")
    if description:
        args += ["--description", description]
    r = _run(args, hermes, timeout=120)
    # ensure the wrapper alias exists (create usually makes it, but be safe)
    ensure_alias(name, hermes)
    return {"ok": r["ok"], "detail": (r["out"] or r["err"])[:400]}


def profile_delete(name, hermes=None):
    if not name or name == "default":
        return {"ok": False, "detail": "No se puede borrar el perfil default."}
    r = _run(["profile", "delete", "-y", name], hermes, timeout=60)
    return {"ok": r["ok"], "detail": (r["out"] or r["err"])[:400]}


def ensure_alias(name, hermes=None):
    if os.path.exists(wrapper_path(name)):
        return True
    _run(["profile", "alias", name], hermes, timeout=40)
    return os.path.exists(wrapper_path(name))


# ── config (profile-targeted) ──────────────────────────────────────────────────
def config_get(key, hermes=None, profile=None):
    r = _run(["config", "get", key], hermes, timeout=30, profile=profile)
    return r["out"].strip() if r["ok"] else ""


def config_set(key, value, hermes=None, profile=None):
    r = _run(["config", "set", key, str(value)], hermes, timeout=40, profile=profile)
    return {"ok": r["ok"], "detail": (r["out"] or r["err"])[:300]}


def config_path(hermes=None, profile=None):
    r = _run(["config", "path"], hermes, timeout=30, profile=profile)
    return r["out"].strip() if r["ok"] else ""


def env_path(hermes=None, profile=None):
    r = _run(["config", "env-path"], hermes, timeout=30, profile=profile)
    return r["out"].strip() if r["ok"] else ""


def set_env_vars(updates, hermes=None, profile=None):
    """Upsert KEY=value lines in the profile's .env, preserving everything else."""
    path = env_path(hermes, profile)
    if not path or not os.path.isdir(os.path.dirname(path)):
        return {"ok": False, "detail": "No se encontró el .env de Hermes."}
    lines = []
    if os.path.exists(path):
        with open(path, encoding="utf-8", errors="replace") as fh:
            lines = fh.read().splitlines()
        try:
            import shutil
            shutil.copy2(path, path + ".bak")
            try:
                os.chmod(path + ".bak", 0o600)   # backup of a secret .env is still a secret
            except Exception:  # noqa: BLE001
                pass
        except Exception:  # noqa: BLE001
            pass
    seen = set()
    for i, ln in enumerate(lines):
        m = re.match(r"\s*([A-Z0-9_]+)\s*=", ln)
        if m and m.group(1) in updates:
            lines[i] = "%s=%s" % (m.group(1), updates[m.group(1)])
            seen.add(m.group(1))
    for k, v in updates.items():
        if k not in seen:
            lines.append("%s=%s" % (k, v))
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    try:
        os.chmod(tmp, 0o600)
    except Exception:  # noqa: BLE001
        pass
    os.replace(tmp, path)
    try:
        os.chmod(path, 0o600)
    except Exception:  # noqa: BLE001
        pass
    return {"ok": True, "detail": "Actualizado %s" % ", ".join(updates.keys()), "path": path}


# ── pairing / gateway / send (profile-targeted) ─────────────────────────────────
def pairing_list(hermes=None, profile=None):
    r = _run(["pairing", "list"], hermes, timeout=30, profile=profile)
    approved, pending = [], 0
    if not r["ok"]:
        return {"approved": approved, "pending_count": pending, "ok": False}
    for line in r["out"].splitlines():
        s = line.strip()
        if "pending" in s.lower():
            m = re.search(r"(\d+)", s)
            if m and "no pending" not in s.lower():
                pending = int(m.group(1))
        cols = re.split(r"\s{2,}", s)
        if len(cols) >= 2 and re.fullmatch(r"\d+", cols[1].strip()):
            approved.append({"platform": cols[0].strip(), "user_id": cols[1].strip(),
                             "name": cols[2].strip() if len(cols) > 2 else ""})
    return {"approved": approved, "pending_count": pending, "ok": True}


def pairing_approve(platform, code, hermes=None, profile=None):
    r = _run(["pairing", "approve", platform, str(code)], hermes, timeout=40, profile=profile)
    return {"ok": r["ok"], "detail": (r["out"] or r["err"])[:300]}


def pairing_revoke(platform, user_id, hermes=None, profile=None):
    r = _run(["pairing", "revoke", platform, str(user_id)], hermes, timeout=40, profile=profile)
    return {"ok": r["ok"], "detail": (r["out"] or r["err"])[:300]}


# Where the status of OTHER agents begins. Everything below it is about somebody else
# and must not answer a question about this one.
_GW_OTHERS = re.compile(r"(?im)^\s*other profiles\b")

# A line saying this gateway is NOT up. Checked first, because every one of these
# sentences contains a word that also appears in the ones saying it IS up.
_GW_DOWN = re.compile(
    r"\bnot\s+(?:currently\s+)?(?:running|active|online|supervised|installed)\b"
    r"|\binactive\b|\bstopped\b|\bno\s+gateway\b", re.I)

# A pid is the one thing every "it is up" line has and no "it is down" line does - and
# the only signal that survives the wording changing between platforms.
_GW_PID = re.compile(r"\bpid\b\W{0,3}\d+", re.I)
_GW_UP = re.compile(r"\b(running|active|online|supervised)\b", re.I)


def gateway_running_from(out):
    """Is THIS gateway up, from what `hermes gateway status` printed?

    Three real outputs it has to get right:

        Windows, up       "Gateway process running (PID: 146524)"
        Mac under launchd "Gateway is supervised by launchd (PID 767)"
        either, down      "Gateway process not running"

    A substring search for run|active|online answers the first correctly and the other
    two backwards: the Mac line contains no "running" at all, and "not running"
    contains "running" (as "inactive" contains "active"). Wrong in the first direction
    starts a second gateway every fifteen minutes against one that is working - the Mac
    did it 291 times in a day. Wrong in the second leaves a dead agent alone and tells
    nobody, which is the worse of the two.

    Line by line, and only down to the "Other profiles" heading, because the command
    prints every other agent and its pid underneath - so the question "is daneel up"
    was being answered by whichever agent happened to be.
    """
    head = _GW_OTHERS.split(out or "", 1)[0]
    for line in head.splitlines():
        line = line.strip()
        if not line or _GW_DOWN.search(line):
            continue
        # A line naming a pid, or saying it is up, settles it - and wins over a
        # "not supervised" beside it: one of those is the gateway and the other is a
        # remark about how it was started.
        if _GW_PID.search(line) or _GW_UP.search(line):
            return True
    # Nothing said it was up. Said so explicitly or said nothing at all, the answer the
    # supervisor acts on is the same, and "start it" is the recoverable mistake.
    return False


def gateway_status(hermes=None, profile=None):
    r = _run(["gateway", "status"], hermes, timeout=30, profile=profile)
    running = r["ok"] and gateway_running_from(r["out"])
    return {"ok": r["ok"], "running": bool(running), "detail": (r["out"] or r["err"])[:400]}


def _wait_gateway(want_running, hermes, profile, seconds):
    """Wait for the gateway to actually reach a state, rather than trusting the command that
    asked for it. Returns True if it got there."""
    deadline = time.time() + seconds
    while True:
        if bool(gateway_status(hermes, profile).get("running")) == want_running:
            return True
        if time.time() >= deadline:
            return False
        time.sleep(1.5)


def gateway_restart_safe(hermes=None, profile=None, timeout=90):
    """stop -> confirm it is gone -> start -> confirm it is up.

    `gateway restart` has been seen racing itself on Windows into two gateways (Hermes kills one,
    but the flow reports several PIDs on the way). Doing it in two explicit steps, each verified,
    has no race to lose.
    """
    steps = []
    stop = _run(["gateway", "stop"], hermes, timeout=timeout, profile=profile)
    steps.append("stop: " + ((stop["out"] or stop["err"]).strip()[:120] or "ok"))
    gone = _wait_gateway(False, hermes, profile, 20)
    if not gone:
        steps.append("seguía corriendo tras 20s; arranco de todas formas")
    start = _run(["gateway", "start"], hermes, timeout=timeout, profile=profile)
    steps.append("start: " + ((start["out"] or start["err"]).strip()[:120] or "ok"))
    up = _wait_gateway(True, hermes, profile, 30)
    return {"ok": bool(up), "detail": " | ".join(steps)[:400],
            "running": up,
            "restarted_cleanly": bool(gone and up)}


def gateway(action, hermes=None, profile=None, timeout=90):
    # A restart is the one action with a race in it, so it goes through the deterministic path.
    if action == "restart":
        return gateway_restart_safe(hermes, profile, timeout)
    r = _run(["gateway", action], hermes, timeout=timeout, profile=profile)
    return {"ok": r["ok"], "detail": (r["out"] or r["err"])[:400]}


def send(target, text, hermes=None, profile=None):
    r = _run(["send", "--to", target, text], hermes, timeout=40, profile=profile)
    return {"ok": r["ok"], "detail": (r["out"] or r["err"])[:300]}
