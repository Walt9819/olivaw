r"""A message the computer carries on its own, and the files an agent has made.

Two features, one suite, because they are the two halves of the same complaint: the
phone could not be trusted to still be there when the answer arrived, and the agent's
work could only be seen through whichever conversation happened to be open.

What is actually being tested here
-----------------------------------
**That the socket is no longer the delivery.** `talk.send` held one HTTP request open
for the whole of an agent turn - minutes - and answered down it. Everything about a
phone is hostile to that socket: wifi hands over to mobile data, a relay re-attaches, a
carrier drops an idle tunnel, Android freezes the app in a pocket. When it broke the
turn still ran, and the owner saw a failed send, no answer and no notification, while
the agent was in fact working. So `start()` must return before the turn is finished,
and `look()` must be able to produce that turn's answer to anyone who asks later, down
any connection.

**That it never says anything twice.** These agents DO things. The dangerous shape of
this feature is a retry after a lost connection becoming a second "send that email", so
a `client_id` names a message for its whole life and the same id must always name the
same turn. This is the one property worth more than the feature.

**That two messages stay in the order they were typed.** Hermes replays a session's
history into every turn, so overlapping turns on one conversation each answer a prompt
the other is still changing - and "the second thing I said was answered first" is not a
thing a chat may do.

**That the files screen means the agent's files.** It is reached from the agent's own
settings and listed the files of one conversation, so a three-month-old agent showed
four of them. And since it hands files out of a folder a person can drop anything into,
the denylist and the walk limits matter as much as the listing.

Run: python tools/test_turns.py
"""

import io
import os
import shutil
import sys
import tempfile
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from wizard import talk as T            # noqa: E402
from wizard import talk_turns as TT     # noqa: E402

PASSED, FAILED = [], []


def check(name, cond, extra=""):
    (PASSED if cond else FAILED).append(name)
    print(("  ok   " if cond else "  FAIL ") + name +
          (("\n       " + str(extra)) if (extra and not cond) else ""))


def section(t):
    print("\n=== %s ===" % t)


class Agent(object):
    """A stand-in for the thing on the other end of `talk.send`.

    Records what it was asked and when, so the tests can say something about ORDER and
    about overlap rather than only about results.
    """

    def __init__(self, delay=0.15):
        self.delay = delay
        self.log = []
        self.live = 0
        self.overlapped = False
        self.fail = set()
        self.boom = set()
        self._lock = threading.Lock()

    def send(self, profile=None, hermes=None, session_id="", text="", files=None,
             timeout=600):
        with self._lock:
            self.live += 1
            if self.live > 1:
                self.overlapped = True
            self.log.append(("in", session_id, text))
        try:
            time.sleep(self.delay)
            if text in self.boom:
                raise RuntimeError("el agente explotó")
            if text in self.fail:
                return {"ok": False, "detail": "El agente dejó de escuchar."}
            return {"ok": True, "reply": "eco: " + text, "files": [],
                    "session_id": session_id or "nueva"}
        finally:
            with self._lock:
                self.live -= 1
                self.log.append(("out", session_id, text))

    @property
    def asked(self):
        return [t for (w, _s, t) in self.log if w == "in"]


def wait_for(fn, limit=6.0):
    end = time.time() + limit
    while time.time() < end:
        if fn():
            return True
        time.sleep(0.02)
    return False


def turns_part():
    section("the send returns before the agent has finished")
    TT._reset_for_tests()
    agent = Agent(delay=0.4)
    T.send = agent.send

    began = time.time()
    first = TT.start(session_id="s1", text="hola", client_id="c1")
    took = time.time() - began
    check("accepted without waiting for the turn", took < 0.2, "%.2fs" % took)
    check("and says so", first.get("ok") and first.get("queued"))
    check("with a turn to ask after", bool(first["turn"]["id"]))
    check("which is running", first["turn"]["state"] == "running")
    check("the message itself is not echoed back", "text" not in first["turn"])

    tid = first["turn"]["id"]
    check("asking at once still says running",
          TT.look(ids=[tid])["turns"][0]["state"] == "running")
    check("the answer arrives on its own",
          wait_for(lambda: TT.look(ids=[tid])["turns"][0]["state"] == "done"))
    check("and it is the agent's answer",
          TT.look(ids=[tid])["turns"][0].get("reply") == "eco: hola")

    section("the same message sent twice is one message")
    TT._reset_for_tests()
    agent = Agent(delay=0.3)
    T.send = agent.send
    a = TT.start(session_id="s1", text="manda ese correo", client_id="same")
    b = TT.start(session_id="s1", text="manda ese correo", client_id="same")
    c = TT.start(session_id="s1", text="manda ese correo", client_id="same")
    check("the second attempt is the first turn", b["turn"]["id"] == a["turn"]["id"])
    check("and the third", c["turn"]["id"] == a["turn"]["id"])
    check("and it says it recognised them", b.get("again") and c.get("again"))
    wait_for(lambda: TT.look(ids=[a["turn"]["id"]])["turns"][0]["state"] == "done")
    check("the agent was asked exactly once", agent.asked == ["manda ese correo"],
          agent.asked)

    # The id is what makes them the same message - not the words. Somebody who really
    # does mean to say a thing twice must be able to.
    again = TT.start(session_id="s1", text="manda ese correo", client_id="otro")
    check("saying it again on purpose is a new turn",
          again["turn"]["id"] != a["turn"]["id"])
    wait_for(lambda: TT.look(ids=[again["turn"]["id"]])["turns"][0]["state"] == "done")
    check("and the agent hears it the second time", len(agent.asked) == 2, agent.asked)

    # ...and the id only counts within the conversation it was sent to, or a retry in
    # one thread would silently answer a message in another.
    TT._reset_for_tests()
    agent = Agent(delay=0.05)
    T.send = agent.send
    one = TT.start(session_id="s1", text="hola", client_id="shared")
    two = TT.start(session_id="s2", text="hola", client_id="shared")
    check("the same id in another conversation is another message",
          one["turn"]["id"] != two["turn"]["id"])

    section("two messages keep the order they were typed in")
    TT._reset_for_tests()
    agent = Agent(delay=0.25)
    T.send = agent.send
    t1 = TT.start(session_id="s1", text="primero", client_id="c1")["turn"]["id"]
    t2 = TT.start(session_id="s1", text="segundo", client_id="c2")["turn"]["id"]
    check("the second is accepted immediately too",
          TT.look(ids=[t2])["turns"][0]["state"] == "running")
    done = TT.look(ids=[t1, t2], wait=8)
    check("both finish", [t["state"] for t in done["turns"]] == ["done", "done"],
          [t["state"] for t in done["turns"]])
    check("asked in the order they were sent", agent.asked == ["primero", "segundo"],
          agent.asked)
    check("and never at the same time", not agent.overlapped)

    # Different conversations are different queues, or one slow agent would hold up
    # every other agent on the machine.
    TT._reset_for_tests()
    agent = Agent(delay=0.3)
    T.send = agent.send
    began = time.time()
    x = TT.start(session_id="sa", text="a", client_id="ca")["turn"]["id"]
    y = TT.start(session_id="sb", text="b", client_id="cb")["turn"]["id"]
    TT.look(ids=[x, y], wait=8)
    both = time.time() - began
    check("two conversations answer side by side", both < 0.55, "%.2fs" % both)

    section("waiting for an answer instead of asking over and over")
    TT._reset_for_tests()
    agent = Agent(delay=0.4)
    T.send = agent.send
    tid = TT.start(session_id="s1", text="hola", client_id="c1")["turn"]["id"]
    began = time.time()
    res = TT.look(ids=[tid], wait=10)
    held = time.time() - began
    check("the poll is held until the answer exists", 0.3 < held < 2.0, "%.2fs" % held)
    check("and comes back with it", res["turns"][0].get("reply") == "eco: hola")

    began = time.time()
    TT.look(ids=[tid], wait=5)
    check("a poll with nothing left to wait for returns at once",
          (time.time() - began) < 0.3)

    began = time.time()
    TT.look(ids=["no-existe"], wait=1)
    check("and so does one about a turn nobody knows",
          (time.time() - began) < 0.3)

    section("what the phone is told when something goes wrong")
    TT._reset_for_tests()
    agent = Agent(delay=0.05)
    agent.fail.add("fallará")
    T.send = agent.send
    tid = TT.start(session_id="s1", text="fallará", client_id="c1")["turn"]["id"]
    got = TT.look(ids=[tid], wait=5)["turns"][0]
    check("a turn the agent refused is failed", got["state"] == "failed")
    check("with the reason the agent gave",
          "dejó de escuchar" in got.get("detail", ""), got)

    agent.boom.add("explotará")
    tid = TT.start(session_id="s1", text="explotará", client_id="c2")["turn"]["id"]
    got = TT.look(ids=[tid], wait=5)["turns"][0]
    check("a worker that raises does not leave a turn running for ever",
          got["state"] == "failed", got)
    check("and says what happened", "explotó" in got.get("detail", ""), got)

    check("a turn nobody minted is unknown rather than an error",
          TT.look(ids=["inventado"])["turns"][0]["state"] == "unknown")

    section("nothing is accepted that could not be sent")
    TT._reset_for_tests()
    T.send = Agent(delay=0.01).send
    check("an empty message is refused",
          TT.start(session_id="s1", text="   ", client_id="c")["ok"] is False)
    check("a message with a file but no words is not",
          TT.start(session_id="s1", text="", files=["id"], client_id="c")["ok"])
    bad = TT.start(session_id="../../etc", text="hola", client_id="c2")
    check("and a conversation id that is a path is refused", bad["ok"] is False, bad)

    section("the table does not grow for ever")
    TT._reset_for_tests()
    T.send = Agent(delay=0.01).send
    ids = [TT.start(session_id="s%d" % i, text="x", client_id="c%d" % i)["turn"]["id"]
           for i in range(TT.MAX_TURNS + 12)]
    wait_for(lambda: all(t["state"] != "running"
                         for t in TT.look(ids=ids[-5:])["turns"]))
    TT.look(ids=ids[-1:])          # any call evicts
    states = {t["id"]: t["state"] for t in TT.look(ids=ids)["turns"]}
    check("the oldest fall off the end", states[ids[0]] == "unknown")
    check("and the newest are still there", states[ids[-1]] == "done")

    # A forgotten turn is not a failure - the message WAS accepted, so it was almost
    # certainly answered, and the answer is in the conversation. Calling it failed
    # would invite a retry that says everything twice.
    check("a forgotten turn is never reported as failed",
          "failed" not in [states[i] for i in ids[:6]])

    section("letting go of a turn")
    TT._reset_for_tests()
    agent = Agent(delay=0.3)
    T.send = agent.send
    tid = TT.start(session_id="s1", text="hola", client_id="c1")["turn"]["id"]
    check("a turn still running is not forgotten on request",
          TT.drop(ids=[tid])["forgotten"] == 0)
    check("because the agent is already doing it",
          TT.look(ids=[tid])["turns"][0]["state"] == "running")
    TT.look(ids=[tid], wait=5)
    check("once answered it can be", TT.drop(ids=[tid])["forgotten"] == 1)
    check("and then nobody knows it",
          TT.look(ids=[tid])["turns"][0]["state"] == "unknown")

    section("a conversation opened by the message itself")
    TT._reset_for_tests()
    T.send = Agent(delay=0.05).send
    tid = TT.start(session_id="s9", text="hola", client_id="c1")["turn"]["id"]
    got = TT.look(ids=[tid], wait=5)["turns"][0]
    check("the turn carries the conversation it ended up in",
          got["session_id"] == "s9", got)


def files_part():
    section("every file the agent has, not every file in one conversation")
    tmp = tempfile.mkdtemp()
    os.environ["CLAUDE_BRIDGE_WORKSPACE"] = tmp

    def put(rel, text="x"):
        p = os.path.join(tmp, rel.replace("/", os.sep))
        d = os.path.dirname(p)
        if d and not os.path.isdir(d):
            os.makedirs(d)
        io.open(p, "w", encoding="utf-8").write(text)
        time.sleep(0.01)
        return p

    put("informe.pdf")
    put("adjuntos/foto-que-mande.png")
    put("proyecto/notas.md")
    # None of these are the agent's work, and all of them would bury it.
    put("node_modules/left-pad/index.js")
    put(".git/config", "[core]")
    put("proyecto/.hidden.txt")
    # And this one must never leave the machine however it is asked for.
    put(".env", "API_KEY=secreto")
    put("credentials.json", "{}")
    put("id_rsa", "-----BEGIN")

    got = T.agent_files()
    names = [f["rel"] for f in got["files"]]
    check("the agent's own files are listed",
          "informe.pdf" in names and "proyecto/notas.md" in names, names)
    check("so is what the owner sent it", "adjuntos/foto-que-mande.png" in names, names)
    check("marked as the owner's rather than the agent's work",
          [f["mine"] for f in got["files"] if f["rel"].startswith("adjuntos/")] == [True])
    check("a checkout's innards are not", not any("node_modules" in n for n in names))
    check("nor a .git folder", not any(".git" in n for n in names))
    check("nor hidden files", not any(".hidden" in n for n in names))
    check("and no credential is listed at all",
          not any(n in names for n in (".env", "credentials.json", "id_rsa")), names)

    check("newest first", names[0] == "proyecto/notas.md", names)
    check("each has a fetch id rather than a path",
          all(f.get("id") for f in got["files"]))
    check("and a size somebody can read",
          all(f.get("human") for f in got["files"]))

    # The id is the only way out, and it is the same door the chat uses - so the
    # denylist is re-checked there too.
    one = [f for f in got["files"] if f["rel"] == "informe.pdf"][0]
    check("spending the id gives the file", T.file_bytes(one["id"])["ok"])
    check("an id nobody minted gives nothing",
          T.file_bytes("inventado")["ok"] is False)

    section("finding one of them")
    check("by name", [f["rel"] for f in T.agent_files(query="notas")["files"]]
          == ["proyecto/notas.md"])
    check("by folder", [f["rel"] for f in T.agent_files(query="proyecto")["files"]]
          == ["proyecto/notas.md"])
    check("accents and capitals do not have to match",
          len(T.agent_files(query="INFORME")["files"]) == 1)
    check("and a word that is in nothing finds nothing",
          T.agent_files(query="zzz")["total"] == 0)

    section("a page at a time")
    page = T.agent_files(limit=2)
    check("a page is a page", len(page["files"]) == 2)
    check("and says there is more", page["has_more"] is True)
    check("the whole thing is counted anyway", page["total"] == 3, page["total"])
    rest = T.agent_files(limit=2, offset=2)
    check("the next page carries on", len(rest["files"]) == 1)
    check("and says it is the end", rest["has_more"] is False)
    check("no file appears on both pages",
          not ({f["rel"] for f in page["files"]} & {f["rel"] for f in rest["files"]}))

    section("a workspace that is not there, and one that is enormous")
    shutil.rmtree(tmp, ignore_errors=True)
    gone = T.agent_files()
    check("an agent with no folder yet is not an error", gone["ok"] is True)
    check("and says so in words", "carpeta" in gone.get("detail", ""), gone)

    tmp = tempfile.mkdtemp()
    os.environ["CLAUDE_BRIDGE_WORKSPACE"] = tmp
    deep = tmp
    for i in range(12):
        deep = os.path.join(deep, "n%d" % i)
    os.makedirs(deep)
    io.open(os.path.join(deep, "hondo.txt"), "w").write("x")
    io.open(os.path.join(tmp, "arriba.txt"), "w").write("x")
    names = [f["rel"] for f in T.agent_files()["files"]]
    check("a walk does not go down for ever", "arriba.txt" in names and
          not any("hondo" in n for n in names), names)

    saved = T.WALK_FILES
    T.WALK_FILES = 5
    for i in range(20):
        io.open(os.path.join(tmp, "f%d.txt" % i), "w").write("x")
    check("and it stops counting at some point",
          len(T.agent_files()["files"]) <= 5)
    T.WALK_FILES = saved

    os.environ.pop("CLAUDE_BRIDGE_WORKSPACE", None)
    shutil.rmtree(tmp, ignore_errors=True)


def main():
    real_send = T.send
    try:
        turns_part()
        files_part()
    finally:
        T.send = real_send
    print("\n%d passed, %d failed" % (len(PASSED), len(FAILED)))
    for f in FAILED:
        print("  - " + f)
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
