r"""Who may talk to whom - the org chart behind the intercom.

`intercom.py` built the road: agent A can call agent B, B answers as itself, and the
envelope tells B that a peer is information and never an order. What it never had was a
map. `roster()` returns every agent on the machine and `find()` accepts any of them, so
four agents are an all-to-all mesh of twelve possible directions, all of them open, with
one global on/off switch over the lot.

That is fine with two agents and a mess with six. The owner's actual intent is shaped like
an organisation: the clinic agent may ask the finance agent about prices; finance has no
business asking the clinic anything; the publishing agent talks to nobody. This module is
that intent, written down.

TWO LAYERS, AND THEY ARE NOT THE SAME THING
-------------------------------------------
  * **The prompt layer says WHEN.** Each agent's skill is rewritten to name only the
    colleagues it may actually call, with the owner's own sentence for each link
    ("para preguntar precios y disponibilidad"). That is what makes an agent reach out at
    the right moment instead of guessing.
  * **The code layer says WHETHER.** `allows()` is checked inside `intercom.send()`
    BEFORE the target is spawned. This is the half that is actually load-bearing: an agent
    runs `agent_call.py` through its terminal and can type any `--to` it likes. Skill text
    is advice. This function is the gate.

Writing the rule only into the skill would be security theatre, and writing it only into
the gate would produce agents that never think to ask. Both, or neither.

NOT BREAKING MACHINES THAT ALREADY WORK
---------------------------------------
Olivaw is installed on machines whose agents talk to each other today. Shipping a policy
file that defaults to "deny" would, on update, silently cut every one of those links - a
change the owner never asked for, announced by nothing except an agent that has gone quiet.

So the absence of a decision is not a decision:

  * **no teams.json at all** -> legacy mode -> everything is allowed, exactly as before.
    The owner is told, in the console, that her team has no map yet and what adopting one
    would mean.
  * **teams.json present and readable** -> the links are the only truth. A call with no
    link is refused, by name.
  * **teams.json present and CORRUPT** -> refuse. This is the one case that fails closed,
    and deliberately: a file that exists is an owner who has configured something. Silently
    reopening every link because we could not parse her rules is the real failure. She gets
    a loud error in the console and a `.bak` to recover from.

Adoption is therefore explicit and non-destructive: `adopt()` writes down the mesh that is
*already* running - every pair, in both directions - so the first map the owner sees is a
true picture of her machine, and behaviour on the day of the update is byte-identical.
Pruning is then something she does on purpose.

A TWO-WAY LINK IS TWO INSTRUCTIONS, NOT ONE
-------------------------------------------
The first version of this file gave a link ONE sentence - "para qué sirve" - and a
`both: true` flag. That reads fine in the console and is wrong in the skill, because the
sentence is read by both ends as *their* reason to write. A real file on this machine said:

    baco ⇄ forja — "Cuestiones relacionadas con código y detalles técnicos"

Correct for Ábaco (finance) writing to Forja (the developer). But Forja read the same line
as its trigger for calling the *finance* agent, so the only instruction the developer had
was to ask the accountant about code.

So a two-way link carries `why` (from -> to) and `why_back` (to -> from), and `why_for()`
is the single place that picks. `why_back` empty falls back to `why`: files written before
this existed keep behaving exactly as they did, and the console flags the pair rather than
silently dropping a sentence the owner typed. Nothing here is a permission - both
directions were already allowed by `both` - it is only *which* advice each agent is given.

WIDENING IS NOT THE AGENT'S CALL
--------------------------------
An agent can be told "deja que Daneel le pregunte a Heraldo", so `tools/team_policy.py`
exists. But a tool that lets an agent grant itself a link is a tool that lets an agent
grant itself a link. The split:

  * **narrowing applies immediately** - removing a link, tightening a cap, writing a role.
    An agent may always ask for less.
  * **widening becomes a pending request** the owner accepts in Olivaw. It changes nothing
    until she does.

The quota, the depth limit and the envelope in `intercom.py` are untouched and still apply
underneath all of this. This module can only ever subtract.
"""

import json
import os
import re
import time

HERE = os.path.dirname(os.path.abspath(__file__))
INSTALL_DIR = os.path.dirname(HERE)

CONFIG_NAME = "teams.json"

# Status of the policy file, and the whole reason this module is safe to ship to a machine
# that is already running.
LEGACY = "legacy"      # never configured: allow everything, like before
OK = "ok"              # configured: links are the truth
BROKEN = "broken"      # configured but unreadable: refuse, and say so

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,38}$")

MAX_ROLE = 120
MAX_DESC = 600
MAX_WHY = 300
MAX_LINKS = 200
MAX_PENDING = 20


def _norm(s):
    return (s or "").strip().lower()


def _clip(s, n):
    s = re.sub(r"\s+", " ", (s or "").replace("\r", " ").replace("\n", " ")).strip()
    return s[:n]


def config_path(install_dir=None):
    return os.path.join(install_dir or INSTALL_DIR, CONFIG_NAME)


# ── reading ──────────────────────────────────────────────────────────────────
def read(install_dir=None):
    """(status, data). The three-way answer the whole module turns on.

    A missing file and an unreadable file mean opposite things here, so they must never
    collapse into the same empty dict - which is what a plain try/except returning {} would
    have done, and would have turned "I cannot read your rules" into "you have no rules".
    """
    path = config_path(install_dir)
    try:
        with open(path, encoding="utf-8") as fh:
            raw = fh.read()
    except FileNotFoundError:
        return LEGACY, _blank()
    except OSError as e:
        return BROKEN, dict(_blank(), error=str(e))
    try:
        data = json.loads(raw)
    except ValueError as e:
        return BROKEN, dict(_blank(), error="el archivo no es JSON válido (%s)" % e)
    if not isinstance(data, dict):
        return BROKEN, dict(_blank(), error="el archivo no tiene la forma esperada")
    return OK, _clean(data)


def _blank():
    return {"version": 1, "cards": {}, "links": [], "pending": []}


def _clean(data):
    """Normalise a loaded file. Tolerant of hand edits; strict about shape."""
    out = _blank()
    out["version"] = 1

    cards = data.get("cards")
    if isinstance(cards, dict):
        for slug, c in cards.items():
            slug = _norm(slug)
            if not SLUG_RE.match(slug) or not isinstance(c, dict):
                continue
            out["cards"][slug] = {
                "role": _clip(c.get("role"), MAX_ROLE),
                "description": _clip(c.get("description"), MAX_DESC),
                "never": _clip(c.get("never"), MAX_WHY),
            }

    links = data.get("links")
    if isinstance(links, list):
        seen = set()
        for ln in links[:MAX_LINKS]:
            cl = _clean_link(ln)
            if not cl:
                continue
            key = (cl["from"], cl["to"])
            if key in seen:
                continue
            seen.add(key)
            out["links"].append(cl)

    pend = data.get("pending")
    if isinstance(pend, list):
        for p in pend[:MAX_PENDING]:
            cl = _clean_link(p)
            if not cl:
                continue
            cl["at"] = _clip(p.get("at"), 40)
            out["pending"].append(cl)
    return out


def _clean_link(ln):
    if not isinstance(ln, dict):
        return None
    frm, to = _norm(ln.get("from")), _norm(ln.get("to"))
    if not SLUG_RE.match(frm) or not SLUG_RE.match(to) or frm == to:
        return None
    out = {"from": frm, "to": to, "both": bool(ln.get("both")),
           "why": _clip(ln.get("why"), MAX_WHY),
           "why_back": _clip(ln.get("why_back"), MAX_WHY),
           "enabled": ln.get("enabled", True) is not False}
    for key in ("max_turns", "hourly_limit"):
        v = ln.get(key)
        try:
            v = int(v)
        except (TypeError, ValueError):
            v = None
        out[key] = v if (v and v > 0) else None
    out["hours"] = _clean_hours(ln.get("hours"))
    return out


def _clean_hours(h):
    """{"from": 9, "to": 18} or None. A window that spans midnight is allowed."""
    if not isinstance(h, dict):
        return None
    try:
        a, b = int(h.get("from")), int(h.get("to"))
    except (TypeError, ValueError):
        return None
    if not (0 <= a <= 23 and 0 <= b <= 23) or a == b:
        return None
    return {"from": a, "to": b}


# ── writing ──────────────────────────────────────────────────────────────────
def save(data, install_dir=None):
    """Atomic, with one backup. The file is a gate; a half-written one refuses traffic."""
    path = config_path(install_dir)
    body = _clean(data if isinstance(data, dict) else {})
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        if os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as fh:
                    old = fh.read()
                with open(path + ".bak", "w", encoding="utf-8", newline="\n") as fh:
                    fh.write(old)
            except OSError:
                pass
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(body, fh, indent=2, ensure_ascii=False)
        os.replace(tmp, path)
        return {"ok": True}
    except OSError as e:
        return {"ok": False, "detail": str(e)}


# ── the gate ─────────────────────────────────────────────────────────────────
def link_between(data, frm, to):
    """The link that would authorise frm -> to, in either stored direction."""
    frm, to = _norm(frm), _norm(to)
    for ln in data.get("links") or []:
        if ln["from"] == frm and ln["to"] == to:
            return ln
        if ln["both"] and ln["from"] == to and ln["to"] == frm:
            return ln
    return None


def why_for(ln, caller):
    """The sentence THIS agent should read on this link.

    One link, two possible readers. Getting this wrong is not a security hole - both
    directions of a `both` link were already allowed - but it is the difference between an
    agent that knows when to write and one that has been handed somebody else's job
    description.
    """
    if not ln:
        return ""
    if ln.get("both") and _norm(caller) == ln["to"]:
        return ln.get("why_back") or ln.get("why") or ""
    return ln.get("why") or ""


def shares_one_sentence(ln):
    """A two-way link whose return direction has no sentence of its own.

    Not an error - it is what every link written before `why_back` existed looks like, and
    the fallback keeps those working. The console asks about them because the stored
    sentence is almost never true in both directions.
    """
    return bool(ln and ln.get("both") and ln.get("why") and not ln.get("why_back"))


def _within_hours(hours, now=None):
    if not hours:
        return True
    h = time.localtime(now or time.time()).tm_hour
    a, b = hours["from"], hours["to"]
    return (a <= h < b) if a < b else (h >= a or h < b)


def allows(frm, to, install_dir=None, now=None):
    """(allowed, reason). The reason is read by another agent, so it must be actionable.

    Called before the target is spawned: a refusal here costs nothing, while the call it
    prevents is a full turn of another agent.
    """
    frm, to = _norm(frm), _norm(to)
    if frm == to:
        return False, "Ese eres tú."
    status, data = read(install_dir)

    if status == BROKEN:
        return False, ("El mapa del equipo (%s) no se puede leer: %s. Mientras tanto "
                       "nadie puede llamar a nadie. Dile a tu dueño que lo arregle en "
                       "Olivaw." % (CONFIG_NAME, data.get("error") or "archivo ilegible"))
    if status == LEGACY:
        return True, ""

    ln = link_between(data, frm, to)
    if not ln:
        return False, ("No hay un enlace de «%s» a «%s» en el mapa del equipo, así que no "
                       "puedo pasarle tu mensaje. Si hace falta, pídeselo a tu dueño: él "
                       "lo abre en Olivaw." % (frm, to))
    if not ln["enabled"]:
        return False, ("El enlace de «%s» a «%s» está pausado por tu dueño." % (frm, to))
    if not _within_hours(ln["hours"], now):
        return False, ("El enlace de «%s» a «%s» sólo está abierto de %02d:00 a %02d:00. "
                       "Resuelve con lo que tengas o espera."
                       % (frm, to, ln["hours"]["from"], ln["hours"]["to"]))
    return True, ""


def limits_for(frm, to, install_dir=None):
    """Per-link caps, when the owner set any. {} means 'use the global ones'."""
    status, data = read(install_dir)
    if status != OK:
        return {}
    ln = link_between(data, frm, to)
    if not ln:
        return {}
    return {k: ln[k] for k in ("max_turns", "hourly_limit") if ln.get(k)}


def neighbours(slug, roster=None, install_dir=None):
    """Who `slug` may call, with the owner's sentence for each - the skill's whole body.

    In legacy mode this is the full roster, which is what those machines do today.
    """
    slug = _norm(slug)
    status, data = read(install_dir)
    names = {a["slug"]: a for a in (roster or [])}
    out = []
    if status == BROKEN:
        return out
    if status == LEGACY:
        targets = [(s, "", None) for s in names if s != slug]
    else:
        targets = []
        for ln in data.get("links") or []:
            if not ln["enabled"]:
                continue
            # why_for(), not ln["why"]: on a two-way link the agent at the far end gets
            # the sentence written for IT, which is the whole point of why_back.
            if ln["from"] == slug:
                targets.append((ln["to"], why_for(ln, slug), ln["hours"]))
            elif ln["both"] and ln["to"] == slug:
                targets.append((ln["from"], why_for(ln, slug), ln["hours"]))
    seen = set()
    for target, why, hours in targets:
        if target in seen:
            continue
        seen.add(target)
        card = (data.get("cards") or {}).get(target) or {}
        a = names.get(target) or {}
        out.append({"slug": target, "name": a.get("name") or target,
                    "role": card.get("role", ""),
                    "description": card.get("description", ""),
                    "never": card.get("never", ""),
                    "why": why, "hours": hours,
                    "known": bool(a)})
    out.sort(key=lambda x: x["name"].lower())
    return out


# ── adoption: write down what is already running ─────────────────────────────
def adopt(roster, install_dir=None, cards=None):
    """Freeze today's all-to-all mesh into explicit links, changing no behaviour.

    Every ordered pair becomes one `both: true` link, which is exactly what the machine
    does now. The owner then deletes the ones she never wanted, which is a much safer first
    move than being handed an empty map and told her agents have stopped talking.
    """
    slugs = [_norm(a.get("slug")) for a in (roster or []) if _norm(a.get("slug"))]
    slugs = [s for s in slugs if SLUG_RE.match(s)]
    data = _blank()
    data["cards"] = dict(cards or {})
    for i, a in enumerate(slugs):
        for b in slugs[i + 1:]:
            if len(data["links"]) >= MAX_LINKS:
                break
            data["links"].append(_clean_link({"from": a, "to": b, "both": True, "why": ""}))
    res = save(data, install_dir)
    if not res.get("ok"):
        return res
    return {"ok": True, "links": len(data["links"])}


# ── editing ──────────────────────────────────────────────────────────────────
def _require_configured(install_dir):
    status, data = read(install_dir)
    if status == BROKEN:
        raise ValueError("El mapa del equipo no se puede leer: %s"
                         % (data.get("error") or "archivo ilegible"))
    return status, data


def set_card(slug, role=None, description=None, never=None, roster=None,
             install_dir=None):
    """A role and a description. Narrowing-or-neutral: an agent may do this itself.

    The legacy case is the dangerous one and is the reason `roster` is a parameter.
    Describing an agent is not a permission - but on a machine with no teams.json, merely
    SAVING one is what ends legacy mode, and a file containing cards and no links reads as
    "nobody may call anybody". Writing a role would then have cut every conversation on the
    machine, silently, as a side effect of typing a sentence into a text box.

    So on a legacy machine a card write adopts the live mesh in the same breath, and says
    so in `adopted` - the caller owes the owner that sentence.
    """
    slug = _norm(slug)
    if not SLUG_RE.match(slug):
        raise ValueError("«%s» no es un agente válido." % slug)
    status, data = _require_configured(install_dir)
    adopted = False
    if status == LEGACY:
        if not roster:
            raise ValueError("Este equipo todavía no tiene mapa. Adóptalo primero.")
        res = adopt(roster, install_dir, cards=data.get("cards"))
        if not res.get("ok"):
            return dict(res, adopted=False)
        adopted = True
        _, data = _require_configured(install_dir)
    card = (data["cards"].get(slug) or {"role": "", "description": "", "never": ""})
    if role is not None:
        card["role"] = _clip(role, MAX_ROLE)
    if description is not None:
        card["description"] = _clip(description, MAX_DESC)
    if never is not None:
        card["never"] = _clip(never, MAX_WHY)
    data["cards"][slug] = card
    return dict(save(data, install_dir), adopted=adopted)


def set_link(frm, to, why=None, why_back=None, both=None, max_turns=None,
             hourly_limit=None, hours=None, enabled=None, direction=False,
             install_dir=None):
    """Create or edit a link. The caller decides whether this is owner-authorised.

    The contract is fixed and orientation-free: `why` always means "frm -> to" and
    `why_back` always means "to -> frm", as the CALLER spelled the pair. A link is stored
    once, in whichever direction it was first written, so a caller naming the same pair the
    other way round would otherwise attach each sentence to the wrong agent - the exact
    mix-up why_back exists to end.

    One exception, and it is not cosmetic: a ONE-WAY link's arrow *is* its instruction. If
    the stored link reads `analecta -> baco` and the caller asks for `baco -> analecta,
    both=False`, keeping the stored orientation would leave a one-way link pointing the
    opposite way from the one that was asked for, with the caller's sentence parked in a
    field nobody reads. So `both=False` turns the arrow round exactly as `direction=True`
    does. Only a two-way link, where both agents may write and the arrow is just how the
    pair happens to be filed, keeps the orientation it was first written in.
    """
    status, data = _require_configured(install_dir)
    if status == LEGACY:
        raise ValueError("Este equipo todavía no tiene mapa. Adóptalo primero.")
    frm, to = _norm(frm), _norm(to)
    if frm == to:
        raise ValueError("Un agente no se enlaza consigo mismo.")
    if not SLUG_RE.match(frm) or not SLUG_RE.match(to):
        raise ValueError("Agente no válido.")
    # See the docstring: the owner asking for the arrow to be turned round, and a caller
    # asking for a one-way link in the direction it spelled, are the same instruction.
    turn = bool(direction) or (both is not None and not both)
    existing = None
    swap_incoming = False
    for ln in data["links"]:
        if ln["from"] == frm and ln["to"] == to:
            existing = ln
            break
        if ln["from"] == to and ln["to"] == frm:
            existing = ln
            if turn:
                # Without this the stored orientation won: the form came back showing
                # the old direction, a one-way link could only be reversed by deleting
                # it, and a proposal naming a one-way pair backwards silently inverted
                # it. The two sentences travel with their directions, so they swap with
                # the arrow.
                ln["from"], ln["to"] = frm, to
                ln["why"], ln["why_back"] = ln.get("why_back", ""), ln.get("why", "")
            else:
                frm, to = ln["from"], ln["to"]  # keep the stored orientation
                swap_incoming = True
            break
    if existing is None:
        if len(data["links"]) >= MAX_LINKS:
            raise ValueError("Demasiados enlaces (%d)." % MAX_LINKS)
        existing = _clean_link({"from": frm, "to": to, "both": False, "why": ""})
        data["links"].append(existing)
    if swap_incoming:
        why, why_back = why_back, why
    if why is not None:
        existing["why"] = _clip(why, MAX_WHY)
    if why_back is not None:
        existing["why_back"] = _clip(why_back, MAX_WHY)
    if both is not None:
        existing["both"] = bool(both)
    if enabled is not None:
        existing["enabled"] = bool(enabled)
    for key, val in (("max_turns", max_turns), ("hourly_limit", hourly_limit)):
        if val is not None:
            try:
                n = int(val)
            except (TypeError, ValueError):
                n = 0
            existing[key] = n if n > 0 else None
    if hours is not None:
        existing["hours"] = _clean_hours(hours)
    data["pending"] = [p for p in data["pending"]
                       if not ((p["from"], p["to"]) in ((frm, to), (to, frm)))]
    return save(data, install_dir)


def remove_link(frm, to, install_dir=None):
    """Narrowing. Always allowed, from the console or from an agent."""
    status, data = _require_configured(install_dir)
    if status == LEGACY:
        raise ValueError("Este equipo todavía no tiene mapa. Adóptalo primero.")
    frm, to = _norm(frm), _norm(to)
    before = len(data["links"])
    data["links"] = [ln for ln in data["links"]
                     if not ((ln["from"], ln["to"]) in ((frm, to), (to, frm)))]
    if len(data["links"]) == before:
        raise ValueError("No había un enlace entre «%s» y «%s»." % (frm, to))
    return save(data, install_dir)


# ── pending requests: an agent asking for a link it does not have ────────────
def request_link(frm, to, why="", install_dir=None):
    status, data = _require_configured(install_dir)
    if status == LEGACY:
        raise ValueError("Este equipo todavía no tiene mapa, así que ya puedes hablar "
                         "con quien quieras.")
    frm, to = _norm(frm), _norm(to)
    if frm == to or not SLUG_RE.match(frm) or not SLUG_RE.match(to):
        raise ValueError("Agente no válido.")
    if link_between(data, frm, to):
        return {"ok": True, "already": True}
    for p in data["pending"]:
        if p["from"] == frm and p["to"] == to:
            return {"ok": True, "already_pending": True}
    if len(data["pending"]) >= MAX_PENDING:
        raise ValueError("Ya hay %d peticiones esperando." % MAX_PENDING)
    item = _clean_link({"from": frm, "to": to, "why": why})
    item["at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    data["pending"].append(item)
    res = save(data, install_dir)
    res["pending"] = True
    return res


def decide_request(frm, to, accept, install_dir=None, both=False):
    status, data = _require_configured(install_dir)
    if status == LEGACY:
        raise ValueError("Este equipo todavía no tiene mapa.")
    frm, to = _norm(frm), _norm(to)
    hit = None
    for p in data["pending"]:
        if p["from"] == frm and p["to"] == to:
            hit = p
            break
    if not hit:
        raise ValueError("No hay una petición de «%s» a «%s»." % (frm, to))
    if not accept:
        data["pending"] = [p for p in data["pending"] if p is not hit]
        return save(data, install_dir)
    # One write, not two: set_link drops the pending entry for this pair itself. Saving
    # first and linking second would lose the request if the second write failed.
    return set_link(frm, to, why=hit.get("why", ""), both=both, install_dir=install_dir)


# ── what the console shows ───────────────────────────────────────────────────
def state(roster=None, install_dir=None):
    """The whole map, resolved against who actually exists on this machine."""
    status, data = read(install_dir)
    names = {a["slug"]: a for a in (roster or [])}

    def label(slug):
        a = names.get(slug)
        return (a or {}).get("name") or slug

    links = []
    for ln in data.get("links") or []:
        links.append(dict(ln, from_name=label(ln["from"]), to_name=label(ln["to"]),
                          shared_why=shares_one_sentence(ln),
                          stale=not (ln["from"] in names and ln["to"] in names)))
    pending = [dict(p, from_name=label(p["from"]), to_name=label(p["to"]))
               for p in (data.get("pending") or [])]

    # Agents nobody linked. The complaint this answers: an agent created after the map was
    # drawn joins the machine with no links at all and simply never hears from anyone -
    # silently, because an isolated agent and a deliberately-solitary one look identical.
    # On a mapped machine the console names them; in legacy mode everyone reaches everyone,
    # so nobody is isolated.
    linked = set()
    for ln in data.get("links") or []:
        # Both ends count, including a one-way link's target: being reachable is a
        # connection even when you cannot start the conversation yourself.
        linked.add(ln["from"])
        linked.add(ln["to"])

    agents = []
    isolated = []
    for a in (roster or []):
        card = (data.get("cards") or {}).get(a["slug"]) or {}
        alone = status == OK and a["slug"] not in linked
        if alone:
            isolated.append(a["slug"])
        agents.append({"slug": a["slug"], "name": a.get("name") or a["slug"],
                       "profile": a.get("profile") or a["slug"],
                       "role": card.get("role", ""),
                       "description": card.get("description", ""),
                       "never": card.get("never", ""),
                       "isolated": alone,
                       "reachable": a.get("reachable", True)})
    return {"ok": status != BROKEN, "status": status, "configured": status == OK,
            "broken": status == BROKEN, "error": data.get("error", ""),
            "agents": agents, "links": links, "pending": pending,
            "isolated": isolated,
            "shared_why": [[ln["from"], ln["to"]] for ln in links if ln["shared_why"]],
            "possible": max(0, len(agents) * (len(agents) - 1) // 2)}
