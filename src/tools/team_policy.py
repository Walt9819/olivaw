r"""Read the team map, and change the parts an agent is allowed to change.

The owner edits this map in Olivaw, with a picture in front of her. But she also says
things like "deja que Daneel le pregunte a Heraldo" out loud, to whichever agent she is
already talking to, and that should get somewhere. This is where it gets.

WHAT AN AGENT MAY DO TO ITS OWN PERMISSIONS
-------------------------------------------
Not much, on purpose. A tool that lets an agent grant itself a link is a tool that lets an
agent grant itself a link - and an agent is something a stranger on WhatsApp can type at.
So the line is drawn at the only place it can be drawn safely:

  * **Less, now.**  Removing or pausing a link, and describing itself. An agent may always
    ask for less than it has, and it may only do it to links it is actually part of -
    cutting two colleagues apart is not "less" for the agent doing the cutting, it is
    sabotage of someone else's work.
  * **More, never.**  Asking for a new link writes a request and nothing else. It sits in
    Olivaw until the owner accepts it. The agent is told, in those words, that it still
    does not have access - so it reports that to the owner instead of assuming and
    retrying.

That asymmetry is the whole design. Everything an agent can reach here either shrinks its
own reach or merely asks.

Exit codes: 0 done · 1 failed · 2 wrong usage · 3 refused on purpose.
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import intercom                        # noqa: E402  (needs the path above)
import teams                           # noqa: E402

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):  # pragma: no cover
        pass


def _me(args):
    return intercom.me(getattr(args, "sender", "") or "")


def _resolve(slug):
    a = intercom.find(slug)
    if not a:
        names = ", ".join(x["slug"] for x in intercom.roster())
        raise ValueError("No conozco al agente «%s». Los que hay: %s." % (slug, names))
    return a["slug"]


def show(args):
    who = _me(args)
    st = teams.state(intercom.roster())
    if st["broken"]:
        print("El mapa del equipo no se puede leer: %s" % st["error"])
        print("Nadie puede llamar a nadie hasta que tu dueño lo arregle en Olivaw.")
        return 1
    print("Tú eres «%s»." % who)
    print()
    if not st["configured"]:
        print("Este equipo todavía no tiene mapa: por ahora cualquiera puede")
        print("preguntarle a cualquiera. Tu dueño puede dibujarlo en Olivaw.")
        print()
    print("Quién es quién:")
    for a in st["agents"]:
        mark = "  (tú)" if a["slug"] == who else ""
        print("  %-12s %s%s" % (a["slug"], a["name"], mark))
        if a["role"]:
            print("               %s" % a["role"])
        if a["never"] and a["slug"] == who:
            print("               NO te pidan: %s" % a["never"])
    print()
    if st["configured"]:
        print("Quién puede hablar con quién:")
        if not st["links"]:
            print("  (nadie: el mapa está vacío)")
        for ln in st["links"]:
            arrow = "<->" if ln["both"] else "-->"
            flags = []
            if not ln["enabled"]:
                flags.append("en pausa")
            if ln["hours"]:
                flags.append("%02d:00-%02d:00" % (ln["hours"]["from"], ln["hours"]["to"]))
            if ln["stale"]:
                flags.append("alguno ya no está")
            tail = ("   [%s]" % ", ".join(flags)) if flags else ""
            print("  %s %s %s%s" % (ln["from"], arrow, ln["to"], tail))
            # Each direction's own sentence, named by direction. One line labelled "why"
            # under a two-way arrow is how an agent ends up reading the other one's job.
            if ln["why"]:
                print("        %s -> %s: %s" % (ln["from"], ln["to"], ln["why"]))
            if ln["both"] and ln.get("why_back"):
                print("        %s -> %s: %s" % (ln["to"], ln["from"], ln["why_back"]))
            elif ln.get("shared_why"):
                print("        (el sentido de vuelta usa esa misma frase; tu dueño "
                      "todavía no ha escrito una propia)")
        if st["pending"]:
            print()
            print("Peticiones esperando a tu dueño:")
            for p in st["pending"]:
                print("  %s --> %s   %s" % (p["from"], p["to"], p.get("why", "")))
    print()
    mine = intercom.neighbours_of(who)
    print("Tú puedes escribirle a: %s"
          % (", ".join(a["slug"] for a in mine) or "nadie por ahora"))
    return 0


def describe(args):
    """An agent describing ITSELF. Not a permission, and nobody knows it better."""
    who = _me(args)
    if args.describe.lower() not in ("", "me", "yo", who):
        print("Sólo puedes describirte a ti mismo («%s»). La ficha de los demás la "
              "escribe cada uno, o tu dueño en Olivaw." % who, file=sys.stderr)
        return 3
    if args.role is None and args.description is None and args.never is None:
        print("Dime qué escribir: --role, --description o --never.", file=sys.stderr)
        return 2
    try:
        res = teams.set_card(who, role=args.role, description=args.description,
                             never=args.never, roster=intercom.roster())
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 3
    if not res.get("ok"):
        print(res.get("detail", "No se pudo guardar."), file=sys.stderr)
        return 1
    print("Guardado: así te verá tu dueño en el mapa del equipo.")
    if res.get("adopted"):
        # The owner has to hear this. Her machine just moved from "everyone may talk to
        # everyone, implicitly" to an explicit map - identical in behaviour, different in
        # kind - and the agent typing a sentence about itself is what caused it.
        print()
        print("Aviso para tu dueño: este equipo no tenía mapa, así que he anotado las")
        print("conexiones que ya existían tal cual estaban. No ha cambiado nada de lo")
        print("que podían hacer; ahora están escritas y puede editarlas en Olivaw.")
    return 0


def unlink(args, pause=False):
    who = _me(args)
    try:
        other = _resolve(args.unlink or args.pause)
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 2
    ln = None
    status, data = teams.read()
    if status == teams.BROKEN:
        print("El mapa no se puede leer. Que lo arregle tu dueño en Olivaw.",
              file=sys.stderr)
        return 1
    if status == teams.LEGACY:
        print("Este equipo todavía no tiene mapa, así que no hay un enlace concreto que "
              "quitar. Dile a tu dueño que lo dibuje en Olivaw.", file=sys.stderr)
        return 3
    ln = teams.link_between(data, who, other)
    if not ln:
        print("No tienes ningún enlace con «%s»." % other, file=sys.stderr)
        return 3
    try:
        if pause:
            teams.set_link(ln["from"], ln["to"], enabled=False)
            print("Pausado. Ni tú ni «%s» podéis usar ese enlace hasta que tu dueño lo "
                  "reactive en Olivaw." % other)
        else:
            teams.remove_link(who, other)
            print("Quitado. Ya no puedes escribirle a «%s», y tu dueño puede volver a "
                  "abrirlo en Olivaw cuando quiera." % other)
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 3
    return 0


def ask(args):
    """Ask for a link. Grants nothing - that is the point of it existing."""
    who = _me(args)
    frm, to = who, args.link
    if args.from_agent:
        # Relaying the owner's own words ("deja que Daneel hable con Heraldo"). Still only
        # a request: this tool cannot tell the owner's voice from a customer's.
        frm = args.from_agent
    try:
        frm = _resolve(frm)
        to = _resolve(to)
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 2
    if frm == to:
        print("Un agente no se enlaza consigo mismo.", file=sys.stderr)
        return 2
    try:
        res = teams.request_link(frm, to, args.why or "")
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 3
    if res.get("already"):
        print("Ya existe ese enlace: «%s» puede escribirle a «%s»." % (frm, to))
        return 0
    if res.get("already_pending"):
        print("Esa petición ya está esperando a tu dueño. No insistas.")
        return 0
    if not res.get("ok"):
        print(res.get("detail", "No se pudo anotar."), file=sys.stderr)
        return 1
    print("Anotado: «%s» --> «%s»." % (frm, to))
    print("Esto NO abre el enlace. Queda esperando en Olivaw y decide tu dueño;")
    print("díselo con una frase para que sepa qué está aprobando.")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="team_policy",
        description="El mapa del equipo: quién puede hablar con quién.")
    ap.add_argument("--show", action="store_true", help="ver el mapa completo")
    ap.add_argument("--describe", nargs="?", const="me", default=None,
                    help="escribir tu propia ficha (sólo la tuya)")
    ap.add_argument("--role", default=None, help="en una línea, de qué te encargas")
    ap.add_argument("--description", default=None, help="qué sabes y qué tienes a mano")
    ap.add_argument("--never", default=None, help="qué NO deben pedirte")
    ap.add_argument("--unlink", default="", help="quitar tu enlace con ese agente")
    ap.add_argument("--pause", default="", help="pausar tu enlace con ese agente")
    ap.add_argument("--link", default="", help="pedir un enlace (no te lo da)")
    ap.add_argument("--from", dest="from_agent", default="",
                    help="pedir el enlace para otro agente (sigue siendo una petición)")
    ap.add_argument("--why", default="", help="para qué hace falta (con --link)")
    ap.add_argument("--me", dest="sender", default="", help="tu propio slug")
    args = ap.parse_args(argv)

    if args.describe is not None:
        return describe(args)
    if args.unlink:
        return unlink(args)
    if args.pause:
        return unlink(args, pause=True)
    if args.link:
        return ask(args)
    return show(args)


if __name__ == "__main__":
    sys.exit(main())
