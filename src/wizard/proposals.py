r"""
Proposals: the agent asks before it builds, and remembers the answer.

The nightly routine already reads the day and stores what matters. Reading the day also tells it
what the owner keeps doing by hand — and that is a proposal waiting to happen: a note, a script,
a recurring report, a skill. What it must NOT do is build things nobody asked for, so each idea
is written down as a proposal with a status, and nothing gets built until the owner says yes.

The answer can arrive two ways, and both land in the same file:

  * in the chat ("1", "sí, hazlo", "no") — the next nightly run sees the reply while re-reading
    the day and updates the proposal itself;
  * in the wizard, with the accept/decline buttons — that is what this module is for.

Accumulated answers are the point. A rejected idea is never proposed again, and the pattern
behind a rejection ("no quiere que toque Odoo sin avisar") is worth more than the idea itself:
the routine distils it into _Aprendizaje.md, which it reads before proposing anything new. That
is how this gets personal instead of merely helpful.

One markdown file per proposal, inside the vault. No database: the owner can read, edit or
delete them in Obsidian, and the agent reads them back with grep.
"""

import io
import os
import re
import time

from . import selfcare

# 20260822-informe-semanal-ventas — date prefix so they sort, slug so they are recognisable.
# This is also the only thing that ever reaches a filesystem path, hence the strict shape.
ID_RE = re.compile(r"^[0-9]{8}-[a-z0-9][a-z0-9._-]{0,60}$")

STATES = ("pendiente", "aceptada", "rechazada", "hecha", "descartada")
OPEN_STATES = ("pendiente",)

PROPOSALS_DIR = "proposals"
LEARNING_FILE = "_Aprendizaje.md"

# Field names as the routine writes them (Spanish, like the rest of the vault).
_FIELDS = ("titulo", "estado", "categoria", "esfuerzo", "propuesta", "decidida", "por_que",
           "beneficio", "reversible")


def _mem_dir():
    """Where the agent keeps its own notes: <vault>/90-Agent, or the workspace fallback."""
    ws = selfcare.workspace_dir()
    vault = selfcare.vault_dir(ws)
    return os.path.join(vault, selfcare.AGENT_DIR) if vault else os.path.join(ws, "agent-memory")


def dir_path():
    return os.path.join(_mem_dir(), PROPOSALS_DIR)


def _parse(text):
    """Read the frontmatter block. Deliberately forgiving: this file is also hand-editable, and a
    typo in one field must not hide the proposal."""
    meta, body = {}, text
    m = re.match(r"^---\s*\r?\n(.*?)\r?\n---\s*\r?\n?(.*)$", text, re.S)
    if m:
        body = m.group(2)
        for line in m.group(1).splitlines():
            mm = re.match(r"^\s*([A-Za-z_][\w-]*)\s*:\s*(.*?)\s*$", line)
            if mm:
                meta[mm.group(1).strip().lower()] = mm.group(2).strip().strip('"').strip("'")
    return meta, body


def _title_from_body(body):
    for line in body.splitlines():
        line = line.strip()
        if line.startswith("#"):
            return line.lstrip("#").strip()
        if line:
            return line[:120]
    return ""


def _read(path):
    with io.open(path, encoding="utf-8", errors="replace") as fh:
        return fh.read()


def _load(pid, path):
    meta, body = _parse(_read(path))
    estado = (meta.get("estado") or "pendiente").lower()
    if estado not in STATES:
        estado = "pendiente"
    return {
        "id": pid,
        "title": meta.get("titulo") or _title_from_body(body) or pid,
        "state": estado,
        "category": meta.get("categoria", ""),
        "effort": meta.get("esfuerzo", ""),
        "proposed": meta.get("propuesta", ""),
        "decided": meta.get("decidida", ""),
        "why": meta.get("por_que", "") or meta.get("beneficio", ""),
        "reversible": meta.get("reversible", ""),
        "body": body.strip()[:4000],
    }


def _path_for(pid):
    if not ID_RE.match(pid or ""):
        return ""
    d = dir_path()
    p = os.path.join(d, pid if pid.endswith(".md") else pid + ".md")
    # Belt and braces: the regex already forbids separators, but the proposal id is owner-supplied
    # input reaching a path, so confirm it did not escape the directory.
    if os.path.normcase(os.path.dirname(os.path.abspath(p))) != os.path.normcase(
            os.path.abspath(d)):
        return ""
    return p


def learning():
    """What the agent has concluded from past answers — the part that makes this personal."""
    p = os.path.join(dir_path(), LEARNING_FILE)
    try:
        return _read(p).strip()
    except Exception:  # noqa: BLE001
        return ""


def listing(limit=40):
    d = dir_path()
    items = []
    try:
        names = sorted(os.listdir(d), reverse=True)
    except Exception:  # noqa: BLE001
        names = []
    for name in names:
        if not name.lower().endswith(".md") or name.startswith("_"):
            continue
        pid = name[:-3]
        if not ID_RE.match(pid):
            continue
        try:
            items.append(_load(pid, os.path.join(d, name)))
        except Exception:  # noqa: BLE001
            continue
    order = {"pendiente": 0, "aceptada": 1, "hecha": 2, "rechazada": 3, "descartada": 4}
    items.sort(key=lambda it: (order.get(it["state"], 9), it["id"]), reverse=False)
    tally = {}
    for it in items:
        tally[it["state"]] = tally.get(it["state"], 0) + 1
    return {"ok": True, "dir": d, "exists": os.path.isdir(d), "proposals": items[:limit],
            "tally": tally, "pending": [i for i in items if i["state"] in OPEN_STATES],
            "learning": learning()[:4000]}


def _stamp_frontmatter(text, updates, note=None):
    """Rewrite only the fields we own, keeping everything else (and the owner's edits) intact."""
    m = re.match(r"^(---\s*\r?\n)(.*?)(\r?\n---\s*\r?\n?)(.*)$", text, re.S)
    if not m:
        head = "---\n" + "\n".join("%s: %s" % kv for kv in updates.items()) + "\n---\n\n"
        return head + text + (("\n\n" + note) if note else "\n")
    lines = m.group(2).splitlines()
    left = dict(updates)
    out = []
    for line in lines:
        mm = re.match(r"^\s*([A-Za-z_][\w-]*)\s*:", line)
        key = mm.group(1).strip().lower() if mm else ""
        if key in left:
            out.append("%s: %s" % (key, left.pop(key)))
        else:
            out.append(line)
    out += ["%s: %s" % kv for kv in left.items()]
    body = m.group(4)
    if note:
        body = body.rstrip() + "\n\n" + note + "\n"
    return m.group(1) + "\n".join(out) + m.group(3) + body


def decide(pid, state, comment=""):
    """Answer a proposal. The agent reads this back on its next run: an accepted one gets built,
    a rejected one is never proposed again, and the comment is the reason it learns from."""
    state = (state or "").strip().lower()
    if state not in ("aceptada", "rechazada", "pendiente"):
        return {"ok": False, "detail": "Respuesta no válida."}
    path = _path_for(pid)
    if not path or not os.path.isfile(path):
        return {"ok": False, "detail": "No encontré esa propuesta."}
    today = time.strftime("%Y-%m-%d")
    comment = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", str(comment or ""))[:1200].strip()
    note = None
    if comment or state != "pendiente":
        verdict = {"aceptada": "✅ Aceptada", "rechazada": "❌ Rechazada",
                   "pendiente": "↩︎ Reabierta"}[state]
        note = ("## Respuesta del dueño (%s)\n\n%s%s" %
                (today, verdict, ("\n\n> " + comment.replace("\n", "\n> ")) if comment else ""))
    try:
        text = _read(path)
        updates = {"estado": state, "decidida": today if state != "pendiente" else ""}
        with io.open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write(_stamp_frontmatter(text, updates, note))
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "detail": "No pude escribir la propuesta: %s" % e}
    return {"ok": True, "id": pid, "state": state, **listing()}
