# -*- coding: utf-8 -*-
r"""The first screen, and the two minutes it used to take to draw.

What was wrong
--------------
The dashboard is the first thing the owner sees, and on this machine - seven agents - it
spent **124 seconds** before anything appeared, and 285 more refining it. Not one slow
thing: three cheap mistakes multiplying.

* It walked the agents one after another. Nothing any agent answers depends on another
  agent, so that was seven times a wait that could have been one.
* Each agent's **fast** answer launched `hermes` three times anyway. "Fast" had been
  written to mean "skip the network", but on Windows the expensive thing is not the
  network - it is starting a Python interpreter, two and a quarter seconds a time.
* One of those three launches asked whether that agent's gateway was running. The
  dashboard already knew: it asks every agent that, together, three lines earlier. And
  `hermes gateway status` prints EVERY profile and its pid, so the first of the seven
  calls already held all seven answers.

So these tests are about the shape of the fix rather than the clock: that a fast answer
costs no subprocess and no network, that one gateway read answers for every agent, that
what it cannot PROVE is asked properly rather than guessed, and that a caller who
already knows something is not made to find it out again.

Run: python tools/test_dashboard.py
"""

import os
import shutil
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from wizard import connections as C      # noqa: E402
from wizard import hermes_ctl as H       # noqa: E402
from wizard import talk as T             # noqa: E402

PASSED, FAILED = [], []


def check(name, cond, extra=""):
    (PASSED if cond else FAILED).append(name)
    print(("  ok   " if cond else "  FAIL ") + name +
          (("\n       " + str(extra)) if (extra and not cond) else ""))


def section(t):
    print("\n=== %s ===" % t)


# What `hermes gateway status` really prints on this machine, captured verbatim -
# including the tick, the em dash and the alignment, because this is a parser and the
# characters are the input.
REAL = ("\u2713 Windows login item installed: C:\\Users\\x\\Startup\\Hermes_Gateway.vbs\n"
        "\u2713 Gateway process running (PID: 136116)\n"
        "\n"
        "Other profiles:\n"
        "  \u2713 analecta         \u2014 PID 123968\n"
        "  \u2713 baco             \u2014 PID 158044\n"
        "  \u2713 daneel           \u2014 PID 77968\n"
        "  \u2713 forja            \u2014 PID 117620\n"
        "  \u2713 heraldo          \u2014 PID 81732\n"
        "  \u2713 squadron         \u2014 PID 72676\n")


class Runs(object):
    """Stands in for every `hermes` launch, and counts them."""

    def __init__(self, out="", ok=True):
        self.calls = []
        self.out = out
        self.ok = ok

    def __call__(self, args, timeout=None, **kw):
        self.calls.append(list(args))
        return {"ok": self.ok, "out": self.out, "err": "", "code": 0 if self.ok else 1}

    def said(self, word):
        return [c for c in self.calls if word in " ".join(str(x) for x in c)]


def main():
    section("one gateway read, every agent")
    runs = Runs(REAL)
    old_run, H.run = H.run, runs
    try:
        st = H.gateway_states("hermes")
    finally:
        H.run = old_run
    check("one launch, not seven", len(runs.calls) == 1, runs.calls)
    check("the agent it was asked about is settled", st.get("default") is True, st)
    check("and so is every other one", all(st.get(s) is True for s in
          ("analecta", "baco", "daneel", "forja", "heraldo", "squadron")), st)
    check("nothing else wandered in", len(st) == 7, st)

    section("what it cannot prove, it does not answer")
    # A profile named with no pid beside it. Reporting it stopped would put a red
    # "pausado" on an agent that may well be working and invite the owner to restart it;
    # leaving it out sends the caller to ask that one agent properly.
    nopid = REAL.replace("  \u2713 daneel           \u2014 PID 77968\n",
                         "  \u2717 daneel           \u2014 not running\n")
    runs = Runs(nopid)
    old_run, H.run = H.run, runs
    try:
        st = H.gateway_states("hermes")
    finally:
        H.run = old_run
    check("an agent with no pid is absent, not 'stopped'", "daneel" not in st, st)
    check("the others are unaffected", st.get("heraldo") is True, st)

    runs = Runs("\u2713 Gateway process running (PID: 1)\n")
    old_run, H.run = H.run, runs
    try:
        st = H.gateway_states("hermes")
    finally:
        H.run = old_run
    check("no 'Other profiles' block is not an error", st == {"default": True}, st)

    runs = Runs("Gateway process not running\n\nOther profiles:\n  \u2713 baco \u2014 PID 7\n")
    old_run, H.run = H.run, runs
    try:
        st = H.gateway_states("hermes")
    finally:
        H.run = old_run
    check("the agent asked about CAN be proved stopped", st.get("default") is False, st)
    check("and its neighbours still read correctly", st.get("baco") is True, st)

    runs = Runs("", ok=False)
    old_run, H.run = H.run, runs
    try:
        st = H.gateway_states("hermes")
    finally:
        H.run = old_run
    check("a command that failed answers for nobody", st == {}, st)

    section("the .env is where it is, not where hermes says it is")
    tmp = tempfile.mkdtemp(prefix="olivaw-dash-")
    os.environ["HERMES_HOME"] = tmp
    try:
        check("home() is the profile's own folder",
              H.home("daneel") == os.path.join(tmp, "profiles", "daneel"), H.home("daneel"))
        check("and the root for the main agent", H.home() == tmp, H.home())

        with open(os.path.join(tmp, ".env"), "w", encoding="utf-8") as fh:
            fh.write("TELEGRAM_BOT_TOKEN=x\n")
        runs = Runs("")
        old_run, H.run = H.run, runs
        try:
            got = H.env_path("hermes")
        finally:
            H.run = old_run
        check("a file that is there is not asked about",
              got == os.path.join(tmp, ".env") and not runs.calls, (got, runs.calls))

        # And when it is NOT there the question is a real one - that is the path the
        # wizard uses to write a profile's first .env, and it must still be asked.
        os.remove(os.path.join(tmp, ".env"))
        elsewhere = os.path.join(tmp, "otro-sitio", ".env")
        runs = Runs(elsewhere)
        old_run, H.run = H.run, runs
        try:
            got = H.env_path("hermes")
        finally:
            H.run = old_run
        check("a file that is not there still asks hermes",
              got == elsewhere and len(runs.calls) == 1, (got, runs.calls))
        check("and what it asked was the env-path question",
              runs.said("env-path"), runs.calls)
    finally:
        os.environ.pop("HERMES_HOME", None)
        shutil.rmtree(tmp, ignore_errors=True)

    section("a fast answer costs no launch and no network")
    gw = C.gateway("heraldo", "hermes", fast=True)
    check("the engine is not asked in fast", gw.get("running") is None and gw.get("checking"), gw)

    runs = Runs("true")
    old_run, H.run = H.run, runs
    try:
        T._forget_gate()
        tk = C.talk("heraldo", "hermes", None, fast=True)
        first = len(runs.calls)
        tk2 = C.talk("heraldo", "hermes", None, fast=True)
        second = len(runs.calls) - first
    finally:
        H.run = old_run
        T._forget_gate()
    check("the switch is one launch, and only the first time",
          first == 1 and second == 0, (first, second))
    check("and it says it is still looking, not that it is ready",
          tk.get("enabled") is True and tk.get("ready") is False and tk.get("checking"),
          tk)
    check("the second answer is the same answer", tk2 == tk, (tk, tk2))

    section("a caller that already knows is not made to ask again")
    runs = Runs(REAL)
    old_run, H.run = H.run, runs
    try:
        snap = C.snapshot("heraldo", "hermes", None, fast=True, gateway_running=True)
    finally:
        H.run = old_run
    check("the gateway was never asked", not runs.said("gateway"), runs.calls)
    check("and the handed-in answer is what comes back",
          (snap.get("gateway") or {}).get("running") is True, snap.get("gateway"))
    check("with a sentence to go with it, not a bare flag",
          "corriendo" in (snap.get("gateway") or {}).get("detail", ""), snap.get("gateway"))

    section("a line that says 'checking' rather than 'off'")
    head = C._headline({"talk": {"enabled": True, "ready": False, "checking": True},
                        "telegram": {"state": "checking"}, "whatsapp": {}, "brain": {}})
    row = [r for r in head if r["label"] == "Desde esta pantalla"][0]
    check("an agent still being checked is not reported off", row["state"] == "checking", row)
    off = C._headline({"talk": {"enabled": False, "ready": False}, "telegram": {},
                       "whatsapp": {}, "brain": {}})
    row = [r for r in off if r["label"] == "Desde esta pantalla"][0]
    check("and one that is genuinely off still says so", row["state"] == "off", row)
    ready = C._headline({"talk": {"enabled": True, "ready": True}, "telegram": {},
                         "whatsapp": {}, "brain": {}})
    row = [r for r in ready if r["label"] == "Desde esta pantalla"][0]
    check("and a ready one is not left saying 'checking'", row["state"] == "ok", row)

    section("waiting for all of them at once")
    from wizard import wizard_server as W

    def _slow(n):
        def go():
            time.sleep(0.3)
            return n
        return go

    t0 = time.time()
    got = W._at_once([_slow(1), _slow(2), _slow(3)])
    took = time.time() - t0
    check("three waits of 0.3s take 0.3s, not 0.9s", took < 0.7, "%.2fs" % took)
    check("and the answers come back in the order asked", got == [1, 2, 3], got)

    def _boom():
        raise RuntimeError("no")

    got = W._at_once([_slow(1), _boom, _slow(3)])
    check("one that fails does not take the page with it", got == [1, None, 3], got)
    check("an empty list is not an error", W._at_once([]) == [], "")

    section("the whole snapshot: one wave, and the fallback is a wave too")
    calls = []

    class Fake(object):
        """Hermes, if it answered instantly and said who was asked what."""

        def profile_list(self, hp):
            calls.append("profile_list")
            return [{"name": n} for n in ("default", "daneel", "heraldo")]

        def gateway_states(self, hp):
            calls.append("gateway_states")
            return {"default": True, "daneel": True}   # heraldo unproven on purpose

        def gateway_status(self, hp, profile=None):
            calls.append("gateway_status:%s" % (profile or "default"))
            return {"running": False}

        def pairing_list(self, hp, profile=None):
            calls.append("pairing_list:%s" % (profile or "default"))
            return {"approved": ["owner-" + (profile or "default")]}

        def profile_exists(self, slug, hp):
            calls.append("profile_exists:%s" % slug)
            return True

    saved = {k: getattr(W, k, None) for k in ("hermes_ctl", "which", "_bridge_up")}
    reg = W.agents_registry.list_agents
    look = W.avatars.look
    assign = W.browser_label.assign_all
    icfg = W.intercom.config
    try:
        W.hermes_ctl = Fake()
        W.which = lambda _n: "hermes"
        W._bridge_up = lambda _p: True
        W.agents_registry.list_agents = lambda _d: [
            {"slug": "daneel", "port": 8643}, {"slug": "heraldo", "port": 8642}]
        W.avatars.look = lambda *a, **k: {}
        W.browser_label.assign_all = lambda *a, **k: None
        W.intercom.config = lambda: {}
        snap = W.agents_snapshot()
    finally:
        for k, v in saved.items():
            setattr(W, k, v)
        W.agents_registry.list_agents = reg
        W.avatars.look = look
        W.browser_label.assign_all = assign
        W.intercom.config = icfg

    check("the profile list is read once for all of them",
          calls.count("profile_list") == 1, calls)
    check("and the gateways once for all of them",
          calls.count("gateway_states") == 1, calls)
    check("the one agent it could not prove is asked, by name",
          calls.count("gateway_status:heraldo") == 1 and
          not [c for c in calls if c.startswith("gateway_status:") and "heraldo" not in c],
          calls)
    check("every agent's owners are read, once each",
          sorted(c for c in calls if c.startswith("pairing_list")) ==
          ["pairing_list:daneel", "pairing_list:default", "pairing_list:heraldo"], calls)
    check("nobody asks whether a profile exists after the list answered",
          not [c for c in calls if c.startswith("profile_exists")], calls)

    rows = {"default": snap["default"]}
    for r in snap["extra"]:
        rows[r["slug"]] = r
    check("the proved ones are reported running",
          rows["default"]["gateway_running"] is True and
          rows["daneel"]["gateway_running"] is True, rows)
    check("and the asked one gets the answer it was given",
          rows["heraldo"]["gateway_running"] is False, rows)
    check("the main agent's owners are its own, not another's",
          rows["default"]["owners"] == ["owner-default"], rows["default"].get("owners"))
    check("and each extra agent's are its own",
          rows["heraldo"]["owners"] == ["owner-heraldo"], rows["heraldo"].get("owners"))

    section("an agent whose profile is gone is still a restore candidate")
    calls[:] = []

    class Gone(Fake):
        def profile_list(self, hp):
            calls.append("profile_list")
            return [{"name": "default"}, {"name": "daneel"}]

    saved_h, W.hermes_ctl = W.hermes_ctl, Gone()
    saved_w, W.which = W.which, (lambda _n: "hermes")
    saved_b, W._bridge_up = W._bridge_up, (lambda _p: True)
    reg = W.agents_registry.list_agents
    look, assign, icfg = W.avatars.look, W.browser_label.assign_all, W.intercom.config
    try:
        W.agents_registry.list_agents = lambda _d: [
            {"slug": "daneel", "port": 8643}, {"slug": "heraldo", "port": 8642}]
        W.avatars.look = lambda *a, **k: {}
        W.browser_label.assign_all = lambda *a, **k: None
        W.intercom.config = lambda: {}
        snap = W.agents_snapshot()
    finally:
        W.hermes_ctl, W.which, W._bridge_up = saved_h, saved_w, saved_b
        W.agents_registry.list_agents = reg
        W.avatars.look, W.browser_label.assign_all, W.intercom.config = look, assign, icfg

    missing = [r for r in snap["extra"] if r["slug"] == "heraldo"][0]
    check("a profile the list does not name is flagged, not drawn as working",
          missing.get("missing_profile") is True and missing.get("gateway_running") is None,
          missing)
    check("and nobody goes asking its gateway how it is",
          not [c for c in calls if c.startswith("gateway_status:heraldo")], calls)

    print("\n%d passed, %d failed" % (len(PASSED), len(FAILED)))
    for f in FAILED:
        print("  - " + f)
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
