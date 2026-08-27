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
INSTALL_ROOT = os.path.dirname(os.path.dirname(_HERE))         # folder holding src/, VERSION
BASE_PORT = 8790          # default agent
PORT_STEP = 2             # each agent uses an even port (bridge). +1 kept free as headroom


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
