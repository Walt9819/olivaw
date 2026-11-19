# -*- coding: utf-8 -*-
r"""Diagnosing a channel that stopped, and putting it back - without lying in either direction.

The case this was written against, on the owner's own machine: one agent of seven lost
Telegram. The gateway was running, the token was good, and Telegram answered 502 Bad
Gateway for that bot and only that bot - sixteen seconds per request, while the other six
bots answered in half a second.

Olivaw got all three answers wrong and had no button to try anything:

  * the 502 arrived after the fifteen-second timeout, so the verdict was "unreachable":
    "sin conexion, o un cortafuegos lo bloquea" - on a machine that was talking to Telegram
    six times over;
  * had it arrived a second earlier it would have been worse. Any non-ok answer was read as
    `token_rejected`, whose advice is "create a new token with /token". BotFather revokes
    the old one the instant it issues the next, so following that advice against a healthy
    bot destroys the working credential AND leaves the 502 exactly where it was;
  * the panel had no reconnect at all. The nearest button was "Pausar", which writes
    enabled=false - an owner reaching for it to repair a channel is turning the agent off.

So: only Telegram SAYING NO to the credential may be reported as a dead token, a verdict
must say where it came from, and the panel must be able to act on it.

No network: every HTTP answer here is canned, so the classification is what is under test
rather than the weather.

Run: python tools/test_reconnect.py
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from wizard import connections, hermes_ctl, telegram_health  # noqa: E402

PASSED, FAILED = [], []
TOK = "8106530142:AAHl5JTv8MvsbEXAMPLEnotarealtoken00"


def _ascii(s):
    return str(s).encode("ascii", "replace").decode("ascii")


def check(name, cond, extra=""):
    (PASSED if cond else FAILED).append(name)
    print(("  ok   " if cond else "  FAIL ") + name +
          (("\n       " + _ascii(extra)[:400]) if (extra and not cond) else ""))


def section(t):
    print("\n=== %s ===" % t)


class Calls(object):
    """A stand-in for http_json that answers by URL and remembers what was asked."""

    def __init__(self, answers):
        self.answers = answers          # (ok, data, status) by method name, or callable
        self.urls = []

    def __call__(self, url, data=None, method=None, timeout=25, headers=None):
        self.urls.append((url, timeout))
        # Longest key first. Every Telegram URL ends in /getMe, including the tokenless
        # probe, so a first-match-wins stub answered the probe with the real token's
        # timeout - and quietly proved the opposite of what the test was asserting.
        for key in sorted(self.answers, key=len, reverse=True):
            if "/" + key in url:
                ans = self.answers[key]
                return ans(url) if callable(ans) else ans
        return (True, {"ok": True, "result": {}}, 200)


def with_http(answers, fn):
    """Run fn with telegram_health's HTTP client replaced, and put the real one back."""
    real = telegram_health.http_json
    calls = Calls(answers)
    telegram_health.http_json = calls
    try:
        return fn(), calls
    finally:
        telegram_health.http_json = real


def verdict(answers, token=TOK):
    out, calls = with_http(answers, lambda: telegram_health.check("heraldo", None, token=token))
    return out, calls


# ── what Telegram's own failures mean ─────────────────────────────────────────

def test_a_sick_bot_is_not_a_dead_token():
    section("Telegram refusing a bot is not Telegram refusing the token")

    bad_gw = (False, {"ok": False, "error_code": 502, "description": "Bad Gateway"}, 502)
    res, _ = verdict({"getMe": bad_gw})
    check("a 502 is reported as Telegram's own trouble", res["state"] == "telegram_down",
          res["state"] + ": " + res["detail"])
    check("it is NOT reported as a rejected token", res["state"] != "token_rejected")
    check("and it never tells the owner to make a new token",
          "nuevo" not in res["detail"] or "NO generes un token nuevo" in res["detail"],
          res["detail"])
    check("it says plainly that the token is not the problem",
          "no de tu token" in res["detail"], res["detail"])
    check("it quotes what Telegram actually answered",
          "Bad Gateway" in res["detail"] and "502" in res["detail"], res["detail"])
    check("waiting cannot change it, so the wait does not start",
          "telegram_down" in telegram_health.TERMINAL)

    busy = (False, {"ok": False, "error_code": 429, "description": "Too Many Requests"}, 429)
    res, _ = verdict({"getMe": busy})
    check("a 429 is rate limiting, not a bad token", res["state"] == "telegram_busy",
          res["state"])
    check("and it says so", "No es el token" in res["detail"], res["detail"])

    section("but a token Telegram refuses IS a dead token")
    dead = (False, {"ok": False, "error_code": 401,
                    "description": "Unauthorized"}, 401)
    res, _ = verdict({"getMe": dead})
    check("401 is the revoked token", res["state"] == "token_rejected", res["state"])
    check("and only there is BotFather suggested", "@BotFather" in res["detail"])
    # 404 is what a malformed token path gets. The credential is unusable either way, so
    # the advice is the same one - but it must be reached deliberately, not by falling
    # through from "anything that was not a 200".
    res, _ = verdict({"getMe": (False, {"ok": False, "error_code": 404,
                                        "description": "Not Found"}, 404)})
    check("404 is a token Telegram does not recognise", res["state"] == "token_rejected",
          res["state"])


def test_silence_is_attributed_to_the_right_side():
    section("no answer at all: whose silence is it")

    # urllib could not complete the request: status 0, which is what a timeout looks like.
    timeout = (False, "timed out", 0)
    # ...but the API itself answers a tokenless probe, so this machine's network is fine.
    res, calls = verdict({"getMe": timeout,
                          "bot" + telegram_health._NO_TOKEN: (False, {}, 401)})
    check("a timeout against a reachable API is Telegram's, not the owner's",
          res["state"] == "telegram_slow", res["state"] + ": " + res["detail"])
    check("it does not tell the owner to check a working connection",
          "sin conexión" not in res["detail"] and "cortafuegos" not in res["detail"],
          res["detail"])
    check("it says how it knows", "No es tu red" in res["detail"], res["detail"])
    check("waiting cannot change that either", "telegram_slow" in telegram_health.TERMINAL)

    probe = [u for u, _t in calls.urls if telegram_health._NO_TOKEN in u]
    check("the reachability probe is made", len(probe) == 1, calls.urls)
    check("and it carries NO token of the owner's",
          probe and TOK not in probe[0], probe)

    section("when the machine really is cut off, the old verdict stands")
    # Nothing answers, including the tokenless probe.
    res, _ = verdict({"getMe": timeout, "bot" + telegram_health._NO_TOKEN: (False, "x", 0)})
    check("it is reported as unreachable from here", res["state"] == "unreachable",
          res["state"] + ": " + res["detail"])
    check("and it does not blame the token",
          "No sé si el token es bueno" in res["detail"], res["detail"])


def test_timeout_is_long_enough_to_hear_the_answer():
    section("the request waits long enough for Telegram to refuse it")
    # Measured: Telegram holds a request for a bot it cannot serve and answers 502 at about
    # sixteen seconds. At fifteen the verdict arrived as a timeout, which reads as "no
    # internet" - the wrong diagnosis, produced by the timeout rather than by the network.
    _res, calls = verdict({"getMe": (True, {"ok": True, "result": {"username": "b"}}, 200),
                           "getWebhookInfo": (True, {"ok": True, "result": {"url": ""}}, 200)})
    gets = [t for u, t in calls.urls if "getMe" in u]
    check("getMe allows more than sixteen seconds", gets and gets[0] >= 18, gets)


def test_unknown_is_not_stopped():
    section("what was never measured is not reported as a fact")
    res, _ = verdict({"getMe": (False, {"ok": False, "error_code": 502,
                                        "description": "Bad Gateway"}, 502)})
    check("the gateway was never asked, so its state is unknown",
          res["gateway_running"] is None, repr(res["gateway_running"]))
    # It used to initialise to False, so a verdict about Telegram's side came back carrying
    # "and its engine is stopped" about an agent that was up and retrying every few minutes.
    check("not False, which would have been a claim nobody checked",
          res["gateway_running"] is not False)


# ── whose log is it ───────────────────────────────────────────────────────────

def test_an_agent_is_diagnosed_from_its_own_log(tmp):
    section("an agent's diagnosis reads that agent's log, not the main one")
    local = os.path.join(tmp, "local")
    shared = os.path.join(local, "hermes", "logs")
    mine = os.path.join(local, "hermes", "profiles", "heraldo", "logs")
    os.makedirs(shared)
    os.makedirs(mine)
    with open(os.path.join(shared, "gateway.log"), "w", encoding="utf-8") as fh:
        fh.write("2026-11-18 20:00:00,1 INFO gateway.run: Connected to Telegram\n")
    with open(os.path.join(mine, "gateway.log"), "w", encoding="utf-8") as fh:
        fh.write("2026-11-18 20:00:01,1 WARNING gateway.run: Connect attempt failed: "
                 "Bad Gateway\n")

    old_local, old_home = os.environ.get("LOCALAPPDATA"), os.path.expanduser("~")
    os.environ["LOCALAPPDATA"] = local
    try:
        paths = telegram_health.log_paths("heraldo")
        check("only its own log is read", all("heraldo" in p for p in paths), paths)
        check("so the main agent's success is not borrowed",
              not telegram_health.scan_logs("heraldo")[0]["connected"],
              telegram_health.scan_logs("heraldo"))
        # The whole reason this matters: scan_logs ORs its signals across every file it is
        # given. With the shared log appended as a fallback, the main agent's "Connected to
        # Telegram" proved a silent agent had connected - and its token error became his.
        check("and its own failure is", telegram_health.scan_logs("heraldo")[0]["conflict"]
              is False and "Bad Gateway" in open(os.path.join(mine, "gateway.log"),
                                                 encoding="utf-8").read())

        section("an agent that never wrote a log still falls back to the shared one")
        paths = telegram_health.log_paths("nuevo")
        check("the shared log is used when there is nothing of its own",
              bool(paths) and all("profiles" not in p for p in paths), paths)
    finally:
        os.environ["LOCALAPPDATA"] = old_local or ""
    return old_home


def test_the_log_on_screen_is_a_diagnosis_not_a_transcript(tmp):
    section("showing the log does not become a way to read the conversations")
    local = os.path.join(tmp, "local2")
    mine = os.path.join(local, "hermes", "profiles", "heraldo", "logs")
    os.makedirs(mine)
    with open(os.path.join(mine, "gateway.log"), "w", encoding="utf-8") as fh:
        fh.write(
            "2026-11-18 20:00:00,1 WARNING hermes_plugins.telegram_platform.adapter: "
            "[Telegram] Connect attempt 1/8 failed: Bad Gateway\n"
            "2026-11-18 20:00:01,1 INFO gateway.run: handled message\n"
            "2026-11-18 20:00:02,1 DEBUG gateway.run: payload\n"
            "Traceback (most recent call last):\n"
            "  File \"x.py\", line 1, in <module>\n"
            "2026-11-18 20:00:03,1 ERROR gateway.run: token %s was rejected\n" % TOK)
    old = os.environ.get("LOCALAPPDATA")
    os.environ["LOCALAPPDATA"] = local
    try:
        d = telegram_health.log_digest("heraldo")
        texts = " | ".join(l["text"] for l in d["lines"])
        check("warnings are shown", "Bad Gateway" in texts, texts)
        check("errors are shown", "was rejected" in texts, texts)
        check("ordinary INFO traffic is not", "handled message" not in texts, texts)
        check("nor DEBUG", "payload" not in texts, texts)
        # Only lines with a logger's own shape get through, so nothing that is merely free
        # text in the file can reach the panel.
        check("nor anything without a logger's shape", "Traceback" not in texts, texts)
        check("a token in a log line is redacted before it is shown",
              TOK not in texts and "«token»" in texts, texts)
        check("and the channel is named in words, not as a dotted path",
              any(l["logger"] == "Telegram" for l in d["lines"]),
              [l["logger"] for l in d["lines"]])
    finally:
        os.environ["LOCALAPPDATA"] = old or ""


# ── WhatsApp: paired on disk is not connected ─────────────────────────────────

class FakeNumbers(object):
    MAX_NUMBERS = 6

    def __init__(self, rows):
        self.rows = rows

    def listing(self, profile=None, hermes=None):
        import copy
        return copy.deepcopy(self.rows)


def wa_snapshot(rows, health, fast=False):
    """connections.whatsapp with the registry and the bridge probe both stood in for."""
    import types
    fake = FakeNumbers(rows)
    mod = types.ModuleType("whatsapp_delivery")
    mod.bridge_health = lambda host, port, timeout=5.0: health(port)
    sys.modules["whatsapp_delivery"] = mod
    real_imp = connections.numbers if hasattr(connections, "numbers") else None
    import wizard
    sys.modules["wizard.numbers"] = fake
    setattr(wizard, "numbers", fake)
    try:
        return connections.whatsapp("heraldo", None, fast=fast)
    finally:
        sys.modules.pop("whatsapp_delivery", None)
        if real_imp is not None:
            setattr(wizard, "numbers", real_imp)


def test_whatsapp_says_whether_it_is_connected_now():
    section("WhatsApp: a phone paired long ago is not a connection today")
    rows = [{"slug": "principal", "label": "Número principal", "port": 3000,
             "builtin": True, "main": True, "linked": True, "enabled": True}]

    live = wa_snapshot(rows, lambda port: {"reachable": True, "connection": "open"})
    check("a bridge that says open counts as connected", live["live_count"] == 1, live)
    check("and the channel reads as working",
          connections._headline({"whatsapp": live})[1]["state"] == "ok",
          connections._headline({"whatsapp": live})[1])

    down = wa_snapshot(rows, lambda port: {"reachable": False, "connection": None})
    check("a bridge nobody answers is not connected", down["live_count"] == 0, down)
    check("the pairing on disk no longer paints it green",
          connections._headline({"whatsapp": down})[1]["state"] == "warn",
          connections._headline({"whatsapp": down})[1])
    check("and it says the link is intact, so nobody rescans a QR for nothing",
          "sigue vinculado" in down["detail"], down["detail"])

    section("and what could not be asked is never called disconnected")
    sys.modules.pop("whatsapp_delivery", None)
    import types
    broken = types.ModuleType("whatsapp_delivery")   # no bridge_health at all
    sys.modules["whatsapp_delivery"] = broken
    try:
        quiet = wa_snapshot(rows, lambda port: None)
    finally:
        sys.modules.pop("whatsapp_delivery", None)
    check("nothing was probed, so nothing is claimed", quiet["probed"] == 0, quiet)
    check("and the row does not turn to a warning on its own",
          connections._headline({"whatsapp": quiet})[1]["state"] == "ok",
          connections._headline({"whatsapp": quiet})[1])

    section("the fast pass stays fast")
    asked = []
    fast = wa_snapshot(rows, lambda port: asked.append(port) or
                       {"reachable": True, "connection": "open"}, fast=True)
    check("no socket is opened while the panel is painting", asked == [], asked)
    check("and it still reports what the disk knows", fast["linked_count"] == 1, fast)


def test_a_known_answer_is_not_asked_again():
    section("the reconnect path does not re-ask Telegram what it just waited for")
    asked = []
    real = connections.telegram
    connections.telegram = lambda *a, **k: asked.append(1) or {"ok": True}
    try:
        snap = connections.snapshot("heraldo", hermes="", install_dir=None, fast=True,
                                    gateway_running=True,
                                    telegram_state={"ok": False, "state": "telegram_down",
                                                    "has_owner": True, "detail": "d"})
    finally:
        connections.telegram = real
    check("a handed-in verdict is used as given", snap["telegram"]["state"] == "telegram_down",
          snap["telegram"])
    check("and Telegram is not asked a second time", asked == [], asked)
    check("the owner lock is still filled in for the panel",
          snap["telegram"]["owner_locked"] is True, snap["telegram"])
    check("the gateway answer handed in is used too", snap["gateway"]["running"] is True,
          snap["gateway"])


# ── the button ────────────────────────────────────────────────────────────────

def test_reconnect_restarts_and_then_measures():
    section("reconnecting restarts the agent and reports what came back")
    from wizard import wizard_server as W

    calls = {"restart": [], "wait": [], "snap": [], "talk": []}
    real = (W._target_profile, W.which, W.hermes_ctl.gateway_restart_safe,
            W.telegram_health.wait_for_connection, W.connections.snapshot,
            W.agents_registry.get, W.hermes_ctl.profile_exists, W.talk.status)
    # Switched off, so the restart must not sit waiting for a channel that is never coming
    # up. (The wait itself is tested below, where it is on.)
    W.talk.status = lambda p, h=None, inst=None: (
        calls["talk"].append(p) or {"ok": True, "enabled": False, "ready": False})

    W._target_profile = lambda body, allow_new=False: body.get("profile")
    W.which = lambda name: "hermes.exe"
    W.hermes_ctl.profile_exists = lambda slug, hp=None: True
    W.agents_registry.get = lambda slug, inst=None: {"slug": slug, "enabled": True}
    W.hermes_ctl.gateway_restart_safe = lambda hp, profile=None, timeout=90: (
        calls["restart"].append(profile) or
        {"ok": True, "running": True, "restarted_cleanly": True, "detail": "stop: ok"})
    W.telegram_health.wait_for_connection = lambda p, h, seconds=30: (
        calls["wait"].append((p, seconds)) or {"ok": False, "state": "telegram_down",
                                               "detail": "502"})
    W.connections.snapshot = lambda *a, **k: (
        calls["snap"].append(k) or
        {"ok": True, "channels": [{"label": "Telegram", "state": "warn", "detail": "502"},
                                  {"label": "WhatsApp", "state": "ok", "detail": ""}]})
    try:
        out = W.Handler._reconnect(None, {"profile": "heraldo"})
        check("it restarts that agent's gateway", calls["restart"] == ["heraldo"],
              calls["restart"])
        check("it waits for the handshake before reporting", len(calls["wait"]) == 1,
              calls["wait"])
        check("the verdict it waited for is handed to the report, not asked again",
              calls["snap"] and calls["snap"][0].get("telegram_state", {}).get("state")
              == "telegram_down", calls["snap"])
        check("the report is measured AFTER the restart", "report" in out, out)
        # The point of the whole button: a gateway that came back up with a channel still
        # down has not been repaired, and a green "listo" is how an owner walks away from
        # an agent that still cannot hear anyone.
        check("a channel still down is not reported as fixed",
              "sigue sin conectar" in out["detail"], out["detail"])
        check("and the channel still down is named", "Telegram" in out["detail"], out["detail"])

        section("an agent that is paused is not secretly started")
        W.agents_registry.get = lambda slug, inst=None: {"slug": slug, "enabled": False}
        calls["restart"] = []
        out = W.Handler._reconnect(None, {"profile": "heraldo"})
        check("it refuses rather than fight the supervisor", out["ok"] is False, out)
        check("nothing was restarted", calls["restart"] == [], calls["restart"])
        check("and it says what to do instead", "Reanúdalo" in out["detail"], out["detail"])

        section("and when everything comes back, it says so plainly")
        good = {"running": True}
        allok = {"channels": [{"label": "Telegram", "state": "ok", "detail": ""}]}
        check("a clean restart reads as done",
              "Listo" in W._reconnect_detail(good, allok),
              W._reconnect_detail(good, allok))
        check("a gateway that never came up says that instead",
              "no llegó a levantarse" in W._reconnect_detail({"running": False}, allok),
              W._reconnect_detail({"running": False}, allok))
    finally:
        (W._target_profile, W.which, W.hermes_ctl.gateway_restart_safe,
         W.telegram_health.wait_for_connection, W.connections.snapshot,
         W.agents_registry.get, W.hermes_ctl.profile_exists, W.talk.status) = real


def test_this_screen_is_given_time_to_come_back():
    section("after a restart, this screen's channel is not judged a second too early")
    from wizard import wizard_server as W
    # The gateway's pid exists before its platforms bind, and the api_server platform binds
    # last. Asking the instant the process was up reported "configurado pero todavía no
    # escucha" about an agent that was three seconds from listening - a warning manufactured
    # by asking too early, on the very screen the owner is watching.
    seen = []
    real = W.talk.status
    W.talk.status = lambda p, h=None, inst=None: (
        seen.append(1) or {"ok": True, "enabled": True, "ready": len(seen) >= 3})
    try:
        st = W._wait_talk("heraldo", "hermes.exe", seconds=20)
        check("it waits until the channel is really listening", st["ready"] is True, st)
        check("and asks more than once to find that out", len(seen) >= 3, len(seen))

        seen[:] = []
        W.talk.status = lambda p, h=None, inst=None: (
            seen.append(1) or {"ok": True, "enabled": False, "ready": False})
        a = __import__("time").time()
        W._wait_talk("heraldo", "hermes.exe", seconds=20)
        check("a channel that is switched off is not waited for at all",
              len(seen) == 1 and (__import__("time").time() - a) < 2, len(seen))
    finally:
        W.talk.status = real


def test_restarting_is_not_pausing():
    section("restart is its own verb, and does not touch whether the agent is enabled")
    from wizard import wizard_server as W
    saved = []
    real = (W.which, W.hermes_ctl.profile_exists, W.hermes_ctl.gateway,
            W.agents_registry.get, W.agents_registry.upsert)
    W.which = lambda name: "hermes.exe"
    W.hermes_ctl.profile_exists = lambda slug, hp=None: True
    W.hermes_ctl.gateway = lambda action, hp=None, profile=None, timeout=90: {
        "ok": True, "detail": action + ":" + str(profile)}
    W.agents_registry.get = lambda slug, inst=None: {"slug": slug, "enabled": True}
    W.agents_registry.upsert = lambda rec, inst=None: saved.append(rec)
    try:
        out = W.Handler._agent_action(None, "heraldo", "restart")
        check("an extra agent can be restarted at all", out["ok"] is True, out)
        check("it is the agent's own gateway", "heraldo" in out["detail"], out)
        check("and the enabled flag is left exactly as it was", saved == [], saved)
    finally:
        (W.which, W.hermes_ctl.profile_exists, W.hermes_ctl.gateway,
         W.agents_registry.get, W.agents_registry.upsert) = real


def main():
    import tempfile
    tmp = tempfile.mkdtemp(prefix="olivaw-reconnect-")
    test_a_sick_bot_is_not_a_dead_token()
    test_silence_is_attributed_to_the_right_side()
    test_timeout_is_long_enough_to_hear_the_answer()
    test_unknown_is_not_stopped()
    test_an_agent_is_diagnosed_from_its_own_log(tmp)
    test_the_log_on_screen_is_a_diagnosis_not_a_transcript(tmp)
    test_whatsapp_says_whether_it_is_connected_now()
    test_a_known_answer_is_not_asked_again()
    test_reconnect_restarts_and_then_measures()
    test_this_screen_is_given_time_to_come_back()
    test_restarting_is_not_pausing()

    print("\n%d passed, %d failed" % (len(PASSED), len(FAILED)))
    for f in FAILED:
        print("  - " + f)
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
