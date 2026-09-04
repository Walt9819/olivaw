r"""Call another agent on this machine and talk to it until the matter is settled.

The agent runs this through its `terminal` tool. All the mechanics, and the reasoning
behind them, live in src/intercom.py - this file is only the door.

    python agent_call.py --list
    python agent_call.py --from chalenus --to daneel --msg "¿Qué sabes de X?"
    python agent_call.py --from chalenus --to daneel --thread ab12cd34 --msg "¿y entonces?"
    python agent_call.py --thread ab12cd34 --show

Each call is ONE full turn of the other agent (~30-120s), so it is not free and it is not
instant. Keep asking in the same thread while it is still useful; the other side ends its
answer with FIN when it considers the matter closed.

Exit codes: 0 answered · 1 the other agent failed or timed out · 2 wrong usage ·
3 refused on purpose (disabled, too deep, quota, thread full).
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import intercom                        # noqa: E402  (needs the path above)

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):  # pragma: no cover
        pass


def show_list():
    who = intercom.me()
    print("Tú eres «%s». En este equipo:" % who)
    for a in intercom.roster():
        mark = "  (tú)" if a["slug"] == who else ""
        how = "" if intercom._base(a["profile"]) else "   [no alcanzable desde aquí]"
        print("  %-12s %s%s%s" % (a["slug"], a["name"], mark, how))
    q = intercom.quota()
    print("\nLlamadas esta hora: %d de %d." % (q["used"], q["limit"]))
    open_ = [t for t in intercom.threads(8) if not t["done"]]
    if open_:
        print("Hilos abiertos:")
        for t in open_:
            print("  %s  %s -> %s  (%d turnos)"
                  % (t["id"], t["from"], t["to"], t["turns"]))


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="agent_call",
        description="Hablar con otro agente de este equipo.")
    ap.add_argument("--list", action="store_true", help="ver con quién puedes hablar")
    ap.add_argument("--to", default="", help="slug del agente al que llamas")
    ap.add_argument("--msg", default="", help="el mensaje; si falta, se lee de stdin")
    ap.add_argument("--from", dest="sender", default="",
                    help="tu propio slug (la skill ya te lo pasa)")
    ap.add_argument("--thread", default="", help="seguir un hilo ya empezado")
    ap.add_argument("--show", action="store_true", help="ver un hilo completo")
    ap.add_argument("--timeout", type=int, default=0, help="segundos de espera")
    args = ap.parse_args(argv)

    if args.list:
        show_list()
        return 0

    if args.show:
        if not args.thread:
            print("Dime qué hilo: --thread <id>.", file=sys.stderr)
            return 2
        text = intercom.transcript(args.thread)
        if not text:
            print("No existe el hilo «%s»." % args.thread, file=sys.stderr)
            return 2
        print(text)
        return 0

    if not args.to:
        print("Falta a quién llamas (--to). Usa --list para ver quiénes hay.",
              file=sys.stderr)
        return 2
    msg = args.msg or (sys.stdin.read() if not sys.stdin.isatty() else "")
    if not (msg or "").strip():
        print("Falta el mensaje (--msg o por stdin).", file=sys.stderr)
        return 2

    r = intercom.send(args.to, msg, sender=args.sender, thread=args.thread,
                      timeout=args.timeout or None)
    if not r.get("ok"):
        print(r.get("detail", "No se pudo."), file=sys.stderr)
        if r.get("thread"):
            print("(hilo %s)" % r["thread"], file=sys.stderr)
        return int(r.get("code", 1))

    print("HILO: %s   (turno %d de %d, quedan %d)"
          % (r["thread"], r["turn"], r["max_turns"], r["left"]))
    print("%s responde en %ss:" % (r["name"], r["seconds"]))
    print()
    print(r["reply"])
    if r.get("done"):
        print("\n(Da el asunto por cerrado. Si te falta algo, pregúntale igual.)")
    elif r["left"] > 0:
        print("\n(Para seguir: --thread %s --msg \"...\")" % r["thread"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
