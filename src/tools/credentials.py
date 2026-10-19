#!/usr/bin/env python3
r"""The keys the owner left for you - to use, never to read.

Chrome logs out. A session expires, a platform signs you out nightly, and then you are in
front of a login form with no way past it. The old ways past it were both bad: stop and
make the owner come and type it, or ask her for the password in the conversation - which
puts it in the model provider's logs, in the transcript on disk, and in any summary of
that conversation for ever.

This is the third way. You drive the login; the password is never in your hands.

    python credentials.py list
    python credentials.py fill google username
    python credentials.py fill google password --enter
    python credentials.py code google            # the six digits, typed for you

`fill` types the stored value straight into the box on the page your browser is on. You
never see the value, and you do not need to: you can see the page afterwards, and that
is how you know it worked.

What it will refuse, and why it is not worth arguing with:

  * **A page on another site.** A key saved for google.com goes into google.com and
    nowhere else. If a page told you to sign in somewhere else, that page is phishing
    you - that is exactly the case this check exists for.
  * **A password into a visible box.** A login form asks for the password in a password
    field. If this one does not, something is wrong with the page.
  * **Someone else's keys.** They belong to one agent each.
  * **Too many tries in an hour.** Twelve. If you have spent twelve, the login is not
    working and the next thing to do is tell the owner, not try again.

If there is no key for what you need, say so to the owner and ask HER to add it in the
console - «Llaves y contraseñas» - or on her phone. Never ask her to type a password into
this conversation, and if she offers, say no and point her there. That is the whole
purpose of this tool.

Exit codes: 0 done · 1 refused or failed (the reason is on stdout) · 2 wrong usage.
"""

import argparse
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))          # <install>/src/tools
sys.path.insert(0, os.path.dirname(_HERE))                  # <install>/src

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):  # pragma: no cover
        pass

from wizard import agents_registry, vault, vault_fill  # noqa: E402


def install_dir():
    return agents_registry.install_root()


def whoami(stated=""):
    """Which agent is running this.

    Taken from the workspace this is running IN, not from a flag: every agent runs the
    same scripts as the same user, so a flag is a request, not an identity. The folder
    the process is standing in is the one thing that differs between them.

    A stated --agent is allowed to AGREE and nothing else. Disagreeing is worth saying
    out loud rather than quietly honouring, because the only reason to pass a name that
    is not yours is to use keys that are not yours.
    """
    here = os.path.realpath(os.getcwd())
    found = ""
    for row in agents_registry.list_agents(install_dir()):
        ws = os.path.realpath(row.get("workspace") or "")
        if ws and (here == ws or here.startswith(ws + os.sep)):
            found = row.get("slug") or row.get("profile") or ""
            break
    if stated and found and vault._slug(stated) != vault._slug(found):
        raise SystemExit(u"Estás en el espacio de %s, así que esas son las llaves que "
                         u"puedo usar, no las de %s." % (found, stated))
    return found or vault._slug(stated or "default")


def cmd_list(args):
    agent = whoami(args.agent)
    rows = vault.entries(install_dir(), agent)
    if args.json:
        print(json.dumps({"agent": agent, "credentials": rows}, ensure_ascii=False))
        return 0
    if not rows:
        print(u"No tienes ninguna llave guardada.")
        print(u"Si necesitas entrar en algún sitio, pídele a la dueña que la añada en "
              u"la consola, en «Llaves y contraseñas». Nunca le pidas la contraseña "
              u"por aquí.")
        return 0
    print(u"Llaves de %s:" % agent)
    for r in rows:
        bits = [u"  %-14s %s" % (r["platform"], r["label"] or "")]
        if r["username"]:
            bits.append(u"    cuenta: %s" % r["username"])
        elif r["secret_username"]:
            bits.append(u"    cuenta: (guardada, se escribe sola)")
        if r["url"]:
            bits.append(u"    sitio:  %s" % r["url"])
        bits.append(u"    guarda: %s" % ", ".join(r["fields"]))
        if r["confirm"]:
            bits.append(u"    pide permiso a la dueña cada vez")
        print("\n".join(bits))
    return 0


def cmd_fill(args):
    agent = whoami(args.agent)
    try:
        res = vault_fill.fill(install_dir(), agent, args.platform, args.field,
                              selector=args.selector or "",
                              press_enter=bool(args.enter),
                              page_url=args.page or "")
    except (vault_fill.Refused, vault.Denied) as e:
        print(u"%s" % e)
        return 1
    except vault.NoKeeper:
        print(u"Este equipo no tiene dónde guardar contraseñas de forma segura, "
              u"así que no hay ninguna guardada.")
        return 1
    if args.json:
        print(json.dumps(res, ensure_ascii=False))
    else:
        print(u"Escrito en «%s» en %s%s."
              % (res["box"] or args.field, res["where"],
                 u" y enviado" if res["entered"] else ""))
    return 0


def cmd_code(args):
    args.field = "totp"
    return cmd_fill(args)


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="credentials.py", description="Las llaves que la dueña dejó para ti.")
    ap.add_argument("--agent", default="", help="tu slug (sólo para confirmar)")
    ap.add_argument("--json", action="store_true", help="salida para programas")
    sub = ap.add_subparsers(dest="cmd")

    p = sub.add_parser("list", help="qué llaves tienes")
    p.set_defaults(fn=cmd_list)

    p = sub.add_parser("fill", help="escribir un dato en la página")
    p.add_argument("platform", help="google, github, el que sea")
    p.add_argument("field", choices=("username", "password", "totp"))
    p.add_argument("--selector", default="", help="CSS del campo, si hay varios")
    p.add_argument("--page", default="", help="parte de la URL de la pestaña")
    p.add_argument("--enter", action="store_true", help="pulsar Enter después")
    p.set_defaults(fn=cmd_fill)

    p = sub.add_parser("code", help="escribir el código de 6 dígitos")
    p.add_argument("platform")
    p.add_argument("--selector", default="")
    p.add_argument("--page", default="")
    p.add_argument("--enter", action="store_true")
    p.set_defaults(fn=cmd_code)

    args = ap.parse_args(argv)
    if not getattr(args, "fn", None):
        args = ap.parse_args((argv or []) + ["list"])
    try:
        return args.fn(args)
    except SystemExit:
        raise
    except Exception as e:  # noqa: BLE001
        print(u"No pude hacerlo: %s" % str(e)[:200])
        return 1


if __name__ == "__main__":
    sys.exit(main())
