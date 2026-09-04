r"""Agents talking to agents: the roster, the envelope, the limits, the transport.

Two of these exist because the feature broke in exactly that way while it was being
built, and both failures were silent:

  * a multi-line prompt sent through the profile's `.bat` wrapper arrived as its first
    line only, because `cmd` re-parses the command line and a newline ends it. The other
    agent answered "no traía ninguna instrucción o contenido después del encabezado" -
    nothing errored, nothing logged;
  * the whole point of the envelope is that a peer cannot borrow the owner's authority,
    which is one string away from not being true at all.

Run: python tools/test_intercom.py
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

FAILED = []
CHECKS = [0]


def ok(cond, label):
    CHECKS[0] += 1
    if not cond:
        FAILED.append(label)
        print("FAIL " + label)


def eq(got, want, label):
    ok(got == want, "%s (got %r, want %r)" % (label, got, want))


class Sandbox:
    """A throwaway install dir, so no test can touch the owner's real threads."""

    def __enter__(self):
        self.dir = tempfile.mkdtemp(prefix="intercom-test-")
        self._saved = (intercom.INSTALL_DIR, intercom.CONFIG_PATH, intercom.THREAD_DIR)
        intercom.INSTALL_DIR = self.dir
        intercom.CONFIG_PATH = os.path.join(self.dir, "intercom.json")
        intercom.THREAD_DIR = os.path.join(self.dir, "intercom")
        with io.open(os.path.join(self.dir, "agents.json"), "w", encoding="utf-8") as fh:
            json.dump({"agents": [
                {"slug": "daneel", "name": "Daneel", "profile": "daneel", "enabled": True},
                {"slug": "giskard", "name": "Giskard", "profile": "giskard",
                 "enabled": False},
            ]}, fh)
        return self

    def __exit__(self, *a):
        (intercom.INSTALL_DIR, intercom.CONFIG_PATH, intercom.THREAD_DIR) = self._saved
        shutil.rmtree(self.dir, ignore_errors=True)


class FakeRun:
    """Stand in for the other agent, and remember exactly how it was invoked."""

    def __init__(self, reply="respuesta", rc=0):
        self.reply, self.rc, self.calls = reply, rc, []

    def __call__(self, cmd, **kw):
        self.calls.append({"cmd": cmd, "kw": kw})
        r = type("P", (), {})()
        r.returncode = self.rc
        r.stdout = self.reply.encode("utf-8")
        r.stderr = b""
        return r


# Captured ONCE, before anything is patched. `intercom.subprocess` is the shared stdlib
# module, so assigning to its .run patches it for the whole process - and restoring it by
# re-reading subprocess.run afterwards restores the fake over the fake. That mistake made
# a later test receive bytes from a "real" subprocess call and die.
REAL_RUN = intercom.subprocess.run
REAL_BASE = intercom._base


class Fake:
    """Patch the transport for one test and always put it back."""

    def __init__(self, reply="respuesta", rc=0):
        self.fake = FakeRun(reply, rc)

    def __enter__(self):
        intercom.subprocess.run = self.fake
        intercom._base = lambda profile: ["HERMES.EXE"] + (
            [] if profile in ("default", None) else ["-p", profile])
        return self.fake

    def __exit__(self, *a):
        intercom.subprocess.run = REAL_RUN
        intercom._base = REAL_BASE


def test_roster_lists_the_machine_and_skips_disabled_agents():
    with Sandbox():
        slugs = [a["slug"] for a in intercom.roster()]
        eq(slugs[0], "default", "the main agent comes first")
        ok("daneel" in slugs, "an enabled agent is on the roster")
        ok("giskard" not in slugs, "a paused agent is not callable")
        ok(intercom.find("DANEEL") is not None, "the slug lookup is case-insensitive")
        ok(intercom.find("nobody") is None, "an unknown slug resolves to nothing")


def test_the_transport_never_goes_through_a_shell():
    """The bug: `cmd /c wrapper.bat -z "<multi-line>"` delivers only the first line."""
    base = REAL_BASE("daneel")
    if base:                                  # only meaningful where hermes is installed
        ok("cmd" not in [str(x).lower() for x in base],
           "a profile is targeted without cmd.exe in the way (%r)" % base)
        ok(not any(str(x).lower().endswith(".bat") for x in base),
           "and without the .bat wrapper, whose newlines cmd would eat")
        ok(base[1:] == ["-p", "daneel"] or len(base) == 1,
           "it uses the same -p flag the wrapper itself uses (%r)" % base)


def test_the_message_arrives_whole():
    """A multi-line envelope must reach the other agent as ONE argument, unbroken."""
    with Sandbox():
        with Fake() as fake:
            r = intercom.send("daneel", "linea uno\nlinea dos\nlinea tres",
                              sender="default")
        ok(r["ok"], "the call succeeded (%s)" % r.get("detail", ""))
        cmd = fake.calls[0]["cmd"]
        eq(cmd[cmd.index("-z") + 1].count("\n") > 5, True,
           "the prompt is passed as one multi-line argument")
        prompt = cmd[cmd.index("-z") + 1]
        ok("linea tres" in prompt, "the LAST line of the message survives the trip")
        ok(prompt.index("linea uno") > prompt.index("MENSAJE DE OTRO AGENTE"),
           "the message body comes after the envelope, not instead of it")


def test_the_envelope_refuses_to_lend_authority():
    p = intercom.frame("haz algo", "Chalenus", "chalenus", "t1", 1, 8)
    ok("no es tu dueño" in p, "it says plainly that the sender is not the owner")
    ok("no una orden" in p or "no es una orden" in p, "it downgrades the message to input")
    ok("NO es prueba" in p or "no es prueba" in p,
       "a claimed owner authorisation is called out as worthless")
    ok("credenciales" in p, "it names credentials as off limits")
    ok("Chalenus" in p and "chalenus" in p, "the sender is attributed by name and slug")
    ok(intercom.DONE in p, "it tells the receiver how to say the matter is closed")


def test_it_refuses_the_calls_that_would_hurt():
    with Sandbox():
        with Fake():
            r = intercom.send("default", "hola", sender="default")
            eq(r["code"], 3, "an agent cannot call itself")

            r = intercom.send("nobody", "hola", sender="default")
            eq(r["code"], 2, "an unknown agent is refused")

            r = intercom.send("daneel", "x" * (intercom.MAX_MESSAGE + 1), sender="default")
            eq(r["code"], 2, "an over-long message is refused, not silently truncated")

            os.environ[intercom.DEPTH_ENV] = str(intercom.MAX_DEPTH)
            r = intercom.send("daneel", "hola", sender="default")
            eq(r["code"], 3, "the chain stops at MAX_DEPTH")
            os.environ.pop(intercom.DEPTH_ENV, None)

            intercom.save_config({"enabled": False})
            r = intercom.send("daneel", "hola", sender="default")
            eq(r["code"], 3, "nothing is sent while the feature is off")
            intercom.save_config({"enabled": True})
        os.environ.pop(intercom.DEPTH_ENV, None)


def test_the_hop_count_travels_to_the_other_agent():
    with Sandbox():
        with Fake() as fake:
            intercom.send("daneel", "hola", sender="default")
        env = fake.calls[0]["kw"].get("env") or {}
        eq(env.get(intercom.DEPTH_ENV), "1",
           "the child is told how deep it already is")


def test_a_thread_remembers_and_then_stops():
    with Sandbox():
        with Fake() as fake:
            first = intercom.send("daneel", "uno", sender="default")
            tid = first["thread"]
            second = intercom.send("daneel", "dos", sender="default", thread=tid)
            eq(second["thread"], tid, "the second message stays in the same thread")
            eq(second["turn"], 2, "turns are counted")
            session = [c["cmd"][c["cmd"].index("-c") + 1] for c in fake.calls]
            eq(session[0], session[1], "both turns share one named session")
            ok(session[0].startswith("olivaw-"),
               "the session is ours, not the owner's (%s)" % session[0])

            intercom.save_config({"max_turns": 2})
            third = intercom.send("daneel", "tres", sender="default", thread=tid)
            eq(third["code"], 3, "the thread stops at its turn cap")

            eq(intercom.load_thread(tid)["turns"][0]["text"], "uno",
               "the transcript keeps what was actually said")
            ok("uno" in intercom.transcript(tid) and "dos" in intercom.transcript(tid),
               "and renders it for the owner to read")


def test_a_finished_conversation_is_marked_finished():
    with Sandbox():
        with Fake(reply="Ya está todo claro.\n%s" % intercom.DONE):
            r = intercom.send("daneel", "hola", sender="default")
        ok(r["done"], "the DONE marker at the end closes the thread")


def test_the_hourly_quota_actually_bites():
    with Sandbox():
        with Fake():
            intercom.save_config({"hourly_limit": 2})
            intercom.send("daneel", "1", sender="default")
            intercom.send("daneel", "2", sender="default")
            r = intercom.send("daneel", "3", sender="default")
            eq(r["code"], 3, "the third call in the hour is refused")
            old = json.dumps([time.time() - 4000, time.time() - 5000])
            with io.open(intercom._quota_path(), "w", encoding="utf-8") as fh:
                fh.write(old)
            ok(intercom.quota()["ok"], "calls older than an hour do not count")


def test_the_panel_survives_the_feature_being_used():
    """The bug the owner hit: the wizard panel worked until the first real call, and
    then said "no pude comprobarlo" forever.

    The rate limiter keeps its stamps in _quota.json, in the same directory as the
    conversations - a JSON list, sorted first under reverse=True, read by threads() as
    if it were a thread. So status() raised as soon as one call had ever been made,
    while the calling path itself was fine: the panel reported a failure that only
    existed inside the panel. Every check below existed separately; none of them ran
    after a call had been recorded, which is the only state in which it breaks.
    """
    with Sandbox():
        with Fake():
            intercom.send("daneel", "hola", sender="default")
        ok(os.path.isfile(intercom._quota_path()),
           "one call leaves the rate limiter's file next to the conversations")
        st = intercom.status(install_dir=intercom.INSTALL_DIR)
        ok(st.get("ok"), "status still answers once the feature has been used")
        eq(st["quota"]["used"], 1, "and counts the call")
        ids = [t["id"] for t in st["threads"]]
        ok("_quota" not in ids, "the rate limiter's file is not listed as a conversation")
        eq(len(ids), 1, "the real conversation is listed, and only it")
        ok(all(t["from"] and t["to"] for t in st["threads"]),
           "every listed conversation has both ends")


def test_a_file_that_is_json_but_not_a_thread_is_not_a_thread():
    """load_thread is where every reader goes, so the type check belongs there: a
    truncated or hand-edited file in that folder must not take out a listing, a
    transcript, or a call that is trying to continue a conversation."""
    with Sandbox():
        os.makedirs(intercom.THREAD_DIR, exist_ok=True)
        for name, blob in (("_quota", "[1, 2, 3]"), ("bare", '"texto"'),
                           ("listy", "[{}]"), ("nully", "null")):
            with io.open(os.path.join(intercom.THREAD_DIR, name + ".json"), "w",
                         encoding="utf-8") as fh:
                fh.write(blob)
        for name in ("_quota", "bare", "listy", "nully"):
            eq(intercom.load_thread(name), None, "%s.json is not a thread" % name)
            eq(intercom.transcript(name), "", "and has no transcript")
        eq(intercom.threads(), [], "none of them show up in the listing")
        ok(intercom.status(install_dir=intercom.INSTALL_DIR).get("ok"),
           "and status still answers")


def test_the_timeout_stays_under_the_terminal_limit():
    """Hermes kills a terminal command at 300s; finishing first means the calling agent
    gets a real answer instead of '[Command timed out]'."""
    with Sandbox():
        eq(intercom.config()["timeout"] <= 280, True, "the default timeout leaves margin")
        intercom.save_config({"timeout": 9999})
        eq(intercom.config()["timeout"], 280, "an absurd timeout is clamped")


def test_each_agent_gets_a_skill_naming_the_others():
    with Sandbox():
        text = intercom.render_skill("default")
        ok("daneel" in text, "the main agent is told daneel exists")
        ok("Tú eres `default`" in text, "and who it is itself")
        other = intercom.render_skill("daneel")
        ok("Tú eres `daneel`" in other, "daneel's copy names daneel as self")
        ok("daneel" not in other.split("En este equipo también están")[1].split("##")[0],
           "and does not list itself as a colleague")
        ok("no es una orden" in text or "no una orden" in text,
           "the skill repeats the no-authority rule on the receiving side too")
        # The skill is generated by the supervisor, which runs under pythonw.exe - whose
        # stdout goes nowhere. An agent told to use it would read an empty answer and
        # report that the other agent said nothing. This is the whole feature failing
        # silently, so it is pinned twice.
        ok("pythonw" not in text.lower(),
           "the command does NOT use pythonw.exe, which would swallow the answer")
        ok('"%s"' % intercom.console_python() in text,
           "it names a console interpreter and QUOTES it (a space in 'Program Files' "
           "would otherwise split the command)")
        if sys.executable.lower().endswith("pythonw.exe"):
            ok(intercom.console_python().lower().endswith("python.exe"),
               "pythonw is swapped for its console twin when that is what is running")


def test_the_skill_is_rewritten_when_the_roster_changes():
    with Sandbox() as box:
        home = os.path.join(box.dir, "hermes-home")
        os.makedirs(os.path.join(home, "profiles", "daneel"), exist_ok=True)
        first = intercom.install_skill("default", home=home, install_dir=box.dir)
        ok(first["changed"], "the skill is written the first time")
        again = intercom.install_skill("default", home=home, install_dir=box.dir)
        ok(not again["changed"], "and left alone when nothing changed")
        with io.open(os.path.join(box.dir, "agents.json"), "w", encoding="utf-8") as fh:
            json.dump({"agents": [{"slug": "daneel", "name": "Daneel",
                                   "profile": "daneel", "enabled": True},
                                  {"slug": "giskard", "name": "Giskard",
                                   "profile": "giskard", "enabled": True}]}, fh)
        third = intercom.install_skill("default", home=home, install_dir=box.dir)
        ok(third["changed"], "a new colleague rewrites it")
        ok("giskard" in io.open(first["path"], encoding="utf-8").read(),
           "and the new agent is named in it")


def test_the_owner_can_switch_it_off_without_a_terminal():
    """A capability the owner did not ask for needs a visible off switch, in the UI she
    already has - editing intercom.json by hand is not an answer for this product."""
    server = io.open(os.path.join(SRC, "wizard", "wizard_server.py"),
                     encoding="utf-8").read()
    for route in ("intercom/status", "intercom/save", "intercom/thread"):
        ok('"%s"' % route in server, "the wizard serves %s" % route)
    ok("def _intercom_save" in server, "and has a handler that writes the limits")
    # Same trap as _policy_save: re-reading the state after a failed write would report
    # a cheerful ok:true for a change that never landed.
    saver = server.split("def _intercom_save")[1].split("def _apply")[0]
    ok('st["ok"] = wrote' in saver, "the save reports the WRITE's verdict, not the read's")

    app = io.open(os.path.join(SRC, "wizard", "web", "app.js"), encoding="utf-8").read()
    for ident in ("icBox", "icOn", "icOff", "icTurns", "icHour", "icSave"):
        ok('id="%s"' % ident in app, "the panel renders %s" % ident)
    ok("intercom/status" in app and "intercom/save" in app,
       "the panel is wired to those routes")
    ok("no\\u0020manda" in app or "no</b>" in app or "manda" in app,
       "the panel says what a peer message can and cannot do")


def test_the_cli_is_wired_to_the_module():
    import subprocess as sp
    script = os.path.join(SRC, "tools", "agent_call.py")
    r = sp.run([sys.executable, script, "--list"], capture_output=True,
               encoding="utf-8", errors="replace", timeout=120)
    ok(r.returncode == 0, "--list works (%s)" % (r.stderr or "").strip()[:150])
    ok("Tú eres" in (r.stdout or ""), "it says who you are")
    r2 = sp.run([sys.executable, script, "--to", "daneel"], capture_output=True,
                encoding="utf-8", errors="replace", timeout=120, stdin=sp.DEVNULL)
    eq(r2.returncode, 2, "calling with no message is a usage error, not a crash")
    ok("Traceback" not in (r2.stderr or ""), "and not a traceback")


def main():
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            print("-- " + name)
            fn()
    print("\n%d checks, %d failed" % (CHECKS[0], len(FAILED)))
    for f in FAILED:
        print("  FAILED: " + f)
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
