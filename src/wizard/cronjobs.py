r"""The scheduled routines, as something the owner can actually tune.

Hermes schedules jobs beautifully and tells you about them badly. Every run arrives on
Telegram under three lines of bookkeeping and above a sentence in English, and a watchdog
that polls six platforms every six hours reports "all fine" four times a day - which is
the one message nobody needs and the reason the one that matters gets scrolled past.

Hermes' only knob is `cron.wrap_response`, per profile, all-or-nothing. So the decision
moves onto the job, where it belongs:

  * **olivaw_clean** - deliver the body alone. No `Cronjob Response:` header, no job_id,
    no English footer.
  * **olivaw_only_on_error** - say nothing when the run went fine.

`cron_patch.py` is what makes the scheduler read them; this module is what reads and
writes them, and what the console lists.

WHY THIS TALKS TO jobs.json DIRECTLY
------------------------------------
`selfcare.py` shells out to `hermes cron list` and parses the printed boxes, which is fine
for "is my routine installed" and useless here: the printed form has no delivery target,
no last status and - obviously - no place to put a flag back. The file is the truth, it is
plain JSON, and Hermes itself tolerates hand edits (`load_jobs` even auto-repairs a bare
list). So we read and write it, under Hermes' own advisory lock, atomically.

Unknown keys survive the round trip: `load_jobs()` returns the stored dicts untouched,
`_normalize_job_record()` only fills blanks, `save_jobs()` dumps the whole dict. A flag set
here outlives `cron pause`, `cron update` and a Hermes upgrade.

WHAT THIS DELIBERATELY DOES NOT DO
----------------------------------
It does not create, edit, pause or delete jobs. Those belong to the agent and to
`hermes cron`, which has the scheduling grammar, the injection scanner and the one-shot
dispatch claim. This module only answers "which routines exist, and how loudly does each
one speak".
"""

import io
import json
import os
import time

# One agent's jobs file can be large-ish (prompts are stored inline); none of this is on a
# hot path, so it is read on demand and never cached.

_LOCK_NAME = ".jobs.lock"
_LOCK_WAIT = 5.0          # seconds. Hermes waits 30; the console must answer a click.


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


def cron_dir(profile=None):
    return os.path.join(profile_home(profile), "cron")


def jobs_file(profile=None):
    return os.path.join(cron_dir(profile), "jobs.json")


# ── the lock Hermes itself takes ─────────────────────────────────────────────
class _Lock(object):
    """Hermes' cross-process cron lock, taken the same way it takes it.

    `cron/jobs.py::_jobs_lock` flocks (or msvcrt-locks) byte 0 of `<cron dir>/.jobs.lock`
    around every load→modify→save. Writing jobs.json without it is how a `cron pause` gets
    clobbered by a concurrent tick - the exact bug Hermes' own changelog describes - so
    this takes it too.

    Failure to lock is NOT failure to write. Hermes degrades to in-process-only locking
    rather than letting a stuck lock freeze the scheduler, and a console that refused to
    save because a lock file could not be opened would be worse than a torn write that
    `_save` makes atomic anyway.
    """

    def __init__(self, profile=None, wait=_LOCK_WAIT):
        self.path = os.path.join(cron_dir(profile), _LOCK_NAME)
        self.wait = wait
        self.fh = None
        self.held = False

    def __enter__(self):
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            self.fh = io.open(self.path, "a+", encoding="utf-8")
        except OSError:
            self.fh = None
            return self
        deadline = time.time() + self.wait
        while True:
            try:
                self._take()
                self.held = True
                break
            except Exception:  # noqa: BLE001
                if time.time() >= deadline:
                    break
                time.sleep(0.1)
        return self

    def _take(self):
        try:
            import fcntl
            fcntl.flock(self.fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return
        except ImportError:
            pass
        import msvcrt
        self.fh.seek(0)
        msvcrt.locking(self.fh.fileno(), msvcrt.LK_NBLCK, 1)

    def __exit__(self, *exc):
        if self.fh is not None:
            if self.held:
                try:
                    try:
                        import fcntl
                        fcntl.flock(self.fh, fcntl.LOCK_UN)
                    except ImportError:
                        import msvcrt
                        self.fh.seek(0)
                        msvcrt.locking(self.fh.fileno(), msvcrt.LK_UNLCK, 1)
                except Exception:  # noqa: BLE001
                    pass
            try:
                self.fh.close()
            except OSError:
                pass
        return False


# ── reading ──────────────────────────────────────────────────────────────────
def _load(profile=None):
    """(whole_document, jobs_list). A missing file is an agent with no routines, not an
    error - most agents have none, and saying "no pude leer" about an empty machine is a
    scare with no cause."""
    path = jobs_file(profile)
    if not os.path.isfile(path):
        return {"jobs": []}, []
    # utf-8-sig for the same reason Hermes uses it: Notepad and PowerShell 5.1 leave a BOM.
    with io.open(path, encoding="utf-8-sig") as fh:
        data = json.load(fh)
    if isinstance(data, list):          # a bare array; Hermes auto-repairs this shape too
        return {"jobs": data}, data
    if not isinstance(data, dict):
        raise ValueError("jobs.json no tiene la forma esperada")
    jobs = data.get("jobs")
    if not isinstance(jobs, list):
        jobs = []
        data["jobs"] = jobs
    return data, jobs


def _sched(job):
    """What to show as the schedule. `schedule_display` is what Hermes prints; the raw
    expression is the fallback for jobs written before that field existed."""
    disp = job.get("schedule_display")
    if isinstance(disp, str) and disp.strip():
        return disp.strip()
    sch = job.get("schedule")
    if isinstance(sch, dict):
        return str(sch.get("display") or sch.get("expr") or sch.get("kind") or "")
    return str(sch or "")


def _short(s, n=160):
    s = " ".join(str(s or "").split())
    return s if len(s) <= n else s[: n - 1] + "…"


def _row(job):
    """One job, as the console needs it. Everything here is read-only except the two
    flags."""
    deliver = job.get("deliver")
    origin = job.get("origin") if isinstance(job.get("origin"), dict) else {}
    return {
        "id": str(job.get("id") or ""),
        "name": str(job.get("name") or job.get("id") or "sin nombre"),
        "schedule": _sched(job),
        "enabled": job.get("enabled", True) is not False,
        "state": str(job.get("state") or ""),
        "last_run": str(job.get("last_run_at") or ""),
        "next_run": str(job.get("next_run_at") or ""),
        "last_status": str(job.get("last_status") or ""),
        "last_error": _short(job.get("last_error"), 200),
        # A script job's whole message IS its stdout - those are the ones where "only tell
        # me if it broke" changes the most, so the console marks them.
        "script": bool(job.get("no_agent")) or bool(job.get("script")),
        "no_agent": bool(job.get("no_agent")),
        "deliver": str(deliver or ""),
        "where": str(origin.get("platform") or ""),
        "clean": bool(job.get("olivaw_clean")),
        "only_on_error": bool(job.get("olivaw_only_on_error")),
        "what": _short(job.get("prompt") or job.get("script") or ""),
    }


def list_jobs(profile=None):
    """Every scheduled job of one profile, newest-looking first is NOT what we want here -
    the owner reads these as a list of her routines, so they stay in the file's own order,
    which is creation order."""
    try:
        _data, jobs = _load(profile)
    except (OSError, ValueError) as e:
        return {"ok": False, "jobs": [],
                "detail": "No pude leer las tareas programadas: %s" % e}
    return {"ok": True, "jobs": [_row(j) for j in jobs if isinstance(j, dict)]}


# ── writing ──────────────────────────────────────────────────────────────────
def _save(data, profile=None):
    """Atomic replace, preserving every key Hermes wrote. `updated_at` is refreshed
    because Hermes refreshes it on its own saves and a stale one would be a lie."""
    path = jobs_file(profile)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    body = dict(data)
    body["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    tmp = path + ".olivaw.tmp"
    with io.open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(body, fh, indent=2, ensure_ascii=False)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


_FLAGS = {"clean": "olivaw_clean", "only_on_error": "olivaw_only_on_error"}


def set_flags(profile=None, job_id="", clean=None, only_on_error=None):
    """Set one job's delivery switches. Returns that job's row, so the page repaints from
    what is now on disk rather than from what it hoped it had written.

    An unknown id is refused rather than appended: a typo that quietly created a job-shaped
    row with no schedule would sit in `cron list` forever doing nothing.
    """
    job_id = str(job_id or "").strip()
    if not job_id:
        raise ValueError("Falta el identificador de la tarea.")
    if clean is None and only_on_error is None:
        raise ValueError("No había nada que cambiar.")

    with _Lock(profile):
        try:
            data, jobs = _load(profile)
        except (OSError, ValueError) as e:
            return {"ok": False, "detail": "No pude leer las tareas programadas: %s" % e}
        found = None
        for j in jobs:
            if isinstance(j, dict) and str(j.get("id") or "") == job_id:
                found = j
                break
        if found is None:
            raise ValueError("No encontré la tarea «%s» en este agente." % job_id)
        for key, stored in _FLAGS.items():
            val = {"clean": clean, "only_on_error": only_on_error}[key]
            if val is None:
                continue
            if val:
                found[stored] = True
            else:
                # Removed, not set to False: a job with no opinion is a job the global
                # setting still decides, and that is a different state from "off".
                found.pop(stored, None)
        try:
            _save(data, profile)
        except OSError as e:
            return {"ok": False, "detail": "No pude guardar: %s" % e}
    return {"ok": True, "job": _row(found)}


def in_use(roster=None):
    """Does any routine on this machine actually use a switch?

    This is what decides whether Hermes' scheduler gets patched at all. An owner who never
    opens the panel should not have a modified `cron/scheduler.py` on her machine for a
    feature she is not using - `hermes update` has to stash and restore around it every
    time, and that is a cost with no benefit.

    A substring scan, not a JSON parse: the files are read on every supervisor poll, the
    keys are ours and unmistakable, and a jobs.json too corrupt to parse is a question for
    `list_jobs`, not a reason to decide nobody is using the feature.
    """
    for a in (roster or []):
        path = jobs_file(a.get("profile") or a.get("slug"))
        try:
            with io.open(path, encoding="utf-8-sig", errors="replace") as fh:
                body = fh.read()
        except OSError:
            continue
        if any(k in body for k in _FLAGS.values()):
            return True
    return False


# ── the whole machine ────────────────────────────────────────────────────────
def state(roster=None, hermes_exe=None):
    """Every agent's routines, plus whether the switches are actually wired up.

    The patch matters to the owner in exactly one way: without it the two checkboxes are
    saved and ignored. So its state travels with the list instead of hiding in a log.
    """
    from . import cron_patch

    try:
        patch = cron_patch.status(hermes_exe=hermes_exe)
    except Exception as e:  # noqa: BLE001
        patch = {"ok": False, "state": "unknown", "detail": str(e)}

    agents = []
    total = 0
    for a in (roster or []):
        res = list_jobs(a.get("profile") or a.get("slug"))
        rows = res.get("jobs") or []
        total += len(rows)
        agents.append({"slug": a.get("slug"), "name": a.get("name") or a.get("slug"),
                       "profile": a.get("profile") or a.get("slug"),
                       "ok": res.get("ok", False), "detail": res.get("detail", ""),
                       "jobs": rows})
    return {"ok": True, "agents": agents, "total": total,
            "patch": {"state": patch.get("state", ""), "ok": bool(patch.get("ok")),
                      "detail": patch.get("detail", ""),
                      "active": patch.get("state") == "applied"}}
