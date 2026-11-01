r"""Scrolling a conversation all the way back to the first thing said in it.

The hole this closes
--------------------
Hermes compacts a long conversation when it crosses a fraction of the model's window -
10% here, which on a 1M window is roughly every hundred thousand tokens. In the mode
this machine runs it does that NON-destructively: the summarised turns are soft-archived
(``active = 0``) rather than deleted, still on disk and still searchable, and Hermes'
own source says why in as many words.

But ``GET /api/sessions/{id}/messages`` calls ``get_messages()`` with no arguments, and
that defaults to the active rows. There is no query parameter and no second route, so
through the HTTP API those turns do not exist - and the phone reads the conversation
through exactly that route. Which meant that scrolling up stopped, and not at the
beginning: ``protect_first_n`` keeps the opening turns live, so what the owner actually
got was the first few messages, a silent hole, the recent ones, and "there is more
above" switched off. On this machine, when this was written, that was 46,513 archived
messages against 13,788 live ones.

So `talk_archive` reads them from where Hermes put them, read-only, and these tests are
about the two things that then matter: that the pages tile the WHOLE conversation with
no gap and nothing shown twice, and that every way this read can fail ends with the
owner getting what they got before rather than something worse.

Run: python tools/test_history.py
"""

import os
import shutil
import sqlite3
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from wizard import talk as T              # noqa: E402
from wizard import talk_archive as A      # noqa: E402

PASSED, FAILED = [], []


def check(name, cond, extra=""):
    (PASSED if cond else FAILED).append(name)
    print(("  ok   " if cond else "  FAIL ") + name +
          (("\n       " + str(extra)) if (extra and not cond) else ""))


def section(t):
    print("\n=== %s ===" % t)


SCHEMA = """
CREATE TABLE sessions (
    id TEXT PRIMARY KEY,
    parent_session_id TEXT
);
CREATE TABLE messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT,
    tool_call_id TEXT,
    tool_calls TEXT,
    tool_name TEXT,
    timestamp REAL NOT NULL,
    token_count INTEGER,
    finish_reason TEXT,
    reasoning TEXT,
    reasoning_content TEXT,
    active INTEGER NOT NULL DEFAULT 1,
    compacted INTEGER NOT NULL DEFAULT 0
);
"""


class Store(object):
    """A state.db shaped like the real one, with a conversation in it."""

    def __init__(self, home):
        self.home = home
        self.path = os.path.join(home, "state.db")
        con = sqlite3.connect(self.path)
        con.executescript(SCHEMA)
        con.commit()
        con.close()
        self.clock = 1000.0

    def _con(self):
        return sqlite3.connect(self.path)

    def session(self, sid, parent=None):
        con = self._con()
        con.execute("INSERT INTO sessions (id, parent_session_id) VALUES (?, ?)",
                    (sid, parent))
        con.commit()
        con.close()

    def say(self, sid, role, content, active=1, tool_calls=None):
        self.clock += 1
        con = self._con()
        con.execute(
            "INSERT INTO messages (session_id, role, content, tool_calls, timestamp,"
            " active) VALUES (?, ?, ?, ?, ?, ?)",
            (sid, role, content, tool_calls, self.clock, active))
        con.commit()
        con.close()

    def turn(self, sid, n, active=1):
        """One exchange, with the tool calls a real one drags along."""
        self.say(sid, "user", "pregunta %d" % n, active)
        self.say(sid, "assistant", None, active, tool_calls='[{"id":"x"}]')
        self.say(sid, "tool", "resultado", active)
        self.say(sid, "assistant", "respuesta %d" % n, active)


def paged(sid, page=25, profile=None):
    """Every message the phone would see, scrolling from the bottom to the top.

    Exactly what the screen does: ask for the last page, then ask for the one before
    the index it was given, until it is told there is nothing above. Returns the
    messages in order, plus how many requests it took.
    """
    out, asks = [], 0
    h = T.history(profile, None, session_id=sid, limit=page)
    asks += 1
    if not h.get("ok"):
        return None, h, asks
    out = list(h["messages"])
    while h.get("has_more"):
        h = T.history(profile, None, session_id=sid, limit=page, before=h["start"])
        asks += 1
        if not h.get("ok"):
            return None, h, asks
        out = list(h["messages"]) + out
        if asks > 200:
            break
    return out, h, asks


def main():
    tmp = tempfile.mkdtemp()
    os.environ["HERMES_HOME"] = tmp
    store = Store(tmp)
    sid = "20260101_120000_aaaa"
    store.session(sid)

    section("a conversation compaction has been through")
    # Forty turns, of which the first thirty were summarised away at some point: on
    # disk, marked inactive, invisible through the HTTP route.
    for n in range(1, 31):
        store.turn(sid, n, active=0)
    store.say(sid, "assistant", "[CONTEXT COMPACTION] resumen de lo anterior", active=1)
    for n in range(31, 41):
        store.turn(sid, n, active=1)

    rows = A.messages(sid, hermes_home=tmp)
    check("the archived turns are found at all", rows is not None and len(rows) > 100,
          None if rows is None else len(rows))
    live = A.messages(sid, hermes_home=tmp, include_archived=False)
    check("and there are far more of them than Hermes' route would hand over",
          len(rows) > len(live) * 3, "%d vs %d" % (len(rows), len(live)))

    h = T.history(session_id=sid, limit=25)
    check("the conversation is forty turns long, not ten", h["total"] == 80, h["total"])
    check("the compaction marker is not one of them",
          not any("CONTEXT COMPACTION" in (m.get("content") or "") for m in h["messages"]))
    check("nor are the tool calls",
          all((m.get("content") or "").strip() for m in h["messages"]))

    section("scrolling to the top")
    all_msgs, last, asks = paged(sid)
    check("it ends", all_msgs is not None, last)
    check("at the first thing anybody said",
          all_msgs[0]["content"] == "pregunta 1", all_msgs[0]["content"])
    check("having shown every message once", len(all_msgs) == 80, len(all_msgs))
    check("with no gap and nothing twice",
          [m["content"] for m in all_msgs if m["role"] == "user"]
          == ["pregunta %d" % n for n in range(1, 41)])
    check("and it said so rather than being asked for ever", last["has_more"] is False)
    check("in as many requests as there are pages", asks == 4, asks)

    section("a page asked for while the agent is still answering")
    # Indices count from the START, which is the whole reason they are safe: a page
    # asked for by distance from the END shifts under the reader every time a new turn
    # lands, and the owner reads the same paragraph twice or skips one.
    h1 = T.history(session_id=sid, limit=10)
    store.turn(sid, 41, active=1)
    older = T.history(session_id=sid, limit=10, before=h1["start"])
    check("the page before is the same page it would have been",
          older["end"] == h1["start"], (older["end"], h1["start"]))
    check("and nothing from it was also on the first page",
          not ({m["content"] for m in older["messages"]}
               & {m["content"] for m in h1["messages"]}))
    check("while the total has grown", T.history(session_id=sid, limit=1)["total"] == 82)

    section("a conversation that was rotated rather than compacted in place")
    # The other mode: Hermes ends the session and starts a child carrying
    # parent_session_id, so what the owner thinks of as one conversation is a chain.
    old, new = "20260201_090000_bbbb", "20260201_110000_cccc"
    store.session(old)
    store.session(new, parent=old)
    for n in range(1, 6):
        store.turn(old, n)
    for n in range(6, 9):
        store.turn(new, n)
    h = T.history(session_id=new, limit=50)
    said = [m["content"] for m in h["messages"] if m["role"] == "user"]
    check("the child conversation carries what came before it",
          said == ["pregunta %d" % n for n in range(1, 9)], said)

    section("every way this can fail leaves the owner where they were")
    check("a session nobody has heard of is not an empty conversation",
          A.messages("no-existe", hermes_home=tmp) is None)
    check("nor is an empty session id", A.messages("", hermes_home=tmp) is None)
    check("a home with no database at all",
          A.messages(sid, hermes_home=os.path.join(tmp, "nada")) is None)

    # The important one: a Hermes that moved its columns must send us back to the HTTP
    # route rather than produce a conversation with the wrong shape.
    other = tempfile.mkdtemp()
    con = sqlite3.connect(os.path.join(other, "state.db"))
    con.executescript("CREATE TABLE messages (id INTEGER PRIMARY KEY, blah TEXT);")
    con.commit()
    con.close()
    check("a database whose schema is not the one we know",
          A.messages(sid, hermes_home=other) is None)
    shutil.rmtree(other, ignore_errors=True)

    # And it must never create one. A path that got this wrong would answer every
    # question "this conversation is empty" for ever after.
    empty = os.path.join(tempfile.mkdtemp(), "nunca")
    A.messages(sid, hermes_home=empty)
    check("and a missing database is never created by looking for it",
          not os.path.exists(os.path.join(empty, "state.db")))

    section("it is a reader and only a reader")
    before = open(store.path, "rb").read()
    A.messages(sid, hermes_home=tmp)
    A.messages(sid, hermes_home=tmp, speech_only=True)
    check("reading the conversation does not change the file",
          open(store.path, "rb").read() == before)

    section("what a search reads")
    speech = A.messages(sid, hermes_home=tmp, speech_only=True)
    whole = A.messages(sid, hermes_home=tmp)
    check("a search is spared the tool calls",
          not any(r.get("tool_calls") for r in speech))
    check("and the turns with nothing in them",
          all((r.get("content") or "").strip() for r in speech))
    check("which is most of what a conversation is made of",
          len(speech) <= len(whole) * 0.6, "%d of %d" % (len(speech), len(whole)))
    check("but not one word anybody said",
          {r["content"] for r in whole if T._is_speech(r)}
          <= {r["content"] for r in speech if r.get("content")})

    section("a conversation longer than anyone meant to have")
    big = "20260301_080000_dddd"
    store.session(big)
    con = store._con()
    con.executemany(
        "INSERT INTO messages (session_id, role, content, timestamp, active)"
        " VALUES (?, 'user', ?, ?, 1)",
        [(big, "m%d" % i, 2000.0 + i) for i in range(A.MAX_ROWS + 500)])
    con.commit()
    con.close()
    t0 = time.time()
    rows = A.messages(big, hermes_home=tmp)
    took = time.time() - t0
    check("a read is bounded", len(rows) <= A.MAX_ROWS, len(rows))
    check("and quick enough to sit inside a phone's request", took < 3.0, "%.2fs" % took)

    os.environ.pop("HERMES_HOME", None)
    shutil.rmtree(tmp, ignore_errors=True)

    print("\n%d passed, %d failed" % (len(PASSED), len(FAILED)))
    for f in FAILED:
        print("  - " + f)
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
