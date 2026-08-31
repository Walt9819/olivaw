r"""Nothing the supervisor watches may be restarted in a hot loop.

Hermes' `gateway run` refuses to start a second gateway for a profile: it prints
"Gateway already running (PID ...)" and exits 1 immediately. The supervisor judged the
child dead and started it again on the next 15-second tick - forever. A freshly created
agent produced 56 spawns in fourteen minutes and would have kept going indefinitely,
burning a process launch every 15 seconds and drowning launcher.log in noise that hides
real failures.

The lesson is not "special-case the gateway". The main bridge and every extra agent's
bridge had the same shape - "child is dead, start it again" on a 15-second timer - and
would have produced the identical disturbance for a port already in use, a bad interpreter
path, or any other refusal. All of them now share one restart policy, and this suite asserts
that a permanently-failing child of ANY kind is attempted a handful of times, not hundreds.

Run: python tools/test_supervision.py
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
              ent.get("gw_rs", {}).get("retry_at", 0) > 0, ent)

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
              ent.get("gw_rs", {}).get("started_at", 0) > 0, ent)
        check("it is no longer marked external", ent.get("gw_external") is False)

        section("backing off after immediate exits")
        ladder = [L._gw_backoff(n) for n in range(1, 8)]
        check("the wait grows: 1, 2, 4, 8 minutes", ladder[:4] == [60, 120, 240, 480], ladder)
        check("and is capped at 15 minutes", max(ladder) == 900, ladder)
        check("it never returns zero, which would be a hot loop", min(ladder) >= 60, ladder)
        check("a first failure still waits a full minute", L._gw_backoff(1) == 60)

        section("the cooldown is honoured by the starter")
        L._hctl = FakeHctl(running=False)
        ent = {"gw_rs": {"started_at": 0.0, "fails": 3,
                         "retry_at": L.time.time() + 300}}
        spawned.clear()
        check("no spawn while backing off",
              L._start_gateway(AGENT, ent) is None and not spawned, spawned)
        ent["gw_rs"]["retry_at"] = 0
        check("and it starts again once the wait has passed",
              L._start_gateway(AGENT, ent) is not None)

        section("agents without a channel are not given a gateway")
        L._hctl = FakeHctl(running=False)
        spawned.clear()
        quiet = dict(AGENT, gateway_enabled=False)
        check("nothing is spawned", L._start_gateway(quiet, {}) is None and not spawned)

        section("the shared policy: quick deaths widen the gap, real runs reset it")
        rs = {"started_at": L.time.time() - 1, "fails": 0, "retry_at": 0.0}
        L._died(rs, "test")
        check("a child that died in 1s counts as a failure", rs["fails"] == 1, rs)
        check("and is not retried immediately", not L._may_start(rs))
        L._died(rs, "test")
        check("consecutive failures accumulate", rs["fails"] == 2, rs)
        rs2 = {"started_at": L.time.time() - 3600, "fails": 3, "retry_at": 999}
        L._died(rs2, "test")
        check("a child that ran for an hour resets the counter", rs2["fails"] == 0, rs2)
        check("and may start again at once", L._may_start(rs2))

        section("a permanently-failing child is not retried hundreds of times")
        # 24 hours of 15-second ticks against something that refuses to stay up.
        TICKS, STEP = 5760, 15
        clock = [1000.0]
        real_time = L.time.time
        L.time.time = lambda: clock[0]
        try:
            rs = {"started_at": 0.0, "fails": 0, "retry_at": 0.0}
            attempts = 0
            for _ in range(TICKS):
                if L._may_start(rs):
                    attempts += 1
                    L._started(rs)
                    clock[0] += 1          # it dies one second later
                    L._died(rs, "doomed")
                clock[0] += STEP
        finally:
            L.time.time = real_time
        check("a whole day of ticks yields a handful of attempts, not thousands",
              attempts <= 100, "attempts=%d over %d ticks" % (attempts, TICKS))
        check("without the policy this would have been one per tick",
              attempts < TICKS / 10, "attempts=%d" % attempts)
        print("       %d attempts across 24h of 15s ticks (was: %d)" % (attempts, TICKS))

        section("an ADOPTED bridge must not be 'started' on every tick")
        # The subtle one, and the reason the first fix was not enough: start_bridge()
        # adopts a port that is already serving and returns None. No child is ever
        # created, so no child can ever die, so a backoff keyed on deaths never engages.
        # The branch simply fired forever. This drives the real keep-alive loop.
        agent = dict(AGENT, gateway_enabled=False)
        starts, lines = [], []
        real = (L._load_extra_agents, L.bridge_status, L.start_bridge, L.log)
        L._load_extra_agents = lambda: [agent]
        L.bridge_status = lambda cfg: True          # something is already serving 8792
        L.start_bridge = lambda cfg: (starts.append(cfg) or None)
        L.log = lambda m: lines.append(m)
        try:
            state = {"extra": {}}
            for _ in range(200):                    # ~50 minutes of ticks
                L._reconcile_extras({"env": {}, "bridge_cmd": [sys.executable]}, state)
            check("start_bridge is not called for a port already served",
                  not starts, "called %d times" % len(starts))
            said = [m for m in lines if "starting bridge for agent" in m]
            check("and it does not announce a start it never makes",
                  not said, "logged %d times" % len(said))
            adopted = [m for m in lines if "adopting it" in m]
            check("the adoption is reported exactly once, not every tick",
                  len(adopted) == 1, "logged %d times" % len(adopted))

            section("but a port that goes quiet IS started")
            L.bridge_status = lambda cfg: False
            state["extra"]["daneel"]["bridge_rs"]["retry_at"] = 0
            L._reconcile_extras({"env": {}, "bridge_cmd": [sys.executable]}, state)
            check("a real start happens once the port stops answering", len(starts) == 1,
                  "called %d times" % len(starts))

            section("and a bridge that refuses to start is not hammered")
            starts.clear()
            state = {"extra": {}}
            for _ in range(200):
                L._reconcile_extras({"env": {}, "bridge_cmd": [sys.executable]}, state)
            check("200 ticks against a refusing bridge yield a handful of attempts",
                  len(starts) <= 60, "called %d times over 200 ticks" % len(starts))
            print("       %d start attempts across 200 ticks (was: 200)" % len(starts))
        finally:
            (L._load_extra_agents, L.bridge_status, L.start_bridge, L.log) = real

        section("a failure to spawn does not become a loop either")
        def boom(cmd, cwd=None, **kw):
            raise OSError("cannot start")
        L.subprocess.Popen = boom
        L._hctl = FakeHctl(running=False)
        ent = {}
        check("returns None instead of raising", L._start_gateway(AGENT, ent) is None)
        check("and sets a cooldown", ent.get("gw_rs", {}).get("retry_at", 0) > 0, ent)
    finally:
        L._hctl, L.subprocess.Popen, L.log = real_hctl, real_popen, real_log

    print("\n%d passed, %d failed" % (len(PASSED), len(FAILED)))
    for f in FAILED:
        print("  - " + f)
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
