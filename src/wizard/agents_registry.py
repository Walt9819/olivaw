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


# ── one agent, one set of resources ──────────────────────────────────────────
# An agent is not a name: it is a name PLUS a Hermes profile, a bridge port and a workspace,
# and the whole thing only works while those four agree with each other. Two entries sharing
# a port means two bridges fighting for one socket - one wins, and every message routed to
# the loser is answered by the wrong brain, or by nothing. Two entries sharing a profile
# means configuring one agent silently reconfigures the other: the UI writes a token, pairs a
# channel, sets a workspace, and it lands on somebody else's agent. That is not a
# hypothetical - it is how a WhatsApp session ends up paired under a profile whose gateway is
# not the one running.
#
# The registry is the only place that can see all of it at once, so it is the only place the
# check belongs. Rejecting at save time is deliberate: a bad row that reaches disk is read
# back by the supervisor on the next boot and by then nobody remembers writing it.

PROFILE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


class Conflict(ValueError):
    """Two agents would end up sharing a resource that only one can own."""


def valid_profile(name):
    """A profile name that is safe to put in a path and to hand to `hermes --profile`.

    Deliberately narrower than "any string": every caller eventually joins this onto
    <hermes home>/profiles/, so a name is a path segment before it is anything else.
    """
    return bool(name) and PROFILE_RE.match(str(name)) is not None


def conflicts(agent, install_dir=None):
    """Every reason this row cannot be saved as-is: [(field, value, other_slug)].

    Only what the write INTRODUCES is judged. A row whose slug, profile and port are
    already exactly what is on disk passes untouched even if it is bad, because the
    alternative is worse: a legacy row with a colliding port would become impossible to
    pause, rename or repair, and pausing it is precisely the fix somebody would reach for.
    Validation exists to stop a new mistake reaching disk, not to trap the owner behind an
    old one.
    """
    slug = agent.get("slug")
    stored = get(slug, install_dir) if slug else None
    if stored and all(str(stored.get(k)) == str(agent.get(k))
                      for k in ("slug", "profile", "port")):
        return []
    out = []
    if not valid_profile(slug):
        out.append(("slug", slug, None))
    prof = agent.get("profile") or slug
    if not valid_profile(prof):
        out.append(("profile", prof, None))
    try:
        port = int(agent.get("port"))
    except (TypeError, ValueError):
        port = None
        out.append(("port", agent.get("port"), None))
    if port == BASE_PORT:
        # BASE_PORT belongs to the default agent, which is not in this registry and so
        # cannot show up in the loop below.
        out.append(("port", port, "default"))
    for other in list_agents(install_dir):
        if other.get("slug") == slug:
            continue                                  # updating in place is not a conflict
        if port is not None and str(other.get("port")) == str(port):
            out.append(("port", port, other.get("slug")))
        if (other.get("profile") or other.get("slug")) == prof:
            out.append(("profile", prof, other.get("slug")))
    return out


_FIELD_ES = {"slug": "identificador", "profile": "perfil", "port": "puerto"}


def describe_conflicts(problems):
    parts = []
    for field, value, other in problems:
        name = _FIELD_ES.get(field, field)
        if other:
            parts.append("el %s %s ya es de «%s»" % (name, value, other))
        else:
            parts.append("el %s «%s» no es válido" % (name, value))
    return "; ".join(parts)


def upsert(agent, install_dir=None):
    problems = conflicts(agent, install_dir)
    if problems:
        raise Conflict(describe_conflicts(problems))
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


# ── which agent a request is talking about ───────────────────────────────────

def _hermes_home():
    env = os.environ.get("HERMES_HOME")
    if env:
        return env
    local = os.environ.get("LOCALAPPDATA")
    if local and os.path.isdir(os.path.join(local, "hermes")):
        return os.path.join(local, "hermes")
    return os.path.join(os.path.expanduser("~"), ".hermes")


def existing_profiles(hermes_home=None):
    """Profile names that really exist on disk under <hermes home>/profiles."""
    base = os.path.join(hermes_home or _hermes_home(), "profiles")
    try:
        return {n for n in os.listdir(base)
                if valid_profile(n) and os.path.isdir(os.path.join(base, n))}
    except OSError:
        return set()


def known_profiles(install_dir=None, hermes_home=None):
    """Every profile this machine legitimately has: registered agents plus what is on disk."""
    known = set()
    for a in list_agents(install_dir):
        for key in ("profile", "slug"):
            if valid_profile(a.get(key)):
                known.add(a[key])
    return known | existing_profiles(hermes_home)


def resolve_profile(value, install_dir=None, hermes_home=None, allow_new=False):
    """Turn whatever the browser sent into a profile this machine actually has.

    The wizard used to take `body["profile"]` at its word and hand it straight to
    `hermes --profile` and to os.path.join(<hermes home>, "profiles", <it>). Two things
    follow from that, and both have bitten:

      * a name that does not exist quietly creates a SECOND configuration - the QR pairs,
        a session lands on disk, the write reports success, and the gateway that is
        actually running has no idea any of it happened;
      * a name is a path segment, and nothing was checking it looked like one.

    Returns None for the default agent, the canonical name for an extra one, and raises
    ValueError for anything else. `allow_new=True` is for the one caller that is
    legitimately creating a profile that does not exist yet.
    """
    name = (value or "").strip()
    if not name or name == "default":
        return None
    if not valid_profile(name):
        raise ValueError("Identificador de agente inválido.")
    if allow_new:
        return name
    known = known_profiles(install_dir, hermes_home)
    if name not in known:
        raise ValueError("Este equipo no tiene ningún agente llamado «%s»." % name)
    # An agent registered under a slug whose Hermes profile is named differently is
    # addressed by its profile from here on - that is the name every path is built from.
    for a in list_agents(install_dir):
        if a.get("slug") == name and valid_profile(a.get("profile")):
            return a["profile"]
    return name
