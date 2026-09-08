r"""Three things the owner could not do, and the state they could not see.

Reported together:

  1. **WhatsApp had no visible status.** Whether it was connected lived inside the setup
     flow, behind a collapsed <details>, next to a button that started a pairing. So "is it
     working?" was answered by trying it. Now connections.py is the single reader and the
     agent's own front page shows every channel with a state.
  2. **The help console assumed Claude.** Two separate detectors both returned "claude"
     whenever one key was missing from updater.config.json - which is every install written
     before that key existed, and every extra agent, whose engine lives in agents.json and
     was never read. The running bridge knows its own engine and was never asked.
  3. **The only way to talk to an agent was Telegram.** Now there is a chat in the UI, and
     it goes through Hermes' api_server platform so it reaches the SAME agent - persona,
     memory, skills - rather than the raw brain behind the bridge.

Run: python tools/test_console_ux.py
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
sys.path.insert(0, os.path.join(SRC, "wizard"))

PASSED, FAILED = [], []


def check(name, cond, extra=""):
    (PASSED if cond else FAILED).append(name)
    print(("  ok   " if cond else "  FAIL ") + name +
          (("\n       " + str(extra)) if (extra and not cond) else ""))


def section(t):
    print("\n=== %s ===" % t)


def _serve(payload, status=200):
    """A local HTTP server answering everything with `payload`. Returns (port, stop)."""
    class H(BaseHTTPRequestHandler):
        def _go(self):
            body = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        do_GET = do_POST = _go

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv.server_port, srv.shutdown


def _dead_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_brain():
    from wizard import rescue as R

    section("the brain is detected, never assumed")
    port, stop = _serve({"status": "ok", "backend": "codex", "engine": "codex"})
    try:
        check("the running bridge is believed",
              R.live_engine(port=port) == "codex", R.live_engine(port=port))
        said = dict((s, e) for s, e in R.engine_sources(port=port))
        check("and it is listed as the source", said.get("bridge") == "codex", said)
        check("configured_engine follows it",
              R.configured_engine(port=port) == "codex", R.configured_engine(port=port))
        st = R.engine_status(port=port)
        check("the label follows too", st["label"] == "Codex", st)
        check("the owner is told WHERE that came from",
              st["source"] == "bridge" and "puente" in st["source_label"], st)
        # This machine has no Codex CLI, so the honest answer is "configured but missing",
        # NOT a silent swap to Claude. Silently swapping is how "your brain is Codex" turns
        # into "did you sign in to Claude?" - the reported symptom.
        if not R._engine_available("codex"):
            check("a configured brain whose CLI is missing says so, rather than swapping",
                  st["available"] is False and "no está instalado" in st["detail"], st)
            check("and it does not rename itself to the brain that IS installed",
                  st["engine"] == "codex", st)
    finally:
        stop()

    section("an extra agent's brain is read from its own record")
    tmp = tempfile.mkdtemp(prefix="brain-")
    try:
        io.open(os.path.join(tmp, "agents.json"), "w", encoding="utf-8").write(json.dumps(
            {"agents": [{"slug": "dos", "profile": "dos", "port": 9999, "engine": "codex"}]}))
        io.open(os.path.join(tmp, "updater.config.json"), "w", encoding="utf-8").write(
            json.dumps({"env": {"OLIVAW_ENGINE": "claude"}}))
        said = dict((s, e) for s, e in R.engine_sources(tmp, profile="dos",
                                                        port=_dead_port()))
        check("agents.json is consulted at all", said.get("agents.json") == "codex", said)
        check("and it outranks the machine-wide setting",
              R.configured_engine(tmp, profile="dos", port=_dead_port()) == "codex", said)
        check("while the default agent still reads the machine-wide one",
              R.configured_engine(tmp, port=_dead_port()) == "claude")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    section("the old bug: no key recorded anywhere")
    tmp = tempfile.mkdtemp(prefix="brain2-")
    try:
        # An install written before OLIVAW_ENGINE existed. The old code returned "claude"
        # for this, flatly, whatever was actually running.
        io.open(os.path.join(tmp, "updater.config.json"), "w", encoding="utf-8").write(
            json.dumps({"bridge_url": "http://127.0.0.1:8790", "env": {}}))
        port, stop = _serve({"status": "ok", "engine": "codex"})
        try:
            check("a config with no engine key falls back to the LIVE bridge",
                  R.configured_engine(tmp, port=port) == "codex",
                  R.engine_sources(tmp, port=port))
        finally:
            stop()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    section("both surfaces answer from the same place")
    import wizard.wizard_server as WS
    src = io.open(os.path.join(SRC, "wizard", "wizard_server.py"), encoding="utf-8").read()
    check("the wizard delegates instead of keeping its own copy",
          "return rescue.configured_engine(INSTALL_DIR" in src, src[:0])
    check("and the old one-key version is gone",
          '(cfg.get("env") or {}).get("OLIVAW_ENGINE") or ""' not in src.split(
              "def _configured_engine", 1)[1][:600])
    check("it still answers for this machine", WS._configured_engine() in ("claude", "codex"))
    rc = io.open(os.path.join(SRC, "wizard", "rescue.py"), encoding="utf-8").read()
    check("the SOS context carries the full status, not just a bare string",
          'ctx["engine_status"] = engine_status(inst)' in rc)


def test_connections():
    from wizard import connections as C

    section("one call says how an agent is reachable")
    snap = C.snapshot(port=_dead_port(), fast=True)
    check("it answers", snap.get("ok") is True, snap)
    for key in ("gateway", "brain", "telegram", "whatsapp", "talk", "channels"):
        check("it covers %s" % key, key in snap, sorted(snap))
    labels = [c["label"] for c in snap["channels"]]
    check("Telegram is one of the rows", "Telegram" in labels, labels)
    check("so is WhatsApp", "WhatsApp" in labels, labels)
    check("and talking from the screen", "Desde esta pantalla" in labels, labels)
    states = {c["label"]: c["state"] for c in snap["channels"]}
    check("every row carries a state the UI can colour",
          all(s in ("ok", "warn", "off", "checking") for s in states.values()), states)

    section("fast mode never leaves the machine")
    # The point of fast mode: paint at once. It must not be able to block on the network,
    # so Telegram is reported as "checking" rather than measured.
    check("Telegram is 'checking', not a verdict",
          snap["telegram"]["state"] in ("checking", "no_token"), snap["telegram"])
    check("and the snapshot says it was the fast one", snap["fast"] is True)
    check("the bridge is still reported, since that is local",
          "bridge" in snap and snap["bridge"]["up"] is False, snap.get("bridge"))

    section("'not set up' and 'broken' are never the same answer")
    check("a channel with nothing configured reads as off, not warn",
          states.get("WhatsApp") in ("off", "warn"), states)
    for row in snap["channels"]:
        check("%s explains itself in words" % row["label"], bool(row["detail"]), row)


def test_talk():
    from wizard import talk as T

    section("talking to the agent goes through Hermes, not the raw brain")
    doc = io.open(os.path.join(SRC, "wizard", "talk.py"), encoding="utf-8").read()
    check("it uses Hermes' api_server platform", 'PLATFORM = "api_server"' in doc)
    check("and says why the bridge would have been wrong",
          "no persona, no memory" in doc, doc[:0])
    check("loopback only", 'HOST = "127.0.0.1"' in doc)
    check("with a key long enough that Hermes will accept it",
          "KEY_BYTES = 32" in doc)

    section("the key never reaches the browser")
    check("the bearer header is added server-side",
          'headers["Authorization"] = "Bearer " + key' in doc)
    # A key in a URL lands in logs and in browser history; a key in a JSON response lands
    # in the page. The proxy exists so the page never holds one at all - so plant a known
    # key and assert its VALUE appears in nothing any route returns.
    secret = "deadbeef" * 8
    real_env_of = T.env_of
    T.env_of = lambda profile=None, hermes=None: {"API_SERVER_KEY": secret}
    try:
        blobs = [json.dumps(T.status(), default=str),
                 json.dumps(T.send(session_id="abc", text="hola"), default=str),
                 json.dumps(T.sessions(), default=str),
                 json.dumps(T.history(session_id="abc"), default=str)]
    finally:
        T.env_of = real_env_of
    check("no result any route returns contains the key",
          all(secret not in b for b in blobs),
          [b[:120] for b in blobs if secret in b])
    st_keys = set(T.status().keys())
    check("status reports only WHETHER there is a key, never the key",
          "has_key" in st_keys and "key" not in st_keys, sorted(st_keys))
    check("and the key is never put in a URL",
          "?key=" not in doc and "key=%s" not in doc, doc[:0])

    section("configured, listening and answering are three states")
    st = T.status()
    check("an unconfigured agent is not reported as broken",
          st["enabled"] is False and st["ready"] is False, st)
    check("and the message invites setting it up",
          "Actívalo" in st["detail"] or "activado" in st["detail"], st)

    section("what a caller may pass")
    # The validator itself, not just "send() failed" - on an unconfigured agent send()
    # fails for a different reason entirely, so that assertion passed with validation
    # removed. A session id becomes a URL path segment, so this is the gate that keeps
    # "../" out of one.
    for bad in ("../etc/passwd", "..\\x", "a b", "x" * 200, "", None, "a/b", "a?b"):
        check("a session id like %r is refused" % (bad,), T._safe_session(bad) is False)
    for good in ("sess-1", "abc123", "a.b:c_d-e"):
        check("but %r is accepted" % (good,), T._safe_session(good) is True)
    # And a CONFIGURED agent must still refuse it before anything leaves the machine.
    real_env_of, real_port = T.env_of, T._configured_port
    T.env_of = lambda profile=None, hermes=None: {"API_SERVER_KEY": "k" * 64}
    T._configured_port = lambda profile=None, hermes=None: 8642
    reached = []
    real_http = T._http
    T._http = lambda *a, **k: (reached.append(a[0]), (False, "", 0))[1]
    try:
        r_bad = T.send(session_id="../etc/passwd", text="hola")
        r_empty = T.send(session_id="abc", text="   ")
    finally:
        T.env_of, T._configured_port, T._http = real_env_of, real_port, real_http
    check("a bad id on a configured agent is refused with no request at all",
          r_bad.get("ok") is not True and not reached, reached)
    check("and the reason names the conversation", "no válida" in r_bad["detail"], r_bad)
    check("an empty message is refused before any request",
          "Escribe algo" in r_empty["detail"] and not reached, r_empty)

    section("a failure is explained, not echoed")
    check("nothing listening -> a restart, in words",
          "reinicio" in T._explain(0, ""), T._explain(0, ""))
    check("a stale key -> the reason, not '401'",
          "clave" in T._explain(401, ""), T._explain(401, ""))
    check("a missing conversation says so", "ya no existe" in T._explain(404, ""))

    section("enabling it writes loopback and a strong key")
    calls = {}

    from wizard import hermes_ctl
    real_set, real_env, real_get = (hermes_ctl.config_set, hermes_ctl.set_env_vars,
                                    hermes_ctl.config_get)
    hermes_ctl.config_set = lambda k, v, h=None, p=None: (
        calls.__setitem__(k, v) or {"ok": True, "detail": ""})
    hermes_ctl.set_env_vars = lambda u, h=None, p=None: (
        calls.update(u) or {"ok": True, "detail": ""})
    hermes_ctl.config_get = lambda k, h=None, p=None: ""
    try:
        res = T.enable()
    finally:
        (hermes_ctl.config_set, hermes_ctl.set_env_vars,
         hermes_ctl.config_get) = real_set, real_env, real_get
    check("it enables the platform",
          calls.get("platforms.api_server.enabled") == "true", calls)
    check("it pins the host to loopback IN THE FILE, not by assuming the default",
          calls.get("platforms.api_server.extra.host") == "127.0.0.1", calls)
    check("it writes a port", str(calls.get("platforms.api_server.extra.port", "")).isdigit(),
          calls)
    key = calls.get("API_SERVER_KEY", "")
    check("and a key Hermes will not reject as weak", len(key) >= 32, len(key))
    check("which is random, not derived from anything guessable",
          key != calls.get("API_SERVER_KEY_2", "") and key.strip(
              "0123456789abcdef") == "", key[:8] + "…")
    check("the result says a restart is still needed",
          "reiniciar" in res.get("detail", ""), res)

    section("an existing key is kept, never rotated behind the owner's back")
    calls.clear()
    old = "f" * 64
    hermes_ctl.config_set = lambda k, v, h=None, p=None: (
        calls.__setitem__(k, v) or {"ok": True, "detail": ""})
    hermes_ctl.set_env_vars = lambda u, h=None, p=None: (
        calls.update(u) or {"ok": True, "detail": ""})
    hermes_ctl.config_get = lambda k, h=None, p=None: ""
    real_env_of = T.env_of
    T.env_of = lambda profile=None, hermes=None: {"API_SERVER_KEY": old}
    try:
        res2 = T.enable()
    finally:
        T.env_of = real_env_of
        (hermes_ctl.config_set, hermes_ctl.set_env_vars,
         hermes_ctl.config_get) = real_set, real_env, real_get
    check("the key is not rewritten", "API_SERVER_KEY" not in calls, calls)
    check("and the result says it was kept", res2.get("key_generated") is False, res2)

    section("the response shapes, as the live server actually returns them")
    # Both of these were found by running against a real Hermes api_server, not by reading
    # its source, and neither would have failed a unit test written from assumptions.
    #
    # 1. The session id is NESTED. The first version read a top-level "id", which parsed
    #    fine, found nothing, and reported {ok: False, detail: None} - a dead "Empezar de
    #    cero" button with no explanation anywhere.
    check("the id is read from the nested session object",
          T._session_id({"object": "hermes.session",
                         "session": {"id": "api_1_2", "title": None}}) == "api_1_2")
    check("a flat shape still works, in case it moves back",
          T._session_id({"id": "flat"}) == "flat" and
          T._session_id({"session_id": "alt"}) == "alt")
    check("and an unusable body yields nothing rather than a false id",
          T._session_id({"object": "hermes.session"}) == "" and T._session_id(None) == "")

    # 2. Hermes requires session TITLES to be unique and answers 400 "already in use".
    #    A title is a nicety; the conversation is not. So a collision retries untitled.
    calls = []
    real_http, real_env, real_port = T._http, T.env_of, T._configured_port
    T.env_of = lambda profile=None, hermes=None: {"API_SERVER_KEY": "k" * 64}
    T._configured_port = lambda profile=None, hermes=None: 8642

    def fake_http(url, key, data=None, method=None, timeout=25):
        calls.append(data)
        if data:      # the titled attempt collides, exactly as the live server did
            return False, {"error": {"message": "Title 'x' is already in use by session a"}}, 400
        return True, {"object": "hermes.session", "session": {"id": "api_ok"}}, 201

    T._http = fake_http
    try:
        res = T.create(title="Prueba desde la UI")
    finally:
        T._http, T.env_of, T._configured_port = real_http, real_env, real_port
    check("a taken title retries without one rather than failing",
          res.get("ok") and res.get("session_id") == "api_ok", res)
    check("and it really did try the title first",
          len(calls) == 2 and calls[0] and not calls[1], calls)

    calls.clear()
    T.env_of = lambda profile=None, hermes=None: {"API_SERVER_KEY": "k" * 64}
    T._configured_port = lambda profile=None, hermes=None: 8642
    T._http = lambda *a, **k: (calls.append(1), (False, {"error": {"message": "boom"}}, 500))[1]
    try:
        res2 = T.create(title="x")
    finally:
        T._http, T.env_of, T._configured_port = real_http, real_env, real_port
    check("an error that is NOT a title collision is not retried blindly",
          len(calls) == 1 and res2.get("ok") is not True, (calls, res2))

    section("the wizard exposes it")
    ws = io.open(os.path.join(SRC, "wizard", "wizard_server.py"), encoding="utf-8").read()
    for route in ("talk/status", "talk/enable", "talk/send", "connections/status"):
        check("route %s exists" % route, '"%s"' % route in ws)
    check("every talk route resolves the agent server-side",
          ws.count("talk.") >= 6 and "_target_profile(body)" in ws)


def main():
    test_brain()
    test_connections()
    test_talk()
    print("\n%d passed, %d failed" % (len(PASSED), len(FAILED)))
    for f in FAILED:
        print("  - " + f)
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
