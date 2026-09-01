r"""Read or change how long this agent's conversation lives. Safe to run at any time.

Why an agent gets to change its own settings
--------------------------------------------
The right answer depends on what the agent is being used for, and the agent is the one
who knows. An assistant doing a long build all afternoon wants a wide window; one that
answers three unrelated questions a day is burning the owner's quota by dragging every
earlier one along. Nobody is going to notice that from outside and edit a YAML file.

So this is a script, not a judgement call: fixed effects, validated bounds, one command.

Usage
-----
    python conversation_policy.py                     # what am I running under?
    python conversation_policy.py --list-presets
    python conversation_policy.py --preset ahorro
    python conversation_policy.py --idle-minutes 240 --compact-at 0.15
    python conversation_policy.py --mode none         # never restart on its own
    python conversation_policy.py --preset equilibrado --profile daneel

Exit codes: 0 done · 1 could not write · 2 wrong usage.

The gateway reads this setting once, when it starts, so a change is not live until it
restarts. **You must not restart it yourself**: that kills the turn you are in the middle
of, and the owner sees her request disappear instead of an answer. So this leaves a note
and the supervisor performs the restart the next time you are idle — usually within
minutes. Say that plainly when you report back. ``--restart-now`` exists for a human at a
console and is the wrong choice from inside a conversation.
"""

import argparse
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))          # <install>/src/tools
sys.path.insert(0, os.path.dirname(_HERE))                  # <install>/src

# The agent runs this through a shell and reads the output back. On Windows a piped stdout
# defaults to cp1252, and every accented word here - "duración", "½ h", "⚠️" - raises
# UnicodeEncodeError before a single line is printed. The agent then sees a traceback where
# it expected its own settings, which is worse than not having the tool at all.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):  # pragma: no cover - very old or wrapped streams
        pass

from wizard import context_policy as cp  # noqa: E402


def _human(state):
    p = state["policy"]
    lines = [state["summary"]]
    lines.append("")
    lines.append("  modo de reinicio : %s" % p["mode"])
    lines.append("  sin hablar       : %d min (%.1f h)" % (p["idle_minutes"],
                                                           p["idle_minutes"] / 60.0))
    lines.append("  hora diaria      : %02d:00" % p["at_hour"])
    lines.append("  resume al        : %d%% de la ventana%s"
                 % (round(p["compact_at"] * 100),
                    ("  (~%s tokens)" % "{:,}".format(state["trigger_tokens"]).replace(",", "."))
                    if state.get("trigger_tokens") else ""))
    lines.append("  preajuste        : %s" % state["preset"])
    lines.append("  configurado      : %s" % ("sí" if state["configured"] else
                                              "no — está con los valores de fábrica de Hermes"))
    lines.append("  archivo          : %s" % state["path"])
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="conversation_policy",
        description="Consulta o cambia cada cuánto se reinicia y se resume la conversación.")
    ap.add_argument("--profile", default=None,
                    help="perfil de Hermes (omítelo para el agente principal)")
    ap.add_argument("--preset", default=None,
                    help="ahorro | equilibrado | memoria | nunca")
    ap.add_argument("--mode", default=None, choices=list(cp.MODES),
                    help="both = por inactividad y a diario · idle · daily · none")
    ap.add_argument("--idle-minutes", type=int, default=None,
                    help="minutos sin hablar antes de empezar de cero (%d-%d)" % cp.LIMITS["idle_minutes"])
    ap.add_argument("--at-hour", type=int, default=None, help="hora del reinicio diario (0-23)")
    ap.add_argument("--compact-at", type=float, default=None,
                    help="fracción de la ventana a la que resume (%.2f-%.2f)" % cp.LIMITS["compact_at"])
    ap.add_argument("--no-compact", action="store_true", help="no resumir nunca (no recomendado)")
    ap.add_argument("--quiet-reset", action="store_true",
                    help="no avisar al usuario cuando la conversación se reinicia")
    ap.add_argument("--restart-now", action="store_true",
                    help="reiniciar el gateway ya (corta la conversación en curso: no lo uses "
                         "desde dentro de una conversación)")
    ap.add_argument("--list-presets", action="store_true", help="ver los preajustes y salir")
    ap.add_argument("--json", action="store_true", help="salida en JSON")
    args = ap.parse_args(argv)

    if args.list_presets:
        if args.json:
            print(json.dumps(cp.PRESETS, indent=2, ensure_ascii=False))
        else:
            for p in cp.PRESETS:
                vals = cp.preset_policy(p["id"])
                print("%-13s %s" % (p["id"], p["label"]))
                print("              %s" % p["note"])
                print("              %s\n" % cp.describe(vals))
        return 0

    if args.preset and args.preset not in [p["id"] for p in cp.PRESETS]:
        print("Preajuste desconocido: %s. Usa --list-presets." % args.preset, file=sys.stderr)
        return 2

    profile = args.profile or None
    state = cp.read(profile=profile)

    wants = {k: v for k, v in (
        ("mode", args.mode),
        ("idle_minutes", args.idle_minutes),
        ("at_hour", args.at_hour),
        ("compact_at", args.compact_at),
    ) if v is not None}
    if args.no_compact:
        wants["compact"] = False
    if args.quiet_reset:
        wants["notify"] = False

    if not wants and not args.preset:
        print(json.dumps(state, indent=2, ensure_ascii=False) if args.json else _human(state))
        return 0

    # A preset is the starting point; explicit flags win over it, and anything neither
    # mentions keeps whatever the profile has now rather than snapping back to a default.
    base = cp.preset_policy(args.preset) if args.preset else dict(state["policy"])
    base.update(wants)

    res = cp.apply(base, profile=profile)
    if res["ok"] and res.get("written"):
        if args.restart_now:
            res["activation"] = cp.activate(profile=profile)
        else:
            cp.mark_pending(profile)
            res["activation"] = {"pending": True}
    after = cp.read(profile=profile)

    if args.json:
        print(json.dumps({"applied": res, "state": after}, indent=2, ensure_ascii=False))
    else:
        print(res["detail"])
        for n in res.get("notes", []):
            print("  · " + n)
        act = res.get("activation") or {}
        if act.get("pending"):
            print("  · Guardado. Se activa cuando el supervisor reinicie el gateway, en cuanto "
                  "esta conversación quede en reposo. No lo reinicies tú.")
        elif act.get("restarted"):
            print("  · Gateway reiniciado: el cambio ya está activo.")
        elif act:
            print("  · " + (act.get("detail") or "El cambio aplica al arrancar el gateway."))
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
