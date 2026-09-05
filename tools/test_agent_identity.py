r"""Which agent is this, and is its brain actually there.

An agent is not a name. It is a name PLUS a Hermes profile, a bridge port and a workspace,
and it only works while those four agree. Three ways that broke on a live install:

  the wrong profile   Every channel mutation read body["profile"] verbatim and handed it to
                      `hermes --profile` and to os.path.join(<hermes home>, "profiles", …).
                      A name for an agent that did not exist was created on the spot: the QR
                      paired, a session landed on disk, the call returned success, and the
                      gateway that was actually running never saw any of it. The owner had a
                      configured WhatsApp bot that answered nobody.
  duplicate rows      Nothing stopped two registry entries sharing a port or a profile. Two
                      bridges then fight for one socket, and configuring one agent silently
                      reconfigures the other.
  a lie about login   `test_brain` answered EVERY failure with "¿Iniciaste sesión en Codex?",
                      including WinError 10061 - connection refused - which means nothing is
                      listening on that port and says nothing at all about authentication.
                      The owner re-authenticated a CLI that was never the problem.

Plus the update log, which said nothing between "checksum ok" and "update ok", so an update
that stalled looked like one that had stopped dead after verification.

Run: python tools/test_agent_identity.py
"""

import io
import json
import os
import shutil
import socket
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
sys.path.insert(0, SRC)

from wizard import agents_registry as R    # noqa: E402
from wizard import checks                  # noqa: E402

PASSED, FAILED = [], []


def check(name, cond, extra=""):
    (PASSED if cond else FAILED).append(name)
    print(("  ok   " if cond else "  FAIL ") + name +
          (("\n       " + str(extra)) if (extra and not cond) else ""))


def section(t):
    print("\n=== %s ===" % t)


def _raises(fn, *a, **kw):
    try:
        fn(*a, **kw)
        return False
    except ValueError:
        return True


# ── a bridge that answers, and one that lies about its engine ────────────────

def _serve(payload):
    """A one-off local HTTP server answering /health with `payload`. Returns (url, stop)."""
    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            body = json.dumps(payload).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            self.send_error(500, "the brain did not answer")

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return "http://127.0.0.1:%d" % srv.server_port, srv.shutdown


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_registry():
    section("two agents can never share what only one can own")
    tmp = tempfile.mkdtemp(prefix="reg-")
    try:
        R.upsert({"slug": "ana", "profile": "ana", "port": 8792}, tmp)
        for name, row in [
            ("the same port as another agent", {"slug": "b", "profile": "b", "port": 8792}),
            ("the same Hermes profile", {"slug": "b", "profile": "ana", "port": 8794}),
            ("the default agent's port", {"slug": "b", "profile": "b", "port": R.BASE_PORT}),
            ("a slug that walks out of the directory", {"slug": "../../x", "profile": "x", "port": 8794}),
            ("a profile that walks out of the directory",
             {"slug": "b", "profile": "..\\..\\etc", "port": 8794}),
            ("a port that is not a number", {"slug": "b", "profile": "b", "port": "ocho"}),
            ("no port at all", {"slug": "b", "profile": "b"}),
        ]:
            try:
                R.upsert(row, tmp)
                check("rejected: " + name, False, "it was ACCEPTED")
            except R.Conflict as e:
                check("rejected: " + name, True, e)
        R.upsert({"slug": "beto", "profile": "beto", "port": 8794}, tmp)
        check("a legitimate second agent still saves", len(R.list_agents(tmp)) == 2)

        section("but a row already on disk does not become a trap")
        data = R.load(tmp)
        data["agents"].append({"slug": "viejo", "profile": "viejo", "port": R.BASE_PORT})
        R.save(data, tmp)
        rec = R.get("viejo", tmp)
        rec["enabled"] = False
        try:
            R.upsert(rec, tmp)
            check("a legacy bad row can still be paused - pausing it is the fix", True)
        except R.Conflict as e:
            check("a legacy bad row can still be paused - pausing it is the fix", False, e)
        rec = R.get("viejo", tmp)
        rec["port"] = 8794
        try:
            R.upsert(rec, tmp)
            check("...but it cannot be moved onto someone else's port", False, "accepted")
        except R.Conflict:
            check("...but it cannot be moved onto someone else's port", True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_profile_resolution():
    section("the browser does not get to choose which agent is configured")
    tmp = tempfile.mkdtemp(prefix="prof-")
    home = tempfile.mkdtemp(prefix="hhome-")
    try:
        os.makedirs(os.path.join(home, "profiles", "clientes"))
        R.upsert({"slug": "ana", "profile": "ana", "port": 8792}, tmp)

        check("nothing means the default agent",
              R.resolve_profile(None, tmp, home) is None)
        check("so does the empty string", R.resolve_profile("", tmp, home) is None)
        check("and so does the literal 'default'",
              R.resolve_profile("default", tmp, home) is None)
        check("a registered agent resolves to its profile",
              R.resolve_profile("ana", tmp, home) == "ana")
        check("a profile that exists on disk resolves too",
              R.resolve_profile("clientes", tmp, home) == "clientes")

        for bad, why in [("nadie", "a name this machine has never heard of"),
                         ("../../etc/passwd", "path traversal"),
                         ("..\\..\\windows", "path traversal, Windows flavour"),
                         ("with/slash", "a separator"),
                         ("a" * 80, "absurdly long"),
                         ("con espacio", "a space")]:
            try:
                R.resolve_profile(bad, tmp, home)
                check("rejected: %s" % why, False, "%r was ACCEPTED" % bad)
            except ValueError:
                check("rejected: %s" % why, True)

        section("an unknown name is refused rather than quietly created")
        # This is the whole point: the old code would hand "nadie" straight to
        # `hermes --profile nadie`, which creates it, and to a path join, which makes the
        # directory. Two configurations then exist and the running gateway is not the one
        # being written to.
        check("resolving does not create anything on disk",
              not os.path.isdir(os.path.join(home, "profiles", "nadie")))

        section("the wizard's own helper, exercised rather than grepped")
        # Importing the server module is safe: it binds nothing until main() runs. Calling
        # the helper is the only way to prove it RESOLVES rather than merely being spelled
        # correctly at each call site - a source grep passes happily on a helper whose body
        # has been gutted back to `return body.get("profile")`.
        from wizard import wizard_server as WS
        check("an unknown agent name is refused",
              _raises(WS._target_profile, {"profile": "nadie-existe"}))
        check("a traversal attempt is refused",
              _raises(WS._target_profile, {"profile": "../../etc/passwd"}))
        check("no profile at all still means the default agent",
              WS._target_profile({}) is None)

        section("the wizard routes every profile through it")
        ws = io.open(os.path.join(SRC, "wizard", "wizard_server.py"), encoding="utf-8").read()
        check("_target_profile exists", "def _target_profile(" in ws)
        body = ws.split("def _target_profile(", 1)[1]
        check("and it is the ONLY place a raw profile is read from the request",
              ws.count('body.get("profile")') == 1 and
              'body.get("profile")' in body.split("\ndef ", 1)[0], ws.count('body.get("profile")'))
        for route in ("telegram_health.check(_target_profile(body)",
                      "image_setup.status(_target_profile(body)",
                      "browser_setup.status(_target_profile(body)",
                      "browser_setup.enable(_target_profile(body)",
                      "browser_setup.disable(_target_profile(body)"):
            check("routed: %s…" % route.split("(")[0], route in ws)
        check("the channel routes (WhatsApp pairing included) use it",
              "profile = _target_profile(body)          # None -> default agent" in ws)
        check("a rejected target is a 400 with the reason, not a 500 'error interno'",
              "except (ValueError, agents_registry.Conflict) as e:" in ws)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        shutil.rmtree(home, ignore_errors=True)


def test_brain_diagnosis():
    section("a refused connection is not a login problem")
    dead = "http://127.0.0.1:%d" % _free_port()
    # Through the button the owner actually presses, not just the helper underneath it:
    # the diagnosis is worthless if test_brain does not consult it.
    t = checks.test_brain(dead, timeout=5, brain="Codex", engine="codex")
    check("the 'probar el cerebro' button reports the port, not the login",
          t.get("state") == "bridge_not_listening", t)
    check("and the word 'sesión' does not appear in its verdict",
          "iniciaste sesión" not in (t.get("detail") or "").lower(), t)
    r = checks.diagnose_bridge(dead, want_engine="codex")
    check("nothing listening -> bridge_not_listening",
          r.get("state") == "bridge_not_listening", r)
    check("the message says the bridge is not running", "puente" in r["detail"].lower()
          or "escuchando" in r["detail"].lower(), r)
    check("and explicitly says this is NOT about signing in",
          "no es un problema de inicio de sesión" in r["detail"].lower(), r["detail"])
    check("it names the port so the owner can act on it", str(r["port"]) in r["detail"])

    section("a bridge running the wrong brain says so")
    url, stop = _serve({"status": "ok", "backend": "claude-code", "engine": "claude"})
    try:
        r = checks.diagnose_bridge(url, want_engine="codex")
        check("engine mismatch -> bridge_wrong_engine",
              r.get("state") == "bridge_wrong_engine", r)
        check("both engines are named", "claude" in r["detail"] and "codex" in r["detail"], r)
        check("the matching engine passes",
              checks.diagnose_bridge(url, want_engine="claude").get("state") == "ok")
        check("and no expectation at all still passes",
              checks.diagnose_bridge(url).get("state") == "ok")

        section("a live bridge whose brain then fails is a DIFFERENT verdict")
        # The port is open and /health is fine; the completion 500s. Only here is a login
        # question reasonable - and it is asked of the CLI, not of the owner.
        r = checks.test_brain(url, timeout=5, brain="Codex", engine="claude")
        check("the state is about the brain, not the port",
              r.get("state") in ("brain_failed", "not_authenticated", "timeout"), r)
        check("it does not claim nothing is listening",
              r.get("state") != "bridge_not_listening", r)
    finally:
        stop()

    section("something else squatting on the port")
    url2, stop2 = _serve("not json at all")
    try:
        # _serve returns valid JSON for any payload, so simulate the other shape: a server
        # that answers but is not our bridge, i.e. no engine/backend keys.
        r = checks.diagnose_bridge(url2, want_engine="codex")
        check("an answer with no engine is not treated as a wrong engine",
              r.get("state") in ("ok", "bridge_unhealthy"), r)
    finally:
        stop2()

    section("the wizard passes the engine through")
    ws = io.open(os.path.join(SRC, "wizard", "wizard_server.py"), encoding="utf-8").read()
    check("test-brain is told which brain was chosen", "engine=p.engine)" in ws)
    src = io.open(os.path.join(SRC, "wizard", "checks.py"), encoding="utf-8").read()
    check("the old catch-all login message is gone",
          "El cerebro no respondió. ¿Iniciaste sesión en" not in src)
    check("the login question is now asked of the CLI", "def _login_state(" in src)


def test_update_log():
    section("an update that stalls says where")
    src = io.open(os.path.join(SRC, "launcher.py"), encoding="utf-8").read()
    body = src[src.index("def apply_update"):src.index("def _run_migrations")]
    for phase in ("extracting", "stopping bridges", "backing up", "swapping src/",
                  "starting the new bridge"):
        check("phase logged: %s" % phase, phase in body, body[:400])
    check("the swap warns that it is the point of no return",
          "point of no return" in body)
    check("the final verdict is still logged", 'log(f"update ok:' in body)
    check("and so is the failure", 'log(f"update failed' in body)
    want = ["update {ver}: extracting", "update {ver}: stopping bridges",
            "update {ver}: backing up", "update {ver}: swapping src/",
            "update {ver}: starting the new bridge"]
    order = [body.find(w) for w in want]      # find, not index: a missing phase must report
    check("the phases are logged in the order they happen",
          all(i >= 0 for i in order) and order == sorted(order),
          dict(zip(want, order)))


def test_codex_image_contract():
    section("a Codex brain must DRAW when asked to draw")
    import re
    src = io.open(os.path.join(SRC, "claude_bridge.py"), encoding="utf-8").read()
    clause = src[src.index('if ENGINE == "codex":'):]
    clause = clause[:clause.index("MODEL_NAME =")]
    # Read what the MODEL will see, not how the source is wrapped: the prompt is built from
    # adjacent string literals, so a sentence the model reads as one line is split across
    # two in the file. Joining the seams is the difference between testing the contract and
    # testing the line width.
    text = re.sub(r'"\s*\n\s*"', "", clause)
    check("the request is an obligation, in the same turn",
          "you must actually call that tool in the same turn" in text, text[-1200:])
    check("a revision is named explicitly as a new call",
          "make another version of an image" in text and "revise" in text)
    check("describing the image instead of making it is called out as a refusal",
          "is not an answer to that request - it is a refusal dressed as one" in text)
    check("a follow-up on an image just produced counts as a new request",
          "including a follow-up about an image you just produced" in text)
    check("the prompt must be restated in full, since nothing carries over",
          "restate the whole scene in the new prompt" in text)
    check("an invented MEDIA path is forbidden, with the reason",
          "Never write a MEDIA: line for a path you did not receive from the tool" in text)
    check("a failure must be reported as a failure",
          "do not describe the picture as though it exists" in text)
    check("the old weak wording is gone",
          "Use it when asked for an image and the tool catalog has" not in text)


def main():
    test_registry()
    test_profile_resolution()
    test_brain_diagnosis()
    test_update_log()
    test_codex_image_contract()
    print("\n%d passed, %d failed" % (len(PASSED), len(FAILED)))
    for f in FAILED:
        print("  - " + f)
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
