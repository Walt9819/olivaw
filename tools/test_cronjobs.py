r"""Scheduled routines that speak when they have something to say.

Two switches, written onto the job itself because Hermes' own knob (`cron.wrap_response`)
is per-profile and all-or-nothing:

  * `olivaw_clean`          - the body alone, no header and no English footer;
  * `olivaw_only_on_error`  - silence on a run that went fine.

What is actually worth testing here is not "does a checkbox save". It is:

  * **the patch cannot half-apply.** `cron/scheduler.py` runs every scheduled job on the
    machine and is imported by the gateway at boot, so a block landing one indent out is
    not a feature that quietly fails - it is an agent that does not start. Every write is
    compiled before it is written, removal is byte-exact, and a Hermes release that moves
    the anchors must make us refuse rather than guess.

  * **the injected code says what the docstring says.** The blocks are lifted back OUT of
    the patched file and executed, so these tests exercise the lines that ship, not a
    paraphrase of them. A failed run keeps its header even when the job is marked clean -
    that is the rule that makes "quiet" safe, and it has a test of its own.

  * **a flag survives Hermes.** `cron pause`, `cron update` and `hermes update` all
    round-trip jobs.json. A switch that evaporated on the next edit would look exactly
    like a switch that never worked.

  * **we never damage someone else's file.** jobs.json belongs to Hermes. Writing it means
    preserving every key we do not understand, every other job, and the lock Hermes takes
    around its own writes.

Run: python tools/test_cronjobs.py
"""

import io
import json
import os
import re
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SRC = os.path.join(ROOT, "src")
sys.path.insert(0, SRC)

from wizard import cron_patch  # noqa: E402
from wizard import cronjobs  # noqa: E402

FAILED = []
CHECKS = [0]


def ok(cond, label):
    CHECKS[0] += 1
    if not cond:
        FAILED.append(label)
        print("FAIL " + label)


def eq(got, want, label):
    ok(got == want, "%s (got %r, want %r)" % (label, got, want))


# ── a Hermes home we own ─────────────────────────────────────────────────────
def _home():
    """A throwaway HERMES_HOME with a default profile and one extra."""
    d = tempfile.mkdtemp(prefix="olivaw-cron-")
    os.environ["HERMES_HOME"] = d
    for sub in (os.path.join(d, "cron"),
                os.path.join(d, "profiles", "baco", "cron")):
        os.makedirs(sub, exist_ok=True)
    return d


def _write_jobs(home, profile, jobs, extra=None):
    path = (os.path.join(home, "cron", "jobs.json") if profile in ("", "default")
            else os.path.join(home, "profiles", profile, "cron", "jobs.json"))
    doc = {"jobs": jobs, "updated_at": "2026-01-01T00:00:00"}
    doc.update(extra or {})
    with io.open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(doc, fh, indent=2, ensure_ascii=False)
    return path


def _read_raw(path):
    with io.open(path, encoding="utf-8-sig") as fh:
        return json.load(fh)


JOB_A = {
    "id": "aa11bb22", "name": "Digest diario 9am", "prompt": "Genera el digest…",
    "schedule": {"kind": "cron", "expr": "0 9 * * 2-6", "display": "0 9 * * 2-6"},
    "schedule_display": "0 9 * * 2-6", "enabled": True, "state": "scheduled",
    "deliver": "origin", "last_status": "ok", "no_agent": False,
    "origin": {"platform": "telegram", "chat_id": "1", "chat_name": "Walt"},
    "repeat": {"times": None, "completed": 57},
}
JOB_B = {
    "id": "cc33dd44", "name": "Health check plataformas", "script": "igalenus_health.py",
    "no_agent": True, "schedule": {"kind": "cron", "expr": "0 2,8,14,20 * * *"},
    "enabled": True, "deliver": "origin", "last_status": "ok",
}


# ── reading ──────────────────────────────────────────────────────────────────
def test_reading_what_is_there():
    home = _home()
    try:
        # No file at all is the ordinary agent, not a broken one. Saying "no pude leer las
        # tareas" about a machine with no routines is a scare with no cause.
        res = cronjobs.list_jobs("default")
        ok(res["ok"], "an agent with no jobs file reads fine")
        eq(res["jobs"], [], "and has no routines")
        eq(cronjobs.list_jobs("nunca-existio")["jobs"], [],
           "so does a profile that does not exist")

        _write_jobs(home, "default", [JOB_A, JOB_B])
        rows = cronjobs.list_jobs("default")["jobs"]
        eq(len(rows), 2, "both jobs come back")
        eq(rows[0]["name"], "Digest diario 9am", "with their names")
        eq(rows[0]["schedule"], "0 9 * * 2-6", "and their schedule")
        eq(rows[0]["where"], "telegram", "and where they deliver")
        eq(rows[0]["clean"], False, "nothing is clean until asked")
        eq(rows[0]["only_on_error"], False, "nor silent")
        # The whole message of a script job IS its stdout, so it is the one where "only
        # tell me if it broke" changes the most. The console marks them for that reason.
        eq(rows[1]["script"], True, "a no_agent job is marked as a script")
        eq(rows[0]["script"], False, "an agent job is not")

        # schedule_display is what `cron list` prints; jobs written before that field
        # existed only have the raw expression, and a blank schedule column would read as
        # "this never runs".
        _write_jobs(home, "default", [dict(JOB_B)])
        eq(cronjobs.list_jobs("default")["jobs"][0]["schedule"], "0 2,8,14,20 * * *",
           "a job with no schedule_display falls back to the expression")

        # Hermes opens jobs.json with utf-8-sig because Notepad and PowerShell 5.1 leave a
        # BOM. Reading it with plain utf-8 is a crash on a file the owner merely looked at.
        path = _write_jobs(home, "default", [JOB_A])
        raw = io.open(path, encoding="utf-8").read()
        with io.open(path, "w", encoding="utf-8-sig", newline="\n") as fh:
            fh.write(raw)
        eq(len(cronjobs.list_jobs("default")["jobs"]), 1, "a BOM does not hide the jobs")

        # Hermes auto-repairs a bare array, so we must at least read one.
        with io.open(path, "w", encoding="utf-8", newline="\n") as fh:
            json.dump([JOB_A, JOB_B], fh)
        eq(len(cronjobs.list_jobs("default")["jobs"]), 2, "a bare list still reads")

        with io.open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write("{not json")
        res = cronjobs.list_jobs("default")
        ok(not res["ok"], "a corrupt file is reported, not swallowed")
        ok("No pude leer" in res["detail"], "in words the owner can act on")
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_each_agent_has_its_own():
    """Jobs live per profile. Reading the main agent's file for Ábaco would show her
    routines she cannot edit and hide the ones she can."""
    home = _home()
    try:
        _write_jobs(home, "default", [JOB_A])
        _write_jobs(home, "baco", [JOB_B])
        eq([j["name"] for j in cronjobs.list_jobs("default")["jobs"]],
           ["Digest diario 9am"], "the main agent sees its own")
        eq([j["name"] for j in cronjobs.list_jobs("baco")["jobs"]],
           ["Health check plataformas"], "and an extra agent sees its own")
        ok(cronjobs.jobs_file("baco").replace("\\", "/").endswith(
            "profiles/baco/cron/jobs.json"), "an extra agent's file is under profiles/")
        ok(cronjobs.jobs_file("default").replace("\\", "/").endswith("/cron/jobs.json")
           and "profiles" not in cronjobs.jobs_file("default"),
           "the main agent's file is not")
    finally:
        shutil.rmtree(home, ignore_errors=True)


# ── writing ──────────────────────────────────────────────────────────────────
def test_setting_a_switch():
    home = _home()
    try:
        path = _write_jobs(home, "default", [JOB_A, JOB_B], extra={"schema": 3})
        res = cronjobs.set_flags("default", "aa11bb22", clean=True)
        ok(res["ok"], "setting a switch saves")
        eq(res["job"]["clean"], True, "and answers with the job as it now is")

        doc = _read_raw(path)
        eq(doc["jobs"][0]["olivaw_clean"], True, "the flag is on the job")
        ok("olivaw_only_on_error" not in doc["jobs"][0],
           "the other switch is untouched")
        # Everything we do not understand belongs to Hermes and has to come back out
        # exactly as it went in.
        eq(doc["jobs"][0]["repeat"], {"times": None, "completed": 57},
           "Hermes' own fields survive")
        eq(doc["jobs"][0]["prompt"], "Genera el digest…", "accents and all")
        eq(doc["jobs"][1], JOB_B, "the other job is byte-identical")
        eq(doc["schema"], 3, "and so are the top-level keys we did not write")
        ok(doc.get("updated_at") != "2026-01-01T00:00:00",
           "updated_at is refreshed, because Hermes refreshes it too")

        cronjobs.set_flags("default", "aa11bb22", only_on_error=True)
        doc = _read_raw(path)
        eq(doc["jobs"][0]["olivaw_clean"], True, "one switch does not clear the other")
        eq(doc["jobs"][0]["olivaw_only_on_error"], True, "and the second one is set")

        # Off removes the key rather than storing False: a job with no opinion is a job the
        # global cron.wrap_response still decides, which is a different state from "off".
        cronjobs.set_flags("default", "aa11bb22", clean=False)
        doc = _read_raw(path)
        ok("olivaw_clean" not in doc["jobs"][0], "turning it off removes the key")
        eq(doc["jobs"][0]["olivaw_only_on_error"], True, "and leaves the other alone")
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_what_we_refuse_to_write():
    home = _home()
    try:
        path = _write_jobs(home, "default", [JOB_A])
        before = _read_raw(path)

        for args, why in (
            (dict(job_id="", clean=True), "no id"),
            (dict(job_id="aa11bb22"), "nothing to change"),
            (dict(job_id="no-such-job", clean=True), "an id that is not there"),
        ):
            try:
                cronjobs.set_flags("default", **args)
                ok(False, "refuses: %s" % why)
            except ValueError:
                ok(True, "refuses: %s" % why)
        eq(_read_raw(path), before, "and the file is untouched after every refusal")

        # The dangerous version of "unknown id" is the one that appends. A typo would
        # otherwise create a job-shaped row with no schedule that sits in `cron list`
        # forever doing nothing, and that nobody can explain the origin of.
        eq(len(_read_raw(path)["jobs"]), 1, "an unknown id never becomes a new job")
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_a_switch_survives_hermes():
    """`cron pause`, `cron update` and `hermes update` all round-trip jobs.json. Hermes
    hands back the stored dicts untouched and dumps them whole, so an unknown key lives -
    but that is Hermes' behaviour, not ours, so it gets a test that would notice it
    changing."""
    home = _home()
    try:
        path = _write_jobs(home, "default", [JOB_A])
        cronjobs.set_flags("default", "aa11bb22", clean=True, only_on_error=True)

        # Exactly what Hermes does: load the list, edit a field, dump the list.
        doc = _read_raw(path)
        jobs = doc["jobs"]
        jobs[0]["enabled"] = False
        jobs[0]["state"] = "paused"
        with io.open(path, "w", encoding="utf-8", newline="\n") as fh:
            json.dump({"jobs": jobs, "updated_at": "later"}, fh, indent=2)

        row = cronjobs.list_jobs("default")["jobs"][0]
        eq(row["clean"], True, "the switch is still there after a pause")
        eq(row["only_on_error"], True, "and so is the other one")
        eq(row["enabled"], False, "and the pause took")
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_whether_hermes_gets_patched_at_all():
    """`in_use` is what keeps an untouched machine untouched.

    Both directions are load-bearing and point opposite ways: an owner who never opened
    the panel should not have a modified `cron/scheduler.py` for a feature she is not
    using - and once she HAS asked for a routine to be quiet, the supervisor must keep
    re-applying it, or the next `hermes update` turns the switch off without turning the
    checkbox off and the digest arrives wearing its header again with nothing to explain
    it.
    """
    home = _home()
    roster = [{"slug": "default", "profile": "default"},
              {"slug": "baco", "profile": "baco"}]
    try:
        _write_jobs(home, "default", [JOB_A])
        _write_jobs(home, "baco", [JOB_B])
        ok(not cronjobs.in_use(roster), "a machine with no switches is left alone")

        cronjobs.set_flags("default", "aa11bb22", clean=True)
        ok(cronjobs.in_use(roster), "one switch anywhere is enough")

        cronjobs.set_flags("default", "aa11bb22", clean=False)
        ok(not cronjobs.in_use(roster), "and turning it off is enough to stop")

        # An extra agent's file counts too - it is the same scheduler on the same machine.
        cronjobs.set_flags("baco", "cc33dd44", only_on_error=True)
        ok(cronjobs.in_use(roster), "a switch on an extra agent counts")
        ok(not cronjobs.in_use([{"slug": "default", "profile": "default"}]),
           "and is not seen when that agent is not in the roster")

        # A corrupt file is a question for list_jobs, not a reason to decide the feature
        # is unused and let the next update silently undo it.
        with io.open(cronjobs.jobs_file("baco"), "w", encoding="utf-8") as fh:
            fh.write('{"jobs": [ "olivaw_only_on_error" ')
        ok(cronjobs.in_use(roster), "an unparseable file still counts if it mentions one")
        eq(cronjobs.in_use([]), False, "and an empty roster is simply nobody")
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_the_whole_machine():
    home = _home()
    try:
        _write_jobs(home, "default", [JOB_A])
        _write_jobs(home, "baco", [JOB_B])
        st = cronjobs.state(roster=[{"slug": "default", "name": "Principal",
                                     "profile": "default"},
                                    {"slug": "baco", "name": "Ábaco", "profile": "baco"},
                                    {"slug": "solo", "name": "Solo", "profile": "solo"}])
        eq(st["total"], 2, "every agent's routines are counted together")
        eq([a["slug"] for a in st["agents"]], ["default", "baco", "solo"],
           "including the one with none")
        eq(st["agents"][2]["jobs"], [], "which simply has an empty list")
        eq(st["agents"][1]["name"], "Ábaco", "agents keep their display name")
        ok("state" in st["patch"], "the state says whether the switches are wired up")
    finally:
        shutil.rmtree(home, ignore_errors=True)


# ── the patch ────────────────────────────────────────────────────────────────
# A fixture rather than Hermes' own file, so these run on a machine with no Hermes at all.
# The real scheduler is checked separately, below, when it is installed.
FIXTURE = '''"""A stand-in for Hermes' cron scheduler, carrying only the two anchors."""
import logging

logger = logging.getLogger(__name__)


def load_config():
    return {}


def _deliver_result(job, content, adapters=None, loop=None):
    wrap_response = True
    try:
        wrap_response = load_config().get("cron", {}).get("wrap_response", True)
    except Exception:
        pass

    if wrap_response:
        task_name = job.get("name", job["id"])
        job_id = job.get("id", "")
        delivery_content = (
            f"Cronjob Response: {task_name}\\n"
            f"(job_id: {job_id})\\n"
            f"-------------\\n\\n"
            f"{content}"
        )
    else:
        delivery_content = content
    return delivery_content


def run_one_job(job, adapters=None, loop=None, success=True):
    """Nested exactly as Hermes nests it: the delivery decision sits two `try` blocks
    deep, so the anchor's indentation is the real one."""
    delivery_error = None
    try:
        deliver_content = "whatever the agent said" if success else "it broke"
        try:
            should_deliver = bool(deliver_content.strip())

            if should_deliver:
                try:
                    delivery_error = _deliver_result(job, deliver_content, adapters=adapters, loop=loop)
                except Exception as de:
                    delivery_error = str(de)
        finally:
            pass
    except Exception as e:
        return str(e)
    return delivery_error
'''


def _fixture():
    d = tempfile.mkdtemp(prefix="olivaw-sched-")
    os.makedirs(os.path.join(d, "cron"), exist_ok=True)
    p = os.path.join(d, "cron", "scheduler.py")
    with io.open(p, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(FIXTURE)
    os.environ["OLIVAW_CRON_SCHEDULER"] = p
    os.environ["HERMES_HOME"] = d          # keeps the stamp and backup inside the temp dir
    return d, p


def test_applying_and_taking_it_back():
    d, p = _fixture()
    try:
        eq(cron_patch.scheduler_path(), p, "the override names the file we patch")
        eq(cron_patch.status(p)["state"], "absent", "an unpatched file reads as absent")

        res = cron_patch.apply(p)
        ok(res["applied"] and res["changed"], "applying works")
        eq(cron_patch.status(p)["state"], "applied", "and says so")
        eq(cron_patch.status(p)["hunks_present"], 2, "both hunks are in")

        # The check wa_patch.py never needed: a block one indent out is a SyntaxError in
        # the file that runs every scheduled job AND is imported by the gateway at boot.
        src = io.open(p, encoding="utf-8").read()
        try:
            compile(src, p, "exec")
            ok(True, "the patched scheduler still compiles")
        except SyntaxError as e:
            ok(False, "the patched scheduler still compiles (%s)" % e)

        eq(cron_patch.apply(p)["changed"], False, "applying twice changes nothing")
        eq(src.count("# >>> olivaw-cron v1"), 2, "and does not duplicate the blocks")

        back = cron_patch.remove(p)
        ok(back["changed"], "removing works")
        eq(io.open(p, encoding="utf-8").read(), FIXTURE,
           "and leaves the file byte-identical to what Hermes shipped")
        eq(cron_patch.remove(p)["changed"], False, "removing twice is a no-op")
    finally:
        os.environ.pop("OLIVAW_CRON_SCHEDULER", None)
        shutil.rmtree(d, ignore_errors=True)


def test_refusing_rather_than_guessing():
    """A Hermes release that moves this code must stop us, not make us improvise. The
    scheduler is not a file to be half-right about."""
    d, p = _fixture()
    try:
        moved = FIXTURE.replace("    if wrap_response:\n", "    if wrap_response is True:\n")
        with io.open(p, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(moved)
        st = cron_patch.status(p)
        eq(st["state"], "anchors_moved", "a moved anchor is noticed")
        eq(st["missing_anchors"], ["wrap"], "and named")
        res = cron_patch.apply(p)
        ok(not res["applied"] and not res["changed"], "and nothing is written")
        eq(io.open(p, encoding="utf-8").read(), moved, "the file is exactly as it was")

        # Two copies of an anchor is the other way to be wrong: we would patch one of them
        # and have no idea which.
        with io.open(p, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(FIXTURE + "\n\n" + FIXTURE)
        st = cron_patch.status(p)
        eq(st["state"], "anchors_moved", "a duplicated anchor is noticed too")
        ok("wrap" in st["ambiguous_anchors"], "and named as ambiguous")
        ok(not cron_patch.apply(p)["changed"], "and still nothing is written")

        # Git leaves conflict markers when `hermes update` cannot re-apply its own stash.
        with io.open(p, "w", encoding="utf-8", newline="\n") as fh:
            fh.write("<<<<<<< HEAD\n" + FIXTURE)
        eq(cron_patch.status(p)["state"], "conflicted", "conflict markers are noticed")
        ok(not cron_patch.apply(p)["changed"], "and stop us writing")
    finally:
        os.environ.pop("OLIVAW_CRON_SCHEDULER", None)
        shutil.rmtree(d, ignore_errors=True)


def test_a_write_that_would_not_compile_is_not_written():
    """The guard that makes everything else safe. Faked by handing apply() a hunk whose
    body is nonsense - what a future edit to this module would look like if it got the
    indentation or the syntax wrong."""
    d, p = _fixture()
    real = cron_patch.HUNKS
    try:
        cron_patch.HUNKS = (
            (real[0][0], real[0][1], real[0][2], real[0][3], "if True\n    pass\n"),
            real[1],
        )
        res = cron_patch.apply(p)
        ok(not res["applied"] and not res["changed"], "a broken hunk is refused")
        ok("compilado" in (res.get("detail") or ""), "and says why")
        eq(io.open(p, encoding="utf-8").read(), FIXTURE, "the scheduler is untouched")
    finally:
        cron_patch.HUNKS = real
        os.environ.pop("OLIVAW_CRON_SCHEDULER", None)
        shutil.rmtree(d, ignore_errors=True)


# ── what the injected code actually does ─────────────────────────────────────
def _blocks(path):
    """Lift the injected blocks back out of the patched file.

    These tests run the lines that SHIP, not a paraphrase of them - an edit to the strings
    in cron_patch.py that broke the rule would otherwise pass a test written against the
    rule's description.
    """
    text = io.open(path, encoding="utf-8").read()
    found = re.findall(r"# >>> olivaw-cron v\d+\n(.*?)\s*# <<< olivaw-cron",
                       text, re.S)
    out = []
    for b in found:
        lines = [ln for ln in b.split("\n")]
        pad = min((len(ln) - len(ln.lstrip()) for ln in lines if ln.strip()), default=0)
        out.append("\n".join(ln[pad:] for ln in lines))
    return out


class _Log(object):
    def info(self, *a):
        pass


def test_the_clean_switch_in_the_shipped_code():
    d, p = _fixture()
    try:
        cron_patch.apply(p)
        wrap_block = _blocks(p)[0]

        def decide(job, start=True):
            ns = {"job": job, "wrap_response": start}
            exec(wrap_block, ns)  # noqa: S102 - the point is to run the shipped lines
            return ns["wrap_response"]

        eq(decide({"id": "x"}), True,
           "a job with no opinion keeps the global setting (on)")
        eq(decide({"id": "x"}, start=False), False,
           "and keeps it when the global setting is off")
        eq(decide({"id": "x", "olivaw_clean": True}), False,
           "a clean job drops the header")
        eq(decide({"id": "x", "olivaw_clean": False}), True,
           "and one explicitly not clean keeps it even if the profile went quiet")
        eq(decide({"id": "x", "olivaw_clean": False}, start=False), True,
           "- that is the whole point of a per-job switch")
    finally:
        os.environ.pop("OLIVAW_CRON_SCHEDULER", None)
        shutil.rmtree(d, ignore_errors=True)


def test_the_silence_switch_in_the_shipped_code():
    d, p = _fixture()
    try:
        cron_patch.apply(p)
        quiet_block = _blocks(p)[1]

        def decide(job, success=True, should_deliver=True):
            ns = {"job": job, "success": success, "should_deliver": should_deliver,
                  "logger": _Log(), "dict": dict}
            exec(quiet_block, ns)  # noqa: S102
            return ns["should_deliver"], ns["job"]

        sent, _ = decide({"id": "x"})
        eq(sent, True, "an ordinary job still delivers")

        sent, _ = decide({"id": "x", "olivaw_only_on_error": True})
        eq(sent, False, "a silent job says nothing when the run went fine")

        sent, _ = decide({"id": "x", "olivaw_only_on_error": True}, success=False)
        eq(sent, True, "but it does speak when the run failed")

        # The rule that makes "clean" safe to leave on: when the message IS the error,
        # "which job was this" is the most useful line in it.
        sent, job = decide({"id": "x", "olivaw_clean": True}, success=False)
        eq(sent, True, "a failed run delivers")
        eq(job.get("olivaw_clean"), False, "and gets its header back even when marked clean")

        _sent, job = decide({"id": "x", "olivaw_clean": True})
        eq(job.get("olivaw_clean"), True, "a successful run stays clean")

        # Suppression must not resurrect a delivery something else already cancelled -
        # [SILENT] and an empty response both arrive here as should_deliver=False.
        sent, _ = decide({"id": "x"}, should_deliver=False)
        eq(sent, False, "and a delivery already cancelled upstream stays cancelled")
    finally:
        os.environ.pop("OLIVAW_CRON_SCHEDULER", None)
        shutil.rmtree(d, ignore_errors=True)


def test_the_two_switches_end_to_end_in_the_fixture():
    """Run the fixture's own `_deliver_result` after patching: the header really does
    disappear from the delivered text, and really does come back."""
    d, p = _fixture()
    try:
        cron_patch.apply(p)
        ns = {"__name__": "sched_fixture"}
        exec(compile(io.open(p, encoding="utf-8").read(), p, "exec"), ns)  # noqa: S102
        deliver = ns["_deliver_result"]

        noisy = deliver({"id": "aa11", "name": "Digest"}, "el cuerpo del mensaje")
        ok(noisy.startswith("Cronjob Response: Digest"), "by default the header is there")
        ok("(job_id: aa11)" in noisy, "with the job id")

        clean = deliver({"id": "aa11", "name": "Digest", "olivaw_clean": True},
                        "el cuerpo del mensaje")
        eq(clean, "el cuerpo del mensaje", "a clean job delivers the body and nothing else")
    finally:
        os.environ.pop("OLIVAW_CRON_SCHEDULER", None)
        shutil.rmtree(d, ignore_errors=True)


# ── the real thing, when it is installed ─────────────────────────────────────
def test_the_anchors_still_match_the_installed_hermes():
    """The fixture proves the mechanism; only Hermes' own file proves the anchors. Skipped
    on a machine with no Hermes, which is most CI."""
    os.environ.pop("OLIVAW_CRON_SCHEDULER", None)
    os.environ.pop("HERMES_HOME", None)
    path = cron_patch.scheduler_path()
    if not path:
        print("   (no Hermes installed here - anchor check skipped)")
        return
    text = io.open(path, encoding="utf-8", errors="replace").read().replace("\r\n", "\n")
    clean = cron_patch.strip(text)
    for name, anchor, _w, _i, _c in cron_patch.HUNKS:
        eq(clean.count(anchor), 1, "anchor '%s' matches the installed scheduler once" % name)


def main():
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            print("-- " + name)
            try:
                fn()
            except Exception as e:  # noqa: BLE001
                CHECKS[0] += 1
                FAILED.append("%s raised %s: %s" % (name, type(e).__name__, e))
                print("FAIL %s raised %s: %s" % (name, type(e).__name__, e))
    print("\n%d passed, %d failed" % (CHECKS[0] - len(FAILED), len(FAILED)))
    for f in FAILED:
        print("  FAILED: " + f)
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
