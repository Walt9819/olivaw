r"""The console's team routes, driven the way the page drives them.

teams.py is tested on its own, and the front-end is tested on its own. This is the seam
between them, where a feature is wired up wrong rather than written wrong: a field the page
sends and the server drops, a route that writes when it was supposed to only propose, or -
the one that matters most here - a new agent's connections being created from a payload the
owner never saw.

`asked` is the whole safety story for connecting during setup. An older page, a machine
with one agent, a reconfigure: none of them send it, and none of them may end up with a
teams.json they did not ask for. That is tested here because it cannot be tested anywhere
else: teams.py does not know what a wizard step is, and the page does not know what the
server does with it.

Nothing here is allowed to touch the real machine, so the two side effects that reach
outside the sandbox - rewriting every agent's skill, and queueing a gateway restart - are
replaced before the first route is called.

Run: python tools/test_team_routes.py
"""

import io
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SRC = os.path.join(ROOT, "src")
sys.path.insert(0, SRC)

import intercom  # noqa: E402
import teams  # noqa: E402
import teams_advisor  # noqa: E402
from wizard import wizard_server as WS  # noqa: E402

FAILED = []
CHECKS = [0]


def ok(cond, label):
    CHECKS[0] += 1
    if not cond:
        FAILED.append(label)
        print("FAIL " + label)


def eq(got, want, label):
    ok(got == want, "%s (got %r, want %r)" % (label, got, want))


ROSTER = [{"slug": "daneel", "name": "Daneel", "profile": "daneel"},
          {"slug": "heraldo", "name": "HERALDO", "profile": "heraldo"}]


class Sandbox:
    """A throwaway install dir, with the two escapes to the real machine blocked."""

    def __enter__(self):
        self.dir = tempfile.mkdtemp(prefix="routes-test-")
        self.reloads = []
        self.taught = [0]
        self._saved = (WS.INSTALL_DIR, teams.INSTALL_DIR, intercom.INSTALL_DIR,
                       intercom.CONFIG_PATH, intercom.THREAD_DIR,
                       intercom.ensure_all, WS._queue_gateway_reload)
        WS.INSTALL_DIR = self.dir
        teams.INSTALL_DIR = self.dir
        intercom.INSTALL_DIR = self.dir
        intercom.CONFIG_PATH = os.path.join(self.dir, "intercom.json")
        intercom.THREAD_DIR = os.path.join(self.dir, "intercom")

        def no_skills(*a, **kw):
            self.taught[0] += 1
            return [{"ok": True, "changed": True, "profile": "daneel"}]

        intercom.ensure_all = no_skills
        WS._queue_gateway_reload = lambda prof: self.reloads.append(prof)
        self._home = os.environ.pop("HERMES_HOME", None)
        with io.open(os.path.join(self.dir, "agents.json"), "w", encoding="utf-8") as fh:
            json.dump({"agents": ROSTER}, fh)
        self.h = WS.Handler.__new__(WS.Handler)   # the router, without a socket
        return self

    def __exit__(self, *a):
        (WS.INSTALL_DIR, teams.INSTALL_DIR, intercom.INSTALL_DIR,
         intercom.CONFIG_PATH, intercom.THREAD_DIR,
         intercom.ensure_all, WS._queue_gateway_reload) = self._saved
        if self._home is not None:
            os.environ["HERMES_HOME"] = self._home
        shutil.rmtree(self.dir, ignore_errors=True)

    def post(self, route, body=None):
        return self.h.dispatch("/api/" + route, body or {})

    def links(self):
        return teams.read(self.dir)[1]["links"]

    def link(self, a, b):
        """The stored link for a pair. Not links()[0]: the roster always carries the main
        agent too, so adopt() writes three links for two extra agents and the first one is
        not the one under test."""
        for ln in self.links():
            if {ln["from"], ln["to"]} == {a, b}:
                return ln
        return {}


# ── the link routes carry both instructions ──────────────────────────────────
def test_a_link_saved_from_the_console_keeps_each_direction_apart():
    with Sandbox() as s:
        s.post("teams/adopt")
        st = s.post("teams/link", {"from": "daneel", "to": "heraldo", "both": True,
                                   "why": "ida", "why_back": "vuelta"})
        ok(st["ok"], "the route saves")
        ln = s.link("daneel", "heraldo")
        eq(ln["why"], "ida", "the outbound sentence is stored")
        eq(ln["why_back"], "vuelta", "and the return one, separately")
        ok(s.taught[0] >= 1, "every agent's skill is rewritten in the same request")
        ok(s.reloads, "and the gateways are queued to pick it up")


def test_the_whole_state_comes_back_so_the_page_cannot_go_stale():
    with Sandbox() as s:
        st = s.post("teams/adopt")
        ok("team" in st and "agents" in st["team"], "the answer is the whole state")
        ok(st["team"]["configured"], "and says the map now exists")


def test_a_bad_pair_is_the_owners_mistake_not_an_internal_error():
    with Sandbox() as s:
        s.post("teams/adopt")
        try:
            s.post("teams/link", {"from": "daneel", "to": "daneel"})
            ok(False, "linking an agent to itself is refused in Spanish")
        except ValueError as e:
            ok("consigo mismo" in str(e),
               "linking an agent to itself is refused in Spanish")


# ── the name the other agents use ────────────────────────────────────────────
def test_the_owner_can_say_what_the_others_should_call_an_agent():
    """The main agent calls itself Chalenus in its own memory, while every skill on the
    machine called it "Agente principal" - because its display name only came from a .env
    line nothing in Olivaw could write."""
    with Sandbox() as s:
        st = s.post("teams/name", {"slug": "daneel", "name": "Chalenus"})
        ok(st["ok"], "the route saves")
        names = {a["slug"]: a["name"] for a in intercom.roster(s.dir)}
        eq(names["daneel"], "Chalenus", "and the roster uses it from then on")
        cfg = json.load(io.open(os.path.join(s.dir, "intercom.json"), encoding="utf-8"))
        eq(cfg["names"]["daneel"], "Chalenus", "stored where the roster reads it")
        ok(s.taught[0] >= 1, "and the skills are rewritten, or nobody learns the new name")
        snap = WS.agents_snapshot()
        got = [x["name"] for x in snap["extra"] if x["slug"] == "daneel"]
        eq(got, ["Chalenus"],
           "and the sidebar says the same thing, not two names for one agent")
        s.post("teams/name", {"slug": "daneel", "name": ""})
        names = {a["slug"]: a["name"] for a in intercom.roster(s.dir)}
        eq(names["daneel"], "Daneel", "clearing it goes back to the detected name")


def test_naming_an_agent_that_is_not_here_says_so():
    with Sandbox() as s:
        try:
            s.post("teams/name", {"slug": "fantasma", "name": "X"})
            ok(False, "an unknown slug is refused")
        except ValueError as e:
            ok("fantasma" in str(e), "an unknown slug is refused, by name")


# ── proposing is not applying ────────────────────────────────────────────────
def test_asking_for_a_proposal_writes_nothing_and_passes_the_focus_through():
    with Sandbox() as s:
        s.post("teams/adopt")
        before = io.open(teams.config_path(s.dir), encoding="utf-8").read()
        seen = {}

        def fake(roster=None, install_dir=None, focus=""):
            seen["focus"] = focus
            seen["roster"] = [a["slug"] for a in (roster or [])]
            return {"ok": True, "cards": [], "links": [], "note": ""}

        real = teams_advisor.suggest
        teams_advisor.suggest = fake
        try:
            res = s.post("teams/suggest", {"focus": "heraldo"})
        finally:
            teams_advisor.suggest = real
        ok(res["ok"], "the route answers")
        eq(seen["focus"], "heraldo", "the agent being asked about is passed through")
        ok("daneel" in seen["roster"], "with this machine's roster")
        eq(io.open(teams.config_path(s.dir), encoding="utf-8").read(), before,
           "and the map is untouched - a proposal is not a decision")


def test_applying_a_proposal_writes_only_the_ticked_rows():
    with Sandbox() as s:
        s.post("teams/adopt")
        st = s.post("teams/apply", {
            "links": [{"from": "daneel", "to": "heraldo", "both": True,
                       "why": "ida", "why_back": "vuelta"}],
            "cards": [{"slug": "heraldo", "role": "Publica el boletin"}]})
        ok(st["ok"], "the route applies")
        eq(st["applied"]["links"], 1, "and reports what it did")
        ln = s.link("daneel", "heraldo")
        eq((ln["why"], ln["why_back"]), ("ida", "vuelta"), "both directions were written")
        who = {a["slug"]: a for a in st["team"]["agents"]}
        eq(who["heraldo"]["role"], "Publica el boletin", "the card too")


# ── connecting a new agent while it is created ───────────────────────────────
def test_a_page_that_never_asked_changes_nothing():
    with Sandbox() as s:
        res = s.h._team_connect("daneel", {})
        ok(res["skipped"], "no question, no answer, no write")
        ok(not os.path.exists(teams.config_path(s.dir)),
           "and above all no map appears on a machine that had none")


def test_the_new_agent_gets_exactly_the_colleagues_that_were_ticked():
    with Sandbox() as s:
        teams.adopt([{"slug": "heraldo"}], s.dir)      # a map that predates the new agent
        res = s.h._team_connect("daneel", {"asked": True, "connect": ["heraldo"]})
        ok(res["ok"], "it connects")
        eq(res["linked"], ["heraldo"], "to what was ticked")
        ok(teams.allows("daneel", "heraldo", install_dir=s.dir)[0], "the link works")
        ok(teams.allows("heraldo", "daneel", install_dir=s.dir)[0], "in both directions")


def test_ticking_nobody_leaves_the_new_agent_alone_without_touching_the_others():
    """A legacy machine is the delicate case: the map has to be written down to make
    "alone" mean anything at all, and writing it must not cut anybody else."""
    with Sandbox() as s:
        three = ROSTER + [{"slug": "analecta", "name": "Analecta", "profile": "analecta"}]
        with io.open(os.path.join(s.dir, "agents.json"), "w", encoding="utf-8") as fh:
            json.dump({"agents": three}, fh)
        res = s.h._team_connect("daneel", {"asked": True, "connect": []})
        ok(res["adopted"], "the live mesh is written down")
        ok(teams.allows("heraldo", "analecta", install_dir=s.dir)[0],
           "the agents that already existed keep talking, exactly as before")
        ok(teams.allows("analecta", "heraldo", install_dir=s.dir)[0], "both ways")
        ok(not teams.allows("daneel", "heraldo", install_dir=s.dir)[0],
           "and only the new agent is alone - which is what was asked for")
        st = teams.state(intercom.roster(s.dir), s.dir)
        eq(st["isolated"], ["daneel"], "and it is reported as such, not left silent")


def test_a_connection_to_an_agent_that_does_not_exist_is_ignored():
    with Sandbox() as s:
        res = s.h._team_connect("daneel", {"asked": True,
                                           "connect": ["heraldo", "fantasma"]})
        eq(res["linked"], ["heraldo"], "an unknown slug writes no link")
        ok(all("fantasma" not in (ln["from"], ln["to"]) for ln in s.links()),
           "and never reaches the file the gate reads")


def test_an_unreadable_map_is_not_overwritten_by_a_new_agent():
    with Sandbox() as s:
        with io.open(teams.config_path(s.dir), "w", encoding="utf-8") as fh:
            fh.write("{roto")
        res = s.h._team_connect("daneel", {"asked": True, "connect": ["heraldo"]})
        ok(not res["ok"], "it refuses")
        eq(io.open(teams.config_path(s.dir), encoding="utf-8").read(), "{roto",
           "and leaves the owner's file exactly as it was, to be recovered")


# ── teams are groups, and the reply still carries the whole map ───────────────
def test_every_team_route_answers_with_the_whole_map():
    """The page repaints from the reply, so a route that returns anything less than the
    full state blanks the panel.

    This is the shape of bug the front-end tests cannot see: the harness answers with a
    hand-written state, so a server that overwrote `team` with something else still looked
    right there. It took a browser to find it once; it takes this to find it again.
    """
    with Sandbox() as s:
        s.post("teams/adopt")
        for route, body in (("teams/group", {"name": "iGalenus"}),
                            ("teams/assign", {"slug": "daneel", "team": "igalenus"}),
                            ("teams/group", {"id": "igalenus", "name": "iGalenus Core"}),
                            ("teams/assign", {"slug": "daneel", "team": ""}),
                            ("teams/ungroup", {"id": "igalenus"})):
            st = s.post(route, body)
            ok(st["ok"], "%s succeeds" % route)
            ok(isinstance(st.get("team"), dict),
               "%s answers with the map, not with a field of it" % route)
            eq(len(st["team"].get("agents") or []), len(ROSTER) + 1,
               "%s still names every agent" % route)
            ok("teams" in st["team"] and "unassigned" in st["team"],
               "%s carries the groups too" % route)


def test_grouping_from_the_console_moves_nobody_out_of_reach():
    with Sandbox() as s:
        s.post("teams/adopt")
        before = sorted((ln["from"], ln["to"]) for ln in s.links())

        gid = s.post("teams/group", {"name": "Comercial"})["team_id"]
        ok(gid, "the new team's id comes back under its own key")
        s.post("teams/assign", {"slug": "daneel", "team": gid})
        st = s.post("teams/assign", {"slug": "heraldo", "team": ""})

        eq(sorted((ln["from"], ln["to"]) for ln in s.links()), before,
           "filing agents changed no link at all")
        groups = {g["id"]: g["members"] for g in st["team"]["teams"]}
        eq(groups[gid], ["daneel"], "the filed agent is in its team")
        ok("heraldo" in st["team"]["unassigned"],
           "and the one taken out is in no team, not gone")

        # An agent in no team is still a row the page can draw and connect.
        by = {a["slug"]: a for a in st["team"]["agents"]}
        eq(by["heraldo"]["team"], "", "an unfiled agent carries an empty team")
        ok("heraldo" in by, "and is still on the map")


def test_a_team_write_on_a_mapless_machine_adopts_first():
    with Sandbox() as s:
        # No teams.json at all: everything is allowed, and saving a groups-only file would
        # read as "nobody may call anybody".
        ok(not os.path.exists(os.path.join(s.dir, "teams.json")), "no map to start with")
        st = s.post("teams/group", {"name": "Primero"})
        ok(st["ok"], "the route still works")
        ok(st.get("adopted"), "and says it wrote the existing connections down")
        ok(len(s.links()) > 0, "which is why there are links on file now")
        for a in ("daneel", "heraldo", "default"):
            for b in ("daneel", "heraldo", "default"):
                if a != b:
                    ok(teams.allows(a, b, install_dir=s.dir)[0],
                       "%s can still call %s" % (a, b))


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
    print("\n%d checks, %d failed" % (CHECKS[0], len(FAILED)))
    for f in FAILED:
        print("  FAILED: " + f)
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
