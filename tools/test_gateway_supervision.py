r"""The supervisor must not fight a gateway that is already running.

Hermes' `gateway run` refuses to start a second gateway for a profile: it prints
"Gateway already running (PID ...)" and exits 1 immediately. The supervisor judged the
child dead and started it again on the next 15-second tick - forever. A freshly created
agent produced 56 spawns in fourteen minutes and would have kept going indefinitely,
burning a process launch every 15 seconds and drowning launcher.log in noise that hides
real failures.

Two defences, both tested here: ask Hermes before spawning, and back off when a start
fails instead of retrying at full speed.

Run: python tools/test_gateway_supervision.py
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

import launcher as L  # noqa: E402

PASSED, FAILED = [], []


def check(name, cond, extra=""):
    (PASSED if cond else FAILED).append(name)
    print(("  ok   " if cond else "  FAIL ") + name +
          (("\n       " + str(extra)) if (extra and not cond) else ""))


def section(t):
    print("\n=== %s ===" % t)


class FakeHctl:
    """Stands in for hermes_ctl: reports whether a gateway owns the profile."""

    def __init__(self, running):
        self.running = running
        self.status_calls = 0

    def gateway_status(self, hermes=None, profile=None):
        self.status_calls += 1
        return {"running": self.running}

    def _base(self, profile=None):
        return ["cmd", "/c", "%s.bat" % profile]


AGENT = {"slug": "daneel", "profile": "daneel", "port": 8792, "gateway_enabled": True}


def main():
    spawned = []

    def fake_popen(cmd, cwd=None, **kw):
        spawned.append(cmd)
        class P:
            def poll(self_):
                return None
        return P()

    real_hctl, real_popen, real_log = L._hctl, L.subprocess.Popen, L.log
    L.subprocess.Popen = fake_popen
    L.log = lambda m: None
    try:
        section("a gateway that is already running is left alone")
        L._hctl = FakeHctl(running=True)
        ent = {}
        spawned.clear()
        got = L._start_gateway(AGENT, ent)
        check("nothing is spawned", got is None and not spawned, spawned)
        check("it is recorded as externally supervised", ent.get("gw_external") is True)
        check("and a cooldown stops us asking every 15s",
              ent.get("gw_retry_at", 0) > 0)

        before = L._hctl.status_calls
        check("during the cooldown we do not even ask Hermes",
              L._start_gateway(AGENT, ent) is None
              and L._hctl.status_calls == before)

        section("a profile with no gateway is started normally")
        L._hctl = FakeHctl(running=False)
        ent = {}
        spawned.clear()
        got = L._start_gateway(AGENT, ent)
        check("a gateway is spawned", got is not None and len(spawned) == 1, spawned)
        check("with the per-profile wrapper and --external-supervisor",
              "gateway" in spawned[0] and "--external-supervisor" in spawned[0], spawned)
        check("the start time is recorded, so a quick exit can be detected",
              ent.get("gw_started_at", 0) > 0)
        check("it is no longer marked external", ent.get("gw_external") is False)

        section("backing off after immediate exits")
        ladder = [L._gw_backoff(n) for n in range(1, 8)]
        check("the wait grows: 1, 2, 4, 8 minutes", ladder[:4] == [60, 120, 240, 480], ladder)
        check("and is capped at 15 minutes", max(ladder) == 900, ladder)
        check("it never returns zero, which would be a hot loop", min(ladder) >= 60, ladder)
        check("a first failure still waits a full minute", L._gw_backoff(1) == 60)

        section("the cooldown is honoured by the starter")
        L._hctl = FakeHctl(running=False)
        ent = {"gw_retry_at": L.time.time() + 300}
        spawned.clear()
        check("no spawn while backing off",
              L._start_gateway(AGENT, ent) is None and not spawned, spawned)
        ent["gw_retry_at"] = 0
        check("and it starts again once the wait has passed",
              L._start_gateway(AGENT, ent) is not None)

        section("agents without a channel are not given a gateway")
        L._hctl = FakeHctl(running=False)
        spawned.clear()
        quiet = dict(AGENT, gateway_enabled=False)
        check("nothing is spawned", L._start_gateway(quiet, {}) is None and not spawned)

        section("a failure to spawn does not become a loop either")
        def boom(cmd, cwd=None, **kw):
            raise OSError("cannot start")
        L.subprocess.Popen = boom
        L._hctl = FakeHctl(running=False)
        ent = {}
        check("returns None instead of raising", L._start_gateway(AGENT, ent) is None)
        check("and sets a cooldown", ent.get("gw_retry_at", 0) > 0)
    finally:
        L._hctl, L.subprocess.Popen, L.log = real_hctl, real_popen, real_log

    print("\n%d passed, %d failed" % (len(PASSED), len(FAILED)))
    for f in FAILED:
        print("  - " + f)
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
