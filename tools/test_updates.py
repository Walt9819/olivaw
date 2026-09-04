r"""An update nobody can see is indistinguishable from an update that never happened.

The owner reported that Olivaw "does not update itself". Measured before touching
anything, it does: a real v1.0.30 install, given nothing but its own supervisor and the
public releases, went to v1.0.42 in eleven seconds. So the machinery was never the
problem. Three things around it were:

  1. **the log lied.** With no updater.config.json the launcher announced "supervise-only
     mode (no auto-update)" — while its own defaults turn auto_update ON and the idle gate
     is wide open with no bridge answering. The message sent us hunting a broken updater.
  2. **the rest-hours window was dead config.** `update_from_hour` / `update_until_hour`
     were being written into updater.config.json and read by NOTHING, so the fallback for
     a machine that is never idle stayed one hour wide — and if the machine was off or
     busy during that hour, the night passed with no update.
  3. **nothing was visible.** No version on screen, no badge, no button. An owner had no
     way to tell a current install from one stuck for a month, and no way to say "now".

This suite pins all three, plus the piece that decides whether the new button is honest:
the supervisor heartbeat. The request file is only ever read by the supervisor, so if the
supervisor is not running, a button that writes one does nothing at all — and must say so
rather than pretend.

Run: python tools/test_updates.py
"""

import datetime
import io
import json
import os
import shutil
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

import launcher as L                    # noqa: E402
from wizard import updates as U         # noqa: E402

PASSED, FAILED = [], []


def check(name, cond, extra=""):
    (PASSED if cond else FAILED).append(name)
    print(("  ok   " if cond else "  FAIL ") + name +
          (("\n       " + str(extra)) if (extra and not cond) else ""))


def section(t):
    print("\n=== %s ===" % t)


def at(hour):
    return datetime.datetime(2026, 9, 4, hour, 30)


def main():
    tmp = tempfile.mkdtemp(prefix="upd-")
    real = {k: getattr(L, k) for k in
            ("STATE_PATH", "REQUEST_PATH", "RESULT_PATH", "HEARTBEAT_PATH",
             "VERSION_FILE", "INSTALL_DIR", "log", "latest_release", "apply_update",
             "bridge_status")}
    L.STATE_PATH = os.path.join(tmp, "update.state.json")
    L.REQUEST_PATH = os.path.join(tmp, "update.request")
    L.RESULT_PATH = os.path.join(tmp, "update.result.json")
    L.HEARTBEAT_PATH = os.path.join(tmp, "supervisor.alive")
    L.VERSION_FILE = os.path.join(tmp, "VERSION")
    L.INSTALL_DIR = tmp
    L.log = lambda m: None
    io.open(L.VERSION_FILE, "w", encoding="utf-8").write("1.0.30\n")

    try:
        section("the rest-hours window the config was already asking for")
        # These two keys existed on a real machine and were read by nothing, so the hours
        # the owner chose were silently ignored. That is the bug, not a missing feature.
        cfg = {"update_from_hour": 18, "update_until_hour": 24}
        check("the owner's window is honoured at all", L.rest_window(cfg) == (18, 0),
              L.rest_window(cfg))
        check("inside it is rest time", L.in_rest_window(cfg, at(20)) is True)
        check("and 18:00 itself counts", L.in_rest_window(cfg, at(18)) is True)
        check("outside it is not", L.in_rest_window(cfg, at(9)) is False)
        check("'until 24' means to the end of the day, not to 00:00 and stop",
              L.in_rest_window(cfg, at(23)) is True)

        wrap = {"update_from_hour": 22, "update_until_hour": 6}
        check("a window that crosses midnight works at 23:00",
              L.in_rest_window(wrap, at(23)) is True)
        check("and at 02:00", L.in_rest_window(wrap, at(2)) is True)
        check("but not at 12:00", L.in_rest_window(wrap, at(12)) is False)

        check("from == until means 'whenever'",
              L.in_rest_window({"update_from_hour": 3, "update_until_hour": 3}, at(15)) is True)
        check("nonsense values fall back instead of raising",
              L.rest_window({"update_from_hour": "x", "update_until_hour": None,
                             "nightly_hour": 4}) == (4, 7))

        section("widening the fallback never narrows it for an existing install")
        # Every install out there has only `nightly_hour`. Whatever the new window does, the
        # hour that used to work has to keep working, or an update makes updating worse.
        for n in (0, 4, 22, 23):
            old = {"nightly_hour": n}
            check("nightly_hour=%d still updates at %02d:00" % (n, n),
                  L.in_rest_window(old, at(n)) is True)
            check("nightly_hour=%d now also covers %02d:00" % (n, (n + 2) % 24),
                  L.in_rest_window(old, at((n + 2) % 24)) is True)
        check("but it is a window, not always-on",
              L.in_rest_window({"nightly_hour": 4}, at(13)) is False)

        section("a config-less install updates, and no longer claims otherwise")
        # The old log line said "no auto-update" for a state in which updates DO apply.
        src = io.open(os.path.join(ROOT, "src", "launcher.py"), encoding="utf-8").read()
        check("the misleading 'supervise-only (no auto-update)' claim is gone",
              "supervise-only mode (no auto-update)" not in src)
        check("and the honest version says what is actually missing (the notice)",
              "there is no " in src and "Telegram token yet to announce" in src)
        save_cfg = L.CONFIG_PATH
        L.CONFIG_PATH = os.path.join(tmp, "absent.json")
        L._warned.clear()
        cfgless = L.load_config()
        L.CONFIG_PATH = save_cfg
        check("auto_update defaults ON with no config file", cfgless["auto_update"] is True)
        check("and with nothing answering, the idle gate is open",
              L.is_idle(cfgless) is True)

        section("the heartbeat, which decides whether a button can work at all")
        check("with no heartbeat the answer is 'I don't know', not 'it is dead'",
              U.supervisor(tmp)["known"] is False and
              U.supervisor(tmp)["running"] is False, U.supervisor(tmp))
        L.beat({"auto_update": True})
        sup = U.supervisor(tmp)
        check("a fresh beat reads as running", sup["running"] is True and sup["known"] is True,
              sup)
        check("it carries the pid, so a human can go look", sup["pid"] == os.getpid(), sup)
        check("and the version it is supervising", sup["version"] == "1.0.30", sup)
        stale = json.load(io.open(L.HEARTBEAT_PATH, encoding="utf-8"))
        stale["ts"] = time.time() - 600
        json.dump(stale, io.open(L.HEARTBEAT_PATH, "w", encoding="utf-8"))
        check("a beat from ten minutes ago is not a running supervisor",
              U.supervisor(tmp)["running"] is False, U.supervisor(tmp))
        check("and it is still 'known', so the UI can say 'off' with confidence",
              U.supervisor(tmp)["known"] is True)

        section("the UI asks; only the supervisor acts")
        try:
            os.remove(L.REQUEST_PATH)
        except OSError:
            pass
        check("no request pending by default", L.take_request() is False)
        io.open(os.path.join(tmp, "update.result.json"), "w",
                encoding="utf-8").write('{"ok": true, "detail": "stale"}')
        res = U.request(tmp)
        check("the UI can leave a request", res["ok"] is True, res)
        check("and it clears the previous outcome, so the panel cannot show a stale one",
              not os.path.isfile(os.path.join(tmp, "update.result.json")))
        check("the supervisor sees it", L.take_request() is True)
        check("exactly once — a request is consumed, not a standing order",
              L.take_request() is False)

        section("a forced update skips the waiting, never the safety")
        seen = {}
        L.latest_release = lambda repo: {"version": "1.0.42", "zip_url": "u",
                                         "zip_name": "z.zip", "sha_url": "s",
                                         "changelog": "cosas nuevas"}
        L.apply_update = lambda cfg, state, rel: seen.setdefault("applied", rel["version"])
        cfg = {"auto_update": True, "bridge_url": "http://127.0.0.1:1",
               "nightly_hour": 4, "poll_minutes": 45}

        # busy: a turn in flight. Not even the owner's button may interrupt that - the turn
        # is somebody's message and it would be lost.
        L.bridge_status = lambda c: {"inflight": 1, "idle_seconds": 0, "version": "1.0.30"}
        seen.clear()
        L.maybe_update(cfg, {"extra": {}}, forced=True)
        check("mid-turn, a forced update refuses", "applied" not in seen, seen)
        st = json.load(io.open(L.STATE_PATH, encoding="utf-8"))
        check("and the state says why", st["deferred"] == "mid-turn", st)
        r = json.load(io.open(L.RESULT_PATH, encoding="utf-8"))
        check("the owner is told in words, not left waiting",
              r["ok"] is False and r.get("busy") is True and "contestando" in r["detail"], r)

        # awake but not idle for long enough: the automatic path waits, the button does not
        L.bridge_status = lambda c: {"inflight": 0, "idle_seconds": 5, "version": "1.0.30"}
        seen.clear()
        L.maybe_update(cfg, {"extra": {}})
        check("automatically, a busy machine outside rest hours waits",
              "applied" not in seen, seen)
        check("and says so", json.load(io.open(L.STATE_PATH, encoding="utf-8"))
              ["deferred"] == "not idle")
        seen.clear()
        L.maybe_update(cfg, {"extra": {}}, forced=True)
        check("but the owner pressing the button goes ahead",
              seen.get("applied") == "1.0.42", seen)

        # In the rest window, the automatic path stops waiting for idle. maybe_update reads
        # the clock itself, so the window is built AROUND the current hour rather than
        # patching datetime - and a matching window that excludes it proves the gate is
        # really the window and not something else letting it through.
        h = datetime.datetime.now().hour
        seen.clear()
        L.maybe_update(dict(cfg, update_from_hour=h, update_until_hour=(h + 1) % 24),
                       {"extra": {}})
        check("inside rest hours it applies without waiting for idle",
              seen.get("applied") == "1.0.42", seen)
        seen.clear()
        L.maybe_update(dict(cfg, update_from_hour=(h + 2) % 24,
                            update_until_hour=(h + 3) % 24), {"extra": {}})
        check("and outside them, on the same busy machine, it does not",
              "applied" not in seen, seen)

        section("an update must not skip an agent whose bridge was adopted")
        # Found by watching v1.0.43 install itself on a real machine: the extra agent's
        # bridge kept its pid from the previous day and still reported the NEW version,
        # because /status reads the version out of the VERSION file the update had just
        # rewritten. It was executing the old code and nothing could tell - the two bridges
        # only disagreed on `code_sha`. Cause: a supervisor restart had ADOPTED that bridge,
        # so it had no handle here, and stopping handles left it running.
        stopped, freed = [], []
        real_stop, real_free = L.stop_bridge, L._free_bridge_port
        L.stop_bridge = lambda child, timeout=15: stopped.append(child)
        L._free_bridge_port = lambda cfg: freed.append(cfg.get("bridge_url"))
        L.bridge_status = lambda c: {"inflight": 0, "idle_seconds": 9999,
                                     "version": "1.0.43"}
        try:
            # `child: None` is exactly what adoption leaves behind.
            state = {"extra": {"daneel": {"child": None, "gw": None,
                                          "cfg": {"bridge_url": "http://127.0.0.1:8792"}}}}
            L._stop_extras(state)
            check("without free_ports an adopted bridge is left alone (the old behaviour)",
                  freed == [], freed)
            freed[:] = []
            L._stop_extras(state, free_ports=True)
            check("with free_ports its port is taken back, so new code really runs",
                  freed == ["http://127.0.0.1:8792"], freed)
            # And an agent we DO own must not be killed by port-hunting; stopping its
            # handle already worked, and bridge_status is then the only thing consulted.
            owned = {"extra": {"a": {"child": "handle", "gw": None,
                                     "cfg": {"bridge_url": "http://127.0.0.1:8794"}}}}
            stopped[:] = []
            L._stop_extras(owned, free_ports=True)
            check("an owned bridge is stopped by its handle first",
                  "handle" in stopped, stopped)
            check("and the entry is cleared, so nothing later thinks it is still running",
                  owned["extra"]["a"]["child"] is None, owned)
        finally:
            L.stop_bridge, L._free_bridge_port = real_stop, real_free

        src_l = io.open(os.path.join(ROOT, "src", "launcher.py"), encoding="utf-8").read()
        check("the update path asks for the ports back",
              "_stop_extras(state, free_ports=True)" in src_l)
        check("and so does the rollback path, for the same reason",
              src_l.count("_stop_extras(state, free_ports=True)") == 2, src_l.count(
                  "_stop_extras(state, free_ports=True)"))
        # The reason the bug was invisible, written down so nobody trusts /status version
        # as proof that new code is running.
        cb = io.open(os.path.join(ROOT, "src", "claude_bridge.py"), encoding="utf-8").read()
        check("/status still reports the version from the VERSION file (hence unreliable)",
              "_installed_version()" in cb)
        check("but it also reports a code hash, which a stale process cannot fake",
              "code_sha" in cb)

        section("what the panel is told")
        L.bridge_status = lambda c: {"inflight": 0, "idle_seconds": 9999, "version": "1.0.30"}
        L.publish_state(dict(cfg, update_from_hour=18, update_until_hour=24),
                        {"version": "1.0.42", "changelog": "cosas"})
        st = json.load(io.open(L.STATE_PATH, encoding="utf-8"))
        for k in ("current", "latest", "available", "auto_update", "rest_from",
                  "rest_until", "in_rest_window", "poll_minutes", "checked_at"):
            check("state carries %s" % k, k in st, st)
        check("available compares versions, it does not compare strings",
              st["available"] is True and L.vtuple("1.0.9") < L.vtuple("1.0.10"))
        check("the UI's comparison agrees with the updater's",
              all(U.vtuple(v) == L.vtuple(v) for v in
                  ("1.0.9", "1.0.10", "v1.2.3", "1.0", "", "1.0.42")))

        U._cache.update(at=time.time(), rel={"version": "1.0.42", "changelog": "cosas"},
                        error="")
        got = U.status(tmp)
        check("status reports the installed version", got["current"] == "1.0.30", got)
        check("and that a newer one exists", got["available"] is True, got)
        check("and turns the window into something readable",
              got["rest_text"] == "entre las 18:00 y las 00:00", got["rest_text"])
        check("and whether the thing that applies updates is even running",
              "supervisor_running" in got, got)
        io.open(L.VERSION_FILE, "w", encoding="utf-8").write("1.0.42\n")
        check("once installed, nothing is offered",
              U.status(tmp)["available"] is False, U.status(tmp))

        section("the files live where an update cannot delete them")
        # Only src/ and templates/ are replaced, so anything in the install root survives.
        # A state file inside src/ would be wiped by the very update it is reporting on.
        for name, path in (("update.state.json", L.STATE_PATH),
                           ("update.request", L.REQUEST_PATH),
                           ("supervisor.alive", L.HEARTBEAT_PATH)):
            check("%s sits in the install root, not in src/" % name,
                  os.path.dirname(os.path.abspath(path)) == os.path.abspath(tmp) and
                  os.sep + "src" + os.sep not in path, path)

        section("the wizard exposes it, and only through the supervisor")
        ws = io.open(os.path.join(ROOT, "src", "wizard", "wizard_server.py"),
                     encoding="utf-8").read()
        for route in ("update/status", "update/check", "update/apply"):
            check("the route %s exists" % route, '"%s"' % route in ws)
        check("the wizard never swaps src/ itself — it only requests",
              "updates_mod.request(" in ws and "apply_update" not in ws)
        check("a request with no supervisor starts one first, so the button is not a lie",
              "start_supervisor()" in ws and "sup[\"running\"]" in ws)
        check("the running version is handed to the UI",
              "updates_mod.current_version(" in ws)
        app = io.open(os.path.join(ROOT, "src", "wizard", "web", "app.js"),
                      encoding="utf-8").read()
        check("the sidebar shows a version", "verNum" in app and "Olivaw v" in app)
        check("a badge appears only when there is something to install",
              "badge.hidden = !avail" in app)
        check("there is a section with the button", "secUpdate" in app and "updNow" in app)
        check("the panel names the supervisor being off as the reason nothing updates",
              "sin él no se actualiza solo" in app)
        html = io.open(os.path.join(ROOT, "src", "wizard", "web", "index.html"),
                       encoding="utf-8").read()
        check("the shell has somewhere to put both", 'id="verNum"' in html and
              'id="verBadge"' in html)
    finally:
        for k, v in real.items():
            setattr(L, k, v)
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n%d passed, %d failed" % (len(PASSED), len(FAILED)))
    for f in FAILED:
        print("  - " + f)
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
