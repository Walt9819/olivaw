r"""Several WhatsApp numbers, one agent, one set of instructions.

The ask: a business has a sales line and a support line, and wants the SAME agent -
same persona, same CLAUDE.md, same skills, same memory - answering both. Not two agents
that happen to be configured alike, which drift apart the first time somebody edits one.

What Hermes actually allows
---------------------------
The gateway keeps its adapters in ``self.adapters[platform]`` (gateway/run.py) - a dict
keyed by the ``Platform`` enum. So the unit of "one WhatsApp number" is
**(profile, platform-value)**, and that dict key is the entire limitation. It is NOT a
limit in the WhatsApp adapter, which already reads::

    self._bridge_port  = config.extra.get("bridge_port", 3000)
    self._session_path = Path(config.extra.get("session_path", ...))

Port and session directory are already per-instance, and ``extra`` comes straight out of
config.yaml. Two bridges against two numbers is plumbing Hermes has; what is missing is a
second KEY to hang the second adapter on.

So we make one. Hermes lets a plugin register a platform under any name
(``platform_registry.register``) and ``Platform._missing_()`` mints an enum member for it.
This module owns the registry of numbers; wa_plugin.py generates the plugin that turns each
row into a real platform. One profile, one gateway, one brain, N numbers.

Three properties this is built to keep
--------------------------------------
* **The first number never changes.** It stays on the plain ``whatsapp`` platform, port
  3000, default session. No migration, no re-pairing, and if the plugin fails to load the
  original number keeps working exactly as before. Everything here is additive.
* **A number is (slug, platform, port, session) or it is nothing.** Two rows sharing a
  session directory are two bridges fighting over one WhatsApp identity; two sharing a port
  are two bridges where one silently loses. Rejected at save time, for the same reason
  agents_registry rejects a colliding agent: a bad row that reaches disk is read back at
  next boot by something that has forgotten who wrote it.
* **Replies go out the number they came in on.** That is free - the gateway resolves the
  adapter from ``source.platform`` - and it is why each number needs its own platform value
  rather than a shared one. A conversation started BY the agent uses the number marked
  ``main``.

Deliberately not done here
--------------------------
``platform_toolsets`` is not written for extra numbers. Hermes auto-generates
``hermes-<platform>`` for a plugin platform from ``_HERMES_CORE_TOOLS``, and the real
``hermes-whatsapp`` toolset is defined as *literally* ``_HERMES_CORE_TOOLS`` - the same
list. So an extra number inherits a tool surface identical to the first number's, with no
list to write. tools/test_numbers.py pins that equality, so if upstream ever changes one
without the other, a test says so instead of a customer silently gaining or losing tools.
"""

import io
import json
import os
import re
import socket

from . import hermes_ctl

# The first number. Hermes' own default: platform "whatsapp", bridge port 3000, session
# under the profile home. Never stored as a row - it exists whenever WhatsApp is on at all.
BUILTIN_PLATFORM = "whatsapp"
BUILTIN_PORT = 3000
BUILTIN_SLUG = "principal"

PLATFORM_PREFIX = "whatsapp_"
PORT_BASE = 3001
PORT_SPAN = 40
MAX_NUMBERS = 6          # see _too_many(): a soft ceiling with a reason attached

_SLUG_RE = re.compile(r"^[a-z][a-z0-9]{0,15}$")
REGISTRY_NAME = "olivaw-numbers.json"


def hermes_home():
    env = os.environ.get("HERMES_HOME")
    if env:
        return env
    local = os.environ.get("LOCALAPPDATA")
    if local and os.path.isdir(os.path.join(local, "hermes")):
        return os.path.join(local, "hermes")
    return os.path.join(os.path.expanduser("~"), ".hermes")


def profile_home(profile=None):
    if not profile or profile == "default":
        return hermes_home()
    return os.path.join(hermes_home(), "profiles", profile)


def registry_path(profile=None):
    """Where the numbers live: inside the agent's own Hermes profile.

    Deliberately NOT in Olivaw's install directory. The generated plugin runs inside the
    gateway under that profile's runtime scope, where the profile home is the one thing it
    can always resolve; making it hunt for an Olivaw install would give it a second way to
    read the wrong agent's numbers.
    """
    return os.path.join(profile_home(profile), REGISTRY_NAME)


def slugify(name):
    s = re.sub(r"[^a-z0-9]+", "", (name or "").lower())
    if s and s[0].isdigit():
        s = "n" + s
    return s[:16] or "linea"


def valid_slug(slug):
    return bool(slug) and _SLUG_RE.match(str(slug)) is not None


def platform_value(slug):
    return PLATFORM_PREFIX + slug


def env_prefix(slug):
    return "WHATSAPP_%s" % slug.upper()


def allowed_users_env(slug):
    return env_prefix(slug) + "_ALLOWED_USERS"


def allow_all_env(slug):
    return env_prefix(slug) + "_ALLOW_ALL_USERS"


def home_channel_env(slug):
    return env_prefix(slug) + "_HOME_CHANNEL"


def session_path(slug, profile=None):
    # Hermes' newer layout (platforms/<name>/session); the adapter is told this path
    # explicitly, so the legacy-vs-new resolution never applies to an extra number.
    return os.path.join(profile_home(profile), "platforms",
                        "whatsapp-%s" % slug, "session")


def builtin_session_paths(profile=None):
    """Both places Hermes may keep the FIRST number's session (it resolves between them)."""
    home = profile_home(profile)
    return [os.path.join(home, "platforms", "whatsapp", "session"),
            os.path.join(home, "whatsapp", "session")]


# ── the registry ─────────────────────────────────────────────────────────────

def load(profile=None):
    try:
        with io.open(registry_path(profile), encoding="utf-8") as fh:
            data = json.load(fh) or {}
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    rows = data.get("numbers")
    data["numbers"] = [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []
    return data


def save(data, profile=None):
    path = registry_path(profile)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with io.open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
    os.replace(tmp, path)
    return path


def extras(profile=None):
    """The additional numbers only - not the built-in first one."""
    return load(profile).get("numbers", [])


def get(slug, profile=None):
    for row in extras(profile):
        if row.get("slug") == slug:
            return row
    return None


def listing(profile=None, hermes=None):
    """Every number this agent has, first one included, in a single shape for the UI."""
    from . import wa_setup
    out = [{
        "slug": BUILTIN_SLUG,
        "label": (load(profile).get("builtin_label") or "Número principal"),
        "platform": BUILTIN_PLATFORM,
        "port": BUILTIN_PORT,
        "session": _existing(builtin_session_paths(profile)),
        "builtin": True,
        "main": not any(r.get("main") for r in extras(profile)),
        # Its OWN sessions, not wa_setup.whatsapp_linked() - that one answers "does this
        # agent have ANY linked number", which is the right question for installing the
        # client skill and the wrong one here. Using it made the main row report itself
        # linked because a different number was.
        "linked": any(_paired(d) for d in builtin_session_paths(profile)),
        "enabled": wa_setup.whatsapp_on(profile),
    }]
    for row in extras(profile):
        out.append({
            "slug": row.get("slug", ""),
            "label": row.get("label") or row.get("slug", ""),
            "platform": row.get("platform") or platform_value(row.get("slug", "")),
            "port": row.get("port"),
            "session": row.get("session") or session_path(row.get("slug", ""), profile),
            "builtin": False,
            "main": bool(row.get("main")),
            "linked": linked(row, profile),
            "enabled": True,
        })
    return out


def _existing(paths):
    for p in paths:
        try:
            if os.listdir(p):
                return p
        except OSError:
            continue
    return paths[0]


def _paired(session_dir):
    """Has a phone actually been linked into this session directory?

    Baileys writes creds.json the moment its bridge starts, long before anyone scans a QR,
    so the file existing proves nothing. `registered` / `me.id` are what pairing sets.
    """
    try:
        with io.open(os.path.join(session_dir or "", "creds.json"), encoding="utf-8") as fh:
            creds = json.load(fh)
    except (OSError, ValueError):
        return False
    if not isinstance(creds, dict):
        return False
    if creds.get("registered") is True:
        return True
    me = creds.get("me")
    return isinstance(me, dict) and bool((me.get("id") or "").strip())


def linked(row, profile=None):
    """Is a phone really paired to THIS number?"""
    return _paired((row or {}).get("session")
                   or session_path((row or {}).get("slug", ""), profile))


def platform_values(profile=None):
    """Every customer-facing WhatsApp platform value for this agent.

    display_policy asks for this: an extra number is a channel a stranger writes to, and an
    unknown platform name falls through to Hermes' global display defaults, where
    tool_progress is "all". A number nobody silenced is a number that shows the customer
    the agent's working notes.
    """
    return [BUILTIN_PLATFORM] + [r.get("platform") or platform_value(r.get("slug", ""))
                                 for r in extras(profile) if r.get("slug")]


def main_number(profile=None):
    """The number an agent-initiated conversation goes out on."""
    for row in listing(profile):
        if row.get("main"):
            return row
    return listing(profile)[0]


# ── allocation and validation ────────────────────────────────────────────────

def _port_free(port):
    """Nothing listening, and nothing able to steal it out from under us.

    No SO_REUSEADDR on purpose - the question is "can a bridge own this port", and
    REUSEADDR would answer yes for a port already in TIME_WAIT from a bridge that just died
    and is about to be restarted.
    """
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", port))
        return True
    except OSError:
        return False


def used_ports(profile=None):
    ports = {BUILTIN_PORT}
    for row in extras(profile):
        try:
            ports.add(int(row.get("port")))
        except (TypeError, ValueError):
            pass
    return ports


def next_port(profile=None):
    """A port no other number claims AND that nothing else on the machine is holding.

    Both halves matter. The registry alone would hand out a port some unrelated program
    already owns, and the bind test alone would hand out a port belonging to a number whose
    bridge happens to be stopped right now.
    """
    used = used_ports(profile)
    for port in range(PORT_BASE, PORT_BASE + PORT_SPAN):
        if port not in used and _port_free(port):
            return port
    return None


def unique_slug(label, profile=None):
    taken = {r.get("slug") for r in extras(profile)} | {BUILTIN_SLUG}
    base = slugify(label)
    slug, n = base, 2
    while slug in taken:
        slug = "%s%d" % (base[:14], n)
        n += 1
    return slug


class Conflict(ValueError):
    """This row cannot be saved: it would collide with a number that already exists."""


def conflicts(row, profile=None):
    """Every reason this row cannot be saved: [(field, value, other_slug)]."""
    out = []
    slug = row.get("slug")
    if not valid_slug(slug):
        out.append(("slug", slug, None))
    if slug == BUILTIN_SLUG:
        out.append(("slug", slug, BUILTIN_SLUG))
    try:
        port = int(row.get("port"))
    except (TypeError, ValueError):
        port = None
        out.append(("port", row.get("port"), None))
    if port == BUILTIN_PORT:
        out.append(("port", port, BUILTIN_SLUG))
    sess = os.path.normcase(os.path.abspath(row.get("session") or ""))
    if not sess:
        out.append(("session", "", None))
    for b in builtin_session_paths(profile):
        if sess and sess == os.path.normcase(os.path.abspath(b)):
            out.append(("session", row.get("session"), BUILTIN_SLUG))
    for other in extras(profile):
        if other.get("slug") == slug:
            continue                        # updating a row in place is not a conflict
        if port is not None and str(other.get("port")) == str(port):
            out.append(("port", port, other.get("slug")))
        o_sess = os.path.normcase(os.path.abspath(other.get("session") or ""))
        if sess and o_sess == sess:
            out.append(("session", row.get("session"), other.get("slug")))
    return out


# Article included per field: "el puerto" but "la carpeta". Spanish gender is not optional,
# and these strings are shown to the owner.
_FIELD_ES = {"slug": ("el", "identificador"), "port": ("el", "puerto"),
             "session": ("la", "carpeta de sesión")}


def describe(problems):
    parts = []
    for field, value, other in problems:
        art, name = _FIELD_ES.get(field, ("el", field))
        if other:
            parts.append("%s %s %s ya es de «%s»" % (art, name, value, other))
        else:
            parts.append("%s %s «%s» no es válido" % (art, name, value))
    return "; ".join(parts)


def _too_many(profile=None):
    """Each number is a whole Node process holding a WhatsApp Web session.

    Roughly 150-250MB each, plus a socket WhatsApp expects to stay alive. The ceiling is
    not arbitrary politeness: past a handful they start losing connections on an ordinary
    PC, and a number that silently drops is worse than one the owner was told not to add.
    """
    return len(extras(profile)) + 1 >= MAX_NUMBERS


def add(label, profile=None, allowed_users="", main=False):
    """Register another number. Does NOT pair it - that needs the owner and a QR."""
    if _too_many(profile):
        return {"ok": False,
                "detail": "Ya tienes %d números en este agente. Cada uno mantiene su propia "
                          "sesión de WhatsApp abierta, y en un equipo normal más de eso "
                          "empieza a perder conexión. Si necesitas más, conviene un segundo "
                          "equipo o un agente aparte." % (len(extras(profile)) + 1)}
    slug = unique_slug(label, profile)
    port = next_port(profile)
    if port is None:
        return {"ok": False,
                "detail": "No encontré un puerto libre para el puente de este número."}
    row = {
        "slug": slug,
        "label": (label or slug).strip()[:40],
        "platform": platform_value(slug),
        "port": port,
        "session": session_path(slug, profile),
        "main": bool(main),
        "allowed_users": _clean_users(allowed_users),
    }
    problems = conflicts(row, profile)
    if problems:
        raise Conflict(describe(problems))
    data = load(profile)
    if main:
        for other in data["numbers"]:
            other["main"] = False
    data["numbers"].append(row)
    save(data, profile)
    return {"ok": True, "number": row}


def _clean_users(value):
    if isinstance(value, (list, tuple)):
        value = ",".join(str(v) for v in value)
    return ",".join([u.strip() for u in re.split(r"[,\s]+", str(value or "")) if u.strip()])


def update(slug, profile=None, label=None, allowed_users=None, main=None):
    data = load(profile)
    row = None
    for r in data["numbers"]:
        if r.get("slug") == slug:
            row = r
            break
    if row is None:
        return {"ok": False, "detail": "Ese número no existe en este agente."}
    if label is not None:
        row["label"] = str(label).strip()[:40] or row.get("label") or slug
    if allowed_users is not None:
        row["allowed_users"] = _clean_users(allowed_users)
    if main is not None:
        if main:
            for other in data["numbers"]:
                other["main"] = False
        row["main"] = bool(main)
    problems = conflicts(row, profile)
    if problems:
        raise Conflict(describe(problems))
    save(data, profile)
    return {"ok": True, "number": row}


def set_main(slug, profile=None):
    """Which number an agent-initiated conversation leaves from."""
    data = load(profile)
    known = {r.get("slug") for r in data["numbers"]} | {BUILTIN_SLUG}
    if slug not in known:
        return {"ok": False, "detail": "Ese número no existe en este agente."}
    for r in data["numbers"]:
        r["main"] = (r.get("slug") == slug)
    save(data, profile)
    return {"ok": True, "main": slug}


def remove(slug, profile=None, drop_session=False):
    """Forget a number. Its paired session is KEPT unless explicitly dropped.

    Deleting it would unlink the phone, and re-pairing means the owner has to find that
    device again with a QR. Removing a number from a list should not be able to cost that.
    """
    data = load(profile)
    before = len(data["numbers"])
    row = get(slug, profile)
    data["numbers"] = [r for r in data["numbers"] if r.get("slug") != slug]
    if len(data["numbers"]) == before:
        return {"ok": False, "detail": "Ese número no existe en este agente."}
    save(data, profile)
    if drop_session and row:
        import shutil
        shutil.rmtree(row.get("session") or "", ignore_errors=True)
    return {"ok": True, "removed": slug, "session_kept": not drop_session}


# ── writing it into Hermes' own config ───────────────────────────────────────

def _cfg(key, value, profile, hermes=None):
    return hermes_ctl.config_set(key, value, hermes, profile)


def config_plan(profile=None):
    """The config.yaml keys every registered number needs. [(key, value)]."""
    out = []
    for row in extras(profile):
        slug = row.get("slug")
        if not valid_slug(slug):
            continue
        base = "platforms.%s" % (row.get("platform") or platform_value(slug))
        out.append((base + ".enabled", "true"))
        out.append((base + ".extra.bridge_port", str(row.get("port"))))
        out.append((base + ".extra.session_path", row.get("session") or
                    session_path(slug, profile)))
        # Belt and braces on access. `dm_policy` is the ADAPTER's own gate, applied at
        # intake; the WHATSAPP_<SLUG>_ALLOWED_USERS env below is the GATEWAY's, applied in
        # authz. Hermes' adapters default dm_policy to "pairing", and Olivaw never wants a
        # customer line handing out pairing codes, so it is pinned to an allowlist here.
        out.append((base + ".extra.dm_policy", "allowlist"))
        out.append((base + ".extra.group_policy", "disabled"))
    return out


def env_plan(profile=None):
    """The profile .env entries every registered number needs."""
    updates = {}
    for row in extras(profile):
        slug = row.get("slug")
        if not valid_slug(slug):
            continue
        updates[allowed_users_env(slug)] = row.get("allowed_users", "")
        # Explicitly OFF rather than merely absent: an allow-all flag that is missing and
        # one that is false read the same to Hermes, but not to a person auditing the file.
        updates[allow_all_env(slug)] = "0"
    return updates


def apply(profile=None, hermes=None, log=None):
    """Make Hermes agree with the registry. Idempotent; safe on every supervisor pass."""
    from . import wa_plugin
    rows = extras(profile)
    plugin = wa_plugin.ensure(profile, hermes, log=log) if rows else \
        {"ok": True, "changed": False, "reason": "no-extra-numbers"}
    written, failed = [], []
    for key, value in config_plan(profile):
        r = _cfg(key, value, profile, hermes)
        (written if r.get("ok") else failed).append(key)
    env = env_plan(profile)
    env_res = hermes_ctl.set_env_vars(env, hermes, profile) if env else {"ok": True}
    if log and written:
        log("numbers: %s - configured %d key(s) for %d extra number(s)"
            % (profile or "default", len(written), len(rows)))
    if log and failed:
        log("numbers: %s - could not set %s" % (profile or "default", ", ".join(failed)))
    return {"ok": not failed and bool(env_res.get("ok", True)) and plugin.get("ok", True),
            "plugin": plugin, "written": written, "failed": failed,
            "numbers": len(rows) + 1}


def status(profile=None, hermes=None):
    """For the console and the wizard panel."""
    rows = listing(profile, hermes)
    pending = [r for r in rows if not r["linked"]]
    return {
        "ok": True,
        "numbers": rows,
        "count": len(rows),
        "max": MAX_NUMBERS,
        "can_add": len(rows) < MAX_NUMBERS,
        "main": (main_number(profile) or {}).get("slug"),
        "detail": ("Este agente atiende %d número%s de WhatsApp."
                   % (len(rows), "" if len(rows) == 1 else "s")
                   + ("" if not pending else
                      " Falta vincular: %s." % ", ".join(r["label"] for r in pending))),
    }
