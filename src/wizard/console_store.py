"""
Persistent conversations for the SOS console.

The console used to be fire-and-forget: one question, one answer, and the transcript lived in
the browser's localStorage. That is the wrong shape for support work — the owner comes back
tomorrow, hits the same problem, and has to explain everything again.

So each console conversation is stored on disk AND is backed by a real Claude Code session:
we mint the session id ourselves (`--session-id`) on the first turn and hand it back with
`--resume` on every later turn. The context is therefore genuinely still there on Claude's
side — not a summary we paste back in — exactly like reopening a conversation in Claude Code.

Layout (inside the install dir, mode 0600):
    console/<conv_id>.json   -> {id, session_id, title, created, updated, resumable, turns[]}

Everything written here has already passed through rescue.redact().
"""

import json
import os
import re
import time
import uuid

MAX_CONVERSATIONS = 60      # newest kept; older files are pruned
MAX_TURNS = 40              # per conversation
_ID_RE = re.compile(r"^[a-f0-9]{16}$")
_LEGACY = "rescue-console.jsonl"


def _dir(install_dir):
    path = os.path.join(install_dir, "console")
    try:
        os.makedirs(path, exist_ok=True)
    except Exception:  # noqa: BLE001
        pass
    return path


def _path(install_dir, conv_id):
    """Never trust an id from the browser with a filesystem path."""
    if not _ID_RE.match(str(conv_id or "")):
        return None
    return os.path.join(_dir(install_dir), "%s.json" % conv_id)


def _restrict(path):
    try:
        os.chmod(path, 0o600)
    except Exception:  # noqa: BLE001
        pass


def _write(install_dir, conv):
    path = _path(install_dir, conv.get("id"))
    if not path:
        return False
    conv["turns"] = (conv.get("turns") or [])[-MAX_TURNS:]
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(conv, fh, ensure_ascii=False)
        os.replace(tmp, path)
        _restrict(path)
        return True
    except Exception:  # noqa: BLE001
        try:
            os.unlink(tmp)
        except Exception:  # noqa: BLE001
            pass
        return False


def title_for(question):
    t = re.sub(r"\s+", " ", str(question or "").strip())
    return (t[:64] + "…") if len(t) > 64 else (t or "Conversación sin título")


def create(install_dir, question=""):
    conv = {"id": uuid.uuid4().hex[:16],
            "session_id": str(uuid.uuid4()),   # we choose it, so we can resume it later
            "title": title_for(question),
            "created": time.time(), "updated": time.time(),
            "resumable": True, "turns": []}
    _write(install_dir, conv)
    _prune(install_dir)
    return conv


def load(install_dir, conv_id):
    path = _path(install_dir, conv_id)
    if not path or not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            conv = json.load(fh)
        return conv if isinstance(conv, dict) else None
    except Exception:  # noqa: BLE001
        return None


def append_turn(install_dir, conv_id, turn):
    conv = load(install_dir, conv_id)
    if not conv:
        return None
    conv.setdefault("turns", []).append(turn)
    conv["updated"] = time.time()
    if len(conv["turns"]) == 1 and not (conv.get("title") or "").strip():
        conv["title"] = title_for(turn.get("question"))
    _write(install_dir, conv)
    return conv


def set_fields(install_dir, conv_id, **fields):
    conv = load(install_dir, conv_id)
    if not conv:
        return None
    conv.update(fields)
    conv["updated"] = time.time()
    _write(install_dir, conv)
    return conv


def rename(install_dir, conv_id, title):
    t = re.sub(r"\s+", " ", str(title or "").strip())[:80]
    if not t:
        return {"ok": False, "detail": "Escribe un nombre."}
    conv = set_fields(install_dir, conv_id, title=t)
    return {"ok": bool(conv), "title": t} if conv else {"ok": False,
                                                        "detail": "No encontré esa conversación."}


def delete(install_dir, conv_id):
    path = _path(install_dir, conv_id)
    if not path or not os.path.exists(path):
        return {"ok": False, "detail": "No encontré esa conversación."}
    try:
        os.unlink(path)
        return {"ok": True}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "detail": str(e)}


def _summary(conv):
    turns = conv.get("turns") or []
    last = turns[-1] if turns else {}
    return {"id": conv.get("id"), "title": conv.get("title") or "Sin título",
            "created": conv.get("created"), "updated": conv.get("updated"),
            "turns": len(turns), "resumable": bool(conv.get("resumable")),
            "archived": bool(conv.get("archived")),
            "preview": (last.get("reply") or last.get("question") or "")[:120]}


def _all(install_dir):
    out = []
    d = _dir(install_dir)
    try:
        names = [n for n in os.listdir(d) if n.endswith(".json") and _ID_RE.match(n[:-5])]
    except Exception:  # noqa: BLE001
        names = []
    for n in names:
        conv = load(install_dir, n[:-5])
        if conv and conv.get("id"):
            out.append(conv)
    out.sort(key=lambda c: c.get("updated") or 0, reverse=True)
    return out


def _prune(install_dir):
    for conv in _all(install_dir)[MAX_CONVERSATIONS:]:
        delete(install_dir, conv.get("id"))


def list_conversations(install_dir, limit=40):
    migrate_legacy(install_dir)
    try:
        limit = max(1, min(200, int(limit)))
    except (TypeError, ValueError):
        limit = 40
    return {"ok": True, "conversations": [_summary(c) for c in _all(install_dir)[:limit]]}


def get(install_dir, conv_id):
    conv = load(install_dir, conv_id)
    if not conv:
        return {"ok": False, "detail": "Esa conversación ya no existe."}
    return {"ok": True, "conversation": conv}


def migrate_legacy(install_dir):
    """Fold the old flat rescue-console.jsonl log into one archived conversation.

    Those turns ran with session persistence disabled, so they genuinely cannot be resumed on
    Claude's side — they are kept read-only rather than silently dropped."""
    legacy = os.path.join(install_dir, _LEGACY)
    marker = os.path.join(_dir(install_dir), ".legacy-imported")
    if not os.path.exists(legacy) or os.path.exists(marker):
        return
    turns = []
    try:
        with open(legacy, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except Exception:  # noqa: BLE001
                    continue
                turns.append({"ts": rec.get("ts"), "question": rec.get("question", ""),
                              "mode": rec.get("mode", "diagnose"), "reply": rec.get("reply", ""),
                              "events": rec.get("events") or []})
    except Exception:  # noqa: BLE001
        pass
    try:
        with open(marker, "w", encoding="utf-8") as fh:
            fh.write(str(time.time()))
    except Exception:  # noqa: BLE001
        pass
    if not turns:
        return
    conv = {"id": uuid.uuid4().hex[:16], "session_id": None,
            "title": "Conversaciones anteriores (archivo)",
            "created": turns[0].get("ts") or time.time(),
            "updated": turns[-1].get("ts") or time.time(),
            "resumable": False, "archived": True, "turns": turns[-MAX_TURNS:]}
    _write(install_dir, conv)
