r"""The SOS console must keep the conversation, and must call itself Olivaw.

Reported from a live Windows machine: "once the response arrived, the message is deleted and
the conversation is not saved."

The store turned out to be healthy - driving start_job/poll_job for real put the turn on
disk, recorded the session id and listed it back. The loss was a RACE. `_run_job` published
`done=True` and only THEN wrote the turn, while the browser polls every 800ms and, the
instant it sees `done`, re-reads the conversation to display the authoritative transcript.
In that gap the read returned a conversation without the turn just finished - and the UI,
which had already dropped its own copy, rendered nothing. On a fast answer the gap was hit
every time, which is exactly what "the response arrived and then it was deleted" looks like.

So the order is the invariant: persist, then announce. This suite pins it, and pins that the
help screen is labelled Olivaw rather than by whichever brain happens to be underneath -
the owner never chose to talk to Claude or to Codex, they opened Olivaw.

Run: python tools/test_sos.py
"""

import io
import json
import os
import shutil
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from wizard import console_store as store   # noqa: E402
from wizard import rescue                   # noqa: E402

PASSED, FAILED = [], []


def check(name, cond, extra=""):
    (PASSED if cond else FAILED).append(name)
    print(("  ok   " if cond else "  FAIL ") + name +
          (("\n       " + str(extra)) if (extra and not cond) else ""))


def section(t):
    print("\n=== %s ===" % t)


def main():
    tmp = tempfile.mkdtemp(prefix="sos-")
    try:
        section("the store keeps a turn, and keeps the session behind it")
        conv = store.create(tmp, "por qué no contesta mi agente")
        check("a conversation gets an id", bool(conv and conv.get("id")), conv)
        check("and a title taken from the question",
              "no contesta" in (conv.get("title") or ""), conv)
        store.set_fields(tmp, conv["id"], session_id="sess-1")
        store.append_turn(tmp, conv["id"], {"ts": time.time(), "question": "q1",
                                            "mode": "diagnose", "reply": "a1", "events": []})
        again = store.load(tmp, conv["id"])
        check("the turn is on disk", len(again.get("turns") or []) == 1, again)
        check("the session id is kept, so the brain can resume it",
              again.get("session_id") == "sess-1", again)
        check("the file is not world-readable",
              os.path.isfile(os.path.join(tmp, "console", conv["id"] + ".json")))
        got = rescue.get_conversation(conv["id"], tmp)
        check("get_conversation hands it back", got.get("ok") and
              len(got["conversation"]["turns"]) == 1, got)
        lst = rescue.list_conversations(tmp, 40)
        check("and it shows in the listing",
              any(c["id"] == conv["id"] for c in lst.get("conversations") or []), lst)

        section("a browser-supplied id is never trusted with a path")
        for bad in ("../../etc/passwd", "..\\..\\x", "", None, "ZZZZ", "a" * 17):
            check("rejected: %r" % (bad,), store._path(tmp, bad) is None)

        section("persist BEFORE announcing done — the race that deleted the message")
        # The exact ordering, read off the source: a `done` published before the write is a
        # window in which the browser reads a transcript that is missing the answer.
        src = io.open(os.path.join(ROOT, "src", "wizard", "rescue.py"),
                      encoding="utf-8").read()
        fin = src[src.index("    finally:", src.index("def _run_job")):]
        fin = fin[:fin.index("def _save_turn")]
        save_at = fin.index("_save_turn(")
        done_at = fin.index('_job_put(job_id, done=True)')
        check("_save_turn runs first", save_at < done_at,
              "done is still announced before the turn is written")
        check("and the reason is written down where the order lives",
              "before announcing done" in fin.lower() or
              "persist before" in fin.lower(), fin[:200])

        section("the front end never drops the only copy it has")
        app = io.open(os.path.join(ROOT, "src", "wizard", "web", "app.js"),
                      encoding="utf-8").read()
        fl = app[app.index("function finishLive()"):]
        fl = fl[:fl.index("\n  function ", 10)]
        check("the finished turn is adopted locally", "SOS.turns = (SOS.turns || [])" in fl, fl)
        check("and painted before any network call",
              fl.index("paintMsgs(true)") < fl.index('api("rescue/conversation"'), fl)
        check("a shorter server transcript does not overwrite ours",
              ">= (SOS.turns || []).length" in fl, fl)
        check("and a failed reload cannot blank the screen either",
              ".catch(function () { loadList(); })" in fl, fl)

        section("the help screen is Olivaw, not the brand of the brain")
        html = io.open(os.path.join(ROOT, "src", "wizard", "web", "index.html"),
                       encoding="utf-8").read()
        for where, text in (("the sidebar button", "Habla con Olivaw"),
                            ("the SOS subtitle", "Habla con Olivaw sobre tu instalación"),
                            ("the floating button", 'title="¿Algo falla? Habla con Olivaw"'),
                            ("the aria label", 'aria-label="Ayuda: hablar con Olivaw"')):
            check("%s says Olivaw" % where, text in html, where)
        check("nothing in the shell still says 'Habla con Claude'",
              "Habla con Claude" not in html)
        check("nor 'hablar con Claude'", "hablar con Claude" not in html)
        check("the conversation badge says Olivaw",
              "Olivaw recuerda esta conversación" in app)
        check("Claude no longer 'remembers' it on the owner's behalf",
              "Claude recuerda esta conversación" not in app)
        check("the labels are set from one place", "function paintSosLabels(" in app)
        check("and that place hardcodes Olivaw, not the engine",
              'n.textContent = "Habla con Olivaw"' in app)
        # The brain must still be discoverable - it is a real fact about the machine, and
        # support needs it. Just not as the name of the product.
        check("the engine survives as a tooltip, for diagnosing",
              '(motor: " + brain' in app)
        launcher = io.open(os.path.join(ROOT, "src", "launcher.py"), encoding="utf-8").read()
        check("the Start-Menu tooltip says Olivaw too",
              "Hablar con Olivaw (ayuda directa)" in launcher)
        check("and no longer says Claude", "Hablar con Claude sobre Olivaw" not in launcher)

        section("pruning keeps the newest, not the first")
        for i in range(4):
            c = store.create(tmp, "conversación %d" % i)
            store.append_turn(tmp, c["id"], {"ts": time.time() + i, "question": "q",
                                             "mode": "diagnose", "reply": "r", "events": []})
        listing = rescue.list_conversations(tmp, 3)
        check("the listing honours the limit",
              len(listing.get("conversations") or []) <= 3, listing)
        ts = [c.get("updated") or 0 for c in listing.get("conversations") or []]
        check("newest first", ts == sorted(ts, reverse=True), ts)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n%d passed, %d failed" % (len(PASSED), len(FAILED)))
    for f in FAILED:
        print("  - " + f)
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
