r"""Every element the wizard's JavaScript reaches for must actually exist in its markup.

app.js builds its HTML as concatenated strings and then wires it up by id. Nothing checks
that the two halves agree, so deleting or renaming one line of markup leaves a handler
silently bound to `null` - the button is simply dead, and it looks fine in review. That is
exactly the mistake made while adding the escalation panel: an edit dropped `waPill`, and
the WhatsApp pairing status would have stopped updating with no error anywhere.

This cross-checks the ids the code addresses against the ids it renders, and asserts the
escalation panel is wired end to end.

Run: python tools/test_wizard_markup.py
"""

import io
import os
import re
import subprocess
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP = os.path.join(ROOT, "src", "wizard", "web", "app.js")

PASSED, FAILED = [], []


def check(name, cond, extra=""):
    (PASSED if cond else FAILED).append(name)
    print(("  ok   " if cond else "  FAIL ") + name + (("\n       " + str(extra))
                                                       if (extra and not cond) else ""))


def main():
    src = io.open(APP, encoding="utf-8").read()

    print("=== app.js is valid JavaScript ===")
    node = shutil.which("node")
    if node:
        p = subprocess.run([node, "--check", APP], capture_output=True, text=True, timeout=60)
        check("node --check passes", p.returncode == 0, p.stderr[-400:])
    else:
        print("  skip (node not available)")

    # Ids the markup creates: literal attributes in app.js, ids produced through
    # chLine("x"), and the static shell in index.html - the page frame (panel, stepper,
    # toast, the SOS console) lives there, not in the JS.
    shell = io.open(os.path.join(ROOT, "src", "wizard", "web", "index.html"),
                    encoding="utf-8").read()
    rendered = set(re.findall(r'id="([A-Za-z0-9_]+)"', src))
    rendered |= set(re.findall(r'chLine\("([A-Za-z0-9_]+)"\)', src))
    rendered |= set(re.findall(r'id="([A-Za-z0-9_]+)"', shell))
    # Ids the code looks up.
    referenced = set(re.findall(r'\bel\("([A-Za-z0-9_]+)"\)', src))

    print("\n=== every el(\"id\") the code uses is rendered somewhere ===")
    missing = sorted(referenced - rendered)
    check("no handler is bound to an element that is never rendered",
          not missing, "orphans: " + ", ".join(missing))
    print("       %d ids rendered, %d referenced" % (len(rendered), len(referenced)))

    print("\n=== the escalation panel is wired end to end ===")
    for wid in ("escOn", "escBody", "escList", "escWarn", "escPill",
                "escNewLabel", "escNewDesc", "escNewPri", "escAdd", "escSave"):
        check("%s is rendered" % wid, wid in rendered)
    for wid in ("escOn", "escAdd", "escSave"):
        check("%s has a handler" % wid, ('el("%s")' % wid) in src)

    print("\n=== the WhatsApp panel it was added to is intact ===")
    for wid in ("waPair", "waQr", "waCloud", "waUsers", "waSave", "waPill", "waQrBox"):
        check("%s survived the edit" % wid, wid in rendered)

    print("\n=== the agent's folder is asked for, not buried ===")
    for wid in ("wsPath", "wsPick", "wsDefault", "wsInfo"):
        check("%s is rendered" % wid, wid in rendered)
    for wid in ("wsPick", "wsDefault"):
        check("%s has a handler" % wid, ('el("%s")' % wid) in src)
    # Two folds are titled "Opciones avanzadas" (the brain step has one too), so anchor on
    # the Telegram one specifically - that is where the folder used to be hidden.
    check("it sits on the agent step, ahead of Telegram's advanced fold",
          src.index('id="wsPath"') < src.index("Opciones avanzadas (repositorio"))
    check("and it is not inside any fold at all",
          "<details" not in src[src.index("¿Dónde trabajará?"):src.index('id="wsPath"')])
    check("the old duplicate box under advanced options is gone",
          'field2("workspace"' not in src)
    check("the folder is explained, not merely labelled",
          "respaldar" in src and "trabajar" in src)
    check("it does not overstate what lives there",
          "no es donde se guarda su configuraci" in src.lower())
    check("leaving it blank still sends a concrete path, not an empty string",
          "S.workspace || S.wsSuggested" in src)

    print("\n=== it talks to endpoints the server actually serves ===")
    server = io.open(os.path.join(ROOT, "src", "wizard", "wizard_server.py"),
                     encoding="utf-8").read()
    called = sorted(set(re.findall(r'api\("channel/([a-z0-9-]+)"', src)))
    plain = sorted(set(re.findall(r'api\("((?:workspace|telegram|policy)/[a-z-]+)"', src)))
    routed = set(re.findall(r'route == "([a-z0-9/_-]+)"', server))
    missing_routes = [c for c in plain if c not in routed]
    check("every workspace/telegram route the UI calls exists on the server",
          not missing_routes, "unrouted: " + ", ".join(missing_routes))
    handled = set(re.findall(r'sub == "([a-z0-9-]+)"', server))
    unknown = [c for c in called if c not in handled]
    check("no channel endpoint is called that the server does not handle",
          not unknown, "unhandled: " + ", ".join(unknown))
    for ep in ("escalation-get", "escalation-save"):
        check("%s is both called and handled" % ep, ep in called and ep in handled)
    # The conversation-lifetime panel is the one whose absence is silent AND expensive: a
    # dead save button leaves the agent on Hermes' "never restart" default, and the owner
    # only finds out from the bill.
    for ep in ("policy/get", "policy/save"):
        check("%s is both called and routed" % ep,
              ('api("%s"' % ep) in src and ep in routed, ep)
    # The preview and the save must agree on what a preset means. The server builds one
    # from Olivaw's defaults plus the preset's own keys (context_policy.preset_policy);
    # a browser that instead layered it over the CURRENT policy would show "empieza de
    # cero cada 2.5 h" and then save "nunca", because the recommended preset changes
    # nothing by name.
    merge = re.search(r"function polMerge\(values\)\s*\{(.*?)\n    \}", src, re.S)
    check("polMerge builds a preset from the defaults, as the server does",
          bool(merge) and "POL.defaults" in merge.group(1)
          and "POL.policy" not in merge.group(1),
          merge.group(1) if merge else "polMerge not found")
    check("and the server actually sends those defaults",
          '"defaults": context_policy.DEFAULTS' in server)

    print("\n=== the panel cannot promise what Telegram cannot deliver ===")
    check("the save button posts enabled/reasons/custom together",
          re.search(r'escalation-save".{0,200}enabled.{0,120}reasons.{0,120}custom',
                    src, re.S) is not None)
    check("a new custom reason is sent with an empty key for the server to assign",
          'indexOf("nuevo_") === 0' in src)
    check("the Telegram warning is surfaced, not swallowed",
          "telegram_ready" in src and "escWarn" in src)

    print("\n%d passed, %d failed" % (len(PASSED), len(FAILED)))
    for f in FAILED:
        print("  - " + f)
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
