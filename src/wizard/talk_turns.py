r"""A turn the computer finishes whether or not the phone is still listening.

The failure this exists to end
------------------------------
``talk.send`` is one HTTP request held open for as long as the agent takes - up to ten
minutes. The phone made that request and waited inside it. Which means the answer was
delivered down the same socket the question went up, and everything about a phone is
hostile to that socket: wifi hands over to mobile data, the relay re-attaches, a tunnel
times out an idle connection, Android freezes the app in a pocket. When it broke, the
turn still ran - the computer never knew the phone had gone - and the owner saw a send
that failed, no answer, and no notification. The conversation looked dead while the
agent was in fact working, and the reply landed in a history nobody was reading.

So the socket is no longer the delivery. Sending is a request that returns at once with
the id of a turn; the turn runs here, on this machine, to its end. The phone asks what
became of it, as many times as it likes, over as many different connections as it
likes. Losing the network now costs a reconnection, not an answer - which is how every
messaging app the owner already uses behaves, and why it never occurs to anyone there
that leaving a chat could lose a message.

Idempotence, which is the whole safety story
--------------------------------------------
A send that gets no answer is ambiguous in exactly one direction: it may have arrived.
These agents DO things - a second copy of "send that email" is not a cosmetic duplicate
- so a retry must not be a second turn. The phone mints a ``client_id`` per composed
message, not per attempt, and this keys on it: the same ``client_id`` always names the
same turn, however many times it is sent. A retry after a dropped connection therefore
RE-ATTACHES to the turn already running instead of starting another one, and the answer
it eventually gets is that turn's.

Order, which a queue has to promise
-----------------------------------
Two messages typed quickly are two turns, and they are run one after another per
conversation. Hermes replays a session's history into every turn, so overlapping turns
on one session would each be answering a prompt the other was still changing; and
beyond correctness, "the second thing I said was answered first" is not a thing a chat
is allowed to do.

In memory and nowhere else
--------------------------
This table holds what was said and what came back. It lives in this process and is
never written to disk: the conversation itself is already on this machine, in Hermes'
own store, and a second copy on the way to becoming a log is not something this feature
needs. Restarting Olivaw forgets every turn - which the phone is built to survive,
because a turn it cannot find is a turn it reads out of the conversation instead.
"""

import collections
import threading
import time

from . import talk

# How long a finished turn is remembered.
#
# This is not where the answer LIVES - it is in the conversation, and the phone can
# always read it there. This is only the window in which the answer can be handed back
# directly, with its errors intact and without re-reading a thread. Half an hour covers
# a phone that lost signal in a lift, a commute underground, or an owner who put it down
# mid-answer and came back to it; past that, reading the conversation is both correct
# and cheap.
DONE_TTL = 1800.0

# A turn that has been running this long with nothing coming back is presumed lost.
#
# `talk.send` has its own timeout and will return an error of its own in the ordinary
# case. This is the backstop for the extraordinary one - a worker thread wedged inside a
# socket that never closes - so that "pensando…" is not a state the phone can be left in
# for ever.
RUNNING_TTL = 3600.0

# Turns kept at once, oldest evicted first. Each holds one message and one reply, so
# this is bounded in megabytes rather than count; a few dozen is far more than the
# number that can be outstanding and enough that a busy morning is still all there.
MAX_TURNS = 96

_lock = threading.Lock()
# Something to wait on, so a phone asking "has anything changed" can be told the moment
# it does rather than on its next poll. One condition for the whole table: the thing
# being waited for is "any turn moved", and the waiter re-reads what it cares about.
_moved = threading.Condition(_lock)
_turns = collections.OrderedDict()
# One lock per conversation, so turns on the same thread run in order while turns on
# different threads do not wait for each other.
_session_locks = {}

_seq = 0


def _next_id():
    """A name for a turn that is unique in this process and tells its order.

    Not random: two turns minted in the same millisecond must still sort, and the phone
    compares ids to decide which of its outstanding sends is older.
    """
    global _seq
    _seq += 1
    return "t%d-%d" % (int(time.time() * 1000), _seq)


def _evict(now):
    """Forget what is past being useful. Called with the lock held."""
    for tid in [t for t, r in _turns.items() if _stale(r, now)]:
        del _turns[tid]
    while len(_turns) > MAX_TURNS:
        _turns.popitem(last=False)


def _stale(rec, now):
    if rec["state"] == "running":
        return (now - rec["started"]) > RUNNING_TTL
    return (now - (rec.get("finished") or rec["started"])) > DONE_TTL


def _public(rec):
    """What the phone is told about a turn.

    The text that was SENT is not in here. The phone wrote it and still has it; echoing
    it back would only be a second copy of the message travelling over the network for
    no reason.
    """
    out = {"id": rec["id"], "state": rec["state"], "session_id": rec["session_id"],
           "client_id": rec["client_id"], "profile": rec["profile"],
           "started": rec["started"]}
    if rec.get("finished"):
        out["finished"] = rec["finished"]
    if rec["state"] == "done":
        out["reply"] = rec.get("reply") or ""
        out["files"] = rec.get("files") or []
        if rec.get("usage"):
            out["usage"] = rec["usage"]
    elif rec["state"] == "failed":
        out["detail"] = rec.get("detail") or "No se pudo enviar."
    return out


def _session_lock(session_id):
    """Called with `_lock` held."""
    got = _session_locks.get(session_id)
    if got is None:
        got = _session_locks[session_id] = threading.Lock()
    return got


def _run(rec, hermes, install_dir):
    """One turn, start to finish, on a thread of its own.

    The conversation's lock is taken HERE rather than before the thread starts, so that
    a second message typed while the first is still being answered returns an id
    immediately and waits its turn in the background - which is what the phone is
    showing the owner.
    """
    with _lock:
        lock = _session_lock(rec["session_id"])
    with lock:
        # Queued behind another turn and cancelled while it waited: nothing to send.
        with _lock:
            if rec["state"] != "running":
                return
        try:
            res = talk.send(rec["profile"], hermes, session_id=rec["session_id"],
                            text=rec["text"], files=rec["files"])
        except Exception as e:  # noqa: BLE001
            # A worker thread that raises takes its turn's answer with it and leaves the
            # phone waiting for ever, so nothing is allowed out of here uncaught.
            res = {"ok": False, "detail": "Falló al enviarlo: %s" % e}
        with _moved:
            if rec["state"] != "running":
                return
            rec["finished"] = time.time()
            if res.get("ok"):
                rec["state"] = "done"
                rec["reply"] = res.get("reply") or ""
                rec["files"] = res.get("files") or []
                rec["usage"] = res.get("usage")
                # The conversation may have been created by this very turn.
                if res.get("session_id"):
                    rec["session_id"] = res["session_id"]
            else:
                rec["state"] = "failed"
                rec["detail"] = res.get("detail") or "No se pudo enviar."
            _moved.notify_all()


def start(profile=None, hermes=None, session_id="", text="", files=None,
          client_id="", install_dir=None):
    """Accept a message and answer with the turn that will carry it.

    Returns at once. Whether the agent takes two seconds or nine minutes is no longer
    the phone's problem, and whether the phone is still connected is no longer the
    turn's.
    """
    sid = str(session_id or "")
    if not talk._safe_session(sid):
        return {"ok": False, "detail": "Conversación no válida."}
    body = (text or "").strip()
    picked = list(files or [])
    if not body and not picked:
        return {"ok": False, "detail": "Escribe algo primero."}
    cid = str(client_id or "")[:64]
    now = time.time()
    with _lock:
        _evict(now)
        if cid:
            # The same message, sent again because the answer to the first attempt was
            # lost on the way back. This is the whole point: it re-attaches rather than
            # repeating anything the agent would act on twice.
            for rec in _turns.values():
                if rec["client_id"] == cid and rec["session_id"] == sid:
                    return {"ok": True, "queued": True, "again": True,
                            "turn": _public(rec)}
        rec = {"id": _next_id(), "client_id": cid, "session_id": sid,
               "profile": profile, "text": body, "files": picked,
               "state": "running", "started": now}
        _turns[rec["id"]] = rec
    threading.Thread(target=_run, args=(rec, hermes, install_dir),
                     name="olivaw-turn", daemon=True).start()
    return {"ok": True, "queued": True, "turn": _public(rec)}


def look(ids=None, session_id="", wait=0.0):
    """What became of these turns.

    With `wait`, this holds the request open until one of them moves or the time runs
    out - so an answer reaches the phone the moment it exists rather than on the next
    poll, without polling every second to achieve it. Holding this open is safe in the
    way holding the SEND open was not: if the connection dies, the phone reconnects and
    asks again, and nothing was riding on the socket.
    """
    want = [str(i) for i in (ids or []) if str(i)]
    sid = str(session_id or "")
    wait = max(0.0, min(float(wait or 0.0), 55.0))
    deadline = time.time() + wait

    def gather():
        out, pending = [], False
        for tid in want:
            rec = _turns.get(tid)
            if rec is None:
                out.append({"id": tid, "state": "unknown"})
                continue
            out.append(_public(rec))
            if rec["state"] == "running":
                pending = True
        if sid and not want:
            for rec in _turns.values():
                if rec["session_id"] == sid:
                    out.append(_public(rec))
                    if rec["state"] == "running":
                        pending = True
        return out, pending

    with _moved:
        _evict(time.time())
        turns, pending = gather()
        # Nothing to wait FOR: either everything already finished, or none of it is
        # ours to wait on. Either way the answer is now.
        while wait and pending:
            left = deadline - time.time()
            if left <= 0:
                break
            _moved.wait(left)
            _evict(time.time())
            turns, pending = gather()
    return {"ok": True, "turns": turns, "now": time.time()}


def drop(ids=None):
    """Forget these turns. The owner discarded them, or has read the answer.

    A turn still running is left alone to finish: the agent is already doing whatever
    was asked, and forgetting the paperwork would not call it back.
    """
    gone = 0
    with _lock:
        for tid in [str(i) for i in (ids or [])]:
            rec = _turns.get(tid)
            if rec is not None and rec["state"] != "running":
                del _turns[tid]
                gone += 1
    return {"ok": True, "forgotten": gone}


def outstanding(profile=None):
    """Turns still running, for whoever wants to know whether to keep the lights on."""
    with _lock:
        return [_public(r) for r in _turns.values()
                if r["state"] == "running"
                and (profile is None or r["profile"] == profile)]


def _reset_for_tests():
    with _lock:
        _turns.clear()
        _session_locks.clear()
