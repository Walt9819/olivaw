r"""Who may talk to an agent from where: this computer, and this phone, separately.

There used to be one switch. The console called it "Háblale desde aquí", and it decided
both whether the console's chat panel worked AND whether the phone could reach that agent
at all - which is two questions wearing one answer. An owner who wanted an agent on the
phone had to turn on a thing labelled "from here", on a computer they were not sitting at,
and an owner who turned the console's chat off silently took the agent off their phone.

So: two flags per agent, kept here, and the gateway underneath is an implementation
detail. The gateway is what actually carries a conversation, so it runs when EITHER flag
is on and stops when both are off. Neither flag is "the real one".

**The migration matters more than the flags.** An install arriving at this file for the
first time has agents whose gateway is already on or off, and nobody has ever answered
these two questions. Reading an absent flag as "no" would have taken every agent off
every phone the moment this shipped - a silent removal of the thing the owner was using.
So an agent with no entry inherits the gateway's state for both, which makes this change
invisible on arrival and meaningful from the first time somebody touches a switch.

Nothing here starts or stops a gateway: it answers what should be true, and `talk.py`
does it. That keeps the decision testable without a machine to run it on.
"""

import json
import os
import threading

CONF = "talk_access.json"
_lock = threading.Lock()

WHERE = ("desktop", "phone")


def _path(install_dir):
    return os.path.join(install_dir or ".", CONF)


def _read(install_dir):
    try:
        with open(_path(install_dir), "r", encoding="utf-8") as fh:
            got = json.load(fh)
        return got if isinstance(got, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def _key(profile):
    return (profile or "default").strip() or "default"


def flags(install_dir, profile, gateway_on):
    """What this agent allows, with the gateway's state standing in where nothing was said.

    `gateway_on` is passed in rather than read here so that this stays a pure decision:
    the caller already knows it, and asking hermes twice for the same answer is how two
    parts of a screen end up disagreeing.
    """
    row = _read(install_dir).get(_key(profile))
    if not isinstance(row, dict):
        return {"desktop": bool(gateway_on), "phone": bool(gateway_on), "decided": False}
    return {"desktop": bool(row.get("desktop", gateway_on)),
            "phone": bool(row.get("phone", gateway_on)),
            "decided": True}


def set_flag(install_dir, profile, where, on, gateway_on):
    """Record one of the two, and say whether the gateway should now be running.

    Returns (flags, want_gateway). The caller turns the gateway on or off to match -
    including turning it ON for a phone, because a flag the transport cannot carry is a
    switch that lies.
    """
    if where not in WHERE:
        raise ValueError("where must be one of %s" % (WHERE,))
    with _lock:
        got = _read(install_dir)
        key = _key(profile)
        row = got.get(key)
        if not isinstance(row, dict):
            # Written out in full the first time, so the pair stops depending on whatever
            # the gateway happened to be doing on the day somebody first touched it.
            row = {"desktop": bool(gateway_on), "phone": bool(gateway_on)}
        row[where] = bool(on)
        got[key] = row
        p = _path(install_dir)
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(got, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, p)
    return ({"desktop": bool(row["desktop"]), "phone": bool(row["phone"]),
             "decided": True},
            bool(row["desktop"] or row["phone"]))


def decided_for_phone(install_dir, profile):
    """What the owner said about this agent and phones, or None if they never said.

    Lets the caller skip asking hermes whether the gateway is on, which is the standing
    cost in front of every phone request and is only ever used as a default here.
    """
    row = _read(install_dir).get(_key(profile))
    if not isinstance(row, dict) or "phone" not in row:
        return None
    return bool(row["phone"])


def allowed_from_phone(install_dir, profile, gateway_on):
    """The one question remote.py asks before letting a phone near an agent."""
    return bool(flags(install_dir, profile, gateway_on)["phone"])
