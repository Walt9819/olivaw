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
    """Command prefix that targets the right profile (or None if unavailable)."""
    if not profile or profile == "default":
        exe = hermes_path(hermes)
        return [exe] if exe else None
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


def gateway_status(hermes=None, profile=None):
    r = _run(["gateway", "status"], hermes, timeout=30, profile=profile)
    running = r["ok"] and re.search(r"run|active|online", r["out"], re.I) is not None
    return {"ok": r["ok"], "running": bool(running), "detail": (r["out"] or r["err"])[:400]}


def gateway(action, hermes=None, profile=None, timeout=90):
    r = _run(["gateway", action], hermes, timeout=timeout, profile=profile)
    return {"ok": r["ok"], "detail": (r["out"] or r["err"])[:400]}


def send(target, text, hermes=None, profile=None):
    r = _run(["send", "--to", target, text], hermes, timeout=40, profile=profile)
    return {"ok": r["ok"], "detail": (r["out"] or r["err"])[:300]}
