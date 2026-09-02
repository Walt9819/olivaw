r"""Which browser this agent is driving — invisible, or a real window the owner can see.

The agent needs to answer one question before it starts a browsing task: does this need a
logged-in session? Headless Chromium has no logins and never will; the CDP window has
whatever the owner signed into once. Getting that wrong wastes a long tool loop failing at
a login wall.

Usage:
    python browser_mode.py                 # or `status` — what am I driving?
    python browser_mode.py status --json
    python browser_mode.py enable          # ask first: this opens a window on her screen
    python browser_mode.py disable

Exit codes: 0 done · 1 could not · 2 wrong usage.

`enable` opens a visible browser on the owner's screen. Never run it unprompted — propose
it, and let her say yes.
"""

import argparse
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))          # <install>/src/tools
sys.path.insert(0, os.path.dirname(_HERE))                  # <install>/src

# Spanish + a piped stdout is cp1252 on Windows; without this the first accent raises
# UnicodeEncodeError and the agent gets a traceback instead of its browser mode.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):  # pragma: no cover
        pass

from wizard import browser_setup as bs  # noqa: E402


def _human(st):
    lines = []
    if st["mode"] == "cdp" and st["connected"]:
        lines.append("Navegador REAL: manejas una ventana que el dueño puede ver.")
        lines.append("  navegador : %s" % (st["browser"] or "?"))
        lines.append("  endpoint  : %s" % st["cdp_url"])
        lines.append("  perfil    : %s" % st["data_dir"])
        lines.append("")
        lines.append("Las sesiones iniciadas en esa ventana están disponibles. No cierres")
        lines.append("pestañas ajenas ni hagas nada destructivo sin confirmarlo.")
    elif st["mode"] == "cdp":
        lines.append("Configurado en navegador real, pero NO responde: %s" % st["cdp_url"])
        lines.append("  detalle   : %s" % st["detail"])
        lines.append("")
        lines.append("Díselo al dueño: la ventana se cerró. Él puede reabrirla desde Olivaw,")
        lines.append("o puedes proponerle `enable`.")
    else:
        lines.append("Navegador invisible (headless): puedes leer y extraer, pero NO hay")
        lines.append("ninguna sesión iniciada.")
        lines.append("")
        lines.append("Si la tarea necesita entrar a una cuenta suya, proponle activar el")
        lines.append("navegador real — abre una ventana en su pantalla, así que pregúntale.")
        if not st["browser_found"]:
            lines.append("")
            lines.append("Aviso: no hay Chrome/Edge/Brave en este equipo, así que el modo real")
            lines.append("no está disponible aunque lo pida.")
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="browser_mode",
        description="Consulta o cambia qué navegador maneja este agente.")
    ap.add_argument("action", nargs="?", default="status",
                    choices=["status", "enable", "disable"],
                    help="status (por defecto) · enable · disable")
    ap.add_argument("--profile", default=None, help="perfil de Hermes")
    ap.add_argument("--json", action="store_true", help="salida en JSON")
    args = ap.parse_args(argv)
    profile = args.profile or None

    if args.action == "status":
        st = bs.status(profile)
        print(json.dumps(st, indent=2, ensure_ascii=False) if args.json else _human(st))
        return 0

    if args.action == "enable":
        res = bs.enable(profile)
    else:
        res = bs.disable(profile)

    if args.json:
        print(json.dumps({"applied": res, "state": bs.status(profile)},
                         indent=2, ensure_ascii=False))
    else:
        print(res.get("detail", ""))
        if res.get("ok") and args.action == "enable":
            print("  · Perfil del navegador: %s" % res.get("data_dir", ""))
            print("  · Si un sitio pide contraseña, que la escriba el dueño en esa ventana.")
    return 0 if res.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
