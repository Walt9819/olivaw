r"""The part of a conversation Hermes keeps but will not hand back.

What was wrong
--------------
Scrolling up in a long conversation stopped, and not at the beginning.

Hermes compacts a conversation when it crosses a fraction of the model's window -
``compression.threshold``, 10% here, which on a 1M window is about every hundred
thousand tokens. In the mode this machine runs (``compression.in_place``), compaction
is deliberately NON-DESTRUCTIVE: the turns it summarises are soft-archived rather than
deleted - ``active = 0``, still on disk, still searchable, explicitly recoverable - and
the summary plus the protected head and tail become the new live set. That is a good
design and the comment in Hermes' own source says why: one durable session id, history
kept rather than destroyed.

The problem is the door. ``GET /api/sessions/{id}/messages`` calls ``get_messages()``
with no arguments, and that defaults to ``active = 1``. There is no query parameter, no
second route, nothing: through the HTTP API the archived turns do not exist. So the
phone, which reads the conversation through exactly that route, could only ever see
back as far as the last compaction - and because ``protect_first_n`` keeps the opening
turns live, what it actually drew was the first three messages, then a silent hole,
then the recent ones, with "there is more above" switched off. On this machine, at the
time this was written, that was 46,513 archived messages against 13,788 live ones:
three quarters of every long conversation, unreachable and unmentioned.

So this reads them from where Hermes put them.

Why reading the file directly is acceptable here, and how it is kept honest
---------------------------------------------------------------------------
It is the same machine, the same user, and a database this process can already reach -
Olivaw starts the gateway that owns it. SQLite in WAL mode (``state.db-wal`` is sitting
next to it) takes concurrent readers without blocking the writer, and this opens the
file ``mode=ro`` so it cannot write to it even by accident.

What it must never do is make the conversation WORSE than the API's answer, so every
failure here is silent and falls back: no database, a schema that is not the one we
know, a locked file, a session that is not in it - all return None, and `talk.history`
goes on asking Hermes exactly as it did before. This is additive or it is nothing.
"""

import os
import sqlite3

# Known in the schema this was written against. Checked before anything is read,
# because a Hermes that has moved its columns should fall back to the API rather than
# produce a conversation with the wrong shape.
NEEDED = ("id", "session_id", "role", "content", "tool_calls", "timestamp", "active")

# What the API hands back for each row, and therefore what `_dress` expects to find.
CARRIED = ("id", "session_id", "role", "content", "tool_call_id", "tool_calls",
           "tool_name", "timestamp", "token_count", "finish_reason", "reasoning",
           "reasoning_content")

# How far back a chain of parent sessions is followed.
#
# In the OTHER compaction mode Hermes ends the session and starts a child carrying
# `parent_session_id`, so the conversation the owner thinks of as one thing is a chain
# of ids. Bounded because a cycle in that column - however it got there - must not be
# an infinite loop inside a phone's request.
MAX_ANCESTRY = 40

# Rows read at most, over the whole chain. A conversation of six thousand turns is
# real on this machine; a million-row read inside a phone request is not.
MAX_ROWS = 20000


def db_path(hermes_home):
    return os.path.join(hermes_home, "state.db")


def _open(path):
    """Read-only, and never create. Returns None rather than raising."""
    if not path or not os.path.isfile(path):
        return None
    try:
        # file: URI so `mode=ro` is honoured - a plain path would CREATE an empty
        # database if the real one were ever missing, and then quietly answer every
        # question with "this conversation has nothing in it".
        uri = "file:%s?mode=ro" % path.replace("\\", "/").replace("?", "%3f")
        con = sqlite3.connect(uri, uri=True, timeout=2.0)
        return con
    except sqlite3.Error:
        return None


def _columns(con):
    """The columns to read, or None if this is not the schema we know.

    Named rather than `SELECT *` for a reason that showed up on a clock: the table also
    holds `reasoning`, `reasoning_details`, `codex_reasoning_items` and
    `codex_message_items`, which are the largest things in it by a wide margin and are
    thrown away a line later. A deep search reads two hundred and forty conversations,
    so pulling them was most of what the search spent its time on.
    """
    try:
        cols = {r[1] for r in con.execute("PRAGMA table_info(messages)")}
    except sqlite3.Error:
        return None
    if not all(c in cols for c in NEEDED):
        return None
    return [c for c in CARRIED if c in cols]


def _ancestry(con, session_id):
    """This conversation and every session it grew out of, oldest first.

    Only relevant where Hermes rotates rather than compacting in place, and harmless
    where it does not: a session with no parent is a chain of one.
    """
    chain, seen, sid = [], set(), str(session_id or "")
    try:
        cols = {r[1] for r in con.execute("PRAGMA table_info(sessions)")}
    except sqlite3.Error:
        return [sid] if sid else []
    while sid and sid not in seen and len(chain) < MAX_ANCESTRY:
        seen.add(sid)
        chain.append(sid)
        if "parent_session_id" not in cols:
            break
        try:
            row = con.execute(
                "SELECT parent_session_id FROM sessions WHERE id = ?", (sid,)).fetchone()
        except sqlite3.Error:
            break
        sid = (row[0] if row else None) or ""
    chain.reverse()
    return chain


def messages(session_id, hermes_home=None, include_archived=True, speech_only=False):
    """Every stored row of a conversation, archived turns included, oldest first.

    Shaped exactly as the HTTP route shapes them, so everything downstream - the speech
    filter, `_dress`, the windowing - is unchanged and does not need to know where the
    rows came from.

    `speech_only` leaves the tool calls and the empty rows in the database rather than
    carrying them across a thread boundary to be dropped a moment later. For SEARCH,
    which opens hundreds of conversations at once, that is most of the work: a
    conversation of six thousand rows is a couple of hundred things anybody said, and
    the rest is the agent using its tools.

    Returns None when this cannot be answered, which is the caller's signal to ask
    Hermes the ordinary way.
    """
    if not session_id:
        return None
    con = _open(db_path(hermes_home or ""))
    if con is None:
        return None
    try:
        cols = _columns(con)
        if not cols:
            return None
        picked = ", ".join(cols)
        chain = _ancestry(con, session_id)
        if not chain:
            return None
        where = "" if include_archived else " AND active = 1"
        if speech_only:
            # The same cut `_is_speech` makes, made in SQL. Deliberately looser than
            # that function - it also drops compaction markers and rows whose content
            # is only whitespace - because this is a cheap pre-filter and the real
            # filter still runs afterwards. Looser here can only cost a little work;
            # tighter here could lose a message.
            where += (" AND role IN ('user', 'assistant') AND tool_calls IS NULL"
                      " AND content IS NOT NULL AND content <> ''")
        out = []
        for sid in chain:
            try:
                rows = con.execute(
                    "SELECT %s FROM messages WHERE session_id = ?%s ORDER BY id"
                    " LIMIT ?" % (picked, where),
                    (sid, MAX_ROWS - len(out))).fetchall()
            except sqlite3.Error:
                return None
            for r in rows:
                out.append(dict(zip(cols, r)))
            if len(out) >= MAX_ROWS:
                break
        # A session Hermes knows nothing about is not an empty conversation - it is a
        # question this cannot answer, and answering it "empty" would replace a real
        # transcript with a blank screen.
        return out or None
    except sqlite3.Error:
        return None
    finally:
        try:
            con.close()
        except sqlite3.Error:
            pass
