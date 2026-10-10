r"""Teach Hermes' cron scheduler two switches that belong to ONE job, not to all of them.

The complaint
-------------
Every scheduled routine arrives on Telegram wearing this::

    Cronjob Response: Recap viernes 5pm
    (job_id: 8c92b0c076a0)
    -------------

    <the actual message>

    To stop or manage this job, send me a new message (e.g. "stop reminder ...").

Three lines of bookkeeping above the thing the owner wanted to read, a hash she has no
use for, and a sentence in English on a product that is otherwise in Spanish. And a
watchdog that checks six platforms every six hours says all of that plus "everything is
fine" four times a day - when the only run worth a notification is the one that is not.

Why a patch and not a setting
-----------------------------
Hermes has exactly one knob here, `cron.wrap_response` in config.yaml, and it is
all-or-nothing per profile. "Quiet for the health check, keep the header on the invoice
reminder" is not expressible, and silence-on-success is not expressible at all. So the
decision has to live on the job, and the only code that reads a job at delivery time is
`cron/scheduler.py`.

Two keys, written into the job record by `cronjobs.py`:

  * ``olivaw_clean``          - deliver the body alone, no header and no footer.
  * ``olivaw_only_on_error``  - a run that went fine says nothing at all.

A FAILED run always gets its header back, whatever `olivaw_clean` says. When the message
IS the error, "which job was this" is the most useful line in it - and a failure that
arrives anonymous is a failure the owner cannot act on.

How it survives
---------------
Same contract as `wa_patch.py`, for the same reason: `hermes update` git-pulls over its
own checkout and takes the patch with it.

  * every hunk sits between `# >>> olivaw-cron vN` and `# <<< olivaw-cron`, so applying is
    idempotent and removing is exact;
  * anchors must match EXACTLY ONCE or nothing is written - a Hermes release that moves
    this code makes us report `anchors_moved` and leave the scheduler alone, because a
    half-patched scheduler is every routine on the machine;
  * `ensure()` costs two stat() calls when nothing moved, and the supervisor runs it after
    every update poll.

Unknown keys on a job survive Hermes' own round-trip: `load_jobs()` hands back the stored
dicts untouched, `_normalize_job_record()` only fills blanks, and `save_jobs()` dumps the
whole dict. So the two switches outlive `cron pause`, `cron update` and a Hermes upgrade -
and when the patch is NOT applied they are simply inert, which is the right failure: the
owner gets the old noisy message, not a broken job.
"""

import io
import json
import os
import re
import shutil
import sys
import time

# runnable directly (`python src/wizard/cron_patch.py status`), so src/ has to be importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

PATCH_VERSION = 1
MARK = "olivaw-cron"
BEGIN = "# >>> %s v%d" % (MARK, PATCH_VERSION)
END = "# <<< %s" % MARK

# Any version's blocks, so an older patch can be lifted out cleanly before the new one
# goes in. DOTALL because the blocks span lines.
_BLOCK_RE = re.compile(
    r"[ \t]*# >>> %s v\d+\n.*?# <<< %s[ \t]*\n" % (re.escape(MARK), re.escape(MARK)),
    re.DOTALL,
)
_ANY_MARK_RE = re.compile(r"# >>> %s v(\d+)" % re.escape(MARK))

# Git leaves these behind when `hermes update` cannot re-apply its own stash cleanly.
_CONFLICT_RE = re.compile(r"^(<{7}|={7}|>{7})", re.M)


# ── the Python we inject ─────────────────────────────────────────────────────

_WRAP = '''
# Olivaw writes this switch onto the job itself (src/wizard/cronjobs.py), because
# cron.wrap_response is per-profile and the owner's answer is per-routine: she wants
# the digest clean and the invoice reminder labelled. None set on the job means the
# global setting decides, exactly as before.
_olivaw_clean = job.get("olivaw_clean")
if _olivaw_clean is not None:
    wrap_response = not _olivaw_clean
'''

_QUIET = '''
# Olivaw's other switch: a watchdog that only speaks when something broke. The run
# still executes and its output is still saved - this suppresses the DELIVERY, the
# same way the [SILENT] marker above does, so `cron list` and the output directory
# are unchanged and the owner can still read what happened.
if success and job.get("olivaw_only_on_error"):
    logger.info("Job '%s': olivaw_only_on_error and the run was clean - not delivering",
                job["id"])
    should_deliver = False
# A failed run keeps its header even when the job is marked clean: when the message IS
# the error, "which job was this" is the most useful line in it.
elif not success and job.get("olivaw_clean"):
    job = dict(job, olivaw_clean=False)
'''


def _block(code, indent=""):
    """Wrap a hunk in its markers so it can be found and removed exactly."""
    body = code.strip("\n")
    lines = [indent + BEGIN]
    lines.extend((indent + ln) if ln.strip() else "" for ln in body.split("\n"))
    lines.append(indent + END)
    return "\n".join(lines) + "\n"


# (name, anchor, where, indent, code) - `where` is "after" or "before" the anchor text.
# Indentation is load-bearing here in a way it was not in bridge.js: a block one level out
# is a SyntaxError in the file that runs every scheduled job on the machine. Both anchors
# carry their own following line so the indent they imply is unambiguous.
HUNKS = (
    (
        "wrap",
        '    if wrap_response:\n        task_name = job.get("name", job["id"])\n',
        "before",
        "    ",
        _WRAP,
    ),
    (
        "deliver",
        "            if should_deliver:\n"
        "                try:\n"
        "                    delivery_error = _deliver_result(job, deliver_content, "
        "adapters=adapters, loop=loop)\n",
        "before",
        "            ",
        _QUIET,
    ),
)


# ── locating the scheduler ───────────────────────────────────────────────────

_REL = os.path.join("cron", "scheduler.py")


def _candidates(hermes_exe=None):
    seen = []

    env = os.environ.get("OLIVAW_CRON_SCHEDULER")
    if env:
        seen.append(env)

    # From the hermes launcher: <root>/venv/Scripts/hermes -> walk up for the checkout.
    exe = hermes_exe
    if not exe:
        exe = shutil.which("hermes") or ""
    if exe:
        d = os.path.dirname(os.path.abspath(exe))
        for _ in range(5):
            seen.append(os.path.join(d, _REL))
            parent = os.path.dirname(d)
            if parent == d:
                break
            d = parent

    home = os.path.expanduser("~")
    for base in (
        os.environ.get("HERMES_HOME", ""),
        os.path.join(home, "AppData", "Local", "hermes", "hermes-agent"),
        os.path.join(home, ".local", "share", "hermes", "hermes-agent"),
        os.path.join(home, "hermes-agent"),
    ):
        if base:
            seen.append(os.path.join(base, _REL))
            seen.append(os.path.join(base, "hermes-agent", _REL))

    out = []
    for p in seen:
        p = os.path.abspath(os.path.expanduser(p))
        if p not in out:
            out.append(p)
    return out


def scheduler_path(hermes_exe=None):
    """Absolute path to the scheduler Hermes actually runs, or "" if it is not installed."""
    for p in _candidates(hermes_exe):
        if os.path.isfile(p):
            return p
    return ""


def _side_dir():
    """Our own bookkeeping, deliberately outside Hermes' git checkout.

    `hermes update` runs `git stash push --include-untracked`, so anything left beside
    scheduler.py gets dragged through a stash/restore cycle for no benefit.
    """
    local = os.environ.get("LOCALAPPDATA", "")
    home = (os.environ.get("HERMES_HOME")
            or (os.path.join(local, "hermes")
                if local and os.path.isdir(os.path.join(local, "hermes"))
                else os.path.join(os.path.expanduser("~"), ".hermes")))
    d = os.path.join(home, "olivaw-cron")
    try:
        os.makedirs(d, exist_ok=True)
    except OSError:
        pass
    return d


def _stamp_path(path=None):
    return os.path.join(_side_dir(), "cron-stamp.json")


def _backup_path(path=None):
    return os.path.join(_side_dir(), "scheduler.py.upstream")


# ── read / apply / remove ────────────────────────────────────────────────────


def _read(path):
    """Return (text_with_LF_endings, original_eol). Anchors are written with LF."""
    with io.open(path, encoding="utf-8", errors="replace", newline="") as fh:
        raw = fh.read()
    eol = "\r\n" if "\r\n" in raw else "\n"
    return raw.replace("\r\n", "\n"), eol


def _write(path, text, eol="\n"):
    if eol != "\n":
        text = text.replace("\n", eol)
    with io.open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)


def strip(text):
    """Remove every olivaw block, any version. Safe on unpatched text."""
    return _BLOCK_RE.sub("", text)


def status(path=None, hermes_exe=None):
    """What state is the installed scheduler in? Never writes."""
    path = path or scheduler_path(hermes_exe)
    if not path or not os.path.isfile(path):
        return {"ok": False, "state": "no_scheduler", "path": path or "",
                "detail": "El programador de tareas de Hermes no está instalado."}

    text, _eol = _read(path)
    if _CONFLICT_RE.search(text):
        return {"ok": False, "state": "conflicted", "path": path,
                "patch_version": PATCH_VERSION,
                "detail": "scheduler.py tiene marcas de conflicto de git: `hermes update` "
                          "no pudo reaplicar su propio stash."}
    found = _ANY_MARK_RE.findall(text)
    versions = sorted({int(v) for v in found})
    clean = strip(text)

    missing = []
    ambiguous = []
    for name, anchor, _where, _indent, _code in HUNKS:
        n = clean.count(anchor)
        if n == 0:
            missing.append(name)
        elif n > 1:
            ambiguous.append(name)

    if versions and versions == [PATCH_VERSION] and len(found) == len(HUNKS):
        state = "applied"
    elif versions:
        state = "stale" if versions != [PATCH_VERSION] else "partial"
    elif missing or ambiguous:
        state = "anchors_moved"
    else:
        state = "absent"

    return {
        "ok": state in ("applied", "absent"),
        "state": state,
        "path": path,
        "patch_version": PATCH_VERSION,
        "found_versions": versions,
        "hunks_present": len(found),
        "hunks_expected": len(HUNKS),
        "missing_anchors": missing,
        "ambiguous_anchors": ambiguous,
    }


def _compiles(text, path):
    """Would Python still load this file? The one check wa_patch.py never needed.

    bridge.js failing to parse stops WhatsApp. scheduler.py failing to parse stops every
    scheduled job on the machine AND is imported by the gateway at boot, so a bad write
    here is not a feature that quietly does not work - it is an agent that does not start.
    """
    try:
        compile(text, path, "exec")
        return True, ""
    except SyntaxError as e:  # noqa: PERF203
        return False, "línea %s: %s" % (e.lineno, e.msg)


def apply(path=None, hermes_exe=None, log=None):
    """Apply the patch. Idempotent; refuses rather than half-patching."""
    def say(m):
        if log:
            log(m)

    path = path or scheduler_path(hermes_exe)
    st = status(path)
    if st["state"] == "no_scheduler":
        return dict(st, applied=False, changed=False)
    if st["state"] == "applied":
        _write_stamp(path)
        return dict(st, applied=True, changed=False,
                    detail="El programador ya respeta los avisos por tarea.")
    if st["state"] in ("conflicted", "anchors_moved"):
        say("cron_patch: %s (missing=%s ambiguous=%s) - not touching scheduler.py"
            % (st["state"], st.get("missing_anchors"), st.get("ambiguous_anchors")))
        return dict(st, applied=False, changed=False,
                    detail="El programador de Hermes cambió; el parche necesita revisión.")

    text, eol = _read(path)
    out = strip(text)  # lift any older/partial version out first
    for name, anchor, where, indent, code in HUNKS:
        if out.count(anchor) != 1:
            return dict(status(path), applied=False, changed=False,
                        detail="Ancla '%s' no encontrada de forma única." % name)
        block = _block(code, indent)
        out = out.replace(
            anchor,
            (anchor + block) if where == "after" else (block + anchor),
            1,
        )

    good, why = _compiles(out, path)
    if not good:
        say("cron_patch: refusing to write - the result would not compile (%s)" % why)
        return dict(status(path), applied=False, changed=False,
                    detail="El parche no habría compilado (%s); no toqué nada." % why)

    backup = _backup_path(path)
    if not os.path.exists(backup):
        shutil.copy2(path, backup)
    _write(path, out, eol)
    _write_stamp(path)
    say("cron_patch: applied v%d to %s" % (PATCH_VERSION, path))
    after = status(path)
    return dict(after, applied=after["state"] == "applied", changed=True,
                detail="El programador ya respeta los avisos por tarea.")


def remove(path=None, hermes_exe=None):
    """Take every block back out. Leaves an unpatched file untouched."""
    path = path or scheduler_path(hermes_exe)
    if not path or not os.path.isfile(path):
        return {"ok": False, "state": "no_scheduler", "changed": False}
    text, eol = _read(path)
    out = strip(text)
    changed = out != text
    if changed:
        good, why = _compiles(out, path)
        if not good:
            return {"ok": False, "state": "broken", "changed": False, "path": path,
                    "detail": "Quitar el parche no habría compilado (%s)." % why}
        _write(path, out, eol)
    try:
        os.remove(_stamp_path(path))
    except OSError:
        pass
    return {"ok": True, "changed": changed, "path": path}


def _write_stamp(path):
    try:
        stt = os.stat(path)
        with io.open(_stamp_path(path), "w", encoding="utf-8") as fh:
            json.dump({"version": PATCH_VERSION, "size": stt.st_size,
                       "mtime": int(stt.st_mtime), "at": int(time.time())}, fh)
    except OSError:
        pass


def _stamp_matches(path):
    try:
        with io.open(_stamp_path(path), encoding="utf-8") as fh:
            s = json.load(fh)
        stt = os.stat(path)
        return (s.get("version") == PATCH_VERSION
                and s.get("size") == stt.st_size
                and s.get("mtime") == int(stt.st_mtime))
    except Exception:  # noqa: BLE001
        return False


def ensure(hermes_exe=None, log=None):
    """Cheap guard for hot paths: re-apply when `hermes update` overwrites the scheduler."""
    path = scheduler_path(hermes_exe)
    if not path:
        return {"ok": False, "state": "no_scheduler", "changed": False}
    if _stamp_matches(path):
        return {"ok": True, "state": "applied", "changed": False, "path": path}
    return apply(path, log=log)


if __name__ == "__main__":  # pragma: no cover - operator entry point
    cmd = (sys.argv[1] if len(sys.argv) > 1 else "status").lower()
    fn = {"status": status, "apply": apply, "remove": remove, "ensure": ensure}.get(cmd)
    if not fn:
        print("usage: cron_patch.py [status|apply|remove|ensure]")
        raise SystemExit(2)
    result = fn(log=lambda m: print(m)) if cmd in ("apply", "ensure") else fn()
    print(json.dumps(result, indent=2, ensure_ascii=False))
    raise SystemExit(0 if result.get("ok") else 1)
