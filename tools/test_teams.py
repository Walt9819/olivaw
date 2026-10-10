r"""The team map: who may talk to whom, and what happens to machines that had no map.

Most of this file is about one risk. Olivaw is installed on machines whose agents talk to
each other today, and a policy layer is a thing that can silently stop them. So the tests
that matter most are not the ones proving a rule works - they are the ones proving that
the ABSENCE of a rule still means what it meant before:

  * a machine with no teams.json allows exactly what it allowed yesterday;
  * adopting the map changes nothing on the day it is adopted;
  * writing a ROLE on a map-less machine does not quietly cut every link, which is what a
    naive "save the file" would have done - cards and no links reads as "nobody may call
    anybody", and the owner would have typed one sentence and silenced her team.

The other half is the gate itself, including the two places it could be theatre: an agent
claiming to be a colleague to borrow its links, and the check happening after the other
agent has already been spawned.

Run: python tools/test_teams.py
"""

import io
import json
import os
import shutil
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SRC = os.path.join(ROOT, "src")
sys.path.insert(0, SRC)

import intercom  # noqa: E402
import teams  # noqa: E402

FAILED = []
CHECKS = [0]


def ok(cond, label):
    CHECKS[0] += 1
    if not cond:
        FAILED.append(label)
        print("FAIL " + label)


def eq(got, want, label):
    ok(got == want, "%s (got %r, want %r)" % (label, got, want))


ROSTER = [{"slug": "default", "name": "Principal", "profile": "default"},
          {"slug": "daneel", "name": "Daneel", "profile": "daneel"},
          {"slug": "heraldo", "name": "HERALDO", "profile": "heraldo"}]

PAIRS = [("default", "daneel"), ("daneel", "default"),
         ("default", "heraldo"), ("heraldo", "default"),
         ("daneel", "heraldo"), ("heraldo", "daneel")]


class Sandbox:
    """A throwaway install dir with three agents on it."""

    def __enter__(self):
        self.dir = tempfile.mkdtemp(prefix="teams-test-")
        self._saved = (teams.INSTALL_DIR, intercom.INSTALL_DIR,
                       intercom.CONFIG_PATH, intercom.THREAD_DIR)
        teams.INSTALL_DIR = self.dir
        intercom.INSTALL_DIR = self.dir
        intercom.CONFIG_PATH = os.path.join(self.dir, "intercom.json")
        intercom.THREAD_DIR = os.path.join(self.dir, "intercom")
        self._home = os.environ.pop("HERMES_HOME", None)
        with io.open(os.path.join(self.dir, "agents.json"), "w", encoding="utf-8") as fh:
            json.dump({"agents": [a for a in ROSTER if a["slug"] != "default"]}, fh)
        return self

    def __exit__(self, *a):
        (teams.INSTALL_DIR, intercom.INSTALL_DIR,
         intercom.CONFIG_PATH, intercom.THREAD_DIR) = self._saved
        if self._home is None:
            os.environ.pop("HERMES_HOME", None)
        else:
            os.environ["HERMES_HOME"] = self._home
        shutil.rmtree(self.dir, ignore_errors=True)

    def path(self, *p):
        return os.path.join(self.dir, *p)

    def mirror(self):
        """A copy of the modules under this sandbox, for tests that run the real CLI.

        The tools derive the install directory from their own __file__, which is the right
        thing in production and means a subprocess started from the repo reads the REPO's
        agents.json and would write a teams.json next to the source. Copying the few
        modules in here makes that derivation land in the sandbox instead - without a
        test-only environment override, which on a file that IS the permission gate would
        be a bypass sitting in shipped code.
        """
        src = self.path("src")
        os.makedirs(os.path.join(src, "tools"), exist_ok=True)
        for name in ("intercom.py", "teams.py", "winspawn.py"):
            shutil.copy2(os.path.join(SRC, name), os.path.join(src, name))
        for name in ("team_policy.py", "agent_call.py"):
            shutil.copy2(os.path.join(SRC, "tools", name),
                         os.path.join(src, "tools", name))
        return src

    def allowed(self):
        return set(p for p in PAIRS if teams.allows(p[0], p[1], install_dir=self.dir)[0])


class FakeRun:
    def __init__(self, reply="respuesta"):
        self.reply, self.calls = reply, []

    def __call__(self, cmd, **kw):
        self.calls.append(cmd)
        r = type("P", (), {})()
        r.returncode = 0
        r.stdout = self.reply.encode("utf-8")
        r.stderr = b""
        return r


REAL_RUN = intercom.subprocess.run
REAL_BASE = intercom._base


class Fake:
    def __init__(self, reply="respuesta"):
        self.fake = FakeRun(reply)

    def __enter__(self):
        intercom.subprocess.run = self.fake
        intercom._base = lambda profile: ["HERMES.EXE"] + (
            [] if profile in ("default", None) else ["-p", profile])
        return self.fake

    def __exit__(self, *a):
        intercom.subprocess.run = REAL_RUN
        intercom._base = REAL_BASE


# ── the machines that already work ───────────────────────────────────────────
def test_a_machine_with_no_map_behaves_exactly_as_before():
    with Sandbox() as s:
        eq(teams.read(s.dir)[0], teams.LEGACY, "no file means no decision has been made")
        eq(s.allowed(), set(PAIRS), "every pair is still allowed, in both directions")
        ok(not os.path.exists(s.path("teams.json")),
           "and merely asking the question does not create a file")


def test_adopting_the_map_changes_nothing_on_the_day_it_is_adopted():
    with Sandbox() as s:
        before = s.allowed()
        res = teams.adopt(ROSTER, s.dir)
        ok(res["ok"], "adopt succeeded")
        eq(teams.read(s.dir)[0], teams.OK, "the machine is now configured")
        eq(s.allowed(), before, "and every single pair still answers the same way")
        eq(res["links"], 3, "three agents produce three two-way links, not six")


def test_writing_a_role_on_a_mapless_machine_does_not_cut_every_link():
    """The breakage this file exists for.

    Saving a teams.json that holds cards and no links ends legacy mode and reads as "nobody
    may call anybody". The owner would have typed one sentence describing an agent and
    silenced her whole team, with nothing announcing it.
    """
    with Sandbox() as s:
        before = s.allowed()
        res = teams.set_card("daneel", role="Atiende la clinica", roster=ROSTER,
                             install_dir=s.dir)
        ok(res["ok"], "the card was written")
        ok(res["adopted"], "and it says it had to adopt the map to do it")
        eq(s.allowed(), before, "every link the machine had still works")
        st = teams.state(ROSTER, s.dir)
        eq(st["agents"][1]["role"], "Atiende la clinica", "the role is actually stored")


def test_a_card_without_a_roster_refuses_rather_than_guessing():
    with Sandbox() as s:
        try:
            teams.set_card("daneel", role="x", install_dir=s.dir)
            ok(False, "it refused to write a map it cannot reconstruct")
        except ValueError:
            ok(True, "it refused to write a map it cannot reconstruct")
        eq(s.allowed(), set(PAIRS), "and left the machine alone")


# ── the gate ─────────────────────────────────────────────────────────────────
def test_pruning_one_link_cuts_only_that_one():
    with Sandbox() as s:
        teams.adopt(ROSTER, s.dir)
        teams.remove_link("daneel", "heraldo", install_dir=s.dir)
        allowed = s.allowed()
        ok(("daneel", "heraldo") not in allowed, "the pruned direction is refused")
        ok(("heraldo", "daneel") not in allowed, "and so is the way back")
        ok(("default", "daneel") in allowed, "an untouched pair is untouched")
        ok(("default", "heraldo") in allowed, "and so is the other one")


def test_a_one_way_link_is_one_way():
    with Sandbox() as s:
        teams.adopt(ROSTER, s.dir)
        teams.remove_link("daneel", "heraldo", install_dir=s.dir)
        teams.set_link("daneel", "heraldo", why="precios", both=False, install_dir=s.dir)
        ok(teams.allows("daneel", "heraldo", install_dir=s.dir)[0], "the arrow works")
        ok(not teams.allows("heraldo", "daneel", install_dir=s.dir)[0],
           "and the other direction stays shut")


def test_the_arrow_can_be_turned_round():
    with Sandbox() as s:
        teams.adopt(ROSTER, s.dir)
        teams.set_link("daneel", "heraldo", both=False, install_dir=s.dir)
        teams.set_link("heraldo", "daneel", both=False, direction=True, install_dir=s.dir)
        ok(teams.allows("heraldo", "daneel", install_dir=s.dir)[0], "the new way is open")
        ok(not teams.allows("daneel", "heraldo", install_dir=s.dir)[0],
           "and the old way is shut - without having to delete and recreate it")


def test_a_paused_link_refuses_and_says_so():
    with Sandbox() as s:
        teams.adopt(ROSTER, s.dir)
        teams.set_link("daneel", "heraldo", enabled=False, install_dir=s.dir)
        allowed, why = teams.allows("daneel", "heraldo", install_dir=s.dir)
        ok(not allowed, "a paused link does not carry traffic")
        ok("pausa" in why, "and the reason says it is paused, not that it does not exist")


def test_an_hours_window_opens_and_closes():
    with Sandbox() as s:
        teams.adopt(ROSTER, s.dir)
        teams.set_link("daneel", "heraldo", hours={"from": 9, "to": 18}, install_dir=s.dir)
        noon = time.mktime((2026, 10, 3, 12, 0, 0, 0, 0, -1))
        night = time.mktime((2026, 10, 3, 3, 0, 0, 0, 0, -1))
        ok(teams.allows("daneel", "heraldo", install_dir=s.dir, now=noon)[0],
           "inside the window it passes")
        allowed, why = teams.allows("daneel", "heraldo", install_dir=s.dir, now=night)
        ok(not allowed, "outside the window it does not")
        ok("09:00" in why and "18:00" in why,
           "and the refusal names the window, so the agent is never mystified")


def test_a_window_that_crosses_midnight_still_works():
    with Sandbox() as s:
        teams.adopt(ROSTER, s.dir)
        teams.set_link("daneel", "heraldo", hours={"from": 22, "to": 6}, install_dir=s.dir)
        for hour, want in ((23, True), (2, True), (12, False)):
            when = time.mktime((2026, 10, 3, hour, 0, 0, 0, 0, -1))
            eq(teams.allows("daneel", "heraldo", install_dir=s.dir, now=when)[0], want,
               "a night window is open at %02d:00: %s" % (hour, want))


def test_an_unreadable_map_fails_closed_and_keeps_a_backup():
    with Sandbox() as s:
        teams.adopt(ROSTER, s.dir)
        teams.set_link("daneel", "heraldo", why="algo", install_dir=s.dir)
        ok(os.path.exists(s.path("teams.json.bak")), "a save leaves a recoverable copy")
        with io.open(s.path("teams.json"), "w", encoding="utf-8") as fh:
            fh.write("{ not json")
        eq(teams.read(s.dir)[0], teams.BROKEN, "a corrupt file is not an empty one")
        eq(s.allowed(), set(), "nothing is allowed while the rules cannot be read")
        allowed, why = teams.allows("daneel", "heraldo", install_dir=s.dir)
        ok("teams.json" in why, "and the reason names the file to fix")


# ── the gate is not theatre ──────────────────────────────────────────────────
def test_a_refused_call_never_spawns_the_other_agent():
    """A turn of another agent costs 30-120 seconds and real tokens. Checking after the
    subprocess would make the limit a report rather than a limit."""
    with Sandbox() as s:
        teams.adopt(ROSTER, s.dir)
        teams.remove_link("daneel", "heraldo", install_dir=s.dir)
        with Fake() as fake:
            r = intercom.send("heraldo", "hola", sender="daneel", install_dir=s.dir)
        ok(not r["ok"], "the call was refused")
        ok(r.get("blocked"), "and marked as blocked by the map, not as a failure")
        eq(len(fake.calls), 0, "no process was started for a call that was never allowed")


def test_an_agent_cannot_borrow_a_colleagues_links_with_from():
    """--from is typed by the calling agent. Once it decides permissions, trusting it
    would mean any agent could claim to be whichever colleague had the link it wanted."""
    with Sandbox() as s:
        teams.adopt(ROSTER, s.dir)
        teams.remove_link("daneel", "heraldo", install_dir=s.dir)
        os.environ["HERMES_HOME"] = os.path.join(s.dir, "profiles", "daneel")
        try:
            eq(intercom.me("default"), "daneel",
               "the environment decides who you are, not the flag")
            with Fake() as fake:
                r = intercom.send("heraldo", "hola", sender="default", install_dir=s.dir)
            ok(not r["ok"], "claiming to be the main agent does not open daneel's door")
            eq(len(fake.calls), 0, "and nothing was spawned")
        finally:
            os.environ.pop("HERMES_HOME", None)


def test_the_flag_is_still_used_when_the_environment_is_silent():
    with Sandbox():
        os.environ.pop("HERMES_HOME", None)
        eq(intercom.me("daneel"), "daneel",
           "without HERMES_HOME the caller's own claim is all there is")
        eq(intercom.me(""), "default", "and an empty one falls back to the main agent")


def test_the_global_switch_still_beats_the_map():
    with Sandbox() as s:
        teams.adopt(ROSTER, s.dir)
        intercom.save_config({"enabled": False})
        with Fake() as fake:
            r = intercom.send("heraldo", "hola", sender="daneel", install_dir=s.dir)
        ok(not r["ok"], "a link cannot override the owner's off switch")
        eq(len(fake.calls), 0, "and nothing was spawned")


# ── per-link limits ──────────────────────────────────────────────────────────
def test_a_per_link_turn_cap_is_tighter_than_the_global_one():
    with Sandbox() as s:
        teams.adopt(ROSTER, s.dir)
        teams.set_link("daneel", "heraldo", max_turns=2, install_dir=s.dir)
        with Fake():
            a = intercom.send("heraldo", "uno", sender="daneel", install_dir=s.dir)
            b = intercom.send("heraldo", "dos", sender="daneel", thread=a["thread"],
                              install_dir=s.dir)
            c = intercom.send("heraldo", "tres", sender="daneel", thread=a["thread"],
                              install_dir=s.dir)
        eq(a["max_turns"], 2, "the thread reports the link's cap, not the global 8")
        ok(b["ok"], "the second turn is still inside it")
        ok(not c["ok"], "the third is refused")
        ok("2 turnos" in c["detail"], "and the reason quotes the cap that applied")


def test_a_per_link_hourly_cap_bites_without_touching_the_others():
    with Sandbox() as s:
        teams.adopt(ROSTER, s.dir)
        teams.set_link("daneel", "heraldo", hourly_limit=1, install_dir=s.dir)
        with Fake():
            intercom.send("heraldo", "uno", sender="daneel", install_dir=s.dir)
            second = intercom.send("heraldo", "dos", sender="daneel", install_dir=s.dir)
            other = intercom.send("default", "hola", sender="daneel", install_dir=s.dir)
        ok(not second["ok"], "the second call on that link is refused")
        ok("tope de 1" in second["detail"], "and says which cap stopped it")
        ok(other["ok"], "a different link still has its own budget")


def test_the_quota_file_survives_the_shape_it_used_to_have():
    """Every machine running today has a bare JSON array here. Reading one as a dict and
    falling back to empty would hand a rate-limited install a fresh budget."""
    with Sandbox() as s:
        os.makedirs(intercom.THREAD_DIR, exist_ok=True)
        now = time.time()
        with io.open(os.path.join(intercom.THREAD_DIR, "_quota.json"), "w",
                     encoding="utf-8") as fh:
            json.dump([now - 10, now - 20, now - 30], fh)
        eq(intercom.quota()["used"], 3, "the old list is read, not discarded")
        intercom.note_call(frm="daneel", to="heraldo")
        eq(intercom.quota()["used"], 4, "and a new call lands on top of it")
        eq(intercom.quota(frm="daneel", to="heraldo", install_dir=s.dir)["used"], 4,
           "the global count is unaffected by the per-link bucket")


def test_an_expired_stamp_stops_counting():
    with Sandbox():
        os.makedirs(intercom.THREAD_DIR, exist_ok=True)
        now = time.time()
        with io.open(os.path.join(intercom.THREAD_DIR, "_quota.json"), "w",
                     encoding="utf-8") as fh:
            json.dump({"_all": [now - 7200, now - 60]}, fh)
        eq(intercom.quota()["used"], 1, "only the last hour counts")


# ── what the agents are told ─────────────────────────────────────────────────
def test_the_skill_names_only_the_colleagues_it_may_actually_call():
    with Sandbox() as s:
        teams.adopt(ROSTER, s.dir)
        teams.remove_link("daneel", "heraldo", install_dir=s.dir)
        teams.set_link("default", "daneel", why="para cosas de la clinica",
                       install_dir=s.dir)
        teams.set_card("daneel", role="Atiende la clinica", roster=ROSTER,
                       install_dir=s.dir)
        sk = intercom.render_skill("daneel", install_dir=s.dir)
        ok("`heraldo`" not in sk, "a colleague it cannot call is not advertised to it")
        ok("`default`" in sk, "the one it can call is")
        ok("para cosas de la clinica" in sk,
           "and the owner's own sentence travels into the instructions verbatim")
        other = intercom.render_skill("default", install_dir=s.dir)
        ok("Atiende la clinica" in other,
           "the target's role is shown to whoever may write to it")


def test_an_agent_with_no_links_is_told_so_plainly():
    with Sandbox() as s:
        teams.adopt(ROSTER, s.dir)
        teams.remove_link("daneel", "heraldo", install_dir=s.dir)
        teams.remove_link("default", "daneel", install_dir=s.dir)
        sk = intercom.render_skill("daneel", install_dir=s.dir)
        ok("no te ha dado ning" in sk,
           "it says the owner has given it no link, rather than listing nobody")
        ok("--request" in sk or "pedir" in sk.lower(),
           "and tells it what to do about that instead of retrying")


def test_the_skill_is_rewritten_when_the_map_changes():
    with Sandbox() as s:
        teams.adopt(ROSTER, s.dir)
        before = intercom.render_skill("daneel", install_dir=s.dir)
        teams.set_link("daneel", "heraldo", why="una razon nueva", install_dir=s.dir)
        after = intercom.render_skill("daneel", install_dir=s.dir)
        ok(before != after, "a changed link produces different instructions")
        ok("una razon nueva" in after, "carrying the new sentence")


def test_a_broken_map_is_admitted_to_the_agent_not_hidden():
    with Sandbox() as s:
        teams.adopt(ROSTER, s.dir)
        with io.open(s.path("teams.json"), "w", encoding="utf-8") as fh:
            fh.write("[]")
        sk = intercom.render_skill("daneel", install_dir=s.dir)
        ok("no se puede leer" in sk,
           "the agent is told the map is unreadable rather than shown an empty team")


# ── asking for a link ────────────────────────────────────────────────────────
def test_a_request_grants_nothing_until_the_owner_says_yes():
    with Sandbox() as s:
        teams.adopt(ROSTER, s.dir)
        teams.remove_link("daneel", "heraldo", install_dir=s.dir)
        teams.request_link("daneel", "heraldo", "necesito precios", install_dir=s.dir)
        ok(not teams.allows("daneel", "heraldo", install_dir=s.dir)[0],
           "asking is not getting")
        st = teams.state(ROSTER, s.dir)
        eq(len(st["pending"]), 1, "the owner sees one request waiting")
        eq(st["pending"][0]["why"], "necesito precios", "with the reason attached")
        teams.decide_request("daneel", "heraldo", accept=True, install_dir=s.dir)
        ok(teams.allows("daneel", "heraldo", install_dir=s.dir)[0], "yes opens it")
        eq(len(teams.state(ROSTER, s.dir)["pending"]), 0, "and clears the request")


def test_a_rejected_request_opens_nothing_and_disappears():
    with Sandbox() as s:
        teams.adopt(ROSTER, s.dir)
        teams.remove_link("daneel", "heraldo", install_dir=s.dir)
        teams.request_link("daneel", "heraldo", "porfa", install_dir=s.dir)
        teams.decide_request("daneel", "heraldo", accept=False, install_dir=s.dir)
        ok(not teams.allows("daneel", "heraldo", install_dir=s.dir)[0], "still shut")
        eq(len(teams.state(ROSTER, s.dir)["pending"]), 0, "and no longer pending")


def test_asking_twice_does_not_queue_twice():
    with Sandbox() as s:
        teams.adopt(ROSTER, s.dir)
        teams.remove_link("daneel", "heraldo", install_dir=s.dir)
        teams.request_link("daneel", "heraldo", "a", install_dir=s.dir)
        r = teams.request_link("daneel", "heraldo", "b", install_dir=s.dir)
        ok(r.get("already_pending"), "a repeat is recognised")
        eq(len(teams.state(ROSTER, s.dir)["pending"]), 1, "and does not pile up")


# ── odd shapes that must not crash ───────────────────────────────────────────
def test_a_link_to_an_agent_that_is_gone_is_shown_not_fatal():
    with Sandbox() as s:
        teams.adopt(ROSTER, s.dir)
        smaller = [a for a in ROSTER if a["slug"] != "heraldo"]
        st = teams.state(smaller, s.dir)
        stale = [ln for ln in st["links"] if ln["stale"]]
        eq(len(stale), 2, "both links to the departed agent are flagged")
        ok(st["ok"], "and the map still renders")


def test_hand_edited_rubbish_is_dropped_not_obeyed():
    with Sandbox() as s:
        with io.open(s.path("teams.json"), "w", encoding="utf-8") as fh:
            json.dump({"links": [
                {"from": "daneel", "to": "daneel"},            # self
                {"from": "../etc", "to": "daneel"},            # not a slug
                {"from": "daneel", "to": "heraldo", "max_turns": "muchos"},
                {"from": "daneel", "to": "heraldo"},           # duplicate
                "not even a dict",
            ], "cards": {"daneel": {"role": "x" * 500}}}, fh)
        status, data = teams.read(s.dir)
        eq(status, teams.OK, "a readable file with bad rows is still readable")
        eq(len(data["links"]), 1, "only the one usable link survives")
        eq(data["links"][0]["max_turns"], None, "a non-numeric cap becomes no cap")
        eq(len(data["cards"]["daneel"]["role"]), teams.MAX_ROLE, "a long role is clipped")


def test_removing_a_link_that_is_not_there_says_so():
    with Sandbox() as s:
        teams.adopt(ROSTER, s.dir)
        teams.remove_link("daneel", "heraldo", install_dir=s.dir)
        try:
            teams.remove_link("daneel", "heraldo", install_dir=s.dir)
            ok(False, "a second removal is an error, not a silent success")
        except ValueError:
            ok(True, "a second removal is an error, not a silent success")


def test_the_agent_tool_can_narrow_but_only_ask_to_widen():
    import subprocess as sp
    with Sandbox() as s:
        teams.adopt(ROSTER, s.dir)
        teams.remove_link("daneel", "heraldo", install_dir=s.dir)   # so there is one to ask for
        script = os.path.join(s.mirror(), "tools", "team_policy.py")
        env = dict(os.environ, HERMES_HOME=os.path.join(s.dir, "profiles", "daneel"),
                   PYTHONIOENCODING="utf-8")
        env.pop("OLIVAW_CALL_DEPTH", None)

        def run(*args):
            return sp.run([sys.executable, script] + list(args), capture_output=True,
                          encoding="utf-8", errors="replace", timeout=120, env=env,
                          cwd=s.dir)

        r = run("--show")
        eq(r.returncode, 0, "--show works (%s)" % (r.stderr or "")[:120])
        ok("Traceback" not in (r.stderr or ""), "and does not crash")

        r = run("--describe", "heraldo", "--role", "x")
        eq(r.returncode, 3, "an agent cannot write someone else's card")

        r = run("--link", "heraldo", "--why", "para precios")
        eq(r.returncode, 0, "asking for a link is accepted")
        ok("NO" in (r.stdout or ""), "and plainly says it grants nothing")


# ── two lanes, two instructions ──────────────────────────────────────────────
# The bug these are about was live on the owner's own machine: `baco <-> forja` carried
# one sentence, "cuestiones de código y detalles técnicos". Correct for the accountant
# writing to the developer. The developer read the same line as its reason to write to the
# accountant, so the only instruction it had was to ask the bookkeeper about code.
def test_each_direction_of_a_two_way_link_carries_its_own_instruction():
    with Sandbox() as s:
        teams.adopt(ROSTER, s.dir)
        teams.set_link("daneel", "heraldo", both=True,
                       why="para pedirle los numeros del mes",
                       why_back="para avisarle de una cita urgente", install_dir=s.dir)
        out = {a["slug"]: a["why"]
               for a in teams.neighbours("daneel", ROSTER, install_dir=s.dir)}
        back = {a["slug"]: a["why"]
                for a in teams.neighbours("heraldo", ROSTER, install_dir=s.dir)}
        eq(out["heraldo"], "para pedirle los numeros del mes",
           "the agent that starts the link reads the outbound sentence")
        eq(back["daneel"], "para avisarle de una cita urgente",
           "and the one at the other end reads the one written for IT")
        sk_d = intercom.render_skill("daneel", install_dir=s.dir)
        sk_h = intercom.render_skill("heraldo", install_dir=s.dir)
        ok("para pedirle los numeros del mes" in sk_d,
           "the outbound sentence reaches the right skill")
        ok("para pedirle los numeros del mes" not in sk_h,
           "and does NOT reach the other one - this is the whole bug")
        ok("para avisarle de una cita urgente" in sk_h,
           "the return sentence reaches the agent it was written for")


def test_a_link_written_before_why_back_existed_still_works():
    with Sandbox() as s:
        teams.adopt(ROSTER, s.dir)
        # Exactly the shape every teams.json on disk has today.
        teams.set_link("daneel", "heraldo", both=True, why="coordinacion general",
                       install_dir=s.dir)
        back = {a["slug"]: a["why"]
                for a in teams.neighbours("heraldo", ROSTER, install_dir=s.dir)}
        eq(back["daneel"], "coordinacion general",
           "the return direction falls back to the only sentence there is")
        st = teams.state(ROSTER, s.dir)
        ln = [x for x in st["links"] if {x["from"], x["to"]} == {"daneel", "heraldo"}][0]
        ok(ln["shared_why"], "but the console is told the two ends share one sentence")
        ok(["daneel", "heraldo"] in st["shared_why"] or
           ["heraldo", "daneel"] in st["shared_why"], "and it is listed for the banner")


def test_the_sentence_is_chosen_by_who_reads_it_not_by_the_arrow():
    one = {"from": "daneel", "to": "heraldo", "both": False,
           "why": "ida", "why_back": "vuelta"}
    two = dict(one, both=True)
    eq(teams.why_for(one, "daneel"), "ida", "the agent that may write reads the outbound")
    eq(teams.why_for(one, "heraldo"), "ida",
       "a one-way link has ONE instruction, and it is not the return one")
    eq(teams.why_for(two, "daneel"), "ida", "on a two-way link each end reads its own")
    eq(teams.why_for(two, "heraldo"), "vuelta", "including the far end")
    eq(teams.why_for(None, "daneel"), "", "and no link is no instruction")


def test_a_one_way_link_ignores_the_return_sentence():
    with Sandbox() as s:
        teams.adopt(ROSTER, s.dir)
        teams.set_link("daneel", "heraldo", both=False, why="ida",
                       why_back="vuelta", install_dir=s.dir)
        back = [a["slug"] for a in teams.neighbours("heraldo", ROSTER, install_dir=s.dir)]
        ok("daneel" not in back,
           "a sentence for a closed direction does not open it")


def test_naming_the_pair_backwards_does_not_swap_the_instructions():
    """The console, the per-agent panel and a proposal can each name the same pair in a
    different order. The stored link keeps whichever order it was created in, so without
    this each one would attach its sentences to the wrong agent."""
    with Sandbox() as s:
        teams.adopt(ROSTER, s.dir)
        teams.set_link("daneel", "heraldo", both=True, why="D hacia H",
                       why_back="H hacia D", install_dir=s.dir)
        # Same pair, named the other way round, editing only the return direction.
        teams.set_link("heraldo", "daneel", why="H hacia D, corregido",
                       install_dir=s.dir)
        d = {a["slug"]: a["why"]
             for a in teams.neighbours("daneel", ROSTER, install_dir=s.dir)}
        h = {a["slug"]: a["why"]
             for a in teams.neighbours("heraldo", ROSTER, install_dir=s.dir)}
        eq(d["heraldo"], "D hacia H", "the untouched direction is untouched")
        eq(h["daneel"], "H hacia D, corregido",
           "and the edit landed on the direction the caller named")


def test_turning_the_arrow_round_turns_the_sentences_with_it():
    with Sandbox() as s:
        teams.adopt(ROSTER, s.dir)
        teams.set_link("daneel", "heraldo", both=True, why="ida", why_back="vuelta",
                       install_dir=s.dir)
        teams.set_link("heraldo", "daneel", direction=True, install_dir=s.dir)
        _, data = teams.read(s.dir)
        ln = [x for x in data["links"] if {x["from"], x["to"]} == {"daneel", "heraldo"}][0]
        eq(ln["from"], "heraldo", "the arrow was turned round")
        eq(ln["why"], "vuelta", "and the sentence for that direction came with it")
        eq(ln["why_back"], "ida", "as did the other one")


def test_making_a_link_one_way_names_the_direction_the_caller_asked_for():
    """A one-way arrow IS the instruction, so it follows the caller's spelling.

    Olivaw proposes one-way links and nothing makes it name a pair in the order the file
    happens to store it. On this owner's machine it proposed `baco -> analecta` one-way
    over a link stored as `analecta -> baco`. Keeping the stored arrow left analecta able
    to write to baco for no stated reason, baco unable to answer at all, and the proposed
    sentence sitting in a field no agent ever reads - the inverse of what was approved."""
    with Sandbox() as s:
        teams.adopt(ROSTER, s.dir)
        teams.set_link("daneel", "heraldo", both=True, why="D hacia H",
                       why_back="H hacia D", install_dir=s.dir)
        # Same pair, named the other way round, and narrowed to one direction.
        teams.set_link("heraldo", "daneel", both=False, why="solo H hacia D",
                       install_dir=s.dir)
        ok(teams.allows("heraldo", "daneel", install_dir=s.dir)[0],
           "the direction that was asked for is the one that works")
        ok(not teams.allows("daneel", "heraldo", install_dir=s.dir)[0],
           "and the closed one is the other, not the reverse of what was asked")
        h = {a["slug"]: a["why"]
             for a in teams.neighbours("heraldo", ROSTER, install_dir=s.dir)}
        eq(h["daneel"], "solo H hacia D",
           "with the sentence on the direction it was written for")
        d = [a["slug"] for a in teams.neighbours("daneel", ROSTER, install_dir=s.dir)]
        ok("heraldo" not in d,
           "and the agent at the closed end is not told to write")


def test_an_agent_nobody_linked_is_named_as_such():
    with Sandbox() as s:
        teams.adopt(ROSTER, s.dir)
        for other in ("default", "daneel"):
            teams.remove_link("heraldo", other, install_dir=s.dir)
        st = teams.state(ROSTER, s.dir)
        eq(st["isolated"], ["heraldo"],
           "an agent with no link at all is reported, not left to be noticed as silence")
        byslug = {a["slug"]: a for a in st["agents"]}
        ok(byslug["heraldo"]["isolated"], "and flagged on its own row")
        ok(not byslug["daneel"]["isolated"], "while a connected one is not")


def test_being_reachable_counts_as_connected():
    with Sandbox() as s:
        teams.adopt(ROSTER, s.dir)
        for other in ("default", "daneel"):
            teams.remove_link("heraldo", other, install_dir=s.dir)
        teams.set_link("daneel", "heraldo", both=False, install_dir=s.dir)
        st = teams.state(ROSTER, s.dir)
        eq(st["isolated"], [],
           "an agent that can be asked is connected, even if it cannot ask")


def test_a_card_reaches_the_agents_that_may_write_to_it():
    with Sandbox() as s:
        teams.adopt(ROSTER, s.dir)
        teams.set_card("heraldo", role="Publica el boletin",
                       description="Tiene el calendario editorial",
                       never="nada que toque facturacion", roster=ROSTER,
                       install_dir=s.dir)
        sk = intercom.render_skill("daneel", install_dir=s.dir)
        ok("Publica el boletin" in sk, "the role travels into the colleague's skill")
        ok("Tiene el calendario editorial" in sk, "so does what it has to hand")
        ok("nada que toque facturacion" in sk,
           "and what not to ask it, which is the half that prevents the useless call")



# ── named teams: grouping that cannot become a wall ────────────────────────
def test_a_team_never_changes_a_single_permission():
    """The one that matters.

    A grouping feature is the perfect disguise for an accidental narrowing: the owner is
    *organising*, and if the act of filing an agent also cut its links she would have no
    reason to connect the two. So every team operation is run against a machine with a
    real map, and the set of allowed pairs is compared before and after. It must be
    identical every single time.
    """
    with Sandbox() as sb:
        teams.adopt(ROSTER, sb.dir)
        teams.remove_link("daneel", "heraldo", install_dir=sb.dir)
        before = sb.allowed()
        ok(len(before) > 0, "the machine starts with real permissions")

        res = teams.set_team(name="iGalenus", roster=ROSTER, install_dir=sb.dir)
        gid = res["id"]
        eq(sb.allowed(), before, "creating a team changes nothing")

        teams.assign("default", gid, install_dir=sb.dir)
        teams.assign("heraldo", gid, install_dir=sb.dir)
        eq(sb.allowed(), before, "putting two agents in it changes nothing")

        other = teams.set_team(name="Aparte", roster=ROSTER, install_dir=sb.dir)["id"]
        teams.assign("daneel", other, install_dir=sb.dir)
        # daneel -> heraldo is the pair that was REMOVED, and daneel <-> default is one
        # that survives across two different teams. Both have to stay exactly as they are.
        eq(sb.allowed(), before, "and splitting them across two teams changes nothing")
        ok(teams.allows("daneel", "default", install_dir=sb.dir)[0],
           "a link across two teams still works")
        ok(not teams.allows("daneel", "heraldo", install_dir=sb.dir)[0],
           "and one that was cut stays cut")

        teams.set_team(other, name="Otro nombre", roster=ROSTER, install_dir=sb.dir)
        eq(sb.allowed(), before, "renaming changes nothing")

        teams.assign("daneel", "", install_dir=sb.dir)
        eq(sb.allowed(), before, "taking an agent out of every team changes nothing")

        teams.remove_team(gid, install_dir=sb.dir)
        eq(sb.allowed(), before, "and deleting a team changes nothing")

        _st, data = teams.read(sb.dir)
        eq(len(data["links"]), 2, "every link is still on file")


def test_creating_a_team_on_a_mapless_machine_does_not_cut_anything():
    """The same trap `set_card` documents, and the reason both take a roster.

    On a machine with no teams.json everything is allowed. Merely SAVING one ends legacy
    mode, and a file with groups and no links reads as "nobody may call anybody" - so the
    owner would have typed a team name and silenced her whole machine.
    """
    with Sandbox() as sb:
        eq(sb.allowed(), set(PAIRS), "a map-less machine allows everything")
        res = teams.set_team(name="Ventas", roster=ROSTER, install_dir=sb.dir)
        ok(res["ok"], "creating the first team works")
        ok(res["adopted"], "and says it wrote the existing connections down")
        eq(sb.allowed(), set(PAIRS), "everything that worked still works")
        ok(os.path.exists(sb.path("teams.json")), "the machine is now mapped")

    with Sandbox() as sb:
        # Same on the other door into this: assigning before any team exists.
        res = teams.assign("daneel", "", roster=ROSTER, install_dir=sb.dir)
        ok(res["ok"] and res["adopted"], "assigning on a map-less machine adopts first")
        eq(sb.allowed(), set(PAIRS), "and cuts nothing")

    with Sandbox() as sb:
        # Without a roster there is nothing to adopt FROM, so refusing is the only safe
        # answer - writing the file anyway is what would have cut every link.
        try:
            teams.set_team(name="Ventas", install_dir=sb.dir)
            ok(False, "a team write with no roster is refused")
        except ValueError:
            ok(True, "a team write with no roster is refused")
        ok(not os.path.exists(sb.path("teams.json")), "and nothing was written")


def test_an_agent_belongs_to_one_team_at_a_time():
    with Sandbox() as sb:
        teams.adopt(ROSTER, sb.dir)
        a = teams.set_team(name="Uno", roster=ROSTER, install_dir=sb.dir)["id"]
        b = teams.set_team(name="Dos", roster=ROSTER, install_dir=sb.dir)["id"]

        teams.assign("daneel", a, install_dir=sb.dir)
        _st, data = teams.read(sb.dir)
        eq(teams.team_of(data, "daneel")["id"], a, "it is in the first team")

        teams.assign("daneel", b, install_dir=sb.dir)
        _st, data = teams.read(sb.dir)
        eq(teams.team_of(data, "daneel")["id"], b, "moving it puts it in the second")
        groups = {g["id"]: g["members"] for g in data["teams"]}
        eq(groups[a], [], "and takes it out of the first")

        teams.assign("daneel", "", install_dir=sb.dir)
        _st, data = teams.read(sb.dir)
        eq(teams.team_of(data, "daneel"), None, "an empty team means no team")
        eq({g["id"]: g["members"] for g in data["teams"]}[b], [],
           "and it is out of that one too")

        # A hand-edited file can claim an agent twice. Two answers to "which team is it
        # in?" would be two answers everywhere downstream, so the first one wins and the
        # rest is dropped on read.
        with io.open(sb.path("teams.json"), encoding="utf-8") as fh:
            raw = json.load(fh)
        raw["teams"] = [{"id": "x", "name": "X", "members": ["daneel", "heraldo"]},
                        {"id": "y", "name": "Y", "members": ["daneel"]}]
        with io.open(sb.path("teams.json"), "w", encoding="utf-8") as fh:
            json.dump(raw, fh)
        _st, data = teams.read(sb.dir)
        eq(teams.team_of(data, "daneel")["id"], "x", "a doubled agent lands in the first")
        eq({g["id"]: g["members"] for g in data["teams"]}["y"], [],
           "and is not left in the second as well")


def test_deleting_a_team_loses_nothing():
    with Sandbox() as sb:
        teams.adopt(ROSTER, sb.dir)
        gid = teams.set_team(name="Se va", roster=ROSTER, install_dir=sb.dir)["id"]
        teams.assign("daneel", gid, install_dir=sb.dir)
        teams.assign("heraldo", gid, install_dir=sb.dir)
        before = sb.allowed()

        teams.remove_team(gid, install_dir=sb.dir)
        st = teams.state(ROSTER, install_dir=sb.dir)
        eq(st["teams"], [], "the team is gone")
        eq(sorted(st["unassigned"]), ["daneel", "default", "heraldo"],
           "and every one of its agents is simply unfiled")
        eq(len(st["agents"]), 3, "no agent was lost")
        eq(sb.allowed(), before, "and no permission moved")

        try:
            teams.remove_team("no-existe", install_dir=sb.dir)
            ok(False, "deleting a team that is not there is refused")
        except ValueError:
            ok(True, "deleting a team that is not there is refused")


def test_an_agent_in_no_team_is_still_on_the_map():
    """The bug this feature was asked for.

    An agent that existed before teams did has no team by definition. If that took it off
    the diagram, the owner could not connect it to anything - the map would have been
    impossible to draw for exactly the agents that needed it.
    """
    with Sandbox() as sb:
        teams.adopt(ROSTER, sb.dir)
        gid = teams.set_team(name="Nuevos", roster=ROSTER, install_dir=sb.dir)["id"]
        teams.assign("default", gid, install_dir=sb.dir)

        st = teams.state(ROSTER, install_dir=sb.dir)
        eq(len(st["agents"]), 3, "every agent is in the state, team or no team")
        eq(sorted(st["unassigned"]), ["daneel", "heraldo"],
           "the unfiled ones are named as a group of their own")
        by = {a["slug"]: a for a in st["agents"]}
        eq(by["default"]["team_name"], "Nuevos", "a filed agent carries its team")
        eq(by["daneel"]["team"], "", "an unfiled one carries an empty team, not nothing")
        ok("daneel" in [a["slug"] for a in st["agents"]],
           "and is still a row the console can draw and connect")

        # And it really can be connected, which is the half that was impossible.
        teams.remove_link("daneel", "heraldo", install_dir=sb.dir)
        teams.set_link("daneel", "heraldo", why="porque sí", both=True,
                       install_dir=sb.dir)
        ok(teams.allows("daneel", "heraldo", install_dir=sb.dir)[0],
           "an agent with no team can still be given a link")


def test_what_the_console_is_shown():
    with Sandbox() as sb:
        teams.adopt(ROSTER, sb.dir)
        gid = teams.set_team(name="Investigación", roster=ROSTER,
                             install_dir=sb.dir)["id"]
        eq(gid, "investigacion", "the id is readable, with the accents folded out")
        again = teams.set_team(name="Investigación", roster=ROSTER,
                               install_dir=sb.dir)["id"]
        ok(again != gid, "a second team with the same name gets its own id")

        teams.assign("heraldo", gid, install_dir=sb.dir)
        st = teams.state(ROSTER, install_dir=sb.dir)
        g = [x for x in st["teams"] if x["id"] == gid][0]
        eq(g["name"], "Investigación", "the name is the owner's own words")
        eq(g["members"], ["heraldo"], "with its members")
        eq(g["count"], 1, "counted")
        ok(not g["stale"], "and nothing missing")

        # A member list can name an agent that has been deleted since. Drawing a box for
        # nobody is worse than quietly leaving it out - but the console is told, because
        # a team whose roster is half ghosts is something the owner may want to tidy.
        st2 = teams.state([a for a in ROSTER if a["slug"] != "heraldo"],
                          install_dir=sb.dir)
        g2 = [x for x in st2["teams"] if x["id"] == gid][0]
        eq(g2["members"], [], "a departed agent is not drawn")
        ok(g2["stale"], "but the team says it is missing somebody")

        try:
            teams.set_team("no-existe", name="X", roster=ROSTER, install_dir=sb.dir)
            ok(False, "renaming a team that is not there is refused")
        except ValueError:
            ok(True, "renaming a team that is not there is refused")
        try:
            teams.set_team(name="   ", roster=ROSTER, install_dir=sb.dir)
            ok(False, "a team with no name is refused")
        except ValueError:
            ok(True, "a team with no name is refused")


def test_drawing_the_map_keeps_the_filing():
    """`adopt()` rewrites the LINKS. Making it also forget the groups would be a second
    decision nobody asked for."""
    with Sandbox() as sb:
        teams.adopt(ROSTER, sb.dir)
        gid = teams.set_team(name="Queda", roster=ROSTER, install_dir=sb.dir)["id"]
        teams.assign("daneel", gid, install_dir=sb.dir)
        _st, data = teams.read(sb.dir)
        teams.adopt(ROSTER, sb.dir, cards=data.get("cards"), groups=data.get("teams"))
        _st, after = teams.read(sb.dir)
        eq(teams.team_of(after, "daneel")["id"], gid, "the agent is still in its team")


def test_the_skill_says_which_side_of_the_house_a_colleague_is_on():
    with Sandbox() as sb:
        teams.adopt(ROSTER, sb.dir)
        gid = teams.set_team(name="Comercial", roster=ROSTER, install_dir=sb.dir)["id"]
        teams.assign("heraldo", gid, install_dir=sb.dir)
        teams.assign("default", gid, install_dir=sb.dir)

        people = teams.neighbours("default", ROSTER, install_dir=sb.dir)
        by = {p["slug"]: p for p in people}
        eq(by["heraldo"]["team_name"], "Comercial", "a colleague carries its team")
        eq(by["daneel"]["team_name"], "", "one with no team carries none")
        ok("daneel" in by, "and is still listed as callable")

        text = intercom._listing_for("default", install_dir=sb.dir)
        ok("Comercial" in text, "the skill names the team")
        # The sentence that keeps it from being read as a rule. An agent told "your team
        # is X" with nothing else would reasonably infer that X is the limit.
        ok("no un permiso" in text or "no es un permiso" in text,
           "and says plainly that it is not a permission")
        ok("daneel" in text, "a colleague outside the team is still in the list")


def test_how_many_teams_is_too_many():
    with Sandbox() as sb:
        teams.adopt(ROSTER, sb.dir)
        for i in range(teams.MAX_TEAMS):
            teams.set_team(name="Equipo %d" % i, roster=ROSTER, install_dir=sb.dir)
        try:
            teams.set_team(name="Uno de más", roster=ROSTER, install_dir=sb.dir)
            ok(False, "there is a cap on teams")
        except ValueError:
            ok(True, "there is a cap on teams")
        _st, data = teams.read(sb.dir)
        eq(len(data["teams"]), teams.MAX_TEAMS, "and it holds on read too")


def main():
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            print("-- " + name)
            # A test that raises is a failure, not the end of the run. Without this an
            # exception in one test took every later test down with it and the report said
            # nothing about them - which is the worst possible moment to go quiet, because
            # the thing that just broke was a permission gate.
            try:
                fn()
            except Exception as e:  # noqa: BLE001
                CHECKS[0] += 1
                FAILED.append("%s raised %s: %s" % (name, type(e).__name__, e))
                print("FAIL %s raised %s: %s" % (name, type(e).__name__, e))
    print("\n%d checks, %d failed" % (CHECKS[0], len(FAILED)))
    for f in FAILED:
        print("  FAILED: " + f)
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
