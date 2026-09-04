r"""What the UI may know about updating, and how it asks for one.

Why this is not just "call apply_update from the wizard"
-------------------------------------------------------
The supervisor owns updating. It holds the bridge handles, so it is the only process that
can stop every agent, swap the shared ``src/`` underneath them and bring them back — and
after an update that touches ``launcher.py`` it has to hand over to the new one. The wizard
is a separate, short-lived process started from a desktop icon; if it swapped ``src/``
itself, the supervisor would go on running deleted code with orphaned children.

So the two talk through small files in the install root, which an update never touches
(only ``src/`` and ``templates/`` are replaced):

  ``supervisor.alive``      rewritten by the supervisor every ~15s — is it even running?
  ``update.state.json``     what the last check found — what the UI shows
  ``update.request``        written here — "the owner pressed the button, go now"
  ``update.result.json``    the outcome of that request

The request controls only WHEN. What gets installed is the pinned repo's latest release,
verified against its published SHA-256 by the supervisor, so a stray request cannot aim the
machine at other code — and anything able to write this file could already write ``src/``
directly, so it grants no authority that was not already there.

The one honest limit: if the supervisor is not running, the request file is read by nobody.
``status()`` reports that as ``supervisor_running: False`` instead of leaving a button that
silently does nothing, and the route that writes a request starts the supervisor first.
"""

import json
import os
import time

from . import agents_registry

# A supervisor loop is ~15s. Three of them missed is a supervisor that is gone, not one
# that was briefly busy applying an update.
ALIVE_WINDOW = 50.0
# The UI is a page someone reloads; GitHub's unauthenticated limit is 60 requests an hour
# per IP, shared with the supervisor's own polling. Cache long enough that clicking around
# cannot exhaust it, short enough that "check again" feels live.
CHECK_TTL = 180.0

STATE_FILE = "update.state.json"
REQUEST_FILE = "update.request"
RESULT_FILE = "update.result.json"
ALIVE_FILE = "supervisor.alive"

_cache = {"at": 0.0, "rel": None, "error": ""}


def install_dir(explicit=None):
    """The machine's install, which is not always where this code lives (see wizard_server)."""
    return explicit or agents_registry.install_root()


def _path(name, explicit=None):
    return os.path.join(install_dir(explicit), name)


def _read_json(path):
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def current_version(explicit=None):
    try:
        with open(_path("VERSION", explicit), encoding="utf-8") as fh:
            return fh.read().strip()
    except OSError:
        return ""


def vtuple(s):
    """Same shape as the launcher's, so the UI and the updater agree on what "newer" means."""
    out = []
    for p in (s or "0").lstrip("v").split(".")[:3]:
        digits = "".join(ch for ch in p if ch.isdigit())
        out.append(int(digits) if digits else 0)
    while len(out) < 3:
        out.append(0)
    return tuple(out)


def supervisor(explicit=None):
    """{running, pid, age, version} for the process that actually applies updates."""
    hb = _read_json(_path(ALIVE_FILE, explicit)) or {}
    ts = hb.get("ts")
    age = (time.time() - float(ts)) if isinstance(ts, (int, float)) else None
    return {
        "running": age is not None and age <= ALIVE_WINDOW,
        # No heartbeat at all also means "an install older than this feature", which the
        # caller has to treat as unknown rather than as dead.
        "known": ts is not None,
        "pid": hb.get("pid"),
        "age": None if age is None else round(age, 1),
        "version": hb.get("version") or "",
    }


def check(force=False):
    """Ask GitHub what the latest release is, through the updater's own code.

    Imported lazily and by name: launcher.py is a script that also runs as one, and the
    point is to use the SAME resolver the supervisor uses rather than a second opinion that
    can disagree with it about which release is current.
    """
    now = time.time()
    if not force and _cache["rel"] is not None and now - _cache["at"] < CHECK_TTL:
        return _cache["rel"], _cache["error"]
    try:
        from launcher import PINNED_REPO, latest_release
        rel = latest_release(PINNED_REPO)
        _cache.update(at=now, rel=rel, error="")
    except Exception as e:  # noqa: BLE001
        _cache.update(at=now, error=str(e)[:200])
        if _cache["rel"] is None:
            return None, _cache["error"]
    return _cache["rel"], _cache["error"]


def _window_text(a, b):
    if a is None or b is None:
        return ""
    if int(a) == int(b):
        return "a cualquier hora"
    return "entre las %02d:00 y las %02d:00" % (int(a), int(b) % 24)


def status(explicit=None, force=False):
    """Everything the panel needs, in one answer."""
    inst = install_dir(explicit)
    cur = current_version(inst)
    st = _read_json(_path(STATE_FILE, inst)) or {}
    sup = supervisor(inst)
    rel, err = check(force=force)
    latest = (rel or {}).get("version") or st.get("latest") or ""
    changelog = (rel or {}).get("changelog") or st.get("changelog") or ""
    available = bool(latest) and bool(cur) and vtuple(latest) > vtuple(cur)
    a = st.get("rest_from")
    b = st.get("rest_until")
    return {
        "ok": True,
        "current": cur,
        "latest": latest,
        "available": available,
        "changelog": changelog[:1200],
        # auto_update lives in updater.config.json, which the supervisor reads; its
        # heartbeat is the only place the UI can see the value the supervisor is ACTING on
        # rather than one it re-read for itself.
        "auto_update": bool(st.get("auto_update", sup.get("auto_update", True))),
        "rest_from": a, "rest_until": b,
        "rest_text": _window_text(a, b),
        "in_rest_window": bool(st.get("in_rest_window")),
        "checked_at": st.get("checked_at"),
        "poll_minutes": st.get("poll_minutes"),
        "deferred": st.get("deferred") or "",
        "supervisor_running": bool(sup["running"]),
        "supervisor_known": bool(sup["known"]),
        "supervisor_age": sup["age"],
        "pending": os.path.isfile(_path(REQUEST_FILE, inst)),
        "result": _read_json(_path(RESULT_FILE, inst)),
        "error": err or st.get("error") or "",
        "install_dir": inst,
    }


def request(explicit=None):
    """Ask the supervisor to update now. Returns {ok, detail}."""
    inst = install_dir(explicit)
    if not current_version(inst):
        return {"ok": False, "detail": "No encuentro la versión instalada en %s." % inst}
    # Clear the previous outcome first: leaving it there makes the panel show a stale
    # "ya estás en la última" next to a request that has not run yet.
    try:
        os.remove(_path(RESULT_FILE, inst))
    except OSError:
        pass
    try:
        with open(_path(REQUEST_FILE, inst), "w", encoding="utf-8") as fh:
            json.dump({"requested_at": time.time(), "by": "ui"}, fh)
    except OSError as e:
        return {"ok": False, "detail": "No pude dejar la petición: %s" % e}
    return {"ok": True, "detail": "Se lo pedí al supervisor; tarda unos segundos."}
