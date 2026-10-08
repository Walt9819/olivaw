r"""Olivaw proposing the team's wiring - and the three ways that could go wrong.

The feature is "ask the main agent to write the org chart". The agent's answer is text
produced by a language model that has just read files, so the interesting tests are not
about whether it writes nice Spanish. They are:

  * **it must not be able to invent a permission.** A proposal naming an agent that does
    not exist, or naming itself, or repeating a pair, is dropped - not created.
  * **it must not be applied by being asked for.** `suggest()` writes nothing. The owner's
    click is a separate call, with a separate payload: whatever she ticked.
  * **the question must actually ask for two sentences**, because one sentence for a
    two-way link is the bug this whole feature exists to fix. A prompt that forgets to say
    so produces a proposal that is wrong in exactly the old way.

And one regression guard with history behind it: the call must name `-p default`. A bare
`hermes` inherits the caller's HERMES_HOME, which is how every agent-to-main-agent message
spent a month being answered by the sender's own brain (v1.0.52).

Run: python tools/test_teams_advisor.py
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
import teams_advisor as adv  # noqa: E402

FAILED = []
CHECKS = [0]


def ok(cond, label):
    CHECKS[0] += 1
    if not cond:
        FAILED.append(label)
        print("FAIL " + label)


def eq(got, want, label):
    ok(got == want, "%s (got %r, want %r)" % (label, got, want))


ROSTER = [{"slug": "default", "name": "Chalenus", "profile": "default"},
          {"slug": "baco", "name": "Abaco", "profile": "baco"},
          {"slug": "forja", "name": "Forja", "profile": "forja"}]

SLUGS = [a["slug"] for a in ROSTER]


class Sandbox:
    def __enter__(self):
        self.dir = tempfile.mkdtemp(prefix="advisor-test-")
        self._saved = (teams.INSTALL_DIR, intercom.INSTALL_DIR, intercom.CONFIG_PATH,
                       intercom.THREAD_DIR)
        teams.INSTALL_DIR = self.dir
        intercom.INSTALL_DIR = self.dir
        intercom.CONFIG_PATH = os.path.join(self.dir, "intercom.json")
        intercom.THREAD_DIR = os.path.join(self.dir, "intercom")
        self._home = os.environ.pop("HERMES_HOME", None)
        self._ws = os.environ.pop("CLAUDE_BRIDGE_WORKSPACE", None)
        # A fake executable, not a fake _base(): the thing under test here is how the
        # command line is BUILT, and stubbing _base would delete the assertion. This only
        # removes the test's dependence on hermes being installed on the test machine.
        self._exe = intercom._hermes_exe
        intercom._hermes_exe = lambda: os.path.join(self.dir, "hermes.exe")
        rows = []
        for a in ROSTER:
            if a["slug"] == "default":
                continue
            ws = os.path.join(self.dir, "ws", a["slug"])
            os.makedirs(ws, exist_ok=True)
            rows.append(dict(a, workspace=ws))
        with io.open(os.path.join(self.dir, "agents.json"), "w", encoding="utf-8") as fh:
            json.dump({"agents": rows}, fh)
        return self

    def __exit__(self, *a):
        (teams.INSTALL_DIR, intercom.INSTALL_DIR, intercom.CONFIG_PATH,
         intercom.THREAD_DIR) = self._saved
        intercom._hermes_exe = self._exe
        for key, val in (("HERMES_HOME", self._home),
                         ("CLAUDE_BRIDGE_WORKSPACE", self._ws)):
            if val is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = val
        shutil.rmtree(self.dir, ignore_errors=True)

    def persona(self, slug, text):
        ws = os.path.join(self.dir, "ws", slug)
        os.makedirs(ws, exist_ok=True)
        with io.open(os.path.join(ws, "CLAUDE.md"), "w", encoding="utf-8") as fh:
            fh.write(u"# Eres el cerebro de X\n\n## Quien eres\n%s\n\n"
                     u"## Regla de dueño (importante)\nSolo Walt manda.\n" % text)


class Replies:
    """Stands in for a real turn of the main agent."""

    def __init__(self, text):
        self.text, self.calls = text, []

    def __call__(self, argv, timeout):
        self.calls.append(argv)
        return 0, self.text, ""


GOOD = json.dumps({
    "cards": {"baco": {"role": "Lleva las cuentas", "description": "Tiene los precios",
                       "never": "nada de codigo"},
              "forja": {"role": "Escribe el codigo"}},
    "links": [{"from": "baco", "to": "forja", "both": True,
               "why": "Para pedirle una herramienta que te ahorre trabajo manual",
               "why_back": "Para avisarle de que algo va a costar dinero"}],
    "note": "Dos enlaces y dos fichas.",
}, ensure_ascii=False)


# ── the question ─────────────────────────────────────────────────────────────
def test_the_prompt_asks_for_an_instruction_per_direction():
    with Sandbox() as s:
        docs = adv.dossier(ROSTER, s.dir)
        p = adv.build_prompt(docs, [])
        ok("why_back" in p, "the return instruction is part of what is asked for")
        ok("sentidos opuestos" in p,
           "and the prompt says the two are opposite directions")
        ok("instrucciones DISTINTAS" in p,
           "in so many words - this is the sentence the whole feature turns on")
        ok("Nunca repitas la misma frase" in p,
           "with the failure mode named, because it is the one that already happened")
        for a in ROSTER:
            ok("`%s`" % a["slug"] in p, "the prompt names %s" % a["slug"])
        ok("No inventes ninguno" in p, "and forbids inventing more")


def test_the_prompt_carries_what_each_agent_says_it_is():
    with Sandbox() as s:
        s.persona("baco", "Llevo la contabilidad de la clinica y vigilo el margen.")
        docs = adv.dossier(ROSTER, s.dir)
        p = adv.build_prompt(docs, [])
        ok("vigilo el margen" in p,
           "an agent's own instructions are what the proposal is built from")
        ok("Regla de due" not in p,
           "but not the boilerplate every agent shares - that is prompt filled with noise")


def test_the_prompt_shows_the_links_that_already_exist():
    with Sandbox() as s:
        teams.adopt(ROSTER, s.dir)
        teams.set_link("baco", "forja", both=True, why="lo que hay hoy", install_dir=s.dir)
        _, data = teams.read(s.dir)
        p = adv.build_prompt(adv.dossier(ROSTER, s.dir), data["links"])
        ok("lo que hay hoy" in p, "so it corrects what is there instead of starting blank")


# ── the answer ───────────────────────────────────────────────────────────────
def test_a_proposal_cannot_invent_an_agent():
    prop = adv.parse(json.dumps({
        "links": [{"from": "baco", "to": "fantasma", "why": "x"},
                  {"from": "intruso", "to": "forja", "why": "x"},
                  {"from": "baco", "to": "forja", "why": "real"}],
        "cards": {"fantasma": {"role": "no existe"}},
    }), SLUGS)
    eq(len(prop["links"]), 1, "links naming an agent that is not here are dropped")
    eq(prop["links"][0]["to"], "forja", "and the real one survives")
    eq(prop["cards"], [], "so is a card for a slug this machine does not have")


def test_a_proposal_cannot_link_an_agent_to_itself_or_twice():
    prop = adv.parse(json.dumps({"links": [
        {"from": "baco", "to": "baco", "why": "x"},
        {"from": "baco", "to": "forja", "why": "uno"},
        {"from": "forja", "to": "baco", "why": "otro"},
    ]}), SLUGS)
    eq(len(prop["links"]), 1, "a self-link is dropped and a pair appears once")
    eq(prop["links"][0]["why"], "uno", "the first one wins")


def test_the_same_sentence_twice_is_not_two_answers():
    prop = adv.parse(json.dumps({"links": [
        {"from": "baco", "to": "forja", "both": True,
         "why": "Para cuestiones de codigo", "why_back": "para cuestiones de CODIGO"},
    ]}), SLUGS)
    eq(prop["links"][0]["why_back"], "",
       "a repeated sentence is stored as the missing answer it is, not as two")


def test_a_one_way_proposal_carries_no_return_sentence():
    prop = adv.parse(json.dumps({"links": [
        {"from": "baco", "to": "forja", "both": False, "why": "ida", "why_back": "vuelta"},
    ]}), SLUGS)
    eq(prop["links"][0]["why_back"], "", "a closed direction gets no instruction")


def test_an_answer_that_is_not_json_is_a_failure_not_a_half_application():
    for text in ("Claro, te propongo lo siguiente: Abaco deberia hablar con Forja.",
                 "", "{roto", "[1,2,3]"):
        prop = adv.parse(text, SLUGS)
        ok(not prop["ok"], "prose is refused, not guessed at (%r)" % text[:24])
        ok(prop.get("detail"), "and says so")


def test_valid_json_with_nothing_usable_in_it_is_still_a_failure():
    """The second guard. The first one catches prose; this one catches a well-formed
    answer that proposes nothing - which must not come back as an empty success the owner
    is invited to apply."""
    for text in ('{"note": "lo he pensado y lo dejaria como esta"}',
                 '{"cards": {}, "links": []}',
                 '{"links": [{"from": "nadie", "to": "tampoco"}]}'):
        prop = adv.parse(text, SLUGS)
        ok(not prop["ok"], "nothing to apply is not a proposal (%s)" % text[:28])


def test_json_inside_a_code_fence_or_a_sentence_is_still_read():
    fenced = "```json\n" + GOOD + "\n```"
    ok(adv.parse(fenced, SLUGS)["ok"], "a fenced answer is read")
    chatty = "Aqui tienes:\n" + GOOD + "\nEspero que sirva."
    ok(adv.parse(chatty, SLUGS)["ok"], "so is one with a sentence around it")


def test_a_wall_of_text_cannot_bloat_the_file_the_gate_reads():
    prop = adv.parse(json.dumps({
        "cards": {"baco": {"role": "r" * 4000, "description": "d" * 4000}},
        "links": [{"from": "baco", "to": "forja", "why": "w" * 4000}],
    }), SLUGS)
    ok(len(prop["cards"][0]["role"]) <= teams.MAX_ROLE, "a role is clipped")
    ok(len(prop["cards"][0]["description"]) <= teams.MAX_DESC, "so is a description")
    ok(len(prop["links"][0]["why"]) <= teams.MAX_WHY, "and so is an instruction")


# ── asking, end to end ───────────────────────────────────────────────────────
def test_asking_names_the_main_agent_explicitly():
    """A bare `hermes` here would run the proposal inside whatever profile the wizard
    happens to be in - the v1.0.52 bug, in a new place."""
    with Sandbox() as s:
        r = Replies(GOOD)
        res = adv.suggest(ROSTER, s.dir, runner=r)
        ok(res["ok"], "the proposal comes back")
        ok(r.calls, "the agent was actually called")
        argv = r.calls[0]
        ok("-p" in argv, "the call names a profile")
        eq(argv[argv.index("-p") + 1], "default", "and it is the main agent")
        ok("-z" in argv, "as a one-shot turn")


def test_asking_for_a_proposal_changes_nothing():
    with Sandbox() as s:
        teams.adopt(ROSTER, s.dir)
        before = io.open(teams.config_path(s.dir), encoding="utf-8").read()
        adv.suggest(ROSTER, s.dir, runner=Replies(GOOD))
        after = io.open(teams.config_path(s.dir), encoding="utf-8").read()
        eq(after, before, "the map is byte-identical after asking")


def test_a_single_agent_machine_is_told_there_is_nothing_to_wire():
    with Sandbox() as s:
        res = adv.suggest([ROSTER[0]], s.dir, runner=Replies(GOOD))
        ok(not res["ok"], "one agent is not a team")
        ok("un agente" in res["detail"], "and the reason says why")


def test_a_broken_answer_comes_back_with_what_was_actually_said():
    with Sandbox() as s:
        res = adv.suggest(ROSTER, s.dir, runner=Replies("no me apetece"))
        ok(not res["ok"], "an unreadable answer is a failure")
        ok("no me apetece" in (res.get("raw") or ""),
           "and the raw reply is kept, so the owner can see what happened")


# ── applying what was ticked ─────────────────────────────────────────────────
def test_applying_writes_only_what_was_handed_in():
    with Sandbox() as s:
        teams.adopt(ROSTER, s.dir)
        prop = adv.parse(GOOD, SLUGS)
        res = adv.apply_proposal({"links": prop["links"], "cards": prop["cards"]},
                                 ROSTER, s.dir)
        ok(res["ok"], "it applies")
        d = {a["slug"]: a["why"]
             for a in teams.neighbours("baco", ROSTER, install_dir=s.dir)}
        f = {a["slug"]: a["why"]
             for a in teams.neighbours("forja", ROSTER, install_dir=s.dir)}
        ok("herramienta" in d["forja"], "the outbound instruction is stored")
        ok("costar dinero" in f["baco"], "and the return one, on the other agent")
        st = teams.state(ROSTER, s.dir)
        byslug = {a["slug"]: a for a in st["agents"]}
        eq(byslug["baco"]["role"], "Lleva las cuentas", "the card is written too")


def test_applying_a_subset_leaves_the_rest_alone():
    with Sandbox() as s:
        teams.adopt(ROSTER, s.dir)
        teams.set_link("default", "baco", why="algo que escribio ella",
                       install_dir=s.dir)
        adv.apply_proposal({"links": [{"from": "baco", "to": "forja", "both": True,
                                       "why": "a", "why_back": "b"}]}, ROSTER, s.dir)
        d = {a["slug"]: a["why"]
             for a in teams.neighbours("default", ROSTER, install_dir=s.dir)}
        eq(d["baco"], "algo que escribio ella",
           "a link the owner did not tick is untouched")


def test_replace_removes_what_was_not_ticked_and_only_then():
    with Sandbox() as s:
        teams.adopt(ROSTER, s.dir)
        body = {"links": [{"from": "baco", "to": "forja", "both": True, "why": "a"}]}
        adv.apply_proposal(body, ROSTER, s.dir, replace=False)
        eq(len(teams.read(s.dir)[1]["links"]), 3, "by default nothing is removed")
        res = adv.apply_proposal(body, ROSTER, s.dir, replace=True)
        links = teams.read(s.dir)[1]["links"]
        eq(len(links), 1, "with replace, only what was ticked survives")
        eq(res["removed"], 2, "and it says how many it took away")


def test_applying_on_a_mapless_machine_writes_down_what_was_already_running():
    with Sandbox() as s:
        res = adv.apply_proposal(
            {"links": [{"from": "baco", "to": "forja", "both": True, "why": "a"}]},
            ROSTER, s.dir)
        ok(res["adopted"], "the live mesh is adopted in the same breath")
        for pair in (("default", "baco"), ("default", "forja"), ("baco", "default")):
            ok(teams.allows(pair[0], pair[1], install_dir=s.dir)[0],
               "and %s -> %s still works, as it did before" % pair)


def test_a_one_way_proposal_over_a_backwards_link_is_applied_the_way_it_reads():
    """The end-to-end shape of the bug, through the button the owner actually presses."""
    with Sandbox() as s:
        teams.adopt(ROSTER, s.dir)
        # adopt() files this pair as baco -> forja, so it has to be re-made the other way
        # round for the proposal below to be the backwards one. Without this the test
        # passes whether the bug is there or not.
        teams.remove_link("baco", "forja", install_dir=s.dir)
        teams.set_link("forja", "baco", both=True, why="lo que escribio ella",
                       install_dir=s.dir)
        adv.apply_proposal({"links": [{"from": "baco", "to": "forja", "both": False,
                                       "why": "Para pedir costos de nube"}]},
                           ROSTER, s.dir)
        ok(teams.allows("baco", "forja", install_dir=s.dir)[0],
           "the proposed direction is the one that ends up open")
        ok(not teams.allows("forja", "baco", install_dir=s.dir)[0],
           "and the one it did not propose is closed")
        b = {a["slug"]: a["why"]
             for a in teams.neighbours("baco", ROSTER, install_dir=s.dir)}
        eq(b["forja"], "Para pedir costos de nube",
           "with the proposed sentence where the agent will read it")


def test_applying_cannot_name_an_agent_that_is_not_here():
    with Sandbox() as s:
        teams.adopt(ROSTER, s.dir)
        adv.apply_proposal({"links": [{"from": "baco", "to": "fantasma", "why": "x"}],
                            "cards": [{"slug": "fantasma", "role": "x"}]}, ROSTER, s.dir)
        _, data = teams.read(s.dir)
        ok(all("fantasma" not in (ln["from"], ln["to"]) for ln in data["links"]),
           "a payload naming an unknown agent writes no link")
        ok("fantasma" not in data["cards"], "and no card")


def test_a_broken_map_refuses_a_proposal_rather_than_overwriting_it():
    with Sandbox() as s:
        with io.open(teams.config_path(s.dir), "w", encoding="utf-8") as fh:
            fh.write("{roto")
        try:
            adv.apply_proposal({"links": [{"from": "baco", "to": "forja"}]},
                               ROSTER, s.dir)
            ok(False, "an unreadable map refuses the write")
        except ValueError as e:
            ok("no se puede leer" in str(e), "an unreadable map refuses the write")


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
