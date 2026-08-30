r"""The WhatsApp reliability kit, tested against the things that actually break it.

Three units, three failure stories:

  wa_patch            `hermes update` git-pulls over Hermes' own checkout. The patch has to
                      survive that, come back after it, refuse to half-apply when upstream
                      moves the code, and repair a conflicted bridge.js rather than leave
                      Hermes with a file that will not parse.
  whatsapp_delivery   a phone that is off must not be reported as a failed send, and an id
                      the bridge never saw must never be reported as sent.
  escalate_owner      an escalation that cannot be delivered must not be lost, and a
                      Telegram 200 with no Message must not count as delivered.

Run: python tools/test_whatsapp_kit.py
"""

import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
sys.path.insert(0, SRC)
sys.path.insert(0, os.path.join(SRC, "tools"))

from wizard import wa_patch as WP          # noqa: E402
import whatsapp_delivery as WD             # noqa: E402

PASSED = []
FAILED = []


def check(name, cond, extra=""):
    if cond:
        PASSED.append(name)
        print("  ok   " + name)
    else:
        FAILED.append(name)
        print("  FAIL " + name + (("\n       " + str(extra)) if extra else ""))


def section(title):
    print("\n=== %s ===" % title)


# ── a bridge.js good enough to patch ─────────────────────────────────────────
# Only the anchor lines matter, but they are copied verbatim from the real bridge so a
# change upstream shows up here as a failing test rather than a silent no-op in the field.

FAKE_BRIDGE = """\
const express = require('express');
const app = express();
let connectionState = 'connected';
const messageStore = createBoundedMessageStore(512);

function trackSentMessageId(sent) {
  rememberSentId(sent?.key?.id);
}

function startSocket() {
  sock.ev.on('messages.update', async (updates) => {
    for (const { key, update } of updates || []) {
      if (!update?.pollUpdates) continue;
      handlePoll(key, update);
    }
  });

  sock.ev.on('messages.upsert', async ({ messages, type }) => {
    if (type !== 'notify') return;
  });
}

// Health check
app.get('/health', (req, res) => {
  res.json({ status: connectionState });
});
"""


def write_bridge(d, text=FAKE_BRIDGE, eol="\n"):
    p = os.path.join(d, "bridge.js")
    with io.open(p, "w", encoding="utf-8", newline="") as fh:
        fh.write(text.replace("\n", eol))
    return p


def test_patch():
    section("wa_patch: applying")
    tmp = tempfile.mkdtemp(prefix="wapatch-")
    os.environ["HERMES_HOME"] = tmp          # keep bookkeeping out of any real install
    try:
        p = write_bridge(tmp)
        original = io.open(p, encoding="utf-8", newline="").read()

        check("an unpatched bridge reports 'absent'", WP.status(p)["state"] == "absent")

        r = WP.apply(p)
        check("apply succeeds", r["applied"] and r["changed"], r.get("detail"))
        check("every hunk landed", WP.status(p)["hunks_present"] == len(WP.HUNKS))
        body = io.open(p, encoding="utf-8", newline="").read()
        check("the receipt store is present", "olivawReceipts" in body)
        check("the /receipts endpoint is present", "app.get('/receipts'" in body)
        check("the receipt event handler is present", "message-receipt.update" in body)
        check("the poll `continue` is still there (we added, not replaced)",
              "if (!update?.pollUpdates) continue;" in body)

        section("wa_patch: idempotency and reversal")
        r2 = WP.apply(p)
        check("re-applying changes nothing", r2["applied"] and not r2["changed"])
        check("still exactly one copy of each hunk",
              WP.status(p)["hunks_present"] == len(WP.HUNKS))

        WP.remove(p)
        check("remove restores upstream byte for byte",
              io.open(p, encoding="utf-8", newline="").read() == original)

        section("wa_patch: CRLF checkouts")
        pc = write_bridge(tmp, eol="\r\n")
        crlf_before = io.open(pc, "rb").read().count(b"\r\n")
        WP.apply(pc)
        raw = io.open(pc, "rb").read()
        check("patched file is still CRLF throughout",
              raw.count(b"\r\n") > crlf_before and b"\n" not in raw.replace(b"\r\n", b""))
        check("CRLF file reports as applied", WP.status(pc)["state"] == "applied")

        section("wa_patch: upstream moved the code")
        pm = write_bridge(tmp, FAKE_BRIDGE.replace(
            "const messageStore = createBoundedMessageStore(512);",
            "const messageStore = createBoundedMessageStore(1024);   // upstream changed this"))
        st = WP.status(pm)
        check("a moved anchor is detected", st["state"] == "anchors_moved", st)
        check("the moved anchor is named", "store" in st.get("missing_anchors", []), st)
        before = io.open(pm, encoding="utf-8", newline="").read()
        rm = WP.apply(pm)
        check("apply refuses rather than half-patching", not rm["applied"])
        check("the file was left untouched",
              io.open(pm, encoding="utf-8", newline="").read() == before)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        os.environ.pop("HERMES_HOME", None)


def test_conflict_heal():
    section("wa_patch: repairing a bridge.js that git left conflicted")
    if not shutil.which("git"):
        print("  skip (git not available)")
        return
    tmp = tempfile.mkdtemp(prefix="waconf-")
    os.environ["HERMES_HOME"] = tmp
    repo = os.path.join(tmp, "repo")
    os.makedirs(repo)
    try:
        run = lambda *a: subprocess.run(["git"] + list(a), cwd=repo,  # noqa: E731
                                        capture_output=True, text=True)
        run("init", "-q")
        run("config", "user.email", "t@t")
        run("config", "user.name", "t")
        p = write_bridge(repo)
        run("add", "bridge.js")
        run("commit", "-qm", "upstream")

        WP.apply(p)
        check("patched inside a git checkout", WP.status(p)["state"] == "applied")

        # Exactly what a failed `git stash apply` leaves behind.
        text = io.open(p, encoding="utf-8", newline="").read()
        conflicted = text.replace(
            "const olivawReceipts = new Map();",
            "<<<<<<< Updated upstream\nconst olivawReceipts = new Map();\n=======\n"
            "const olivawReceipts = new Map();\n>>>>>>> Stashed changes")
        io.open(p, "w", encoding="utf-8", newline="").write(conflicted)

        check("a conflicted file is reported as such", WP.status(p)["state"] == "conflicted")

        r = WP.apply(p, log=lambda m: print("       " + m))
        check("apply heals it instead of giving up", r.get("applied"), r.get("detail"))
        healed = io.open(p, encoding="utf-8", newline="").read()
        check("no conflict markers survive", "<<<<<<<" not in healed and ">>>>>>>" not in healed)
        check("the patch is back on", WP.status(p)["state"] == "applied")
        check("upstream content is intact", "createBoundedMessageStore(512)" in healed)

        # A conflict that has nothing to do with us must be left for a human.
        WP.remove(p)
        io.open(p, "w", encoding="utf-8", newline="").write(
            "<<<<<<< a\nsomething\n=======\nelse\n>>>>>>> b\n")
        r2 = WP.apply(p)
        check("someone else's conflict is not touched", not r2.get("applied"))
        check("and it is left exactly as found",
              "<<<<<<<" in io.open(p, encoding="utf-8", newline="").read())
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        os.environ.pop("HERMES_HOME", None)


# ── a stand-in bridge for the verifier ───────────────────────────────────────

class _BridgeStub(BaseHTTPRequestHandler):
    receipts = {}
    patched = True

    def log_message(self, *a):
        pass

    def _json(self, code, body):
        raw = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        u = urlparse(self.path)
        if u.path == "/health":
            return self._json(200, {"status": "connected"})
        if u.path == "/receipts":
            if not _BridgeStub.patched:
                return self._json(404, {"error": "Not found"})
            ids = [i for i in parse_qs(u.query).get("ids", [""])[0].split(",") if i]
            found = {i: _BridgeStub.receipts[i] for i in ids if i in _BridgeStub.receipts}
            return self._json(200, {"patch": "olivaw-receipts v1", "connection": "connected",
                                    "tracked": len(_BridgeStub.receipts),
                                    "receipts": found,
                                    "unknown": [i for i in ids if i not in found]})
        self._json(404, {"error": "Not found"})


def test_delivery():
    section("whatsapp_delivery: grading the ack ladder")
    g = WD._grade_one
    check("no entry at all means it was never sent", g(None) == "unknown")
    check("PENDING is not 'sent'", g({"status": 1}) == "pending")
    check("SERVER_ACK is 'sent'", g({"status": 2}) == "sent")
    check("DELIVERY_ACK is 'delivered'", g({"status": 3}) == "delivered")
    check("READ counts as delivered", g({"status": 4}) == "delivered")
    check("PLAYED counts as delivered", g({"status": 5}) == "delivered")
    check("status 0 is a failure", g({"status": 0}) == "failed")
    check("an explicit error wins over any status", g({"status": 4, "error": "x"}) == "failed")

    section("whatsapp_delivery: a batch is only as good as its weakest message")
    order = WD.RANK
    check("unknown outranks pending as the worse verdict",
          order.index("unknown") < order.index("pending"))
    check("'sent' and 'delivered' are the only confirmed verdicts",
          set(WD._CONFIRMED) == {"sent", "delivered"})

    section("whatsapp_delivery: against a live bridge")
    _BridgeStub.patched = True
    _BridgeStub.receipts = {
        "OK1": {"id": "OK1", "status": 3, "statusName": "delivery_ack"},
        "SRV": {"id": "SRV", "status": 2, "statusName": "server_ack"},
        "SIL": {"id": "SIL", "status": 1, "statusName": "pending"},
    }
    srv = HTTPServer(("127.0.0.1", 0), _BridgeStub)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    port = srv.server_address[1]
    try:
        h = WD.bridge_health(port=port)
        check("health sees the patch", h["reachable"] and h["patched"], h)

        r = WD.verify(["OK1"], port=port, delivery_wait=3)
        check("a delivered message is confirmed", r["verdict"] == "delivered" and r["confirmed"])

        t0 = time.time()
        r = WD.verify(["SRV"], port=port, delivery_wait=2, poll_interval=0.3)
        check("a phone that never answers falls back to 'sent'",
              r["verdict"] == "sent" and r["confirmed"], r)
        check("and it does not block past the wait", time.time() - t0 < 6)

        r = WD.verify(["SIL"], port=port, delivery_wait=1, poll_interval=0.3)
        check("no ack at all is NOT confirmed",
              r["verdict"] == "pending" and not r["confirmed"], r)

        r = WD.verify(["GHOST"], port=port, delivery_wait=1, poll_interval=0.3)
        check("an id the bridge never saw is 'unknown', never 'sent'",
              r["verdict"] == "unknown" and not r["confirmed"], r)

        r = WD.verify(["OK1", "GHOST"], port=port, delivery_wait=1, poll_interval=0.3)
        check("one bad message condemns the batch", r["verdict"] == "unknown", r)

        _BridgeStub.patched = False
        r = WD.verify(["OK1"], port=port, delivery_wait=1)
        check("an unpatched bridge says 'unverifiable', not 'sent'",
              r["verdict"] == "unverifiable" and not r["confirmed"], r)
    finally:
        srv.shutdown()

    r = WD.verify(["X"], port=1, delivery_wait=1)
    check("a dead bridge says 'unverifiable'", r["verdict"] == "unverifiable")
    r = WD.verify([], port=1)
    check("verifying nothing proves nothing", not r["confirmed"])


# ── escalation ───────────────────────────────────────────────────────────────

class _TelegramStub(BaseHTTPRequestHandler):
    mode = "ok"
    calls = []

    def log_message(self, *a):
        pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = parse_qs(self.rfile.read(n).decode())
        _TelegramStub.calls.append(body.get("text", [""])[0])
        m = _TelegramStub.mode
        if m == "ok":
            code, payload = 200, {"ok": True, "result": {
                "message_id": 900 + len(_TelegramStub.calls), "date": 1756500000,
                "chat": {"id": 8114329186}}}
        elif m == "down":
            code, payload = 500, {"ok": False, "description": "Internal Server Error"}
        elif m == "revoked":
            code, payload = 401, {"ok": False, "description": "Unauthorized"}
        else:                       # 200 OK, but Telegram created no message
            code, payload = 200, {"ok": True, "result": {}}
        raw = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


def test_escalation():
    section("escalate_owner: the alert is built from the table, not the model")
    home = tempfile.mkdtemp(prefix="esc-")
    os.environ["HERMES_HOME"] = home
    io.open(os.path.join(home, ".env"), "w", encoding="utf-8").write(
        "TELEGRAM_BOT_TOKEN=1:FAKE\nTELEGRAM_HOME_CHANNEL=8114329186\n")

    import escalate_owner as E
    srv = HTTPServer(("127.0.0.1", 0), _TelegramStub)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    E.TELEGRAM_API = "http://127.0.0.1:%d/bot{token}/{method}" % srv.server_address[1]
    E.SEND_ATTEMPTS = 2
    E.deliver_hermes_cli = lambda text, log=print: {
        "ok": False, "channel": "hermes-cli", "error": "stubbed"}
    quiet = lambda m: None       # noqa: E731
    _TelegramStub.mode = "ok"
    _TelegramStub.calls = []

    try:
        r = E.escalate("angry", summary="Lleva 3 mensajes pidiendo su factura.",
                       contact="+5215551234567", contact_name="Juan Pérez",
                       excerpt="llevo una semana esperando", log=quiet)
        check("delivered and confirmed", r["delivered"] and r["code"] == 0, r)
        check("proof is Telegram's own message_id", bool(r["proof"].get("message_id")))
        check("priority comes from the table", r["priority"] == "alta")
        text = _TelegramStub.calls[-1]
        check("the client is named", "Juan Pérez" in text)
        check("the client is quoted verbatim", "llevo una semana esperando" in text)
        check("a wa.me link is built from the number", "wa.me/5215551234567" in text)
        check("the reason label is the fixed one", "Cliente molesto" in text)

        section("escalate_owner: not spamming, not missing")
        n = len(_TelegramStub.calls)
        r2 = E.escalate("angry", summary="Lleva 3 mensajes pidiendo su factura.",
                        contact="+5215551234567", log=quiet)
        check("an identical alert within 5 min is suppressed", len(_TelegramStub.calls) == n)
        check("the caller still sees success", r2["code"] == 0 and r2.get("duplicate_of"))
        r3 = E.escalate("angry", summary="Lleva 3 mensajes pidiendo su factura.",
                        contact="+5215551234567", force=True, log=quiet)
        check("--force overrides suppression",
              len(_TelegramStub.calls) == n + 1 and r3["delivered"])

        section("escalate_owner: nothing is lost when delivery fails")
        _TelegramStub.mode = "down"
        r4 = E.escalate("human_requested", summary="Quiere hablar con una persona",
                        contact="+5215559999999", log=quiet)
        check("not reported as delivered", not r4["delivered"])
        check("exit code 3 means 'queued, unconfirmed'", r4["code"] == 3)
        check("kept in the outbox",
              any(p["id"] == r4["id"] for p in E._load(E.OUTBOX())))
        check("recorded in the ledger regardless",
              any(l["id"] == r4["id"] for l in E._load(E.LEDGER())))

        _TelegramStub.mode = "empty"
        r5 = E.escalate("complaint", summary="Queja por el servicio",
                        contact="+5215558888888", log=quiet)
        check("a 200 with no Message is not 'delivered'",
              not r5["delivered"] and r5["code"] == 3)

        section("escalate_owner: the outbox drains on the next call")
        _TelegramStub.mode = "ok"
        r6 = E.escalate("vip", summary="Cliente importante escribió",
                        contact="+5215557777777", log=quiet)
        check("the new one goes out", r6["delivered"])
        check("the stuck ones go out too", r6["outbox_flushed"] >= 2, r6)
        check("the outbox is empty afterwards", r6["outbox_remaining"] == 0)

        section("escalate_owner: refusing what it should refuse")
        _TelegramStub.mode = "revoked"
        _TelegramStub.calls = []
        E.escalate("legal", summary="Menciona demanda", contact="+52155566", force=True,
                   log=quiet)
        check("a revoked token is not retried in a loop", len(_TelegramStub.calls) == 1)
        check("an invented reason is rejected", E.escalate("furioso", log=quiet)["code"] == 2)
        check("'other' without a summary is rejected", E.escalate("other", log=quiet)["code"] == 2)
        check("every reason has a label, a priority and an icon",
              all(len(v) == 3 and v[1] in ("alta", "media") for v in E.REASONS.values()))

        # The CLI is what the skill actually tells the agent to run, so exercise the
        # argument surface too - testing escalate() alone once let a broken
        # --list-reasons ship, because argparse rejected the call before main() ran.
        section("escalate_owner: the command line the skill documents")
        env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
        script = os.path.join(SRC, "tools", "escalate_owner.py")
        lr = subprocess.run([sys.executable, script, "--list-reasons"],
                            capture_output=True, text=True, env=env, timeout=60)
        check("--list-reasons works on its own", lr.returncode == 0, lr.stderr[-300:])
        check("it lists every reason",
              all(k in lr.stdout for k in E.REASONS), lr.stdout)
        missing = subprocess.run([sys.executable, script, "--summary", "x"],
                                 capture_output=True, text=True, env=env, timeout=60)
        check("a call with no --reason is still refused", missing.returncode == 2)
    finally:
        srv.shutdown()
        shutil.rmtree(home, ignore_errors=True)
        os.environ.pop("HERMES_HOME", None)


def test_injected_js():
    """Run the injected receipt store through Node, if Node is here.

    The JavaScript is what actually runs in production; testing only the Python that
    installs it would leave the ack ladder itself unverified.
    """
    section("the injected JavaScript, driven as Baileys would")
    node = shutil.which("node")
    spec = os.path.join(ROOT, "tools", "test_wa_receipts.mjs")
    if not node or not os.path.isfile(spec):
        print("  skip (node or spec not available)")
        return
    tmp = tempfile.mkdtemp(prefix="wajs-")
    try:
        gen = os.path.join(tmp, "_wa_store.generated.mjs")
        exports = ("\nexport { olivawReceipts, olivawSeedOutbound, olivawRecordStatus,"
                   " olivawRecordReceipt, OLIVAW_STATUS };\n")
        with io.open(gen, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(WP._STORE + exports)
        shutil.copy2(spec, os.path.join(tmp, "test_wa_receipts.mjs"))
        env = dict(os.environ, OLIVAW_RECEIPT_MAX="50")
        p = subprocess.run([node, "test_wa_receipts.mjs"], cwd=tmp, env=env,
                           capture_output=True, text=True, timeout=120)
        for line in (p.stdout or "").splitlines():
            if line.strip().startswith(("ok", "FAIL")):
                print("  " + line.strip())
        check("the injected receipt store passes its own suite", p.returncode == 0,
              (p.stdout or "") + (p.stderr or ""))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_escalation_prefs():
    """What the owner ticks in the wizard is what the agent is told and what actually fires."""
    section("escalation preferences: defaults")
    home = tempfile.mkdtemp(prefix="prefs-")
    os.environ["HERMES_HOME"] = home
    io.open(os.path.join(home, ".env"), "w", encoding="utf-8").write(
        "TELEGRAM_BOT_TOKEN=1:FAKE\nTELEGRAM_HOME_CHANNEL=8114329186\n")
    from wizard import channels, escalation_prefs as P, wa_setup
    import escalate_owner as E

    try:
        g = channels.escalation_get()
        check("the catalog offers every built-in reason", len(g["catalog"]) == len(E.REASONS))
        check("each catalog entry explains when to use it",
              all(c["description"] for c in g["catalog"]))
        check("an unconfigured install has everything ON, not everything off",
              g["prefs"]["enabled"] and len(g["prefs"]["reasons"]) == len(E.REASONS))
        check("and says it has not been configured yet", g["prefs"]["configured"] is False)
        check("Telegram readiness is reported", g["telegram_ready"] is True)

        section("escalation preferences: her own reasons")
        r = channels.escalation_save(
            enabled=True, reasons=["angry", "human_requested"],
            custom=[{"key": "", "label": "Pide cita urgente", "priority": "alta",
                     "selected": True,
                     "description": "Cuando el paciente pide cita para hoy o mañana, o dice "
                                    "que no puede esperar a la fecha que le diste."}])
        check("saving succeeds", r["ok"], r.get("detail"))
        g2 = channels.escalation_get()
        custom = g2["prefs"]["custom"]
        check("the server assigns a CLI-safe key from her label",
              len(custom) == 1 and custom[0]["key"] == "pide_cita_urgente", custom)
        check("a reason she just added is active without her ticking it",
              "pide_cita_urgente" in g2["prefs"]["reasons"], g2["prefs"]["reasons"])
        check("the ones she did not tick are off",
              "refund" not in g2["prefs"]["reasons"])

        section("escalation preferences: refusing what would not work")
        bad = [({"key": "", "label": "Algo", "description": ""}, "no description"),
               ({"key": "", "label": "Algo", "description": "corta"}, "a one-word description"),
               ({"key": "", "label": "", "description": "una descripción larga y clara"}, "no label"),
               ({"key": "angry", "label": "Enojo", "description": "una descripción larga"},
                "a key that collides with a built-in")]
        for entry, why in bad:
            res = channels.escalation_save(enabled=True, reasons=[], custom=[entry])
            check("rejects %s" % why, not res["ok"], res.get("detail"))
        check("preferences were not clobbered by a rejected save",
              "pide_cita_urgente" in channels.escalation_get()["prefs"]["reasons"])

        section("escalation preferences: the agent is told exactly this")
        block = wa_setup._reasons_block(home)
        check("her own reason appears with her own words",
              "pide_cita_urgente" in block and "no puede esperar" in block, block)
        check("it is marked as hers", "suyo" in block)
        check("switched-off reasons are named as switched off",
              "`refund`" in block and "no** le llegará aviso" in block.replace("*", "*"), block)
        skill = wa_setup.render_skill(home)
        check("the skill embeds that block", "pide_cita_urgente" in skill)
        check("the skill documents exit code 4", "`4`" in skill)

        section("escalation preferences: a switched-off reason is recorded, not sent")
        out = E.escalate("refund", summary="pide reembolso", contact="+5215551111111",
                         log=lambda m: None)
        check("exit code 4, not 0 and not 3", out["code"] == 4, out)
        check("explicitly not delivered", out["delivered"] is False and out["muted"])
        check("but it is on the ledger anyway",
              any(x.get("id") == out["id"] for x in E._load(E.LEDGER())))

        section("escalation preferences: switching it off entirely")
        channels.escalation_save(enabled=False, reasons=["angry"], custom=[])
        out2 = E.escalate("angry", summary="molesto", contact="+52155", log=lambda m: None)
        check("even a ticked reason is muted when notifications are off", out2["code"] == 4)
        check("the skill says so plainly",
              "desactivado" in wa_setup._reasons_block(home).lower())

        section("escalation preferences: a second agent keeps its own")
        other = tempfile.mkdtemp(prefix="prefs2-")
        P.save(enabled=True, reasons=["legal"], custom=[], regenerate_skill=False, home=other)
        check("the other profile has its own selection",
              P.load(home=other)["reasons"] == ["legal"])
        check("and this one is untouched", P.load(home=home)["enabled"] is False)
        shutil.rmtree(other, ignore_errors=True)
    finally:
        shutil.rmtree(home, ignore_errors=True)
        os.environ.pop("HERMES_HOME", None)


def main():
    test_patch()
    test_conflict_heal()
    test_injected_js()
    test_delivery()
    test_escalation()
    test_escalation_prefs()
    print("\n%d passed, %d failed" % (len(PASSED), len(FAILED)))
    if FAILED:
        for f in FAILED:
            print("  - " + f)
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
