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


def test_session():
    from wizard import session_health as SH

    section("the login expires, and that has to be seen coming")
    src = io.open(os.path.join(SRC, "wizard", "session_health.py"), encoding="utf-8").read()
    # The access token lasts hours and the CLI renews it silently; the REFRESH token is the
    # one whose death makes the owner log in again. Warning on the wrong one would put a red
    # banner on a healthy machine every morning, which trains people to ignore banners.
    check("the deadline reported is the refresh token, not the access one",
          'out["refresh"] = _epoch(oauth.get("refreshTokenExpiresAt"))' in src)
    # Behavioural, not a comment-grep: with the ACCESS token already past and the refresh
    # token still weeks out, the machine is perfectly healthy - the CLI renews access
    # silently. Alarming here would put a red banner on a working machine every morning.
    acc = SH.claude_expiry().get("access")
    ref = SH.claude_expiry().get("refresh")
    if acc and ref and ref > acc:
        after_access = acc + 3600
        s0 = SH.status(now=after_access)
        # The claim is "an expired access token does not make the session expired", and
        # that is what is asserted. Demanding state == "ok" also asserted that the refresh
        # token was still far away, which is a fact about the clock, not about the code:
        # this went red the week the real refresh token came within the warning window,
        # on a machine where nothing was wrong and nothing had changed.
        check("an expired ACCESS token is not an expired session",
              s0["state"] != "expired" and s0["needs_login"] is False,
              {"state": s0["state"], "alert": s0["needs_login"]})
        check("though it is still reported, for diagnosis",
              s0["access_expires_at"] == acc, s0["access_expires_at"])
    else:
        print("  ..   (no distinct access/refresh pair here; that case unchecked)")

    section("nothing but the two timestamps is read")
    # This module is imported by an HTTP server. A status that leaked a credential to draw a
    # nicer dashboard would be a bad trade at any price.
    check("the credential file is opened only for expiry fields",
          "accessToken" not in src and "refreshToken\"" not in src,
          "a token field name appears in the source")
    real = SH.claude_expiry()
    check("reading it returns timestamps and nothing else",
          set(real) <= {"access", "refresh", "source"}, sorted(real))
    st = SH.status()
    blob = json.dumps(st, default=str).lower()
    for word in ("accesstoken", "refreshtoken", "bearer", "sk-", "eyj"):
        check("no %r anywhere in the status payload" % word, word not in blob)

    section("the states, walked across a real deadline")
    # Driven off the machine's own expiry so the arithmetic is exercised against a real
    # timestamp rather than a made-up one.
    exp = real.get("refresh")
    if not exp:
        print("  ..   (no Claude credential file here; timeline unchecked)")
    else:
        for label, when, want, alert in (
            ("a month out", exp - 30 * 86400, "ok", False),
            ("just outside the warning", exp - (SH.WARN_DAYS + 1) * 86400, "ok", False),
            ("just inside it", exp - (SH.WARN_DAYS - 1) * 86400, "expiring", False),
            ("tomorrow", exp - 1.5 * 86400, "expiring", False),
            ("an hour before", exp - 3000, "expiring", False),
            ("one minute after", exp + 60, "expired", True),
        ):
            s = SH.status(now=when)
            check("%s -> %s" % (label, want), s["state"] == want, s["state"])
            check("   and the alert is %s" % ("on" if alert else "off"),
                  s["needs_login"] is alert, s)
        # "expiring" must NOT raise the alarm: the session still works, and an alert that
        # fires while everything is fine is an alert nobody reads by the time it matters.
        s = SH.status(now=exp - 86400)
        check("a session about to expire still works, so it warns rather than alarms",
              s["needs_login"] is False and s["state"] == "expiring", s)
        check("but it does tell the owner when", "caduca" in s["detail"], s["detail"])

    section("deadlines in words, because nobody reads '4.87 days'")
    for days, want in ((None, ""), (-1, "ya caducó"), (0.02, "en menos de una hora"),
                       (0.5, "hoy"), (1.4, "mañana"), (9.2, "en 9 días")):
        check("%s -> %r" % (days, want), SH._when(days) == want, SH._when(days))

    section("milliseconds and seconds both understood")
    check("a millisecond epoch is scaled", abs(SH._epoch(1789040751418) - 1789040751.418) < 1)
    check("a second epoch is left alone", SH._epoch(1789040751) == 1789040751)
    for bad in (None, "", "abc", 0, -5):
        check("%r is not a date" % (bad,), SH._epoch(bad) is None)

    section("Codex is asked, not guessed at")
    # Codex is not installed on the machine this was written on, so its auth file's shape
    # could not be verified. Inventing a field name and reporting a confident wrong date is
    # worse than admitting the date is unknown.
    # The `or` that used to be here made this vacuous - it passed whether certain was
    # True or False. The property is: `certain` is True ONLY when a file really provided a
    # timestamp, and with no file both must be empty.
    tmpc = tempfile.mkdtemp(prefix="codexauth-")
    real_home = SH.codex_home
    try:
        SH.codex_home = lambda: tmpc
        cx = SH.codex_expiry()
        check("with no auth file, no date and no confidence",
              cx["refresh"] is None and cx["certain"] is False, cx)
        # A file that DOES carry an expiry-shaped field: found, and only then trusted.
        io.open(os.path.join(tmpc, "auth.json"), "w", encoding="utf-8").write(
            json.dumps({"tokens": {"access_token": "x", "expires_at": 1789040751418}}))
        cx2 = SH.codex_expiry()
        check("a real expiry field is found", bool(cx2["refresh"]), cx2)
        check("and only then is it called certain", cx2["certain"] is True, cx2)
        check("the token beside it is not carried out",
              "x" not in json.dumps({k: v for k, v in cx2.items() if k != "source"}), cx2)
        # A file with no expiry-shaped field must not invent one.
        io.open(os.path.join(tmpc, "auth.json"), "w", encoding="utf-8").write(
            json.dumps({"tokens": {"access_token": "x"}}))
        cx3 = SH.codex_expiry()
        check("a file with no expiry does not get a guessed one",
              cx3["refresh"] is None and cx3["certain"] is False, cx3)
    finally:
        SH.codex_home = real_home
        shutil.rmtree(tmpc, ignore_errors=True)
    check("and the source says why it is best-effort", "could not be verified" in src)

    section("a brain that is missing is not a brain that is logged out")
    st2 = SH.status(engine="codex")
    if not st2["found"]:
        check("Codex absent reads as not_installed", st2["state"] == "not_installed", st2)
        check("and does NOT raise a login alert - there is nothing to log into",
              st2["needs_login"] is False, st2)

    section("verifying after a login runs a REAL turn")
    check("the shallow check exists for polling while the owner types",
          "if not deep:" in src)
    check("the deep one goes through the same end-to-end test the wizard uses",
          "checks.test_brain(url" in src)
    check("and the test's verdict wins over the CLI's opinion",
          'out["ok"] = bool(t.get("ok"))' in src)
    v = SH.verify(deep=False)
    check("a healthy machine verifies shallowly without touching the bridge",
          v.get("ok") is True and v.get("tested") is False, v)

    section("Olivaw never handles the credential itself")
    check("login delegates to the brain's own flow",
          "p.login(dict(paths or {}))" in src)
    check("and that is written down as the reason",
          "never touches a credential" in src)

    section("the supervisor tells the owner, where they already are")
    # The dashboard shows an expired login, but only to somebody who opens the dashboard.
    # The one notice that reaches the owner where they already are is worth sending - once
    # per change, never on a loop, because an alert repeated every poll gets muted and this
    # is the one that must not be.
    import launcher as L
    lsrc = io.open(os.path.join(SRC, "launcher.py"), encoding="utf-8").read()
    check("the supervisor has a login check", "def _check_login(" in lsrc)
    check("and runs it on the update cadence, not every 15s loop",
          "_check_login(state)" in lsrc.split("if asked or time.time() - last_check", 1)[1][:400],
          lsrc.split("if asked or time.time() - last_check", 1)[1][:300])

    sent = []
    real_notify, real_status = L.notify, L._session.status
    L.notify = lambda cfg, text, maintainer=False: sent.append(text)
    try:
        state = {}
        L._session.status = lambda **k: {"state": "ok", "detail": "bien"}
        L._check_login(state)
        first = len(sent)
        L._check_login(state)
        check("an unchanged state says nothing at all", len(sent) == first == 0, sent)

        L._session.status = lambda **k: {"state": "expired", "detail": "caducó"}
        L._check_login(state)
        check("an expiry is announced once", len(sent) == 1, sent)
        check("and the notice says exactly what to press",
              "Volver a entrar" in sent[-1], sent[-1][:120])
        L._check_login(state)
        check("and not again on the next pass", len(sent) == 1, sent)

        L._session.status = lambda **k: {"state": "ok", "detail": "bien"}
        L._check_login(state)
        check("coming back is announced too, so silence is not the only signal",
              len(sent) == 2 and "renovada" in sent[-1], sent[-1][:80])

        # A machine mid-install has no session yet. Telling it the session "died" would be
        # a confusing first impression, so signed_out only speaks once something was known.
        state2 = {}
        L._session.status = lambda **k: {"state": "signed_out", "detail": "sin sesión"}
        before = len(sent)
        L._check_login(state2)
        check("a half-installed machine is not told its session died",
              len(sent) == before, sent[before:])
    finally:
        L.notify, L._session.status = real_notify, real_status

    section("the dashboard is one call, and the session leads it")
    ws = io.open(os.path.join(SRC, "wizard", "wizard_server.py"), encoding="utf-8").read()
    for route in ("session/status", "session/login", "session/verify", "dashboard"):
        check("route %s exists" % route, '"%s"' % route in ws)
    check("the dashboard carries the session, the agents and the machine",
          '"session": session_health.status' in ws and '"agents": out' in ws and
          '"update": updates_mod.status' in ws)
    import wizard.wizard_server as WS
    d = WS._dashboard(fast=True)
    check("and it really answers", d.get("ok") and "session" in d, sorted(d))
    check("with one entry per agent on this machine",
          len(d["agents"]) >= 1 and all("channels" in a for a in d["agents"]),
          [a["slug"] for a in d["agents"]])


def main():
    test_brain()
    test_connections()
    test_talk()
    test_session()
    print("\n%d passed, %d failed" % (len(PASSED), len(FAILED)))
    for f in FAILED:
        print("  - " + f)
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
