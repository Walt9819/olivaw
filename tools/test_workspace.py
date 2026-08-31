r"""The agent's working directory: the advice must be right, and the default must be safe.

The dangerous change here is not the UI, it is the default. Re-running the wizard on a
machine where an agent already works must not hand it a different folder - the old files
would still be on disk, but the agent would stop opening the directory that holds them.

Run: python tools/test_workspace.py
"""

import io
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from wizard import workspace as W  # noqa: E402

PASSED, FAILED = [], []


def check(name, cond, extra=""):
    (PASSED if cond else FAILED).append(name)
    print(("  ok   " if cond else "  FAIL ") + name +
          (("\n       " + str(extra)) if (extra and not cond) else ""))


def section(t):
    print("\n=== %s ===" % t)


def main():
    tmp = tempfile.mkdtemp(prefix="ws-")
    used = os.path.join(tmp, "ya-trabaja-aqui")
    os.makedirs(used)
    io.open(os.path.join(used, "CLAUDE.md"), "w", encoding="utf-8").write("# hi")

    section("the default must not strand an agent that already works somewhere")
    os.environ["CLAUDE_BRIDGE_WORKSPACE"] = used
    check("an existing working directory is suggested again, not replaced",
          W.suggest("Daneel") == used, W.suggest("Daneel"))

    os.environ["CLAUDE_BRIDGE_WORKSPACE"] = os.path.join(tmp, "nada")
    s = W.suggest("Daneel")
    check("a clean machine gets a folder named after the agent",
          s.lower().endswith("daneel-workspace"), s)
    check("two agents do not silently share one",
          W.suggest("Blanca") != W.suggest("Daneel"))
    check("no name still yields a sensible default",
          W.suggest("").lower().endswith("hermes-workspace"), W.suggest(""))
    os.environ.pop("CLAUDE_BRIDGE_WORKSPACE", None)

    section("what it refuses outright")
    inst = os.path.join(tmp, "olivaw-install")
    os.makedirs(os.path.join(inst, "src"))
    r = W.inspect(os.path.join(inst, "agente"), install_dir=inst)
    check("a folder inside Olivaw's install is refused (updates overwrite it)",
          not r["ok"] and "actualizaci" in r["detail"], r)
    check("empty input is refused with an instruction, not a stack trace",
          not W.inspect("")["ok"])
    f = os.path.join(tmp, "un-archivo.txt")
    io.open(f, "w", encoding="utf-8").write("x")
    check("a file is not accepted as a folder", not W.inspect(f)["ok"])
    check("a path whose parent does not exist is refused",
          not W.inspect(os.path.join(tmp, "no", "existe", "aun"))["ok"])

    section("what it warns about but still allows")
    for fake, why in ((os.path.join(tmp, "OneDrive", "agente"), "OneDrive"),
                      (os.path.join(tmp, "Dropbox", "agente"), "Dropbox"),
                      (os.path.join(tmp, "OneDrive - Acme", "agente"), "a business OneDrive")):
        os.makedirs(os.path.dirname(fake), exist_ok=True)
        res = W.inspect(fake)
        check("%s is allowed but flagged for sync conflicts" % why,
              res["ok"] and any("sincroniza" in w for w in res["warnings"]), res)

    busy = os.path.join(tmp, "mis-cosas")
    os.makedirs(busy)
    for i in range(3):
        io.open(os.path.join(busy, "doc%d.txt" % i), "w", encoding="utf-8").write("x")
    res = W.inspect(busy)
    check("an unrelated folder with files warns that things will mix",
          res["ok"] and any("mezcl" in w for w in res["warnings"]), res)
    check("and it says nothing is deleted",
          any("nada se borra" in w for w in res["warnings"]))

    res = W.inspect(used)
    check("an existing agent folder is recognised, not warned about",
          res["ok"] and res["reused"] and not res["warnings"], res)

    section("useful facts it reports")
    res = W.inspect(os.path.join(tmp, "nueva"))
    check("free space is reported", bool(res["free"]), res)
    check("a folder that does not exist yet is fine (created on activation)",
          res["ok"] and not res["exists"])
    check("the path comes back absolute and normalised",
          os.path.isabs(res["path"]))
    check("~ is expanded", W.inspect("~")["path"] == os.path.abspath(os.path.expanduser("~")))

    section("creating it")
    target = os.path.join(tmp, "creada")
    r = W.create(target)
    check("create() makes the folder", r["ok"] and os.path.isdir(target), r)
    check("create() on a refused path does not make anything",
          not W.create(os.path.join(inst, "nope"), install_dir=inst)["ok"]
          and not os.path.isdir(os.path.join(inst, "nope")))

    section("the picker")
    check("this platform reports whether it has one", isinstance(W.picker_available(), bool))

    shutil.rmtree(tmp, ignore_errors=True)
    print("\n%d passed, %d failed" % (len(PASSED), len(FAILED)))
    for f in FAILED:
        print("  - " + f)
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
