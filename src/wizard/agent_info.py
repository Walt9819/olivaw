r"""What an agent IS, for a screen that has to explain it on a phone.

The console grew up next to these files and can afford to send somebody to a folder. A
phone cannot. So this reads the three things an owner actually asks about an agent -
what it is for, what it knows how to do, and who else it may talk to - and returns them
small enough to cross a 4G connection and plain enough to render without knowing where
any of it lives.

Three places, deliberately named here rather than guessed at by the caller:

* **Its purpose** is ``CLAUDE.md`` in its workspace: the file the owner is told to edit
  in `renaming.py`, and the one that decides what the agent says it is. ``SOUL.md`` sits
  underneath it and belongs to Hermes, not to the owner - it is reported as read-only
  context so a screen can show it without inviting anybody to edit it.
* **Its skills** are folders with a ``SKILL.md`` under ``<hermes>/profiles/<slug>/skills``
  and the machine-wide ``<hermes>/skills``. The frontmatter carries a name and a one-line
  description, which is all a list needs; the body is fetched only when somebody opens one.
* **Who it may talk to** is the intercom roster, plus the skill that tells it HOW - the
  instructions are a skill like any other, so they are read the same way.

Everything here is read-only except `save_purpose`, which writes the one file an owner is
expected to edit.
"""

import io
import os

try:
    from . import agents_registry, talk
except ImportError:  # running from src/ rather than as a package
    from wizard import agents_registry, talk

# Enough to explain, not so much that a phone on mobile data waits for it. A SKILL.md
# longer than this is a reference manual, and the screen says so rather than pretending
# it showed the whole thing.
MAX_BODY = 60000
PEER_SKILL = "hablar-con-otro-agente"


def _hermes_home():
    return agents_registry._hermes_home()      # one resolver, not two


def _profile_dir(profile):
    prof = (profile or "default").strip() or "default"
    if prof == "default":
        return _hermes_home()
    return os.path.join(_hermes_home(), "profiles", prof)


def _read(path, limit=MAX_BODY):
    try:
        with io.open(path, "r", encoding="utf-8", errors="replace") as fh:
            text = fh.read(limit + 1)
    except OSError:
        return None, False
    if len(text) > limit:
        return text[:limit], True
    return text, False


def _frontmatter(text):
    """name and description out of a SKILL.md header, without a YAML parser.

    The header is two or three flat keys and this file ships to machines that may not
    have PyYAML; a dependency for `name:` would be a poor trade. Anything it cannot
    parse falls back to the folder name, which is always right enough to list.
    """
    out = {}
    if not text.startswith("---"):
        return out
    end = text.find("\n---", 3)
    if end < 0:
        return out
    lines = text[3:end].splitlines()
    for i, line in enumerate(lines):
        if ":" not in line or line[:1] in (" ", "	"):
            continue
        k, v = line.split(":", 1)
        k = k.strip()
        if k not in ("name", "description", "version"):
            continue
        v = v.strip().strip('"').strip("'")
        # `description: |` is a block scalar: the text is the indented lines under
        # it, not the pipe. Taking the value verbatim put a lone "|" in the list
        # where a sentence belonged.
        if v in ("|", ">", "|-", ">-", "|+", ">+"):
            body = []
            for nxt in lines[i + 1:]:
                if nxt.strip() and nxt[:1] not in (" ", "	"):
                    break
                body.append(nxt.strip())
            v = " ".join(x for x in body if x).strip()
        if v:
            out[k] = v
    return out


def _skills_in(base, scope):
    rows = []
    try:
        names = sorted(os.listdir(base))
    except OSError:
        return rows
    for name in names:
        path = os.path.join(base, name, "SKILL.md")
        if not os.path.isfile(path):
            continue
        head, _ = _read(path, 4000)
        meta = _frontmatter(head or "")
        rows.append({"id": name, "scope": scope,
                     "name": meta.get("name") or name.replace("-", " "),
                     "description": meta.get("description", ""),
                     "version": meta.get("version", "")})
    return rows


def skills(profile):
    """Its own skills first, then the ones every agent on this machine has.

    An agent that has been given a skill of its own SHADOWS the shared one of the same
    name - that is how Hermes loads them - so listing both would show a skill that is not
    the one in use. The agent's own wins, and the shared copy is dropped.
    """
    own = _skills_in(os.path.join(_profile_dir(profile), "skills"), "agent")
    seen = {r["id"] for r in own}
    shared = [r for r in _skills_in(os.path.join(_hermes_home(), "skills"), "machine")
              if r["id"] not in seen]
    return own + shared


def skill_body(profile, skill_id):
    """The instructions themselves, for one skill, when somebody opens it."""
    sid = os.path.basename((skill_id or "").strip())
    if not sid or sid in (".", ".."):
        return {"ok": False, "detail": u"No sé qué habilidad me pides."}
    for base, scope in ((os.path.join(_profile_dir(profile), "skills"), "agent"),
                        (os.path.join(_hermes_home(), "skills"), "machine")):
        path = os.path.join(base, sid, "SKILL.md")
        if os.path.isfile(path):
            text, cut = _read(path)
            meta = _frontmatter(text or "")
            return {"ok": True, "id": sid, "scope": scope,
                    "name": meta.get("name") or sid.replace("-", " "),
                    "description": meta.get("description", ""),
                    "markdown": text or "", "truncated": cut}
    return {"ok": False, "detail": u"Esa habilidad ya no está."}


def purpose(profile, install_dir=None):
    """What this agent is for, and the file that says so."""
    ws = talk.workspace_for(profile, install_dir)
    path = os.path.join(ws, "CLAUDE.md")
    text, cut = _read(path)
    soul, _ = _read(os.path.join(_profile_dir(profile), "SOUL.md"), 8000)
    return {"ok": True, "path": path, "exists": text is not None,
            "markdown": text or "", "truncated": cut,
            # Hermes' own, underneath the owner's. Shown so the screen can explain where
            # the rest of the behaviour comes from; not editable, because it is not the
            # owner's file to edit.
            "base": soul or "", "editable": True}


def save_purpose(profile, markdown, install_dir=None):
    """Write the one file an owner is expected to edit, and nothing else.

    Refuses an empty body outright. An agent whose CLAUDE.md is blank still answers -
    it falls back to Hermes' own prompt - so the failure is silent and the owner finds
    out when the agent stops sounding like itself.
    """
    text = (markdown or "").strip()
    if not text:
        return {"ok": False, "detail": u"Un propósito vacío dejaría al agente sin el suyo."}
    ws = talk.workspace_for(profile, install_dir)
    try:
        os.makedirs(ws, exist_ok=True)
        path = os.path.join(ws, "CLAUDE.md")
        tmp = path + ".tmp"
        with io.open(tmp, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text if text.endswith("\n") else text + "\n")
        os.replace(tmp, path)
    except OSError as e:
        return {"ok": False, "detail": u"No pude guardarlo: %s" % e}
    # It is read when the agent starts a turn, so the next thing you say to it already
    # uses the new one. Said out loud because "do I have to restart it?" is the first
    # question anybody asks after editing this.
    return {"ok": True, "path": path,
            "detail": u"Guardado. Lo usa desde el siguiente mensaje."}


def peers(profile, install_dir=None, roster=None):
    """Who else this agent may talk to, and the instructions it follows to do it."""
    me = (profile or "default").strip() or "default"
    rows = []
    for a in (roster or []):
        slug = a.get("slug") or a.get("profile")
        if not slug or slug == me:
            continue
        rows.append({"slug": slug, "name": a.get("name") or slug,
                     "reachable": bool(a.get("reachable", True))})
    how = skill_body(profile, PEER_SKILL)
    return {"ok": True, "agents": rows,
            "how": how.get("markdown", "") if how.get("ok") else "",
            "how_name": how.get("name", "") if how.get("ok") else "",
            "can": bool(how.get("ok"))}
