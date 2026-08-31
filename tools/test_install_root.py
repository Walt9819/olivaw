r"""Which directory holds this machine's agent registry - and why getting it wrong is silent.

An agent is two pieces: a Telegram bot, and a brain on its own port. The supervisor starts
the brain by reading agents.json from the installed directory. The wizard used to write that
file next to whatever copy of the code it happened to be running from.

When those differ - a source checkout on one side, the real install on the other - the agent
is created, its bot logs in and receives messages, and its brain is never started, because
nothing ever read the file it was registered in. The owner sees a setup that reported
success and an agent that will not answer. Nothing errors.

Run: python tools/test_install_root.py
"""

import io
import json
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from wizard import agents_registry as R  # noqa: E402

PASSED, FAILED = [], []


def check(name, cond, extra=""):
    (PASSED if cond else FAILED).append(name)
    print(("  ok   " if cond else "  FAIL ") + name +
          (("\n       " + str(extra)) if (extra and not cond) else ""))


def section(t):
    print("\n=== %s ===" % t)


def make_install(path, marker=True):
    os.makedirs(os.path.join(path, "src"), exist_ok=True)
    if marker:
        io.open(os.path.join(path, "updater.config.json"), "w",
                encoding="utf-8").write('{"repo": "x/y"}')
    return path


def with_layout(code_root, candidates):
    """Run install_root() against a pretend machine."""
    old_code, old_cand = R._CODE_ROOT, R._candidate_installs
    R._CODE_ROOT = code_root
    R._candidate_installs = lambda: candidates
    try:
        return R.install_root()
    finally:
        R._CODE_ROOT, R._candidate_installs = old_code, old_cand


def main():
    tmp = tempfile.mkdtemp(prefix="root-")
    os.environ.pop("OLIVAW_INSTALL_DIR", None)
    try:
        code = make_install(os.path.join(tmp, "checkout"), marker=False)
        real = make_install(os.path.join(tmp, "installed"))

        section("the case that actually broke")
        got = with_layout(code, [real])
        check("running from a checkout resolves to the real install", got == real, got)
        check("so the registry is the one the supervisor reads",
              R.registry_path(got) == os.path.join(real, "agents.json"))
        check("and new agents get their directory there too",
              R.agent_dir("daneel", got).startswith(real))

        section("the normal case must not change")
        got = with_layout(real, [real])
        check("running from the install resolves to itself", got == real, got)
        installed_elsewhere = make_install(os.path.join(tmp, "other"))
        got = with_layout(real, [installed_elsewhere, real])
        check("an install never defers to another install",
              got == real, got)

        section("a machine with nothing installed yet")
        got = with_layout(code, [os.path.join(tmp, "does-not-exist")])
        check("falls back to the running code", got == code, got)
        empty = os.path.join(tmp, "empty-dir")
        os.makedirs(empty, exist_ok=True)
        got = with_layout(code, [empty])
        check("a directory without the marker is not mistaken for an install",
              got == code, got)

        section("an install that predates the rename")
        old_named = make_install(os.path.join(tmp, "HermesBridge"))
        got = with_layout(code, [os.path.join(tmp, "Olivaw"), old_named])
        check("an older install location is still found", got == old_named, got)

        section("the override")
        forced = os.path.join(tmp, "forced")
        os.environ["OLIVAW_INSTALL_DIR"] = forced
        try:
            check("OLIVAW_INSTALL_DIR wins over everything",
                  with_layout(code, [real]) == os.path.abspath(forced))
        finally:
            os.environ.pop("OLIVAW_INSTALL_DIR", None)

        section("adopting agents stranded by the old behaviour")
        io.open(os.path.join(code, "agents.json"), "w", encoding="utf-8").write(json.dumps(
            {"agents": [{"slug": "daneel", "name": "Daneel", "port": 8792},
                        {"slug": "blanca", "name": "Blanca", "port": 8794}]}))
        io.open(os.path.join(real, "agents.json"), "w", encoding="utf-8").write(json.dumps(
            {"agents": [{"slug": "blanca", "name": "Blanca (ya registrada)", "port": 8794}]}))

        old_code = R._CODE_ROOT
        R._CODE_ROOT = code
        try:
            adopted = R.reconcile(real)
            check("the stranded agent is adopted", adopted == ["daneel"], adopted)
            agents = R.load(real)["agents"]
            slugs = sorted(a["slug"] for a in agents)
            check("both agents are now in the canonical registry",
                  slugs == ["blanca", "daneel"], slugs)
            check("an agent already registered is not duplicated",
                  len([a for a in agents if a["slug"] == "blanca"]) == 1)
            check("and the canonical entry wins over the stale one",
                  [a for a in agents if a["slug"] == "blanca"][0]["name"]
                  == "Blanca (ya registrada)")
            check("the stale file is left alone, so the change is reversible",
                  os.path.isfile(os.path.join(code, "agents.json")))
            check("running it again adopts nothing new", R.reconcile(real) == [])
        finally:
            R._CODE_ROOT = old_code

        section("reconcile is inert when there is nothing to fix")
        R._CODE_ROOT = real
        try:
            check("same directory on both sides is a no-op", R.reconcile(real) == [])
        finally:
            R._CODE_ROOT = old_code
        solo = make_install(os.path.join(tmp, "solo"))
        R._CODE_ROOT = os.path.join(tmp, "no-agents-here")
        os.makedirs(R._CODE_ROOT, exist_ok=True)
        try:
            check("no stale registry means no work", R.reconcile(solo) == [])
        finally:
            R._CODE_ROOT = old_code

        section("this machine, right now")
        live = R.install_root()
        check("resolves to a directory that exists", os.path.isdir(live), live)
        check("that directory carries the install marker",
              os.path.isfile(os.path.join(live, "updater.config.json")), live)
        print("       -> %s" % live)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n%d passed, %d failed" % (len(PASSED), len(FAILED)))
    for f in FAILED:
        print("  - " + f)
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
