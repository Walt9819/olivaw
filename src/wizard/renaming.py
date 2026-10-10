# -*- coding: utf-8 -*-
"""Renaming an agent, everywhere it is called something.

A name is not a label on a card here. The same agent carries its name in four places,
written at four different moments by four different things, and changing one of them
produces an agent that introduces itself as Daneel on Telegram while the sidebar, the
other agents and its own notes call it something else:

1. ``agents.json`` - what the console lists.
2. ``intercom.json``'s ``names`` map - what the OTHER agents call it, and what every
   generated skill says. This one already had an editor ("teams/name").
3. ``TELEGRAM_HOME_CHANNEL_NAME`` in its Hermes ``.env`` - the name it signs messages
   with when it starts a conversation on its own.
4. **Its own prompt**, ``CLAUDE.md`` in its workspace - the one that decides what it says
   when somebody asks it who it is. Without this, the rename is cosmetic: the agent keeps
   believing it is called the old thing, because that is what its instructions say.

(4) is the careful one. That file is the agent's, not ours: it has been edited by hand and
by the agent itself since setup, so it cannot be regenerated. So the name is maintained in
a marker-delimited block - the same approach ``wa_patch`` and ``cron_patch`` take with
Hermes' source - which is rewritten in place on every rename and inserted once if absent.
The generated heading is retitled too when it still matches the shape ``build_claude_md``
produced, which is the common case and the one the agent reads first.

The old name is kept in the block on purpose. An agent that has been running for months
has memories, notes and vault pages full of the old name, and the sentence that says "that
was me" costs two lines and saves it from treating its own history as somebody else's.
"""

import io
import os
import re

from . import agents_registry

try:
    from . import hermes_ctl
except Exception:  # noqa: BLE001
    hermes_ctl = None


def _intercom():
    """Imported on use, not on import.

    ``intercom`` lives in ``src/``, one level up from this package, and is only importable
    after the host has put ``src`` on sys.path - which wizard_server does deliberately
    late (see the note above its own import). A module-level import here would therefore
    work when the console imports it and fail when a test does.
    """
    import intercom          # noqa: PLC0415
    return intercom


MAX_NAME = 40
BEGIN = u"<!-- olivaw:name -->"
END = u"<!-- /olivaw:name -->"

# "# Eres el cerebro de X — un agente Hermes", as build_claude_md writes it.
_H1 = re.compile(r"^#\s+Eres el cerebro de\s+.+?$", re.M)
# "...un agente Hermes llamado **X**, al que llega **owner** como dueño..."
_CALLED = re.compile(r"(un agente Hermes llamado\s+\*\*)(.+?)(\*\*)")


def clean_name(value):
    """What a name is allowed to be. Control characters are the only real refusal."""
    name = u" ".join((value or u"").split())
    name = u"".join(ch for ch in name if ord(ch) >= 32 and ch != u"\u007f")
    if not name:
        raise ValueError(u"El nombre no puede quedar vacío.")
    if len(name) > MAX_NAME:
        name = name[:MAX_NAME].rstrip()
    return name


def block(new, old=""):
    body = [BEGIN,
            u"## Cómo te llamas",
            u"Te llamas **%s**. Es el nombre con el que te conoce tu dueño, con el que te "
            u"presentas y con el que te llaman los demás agentes de esta casa." % new]
    if old and old != new:
        body.append(
            u"\nAntes te llamabas «%s». Si encuentras ese nombre en tus recuerdos, en tus "
            u"notas o en conversaciones viejas, se refiere a ti: no es otra persona ni otro "
            u"agente." % old)
    body.append(END)
    return u"\n".join(body) + u"\n"


def rewrite_prompt(path, new, old=""):
    """Put the current name into CLAUDE.md without destroying what is around it.

    Returns a short verdict: "written", "created", "skipped" (no workspace / no file) or
    "failed: …". Never raises - a rename whose prompt could not be touched is still a
    rename worth finishing, and the caller reports exactly which parts landed.
    """
    if not path:
        return "skipped"
    try:
        if os.path.exists(path):
            with io.open(path, encoding="utf-8-sig", errors="replace") as fh:
                text = fh.read()
            created = False
        else:
            # An agent whose workspace has no CLAUDE.md yet is not an error: it is one that
            # was never given a prompt. It gets the block and nothing else - inventing a
            # whole persona here would overwrite the setup wizard's job.
            text, created = u"", True

        new_block = block(new, old)
        if BEGIN in text and END in text:
            i, j = text.index(BEGIN), text.index(END) + len(END)
            text = text[:i] + new_block.rstrip(u"\n") + text[j:]
        else:
            text = (text.rstrip(u"\n") + u"\n\n" + new_block) if text.strip() else new_block

        # The generated identity lines, when they still look generated.
        text = _H1.sub(u"# Eres el cerebro de %s — un agente Hermes" % new, text, count=1)
        text = _CALLED.sub(lambda m: m.group(1) + new + m.group(3), text, count=1)

        tmp = path + ".tmp"
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with io.open(tmp, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        os.replace(tmp, path)
        return "created" if created else "written"
    except Exception as e:  # noqa: BLE001
        return "failed: %s" % e


SLUG_RE = re.compile(r"^[a-z0-9]{1,24}$")


def _agent(slug, install_dir=None):
    """(record, profile, workspace, is_default) for an agent that exists, else ValueError."""
    slug = (slug or "").strip().lower()
    if not SLUG_RE.match(slug):
        raise ValueError(u"«%s» no es un agente válido." % slug)
    if slug == "default":
        return None, "default", default_workspace(), True
    rec = agents_registry.get(slug, install_dir)
    if not rec:
        raise ValueError(u"En este equipo no hay ningún agente «%s»." % slug)
    ws = rec.get("workspace") or os.path.join(
        agents_registry.agent_dir(slug, install_dir), "workspace")
    return rec, (rec.get("profile") or slug), ws, False


def default_workspace():
    """Where the main agent's prompt lives.

    The main agent has no row in agents.json - it is the one Hermes came with - so its
    workspace is the bridge's. Same resolution the console's own snapshot uses, so the
    path shown on the agent's page is the file this rewrites.
    """
    return os.environ.get("CLAUDE_BRIDGE_WORKSPACE",
                          os.path.join(os.path.expanduser("~"), "hermes-workspace"))


def current_name(slug, install_dir=None):
    a = _intercom().find(slug, install_dir)
    return (a or {}).get("name") or slug


def rename(slug, name, install_dir=None, hermes=None, workspace=None):
    """Rename one agent in all four places. Returns what actually changed.

    Order matters: the registry and intercom first (cheap, local, and what the console
    repaints from), then the prompt, then the .env. If the .env write fails - Hermes not
    installed, profile gone - the rename still holds everywhere else, and the reply says
    which part did not land rather than claiming a clean success.
    """
    slug = (slug or "").strip().lower()
    new = clean_name(name)
    rec, profile, ws, is_default = _agent(slug, install_dir)
    old = current_name(slug, install_dir)
    if workspace:
        ws = workspace
    done, failed = [], []

    if rec is not None and (rec.get("name") or "") != new:
        rec["name"] = new
        agents_registry.upsert(rec, install_dir)
        done.append("registro")

    ic = _intercom()
    names = dict(ic.config().get("names") or {})
    if names.get(slug) != new:
        names[slug] = new
        wrote = ic.save_config({"names": names})
        (done if wrote.get("ok") else failed).append("nombre entre agentes")

    verdict = rewrite_prompt(os.path.join(ws, "CLAUDE.md") if ws else "", new, old)
    if verdict in ("written", "created"):
        done.append("sus instrucciones")
    elif verdict.startswith("failed"):
        failed.append("sus instrucciones")

    if hermes_ctl is not None:
        try:
            r = hermes_ctl.set_env_vars(
                {"TELEGRAM_HOME_CHANNEL_NAME": new[:64]}, hermes,
                None if is_default else profile)
            (done if r.get("ok") else failed).append("cómo firma en Telegram")
        except Exception:  # noqa: BLE001
            failed.append("cómo firma en Telegram")

    return {"ok": True, "slug": slug, "name": new, "old": old,
            "profile": profile, "is_default": is_default,
            "changed": done, "failed": failed,
            "prompt": verdict, "workspace": ws}
