"""
Registry of ADDITIONAL agents on this machine (agents.json at the install root).

Each agent = an isolated Hermes profile + its own bridge instance:
  {slug, name, profile, port, workspace, claude_config_dir, bot_username, engine}
  `engine` is optional: absent means "same brain as the default agent".

The original `default` agent (Walt's existing setup) is NOT stored here — it keeps
running from updater.config.json exactly as before. This registry only layers extra
agents on top, so nothing about the single-agent path changes.
"""

import json
import os
import re

_HERE = os.path.dirname(os.path.abspath(__file__))            # .../src/wizard
_CODE_ROOT = os.path.dirname(os.path.dirname(_HERE))           # folder holding src/, VERSION
BASE_PORT = 8790          # default agent
PORT_STEP = 2             # each agent uses an even port (bridge). +1 kept free as headroom

# The file the installer writes. Its presence is what makes a directory "the install"
# rather than "a copy of the code".
_INSTALL_MARK = "updater.config.json"


def _candidate_installs():
    """Every place an Olivaw install has ever been put, newest naming first."""
    out = []
    if os.name == "nt":
        local = os.environ.get("LOCALAPPDATA", "")
        if local:
            out += [os.path.join(local, "Olivaw"),
                    os.path.join(local, "HermesBridge")]   # pre-rename installs
    else:
        home = os.path.expanduser("~")
        out += [os.path.join(home, ".olivaw"),
                os.path.join(home, ".hermes-bridge"),
                os.path.join(home, "Library", "Application Support", "Olivaw")]
    return out


def install_root():
    """The one directory holding this machine's agent state - agents.json and agents/.

    Deliberately NOT "the folder this file lives in". Olivaw can be running from a source
    checkout while the REAL install - the one whose supervisor reads agents.json and starts
    each agent's bridge - sits somewhere else. When those two disagree, an agent created in
    the wizard is written where nothing ever reads it: its Telegram bot answers, its brain
    never starts, and the owner gets silence on a setup that reported success.

    Resolution, most specific first:
      1. OLIVAW_INSTALL_DIR - tests, and unusual layouts;
      2. this code's own root IF it carries updater.config.json, i.e. this copy IS the
         install (the normal case, and it keeps the supervisor's behaviour identical);
      3. a real install found in a known location;
      4. this code's own root, for a first run before anything has been installed.
    """
    env = (os.environ.get("OLIVAW_INSTALL_DIR") or "").strip()
    if env:
        return os.path.abspath(os.path.expanduser(env))

    if os.path.isfile(os.path.join(_CODE_ROOT, _INSTALL_MARK)):
        return _CODE_ROOT

    found = [d for d in _candidate_installs()
             if os.path.isfile(os.path.join(d, _INSTALL_MARK))]
    if found:
        # More than one only happens across a rename; the one written last is live.
        found.sort(key=lambda d: os.path.getmtime(os.path.join(d, _INSTALL_MARK)),
                   reverse=True)
        return found[0]

    return _CODE_ROOT


# Kept as a module attribute because callers import it; resolved once at import, like before.
INSTALL_ROOT = install_root()


def registry_path(install_dir=None):
    return os.path.join(install_dir or INSTALL_ROOT, "agents.json")


def load(install_dir=None):
    p = registry_path(install_dir)
    if os.path.exists(p):
        try:
            with open(p, encoding="utf-8") as fh:
                data = json.load(fh) or {}
        except Exception:  # noqa: BLE001
            data = {}
    else:
        data = {}
    data.setdefault("agents", [])
    return data


def save(data, install_dir=None):
    p = registry_path(install_dir)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
    os.replace(tmp, p)


def list_agents(install_dir=None):
    return load(install_dir).get("agents", [])


def get(slug, install_dir=None):
    for a in list_agents(install_dir):
        if a.get("slug") == slug:
            return a
    return None


def reconcile(install_dir=None, log=None):
    """Adopt agents that a previous run registered next to the code instead of the install.

    Anyone who ran the wizard from a checkout before install_root() existed has an
    agents.json the supervisor never reads, and agents that answer on Telegram but have no
    brain behind them. Merging is safe and one-directional: entries already in the canonical
    registry win, orphans are added, and the stale file is left on disk rather than deleted
    so the change is trivially reversible.

    Returns the slugs it adopted.
    """
    target = install_dir or INSTALL_ROOT
    stale = os.path.join(_CODE_ROOT, "agents.json")
    if os.path.abspath(_CODE_ROOT) == os.path.abspath(target) or not os.path.isfile(stale):
        return []
    try:
        with open(stale, encoding="utf-8") as fh:
            orphans = (json.load(fh) or {}).get("agents") or []
    except Exception:  # noqa: BLE001
        return []
    if not orphans:
        return []

    data = load(target)
    known = {a.get("slug") for a in data["agents"]}
    adopted = []
    for a in orphans:
        slug = a.get("slug")
        if slug and slug not in known:
            data["agents"].append(a)
            known.add(slug)
            adopted.append(slug)
    if adopted:
        save(data, target)
        if log:
            log("agents: adopted %s from %s (registered next to the code, where the "
                "supervisor never looks)" % (", ".join(adopted), stale))
    return adopted


def upsert(agent, install_dir=None):
    data = load(install_dir)
    agents = data["agents"]
    for i, a in enumerate(agents):
        if a.get("slug") == agent.get("slug"):
            agents[i] = agent
            break
    else:
        agents.append(agent)
    save(data, install_dir)
    return agent


def remove(slug, install_dir=None):
    data = load(install_dir)
    before = len(data["agents"])
    data["agents"] = [a for a in data["agents"] if a.get("slug") != slug]
    save(data, install_dir)
    return len(data["agents"]) < before


def used_ports(install_dir=None):
    ports = {BASE_PORT}
    for a in list_agents(install_dir):
        try:
            ports.add(int(a.get("port")))
        except (TypeError, ValueError):
            pass
    return ports


def next_port(install_dir=None):
    used = used_ports(install_dir)
    p = BASE_PORT + PORT_STEP
    while p in used:
        p += PORT_STEP
    return p


def slugify(name):
    s = re.sub(r"[^a-z0-9]+", "", (name or "").lower())
    return s[:24] or "agente"


def unique_slug(name, hermes_profiles=None, install_dir=None):
    """A slug not already used by a registered agent OR an existing Hermes profile."""
    taken = {a.get("slug") for a in list_agents(install_dir)}
    taken.add("default")
    for p in (hermes_profiles or []):
        taken.add(p)
    base = slugify(name)
    slug = base
    n = 2
    while slug in taken:
        slug = "%s%d" % (base, n)
        n += 1
    return slug


def agent_dir(slug, install_dir=None):
    return os.path.join(install_dir or INSTALL_ROOT, "agents", slug)
