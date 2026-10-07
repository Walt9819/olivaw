r"""Olivaw reads the team and proposes the wiring. It never applies it.

`teams.py` knows how to store "who may call whom, and what each one should be told". It has
no idea what any of these agents actually *are*, so the owner is handed an empty form with
six names on it and asked to write twelve sentences. On this machine she wrote one and
reused it for everything, which is how the finance agent ended up with "cuestiones de
código y detalles técnicos" as its reason to call the developer - and the developer with
the same sentence as its reason to call the accountant.

The machine already knows what each agent is. Every one of them was created through the
wizard, which wrote its purpose, its business context and its starting instructions into
`<workspace>/CLAUDE.md`. This module gathers that, asks the MAIN agent to turn it into a
proposed org chart, and hands the result back for the owner to approve line by line.

WHY THE MAIN AGENT AND NOT A TEMPLATE
-------------------------------------
Because "Ábaco lleva las finanzas, Forja escribe el código, so Ábaco writes to Forja when a
tool needs building and Forja writes to Ábaco when something costs money" is a judgement
about meaning, not a pattern over strings. The main agent is the one that already knows
this team - it has the memory, it has read the files, and it is the agent the owner talks
to anyway. Asking it is also the half of the feature the owner asked for in her own words:
"with olivaw, preset the configuration on how they can call themselves".

WHAT COMES BACK IS DATA, NOT A DECISION
---------------------------------------
Three things keep a proposal from being a back door into the permission file:

  * **Nothing here writes.** `suggest()` returns a dict. `apply_proposal()` exists, takes
    only what the owner ticked, and is called by the console in response to her click.
  * **Every slug is checked against the live roster.** An invented or misspelled agent is
    dropped, not created. Links to agents that do not exist on this machine cannot appear.
  * **Every sentence goes through teams._clip** at the same limits as a typed one, so a
    model that returns a wall of text cannot bloat the file the gate reads on every call.

The reply is also parsed as JSON and nothing else: no eval, no regex scraping of prose. If
the agent answered with an essay, that is a failed suggestion, not a half-applied one.
"""

import json
import os
import re
import subprocess

import intercom
import teams
from winspawn import quiet

MAX_DOSSIER = 700          # characters of CLAUDE.md per agent in the prompt
MAX_PROMPT = 14000         # the whole prompt; -z takes it as one argument
TIMEOUT = 280              # Hermes kills a terminal command at 300s
SESSION = "olivaw-equipo-mapa"


# ── what we know about each agent, before asking anybody ─────────────────────
def _install_dir(install_dir=None):
    return install_dir or intercom.INSTALL_DIR


def default_workspace(install_dir=None):
    """The main agent's working folder, which is where its CLAUDE.md lives.

    Written by the installer into updater.config.json; the environment variable is the
    same value when the supervisor is the one asking. Both are checked because this module
    runs in the wizard, which does not inherit the bridge's environment.
    """
    env = (os.environ.get("CLAUDE_BRIDGE_WORKSPACE") or "").strip()
    if env:
        return env
    try:
        path = os.path.join(_install_dir(install_dir), "updater.config.json")
        with open(path, encoding="utf-8") as fh:
            cfg = json.load(fh) or {}
        ws = ((cfg.get("env") or {}).get("CLAUDE_BRIDGE_WORKSPACE") or "").strip()
        if ws:
            return ws
    except (OSError, ValueError, AttributeError):
        pass
    return os.path.join(os.path.expanduser("~"), "hermes-workspace")


def _workspaces(install_dir=None):
    out = {"default": default_workspace(install_dir)}
    try:
        path = os.path.join(_install_dir(install_dir), "agents.json")
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh) or {}
    except (OSError, ValueError):
        return out
    for a in (data.get("agents") or []):
        slug = (a.get("slug") or "").strip().lower()
        if slug and a.get("workspace"):
            out[slug] = a["workspace"]
    return out


def _persona(workspace):
    """The "who you are" part of an agent's CLAUDE.md, trimmed to something quotable.

    Only the opening sections: the identity, the purpose and the business context. The rest
    of that file is the shared working style and the owner lock, which is identical for
    every agent and would just fill the prompt with the same paragraph six times.
    """
    if not workspace:
        return ""
    path = os.path.join(workspace, "CLAUDE.md")
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            raw = fh.read(12000)
    except OSError:
        return ""
    stop = raw.find("## Regla de dueño")
    if stop > 0:
        raw = raw[:stop]
    # Drop the markdown headings; what is wanted is the sentences under them.
    lines = []
    for line in raw.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        lines.append(re.sub(r"\*\*(.+?)\*\*", r"\1", s))
    return teams._clip(" ".join(lines), MAX_DOSSIER)


def dossier(roster=None, install_dir=None):
    """Everything this machine already knows about each agent, in one list."""
    install_dir = _install_dir(install_dir)
    people = roster or intercom.roster(install_dir)
    status, data = teams.read(install_dir)
    cards = data.get("cards") or {}
    ws = _workspaces(install_dir)
    out = []
    for a in people:
        slug = a["slug"]
        card = cards.get(slug) or {}
        out.append({
            "slug": slug,
            "name": a.get("name") or slug,
            "role": card.get("role", ""),
            "description": card.get("description", ""),
            "never": card.get("never", ""),
            "workspace": ws.get(slug, ""),
            "persona": _persona(ws.get(slug, "")),
        })
    return out


# ── the question ─────────────────────────────────────────────────────────────
def build_prompt(people, links, focus="", install_dir=None):
    """What the main agent is asked. Deliberately a form to fill in, not a conversation."""
    focus = (focus or "").strip().lower()
    who = []
    for a in people:
        bits = ["### %s  (slug: `%s`)" % (a["name"], a["slug"])]
        if a["role"]:
            bits.append("- Ficha actual: %s" % a["role"])
        if a["description"]:
            bits.append("- Tiene a mano: %s" % a["description"])
        if a["never"]:
            bits.append("- No debe: %s" % a["never"])
        if a["persona"]:
            bits.append("- Sus instrucciones dicen: %s" % a["persona"])
        if len(bits) == 1:
            bits.append("- (no hay ficha ni instrucciones escritas: dedúcelo del nombre)")
        who.append("\n".join(bits))

    now = []
    for ln in links:
        now.append("- %s %s %s%s" % (
            ln["from"], "<->" if ln["both"] else "->", ln["to"],
            ("   ida: «%s»" % ln["why"]) if ln["why"] else "   (sin instrucción)")
            + (("   vuelta: «%s»" % ln["why_back"]) if ln.get("why_back") else ""))
    slugs = ", ".join("`%s`" % a["slug"] for a in people)

    head = ("Eres el agente principal de este equipo. Tu dueño te pide que **propongas** "
            "cómo deberían comunicarse entre sí los agentes de esta máquina.")
    if focus:
        head += (" En particular le interesa **`%s`**: céntrate en sus enlaces, aunque "
                 "puedes proponer ficha para los demás si falta." % focus)
    return "\n".join([
        head,
        "",
        "No estás cambiando nada: esto es una propuesta que él revisa y acepta o descarta "
        "en Olivaw, enlace por enlace.",
        "",
        "## Los agentes de este equipo",
        "",
        "\n\n".join(who),
        "",
        "## Cómo están conectados ahora",
        "",
        ("\n".join(now) if now else "- (todavía no hay ningún enlace)"),
        "",
        "## Qué tienes que devolver",
        "",
        "SÓLO un objeto JSON, sin texto antes ni después, sin ```. Esta forma exacta:",
        "",
        '{"cards": {"<slug>": {"role": "...", "description": "...", "never": "..."}},',
        ' "links": [{"from": "<slug>", "to": "<slug>", "both": true,',
        '            "why": "...", "why_back": "..."}],',
        ' "note": "una frase para el dueño"}',
        "",
        "Reglas:",
        "- Usa sólo estos slugs: %s. No inventes ninguno." % slugs,
        "- `role`: de qué se encarga ese agente, una línea, máximo 120 caracteres.",
        "- `description`: qué sabe y qué tiene a mano, máximo 600 caracteres.",
        "- `never`: qué no deberían pedirle los demás. Déjalo vacío si no aplica.",
        "- **`why` y `why_back` son instrucciones DISTINTAS y van en sentidos opuestos.**",
        "  `why` es lo que lee «from» para saber *cuándo* escribirle a «to».",
        "  `why_back` es lo que lee «to» para saber *cuándo* escribirle a «from».",
        "  Nunca repitas la misma frase en las dos: si no se te ocurre una razón real para "
        "  el sentido de vuelta, pon `both: false` y deja el enlace en un solo sentido.",
        "- Empieza cada frase con «Para…» o «Cuando…», en segunda persona, concreta: "
        "  «Para preguntar el precio de un tratamiento antes de confirmárselo al paciente».",
        "- No propongas enlaces de un agente consigo mismo.",
        "- Menos es más: propón los enlaces que de verdad hacen falta para el trabajo de "
        "  cada uno, no todos con todos.",
        "- Si un enlace actual ya está bien, inclúyelo igual con sus frases corregidas.",
        "",
        "Responde ahora con el JSON y nada más.",
    ])[:MAX_PROMPT]


# ── asking ───────────────────────────────────────────────────────────────────
def _run(argv, timeout):
    p = subprocess.run(argv, **quiet(capture_output=True, timeout=timeout))
    return (p.returncode,
            (p.stdout or b"").decode("utf-8", "replace"),
            (p.stderr or b"").decode("utf-8", "replace"))


def suggest(roster=None, install_dir=None, focus="", timeout=None, runner=None):
    """Ask the main agent. Returns a proposal; writes nothing.

    `runner` is injected by the tests so the whole path - prompt, parsing, validation - can
    be exercised without a 90-second turn of a real agent.
    """
    install_dir = _install_dir(install_dir)
    people = roster or intercom.roster(install_dir)
    if len(people) < 2:
        return {"ok": False, "detail": "Sólo hay un agente en este equipo: no hay nada "
                                       "que conectar todavía."}
    docs = dossier(people, install_dir)
    status, data = teams.read(install_dir)
    prompt = build_prompt(docs, data.get("links") or [], focus=focus,
                          install_dir=install_dir)

    base = intercom._base("default")
    if not base:
        return {"ok": False, "detail": "No encontré cómo ejecutar al agente principal en "
                                       "este equipo."}
    run = runner or _run
    try:
        code, out, err = run(base + ["-z", prompt, "-c", SESSION], timeout or TIMEOUT)
    except subprocess.TimeoutExpired:
        return {"ok": False, "detail": "El agente principal no contestó a tiempo. Vuelve a "
                                       "intentarlo, o escribe las frases tú mismo."}
    except OSError as e:
        return {"ok": False, "detail": "No pude preguntarle al agente principal: %s" % e}
    if code != 0 and not (out or "").strip():
        return {"ok": False, "detail": "El agente principal falló: %s"
                                       % ((err or "sin salida").strip()[:300])}

    prop = parse(out, [a["slug"] for a in people])
    if not prop["ok"]:
        return dict(prop, raw=(out or "").strip()[:1200])
    prop["focus"] = (focus or "").strip().lower()
    return prop


# ── reading the answer ───────────────────────────────────────────────────────
def _json_block(raw):
    """The JSON object in a reply that may also contain a sentence or a code fence."""
    raw = (raw or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```[a-zA-Z]*\s*", "", raw)
        raw = re.sub(r"\s*```\s*$", "", raw)
    a, b = raw.find("{"), raw.rfind("}")
    if a < 0 or b <= a:
        return None
    try:
        return json.loads(raw[a:b + 1])
    except ValueError:
        return None


def parse(raw, slugs):
    """Turn a reply into a proposal, dropping everything that is not about this machine.

    Validation is not politeness here. This text was produced by a language model reading
    files; the only reason it is safe to show as a list of one-click buttons is that
    nothing survives this function except known slugs and clipped sentences.
    """
    known = set(s for s in (slugs or []))
    data = _json_block(raw)
    if not isinstance(data, dict):
        return {"ok": False, "detail": "El agente principal no contestó con un JSON que "
                                       "pueda leer. Puedes intentarlo otra vez."}
    cards, seen = [], set()
    raw_cards = data.get("cards")
    for slug, c in (raw_cards.items() if isinstance(raw_cards, dict) else []):
        slug = (slug or "").strip().lower()
        if slug not in known or slug in seen or not isinstance(c, dict):
            continue
        seen.add(slug)
        card = {"slug": slug,
                "role": teams._clip(c.get("role"), teams.MAX_ROLE),
                "description": teams._clip(c.get("description"), teams.MAX_DESC),
                "never": teams._clip(c.get("never"), teams.MAX_WHY)}
        if card["role"] or card["description"] or card["never"]:
            cards.append(card)

    links, pairs = [], set()
    raw_links = data.get("links")
    for ln in (raw_links if isinstance(raw_links, list) else [])[:teams.MAX_LINKS]:
        if not isinstance(ln, dict):
            continue
        frm = (ln.get("from") or "").strip().lower()
        to = (ln.get("to") or "").strip().lower()
        if frm not in known or to not in known or frm == to:
            continue
        key = tuple(sorted([frm, to]))
        if key in pairs:
            continue
        pairs.add(key)
        both = bool(ln.get("both"))
        why = teams._clip(ln.get("why"), teams.MAX_WHY)
        back = teams._clip(ln.get("why_back"), teams.MAX_WHY) if both else ""
        # The one thing the prompt asks for twice and models still get wrong. A repeated
        # sentence is the bug this feature exists to fix, so it does not get stored as if
        # it were two answers.
        if back and back.lower() == why.lower():
            back = ""
        links.append({"from": frm, "to": to, "both": both, "why": why, "why_back": back})

    note = teams._clip(data.get("note"), 300)
    if not cards and not links:
        return {"ok": False, "detail": "El agente principal no propuso nada que pueda "
                                       "aplicar."}
    return {"ok": True, "cards": cards, "links": links, "note": note}


# ── applying what the owner ticked ───────────────────────────────────────────
def apply_proposal(proposal, roster, install_dir=None, replace=False):
    """Write the parts of a proposal the owner accepted. Called from her click, never here.

    `replace` removes the links she did NOT keep. Off by default: a proposal that silently
    deleted connections the owner had made by hand would be the same class of surprise as
    the deny-by-default file this whole module is careful to avoid.
    """
    install_dir = _install_dir(install_dir)
    proposal = proposal if isinstance(proposal, dict) else {}
    known = set(a["slug"] for a in (roster or []))

    status, _ = teams.read(install_dir)
    if status == teams.BROKEN:
        raise ValueError("El mapa del equipo no se puede leer; arréglalo antes de aplicar "
                         "una propuesta.")
    adopted = False
    if status == teams.LEGACY:
        res = teams.adopt(roster, install_dir)
        if not res.get("ok"):
            return dict(res, ok=False)
        adopted = True

    cards = 0
    for c in (proposal.get("cards") or []):
        slug = (c.get("slug") or "").strip().lower()
        if slug not in known:
            continue
        teams.set_card(slug, role=c.get("role"), description=c.get("description"),
                       never=c.get("never"), roster=roster, install_dir=install_dir)
        cards += 1

    keep = set()
    wrote = 0
    for ln in (proposal.get("links") or []):
        frm = (ln.get("from") or "").strip().lower()
        to = (ln.get("to") or "").strip().lower()
        if frm not in known or to not in known or frm == to:
            continue
        teams.set_link(frm, to, why=ln.get("why"), why_back=ln.get("why_back"),
                       both=bool(ln.get("both")), enabled=True, install_dir=install_dir)
        keep.add(tuple(sorted([frm, to])))
        wrote += 1

    removed = 0
    if replace:
        _, data = teams.read(install_dir)
        for ln in list(data.get("links") or []):
            if tuple(sorted([ln["from"], ln["to"]])) in keep:
                continue
            try:
                teams.remove_link(ln["from"], ln["to"], install_dir=install_dir)
                removed += 1
            except ValueError:
                pass
    return {"ok": True, "cards": cards, "links": wrote, "removed": removed,
            "adopted": adopted}
