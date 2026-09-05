r"""A customer sees the answer. Never the work behind it.

The incident
------------
A WhatsApp customer was shown terminal output, tool progress, and a diagnostic about
conversation compression. None of it was a bug in the thing that produced it — the
compression message in particular was a *correct* fallback, refusing to compress a
transcript that would have grown. It was rendered to a customer, and that is the defect.

Why it happened, exactly
------------------------
Nothing in Olivaw has ever written a single ``display.*`` key. ``config_writer.apply_hermes``
sets the model, the conversation policy and the profile ``.env``, and stops. So every agent
Olivaw creates inherits Hermes' own per-platform defaults, and Hermes files WhatsApp under
``_TIER_MEDIUM`` (gateway/display_config.py)::

    tool_progress:               "new"
    interim_assistant_messages:  True
    long_running_notifications:  True
    busy_ack_detail:             True

Those are good defaults for a personal inbox. For the channel where *clients* write they are
a leak, and they are on unless somebody turns them off. Nobody was turning them off.

The rule
--------
The owner's channel may show everything: she is the operator, the work is hers to watch.
Any channel a stranger can write to shows the final answer and nothing else. That is a
transport-level guarantee, not an instruction to the model — an agent can be confused,
prompted badly, or simply wrong, and none of that may become a customer's problem.

Design notes worth keeping
--------------------------
* **Only channels that are actually on.** A Telegram-only agent gets zero writes, so the
  common install pays nothing for this. Each ``hermes config set`` is a process spawn.
* **Never overwrite a deliberate choice.** Written per key: a key already present in
  config.yaml is left exactly as it is, so an owner who *wants* progress on WhatsApp keeps
  it and this module stops arguing. That is the same contract context_policy.py honours.
* **The values survive Hermes' own coercion.** ``hermes config set <k> off`` coerces "off"
  to the boolean ``False`` for any key it has no default for — and ``display.platforms``
  has no ``whatsapp`` entry in Hermes' DEFAULT_CONFIG, so that is every key here. It is
  harmless: ``display_config._normalise`` maps ``False`` back to ``"off"`` for
  ``tool_progress`` and to ``False`` for the booleans. Checked against Hermes' source
  rather than assumed, because a silently-dead config write is worse than no write.
* **``show_commentary`` is deliberately not set.** It is global-only, so setting it would
  also silence the owner's own channel. Codex's commentary reaches a platform through the
  interim-message path, which is off here per platform — narrower, and it does the job.
"""

import os
import re

from . import hermes_ctl

# Channels a stranger can end up writing to. Telegram is absent on purpose: Olivaw
# owner-locks it at setup (TELEGRAM_ALLOWED_USERS) and it is the operator's own window.
CUSTOMER_PLATFORMS = ("whatsapp", "whatsapp_cloud", "google_chat", "slack",
                      "signal", "discord", "email")

# Exactly the keys Hermes accepts per platform (gateway/display_config.OVERRIDEABLE_KEYS).
# Anything invented here would be written, ignored, and believed.
QUIET = (
    ("tool_progress", "off"),                 # tool names, arguments, terminal output
    ("streaming", False),                     # half-finished sentences
    ("interim_assistant_messages", False),    # mid-turn commentary and status
    ("long_running_notifications", False),    # "still working" heartbeats
    ("busy_ack_detail", False),               # "iteration 7 of 60"
    ("busy_steer_ack_enabled", False),        # "Steered into current run"
    ("show_reasoning", False),                # thinking summaries
)

# What turns each channel on, in the profile's own .env. Presence of a non-empty value is
# the signal — the same thing the gateway itself keys off.
#
# Deliberately the FLAG here, not a proven pairing (which is what wa_setup uses to decide
# whether to install the client-handling skill). The two are asymmetric on purpose: a skill
# on an agent with no WhatsApp is actively misleading, so it waits for proof; a display
# setting on a channel nobody uses is inert, and it has to already be in place before the
# very first customer message — which may arrive seconds after a QR is scanned, with no
# supervisor pass in between.
_ENV_SIGNALS = {
    "whatsapp": ("WHATSAPP_ENABLED",),
    "whatsapp_cloud": ("WHATSAPP_CLOUD_ACCESS_TOKEN", "WHATSAPP_CLOUD_PHONE_NUMBER_ID"),
    "google_chat": ("GOOGLE_CHAT_SERVICE_ACCOUNT_JSON",),
    "slack": ("SLACK_BOT_TOKEN", "SLACK_APP_TOKEN"),
    "signal": ("SIGNAL_PHONE_NUMBER", "SIGNAL_ACCOUNT"),
    "discord": ("DISCORD_BOT_TOKEN",),
    "email": ("EMAIL_ADDRESS", "SMTP_HOST"),
}

_FALSEY = ("", "0", "false", "no", "off")


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


def config_file(hermes=None, profile=None):
    """The profile's own config.yaml. Ask Hermes first — it knows about HERMES_HOME."""
    try:
        p = hermes_ctl.config_path(hermes, profile)
        if p and os.path.isfile(p):
            return p
    except Exception:  # noqa: BLE001
        pass
    return os.path.join(profile_home(profile), "config.yaml")


def env_file(hermes=None, profile=None):
    try:
        p = hermes_ctl.env_path(hermes, profile)
        if p:
            return p
    except Exception:  # noqa: BLE001
        pass
    return os.path.join(profile_home(profile), ".env")


def read_env(path):
    out = {}
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip().strip('"').strip("'")
    except OSError:
        pass
    return out


def enabled_platforms(profile=None, hermes=None, env=None):
    """Which customer channels this profile actually has switched on.

    An agent with none of them - the ordinary Telegram-only install - is left completely
    alone, which is the point: this must not cost a process spawn it does not owe.
    """
    env = read_env(env_file(hermes, profile)) if env is None else env
    out = []
    for plat in CUSTOMER_PLATFORMS:
        for key in _ENV_SIGNALS.get(plat, ()):
            val = (env.get(key) or "").strip()
            if val and val.lower() not in _FALSEY:
                out.append(plat)
                break
    return out


# ── reading what is already there ────────────────────────────────────────────
# A tiny indentation reader rather than a YAML parser: this package is stdlib-only by
# design, Hermes writes these blocks itself with two-space indentation and scalar leaves,
# and the only question being asked is "is this key present at all".

_KEY = re.compile(r"^(\s*)([A-Za-z_][A-Za-z0-9_]*)\s*:\s*(.*)$")


def _child_keys(text, path):
    """The keys explicitly written under a dotted path, e.g. display.platforms.whatsapp.

    Returns a set, empty when the path is absent. Only presence matters - the value is
    the owner's business, and reading it would only tempt this module into an opinion.

    Each level's indentation is LEARNED from its first child rather than assumed to be two
    spaces: Hermes writes two, a hand edit may write four, and a reader that guesses wrong
    reports "nothing is set" and then overwrites the very choice it was meant to protect.
    """
    lines = [ln for ln in text.splitlines()
             if ln.strip() and not ln.lstrip().startswith("#")]
    i, parent = 0, -1
    for head in path:
        level, found = None, False
        while i < len(lines):
            m = _KEY.match(lines[i])
            if not m:                     # list items and continuations: not our shape
                i += 1
                continue
            indent, key, rest = len(m.group(1)), m.group(2), m.group(3).strip()
            if indent <= parent:
                return set()              # walked out of the parent block
            if level is None:
                level = indent
            if indent != level:           # deeper, under some sibling we do not want
                i += 1
                continue
            i += 1
            if key != head:
                continue
            if rest:
                return set()              # a scalar where a mapping was expected
            parent, found = indent, True
            break
        if not found:
            return set()
    out, level = set(), None
    while i < len(lines):
        m = _KEY.match(lines[i])
        i += 1
        if not m:
            continue
        indent, key = len(m.group(1)), m.group(2)
        if indent <= parent:
            break
        if level is None:
            level = indent
        if indent == level:
            out.add(key)
    return out


def written(profile=None, hermes=None, path=None):
    """{platform: set(keys explicitly set)} - what the owner or a past run already chose."""
    path = path or config_file(hermes, profile)
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    except OSError:
        return {}
    return {plat: _child_keys(text, ("display", "platforms", plat))
            for plat in CUSTOMER_PLATFORMS}


def plan(profile=None, hermes=None, platforms=None, env=None, path=None):
    """The writes this profile still needs, as [(dotted_key, value)]. Empty means done."""
    plats = platforms if platforms is not None else enabled_platforms(profile, hermes, env)
    if not plats:
        return []
    have = written(profile, hermes, path)
    todo = []
    for plat in plats:
        already = have.get(plat) or set()
        for key, value in QUIET:
            if key not in already:
                todo.append(("display.platforms.%s.%s" % (plat, key), value))
    return todo


def _as_arg(value):
    return "off" if value == "off" else ("true" if value is True else
                                         "false" if value is False else str(value))


def apply(profile=None, hermes=None, log=None, platforms=None):
    """Write the missing keys. Idempotent, and silent when there is nothing to do."""
    todo = plan(profile, hermes, platforms)
    if not todo:
        return {"ok": True, "changed": False, "written": [], "reason": "already-quiet"}
    written_keys, failed = [], []
    for key, value in todo:
        r = hermes_ctl.config_set(key, _as_arg(value), hermes, profile)
        (written_keys if r.get("ok") else failed).append(key)
    if log and written_keys:
        log("display policy: %s - silenced %d setting(s) on customer channels (%s)"
            % (profile or "default", len(written_keys),
               ", ".join(sorted({k.split(".")[2] for k in written_keys}))))
    if log and failed:
        log("display policy: %s - could not set %s" % (profile or "default",
                                                       ", ".join(failed)))
    return {"ok": not failed, "changed": bool(written_keys),
            "written": written_keys, "failed": failed}


def ensure(profile=None, hermes=None, log=None):
    if not hermes_ctl.available(hermes):
        return {"ok": False, "changed": False, "reason": "no-hermes"}
    plats = enabled_platforms(profile, hermes)
    if not plats:
        return {"ok": True, "changed": False, "reason": "no-customer-channel"}
    r = apply(profile, hermes, log=log, platforms=plats)
    r["platforms"] = plats
    return r


def ensure_all(agents=None, hermes=None, log=None):
    """Every agent on the machine, the default one included.

    Existing installs are the reason this exists: an agent set up before today has a
    customer channel running on Hermes' defaults right now.
    """
    out = []
    if not hermes_ctl.available(hermes):
        return out
    profiles = [None] + [a.get("profile") or a.get("slug")
                         for a in (agents or []) if (a.get("profile") or a.get("slug"))]
    seen = set()
    for prof in profiles:
        key = prof or "default"
        if key in seen:
            continue
        seen.add(key)
        try:
            r = ensure(prof, hermes, log=log)
        except Exception as e:  # noqa: BLE001
            r = {"ok": False, "changed": False, "reason": "error", "detail": str(e)}
        r["profile"] = key
        out.append(r)
    return out


def status(profile=None, hermes=None):
    """For the console: which customer channels are on, and whether they are quiet."""
    plats = enabled_platforms(profile, hermes)
    pending = plan(profile, hermes, plats)
    by_plat = {}
    for key, _v in pending:
        by_plat.setdefault(key.split(".")[2], []).append(key.split(".")[3])
    return {
        "ok": True,
        "platforms": plats,
        "quiet": not pending,
        "pending": by_plat,
        "detail": ("Este agente no tiene canales de clientes; no hay nada que silenciar."
                   if not plats else
                   "Los clientes sólo ven la respuesta final." if not pending else
                   "En %s todavía se le puede escapar al cliente lo que está haciendo por "
                   "dentro." % ", ".join(sorted(by_plat))),
    }
