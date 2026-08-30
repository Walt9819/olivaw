"""Teach Hermes' WhatsApp bridge to remember delivery receipts.

Baileys already tells the bridge how far every outbound message got - `messages.update`
carries `update.status` (PENDING -> SERVER_ACK -> DELIVERY_ACK -> READ -> PLAYED) and
`message-receipt.update` carries per-recipient read/played timestamps. Upstream throws
both away: the `messages.update` handler starts with `if (!update?.pollUpdates) continue;`
and there is no receipt handler at all. So `/send` answers `{success: true}` the moment
Baileys accepts the bytes, and nothing downstream can ever tell whether the message
actually left the machine.

This module adds the missing bookkeeping and a `/receipts` endpoint to read it back,
WITHOUT forking the bridge:

  * every hunk is wrapped in `// >>> olivaw-receipts vN` ... `// <<< olivaw-receipts`,
    so applying is idempotent and removing is exact;
  * anchors must match EXACTLY ONCE or nothing is written - a Hermes update that moves
    the code makes us report `anchors_moved`, never a half-patched bridge;
  * `ensure()` is cheap (stat + marker scan) and re-applies after `hermes update`
    overwrites the file, which it will, because the install is an editable git checkout.

Nothing here changes what the bridge SENDS. The patch only records what WhatsApp says
came back, and exposes it read-only.
"""

import io
import json
import os
import re
import shutil
import time

PATCH_VERSION = 1
MARK = "olivaw-receipts"
BEGIN = "// >>> %s v%d" % (MARK, PATCH_VERSION)
END = "// <<< %s" % MARK

# Any version's blocks, so an older patch can be lifted out cleanly before the new one
# goes in. DOTALL because the blocks span lines.
_BLOCK_RE = re.compile(
    r"[ \t]*// >>> %s v\d+\n.*?// <<< %s[ \t]*\n" % (re.escape(MARK), re.escape(MARK)),
    re.DOTALL,
)
_ANY_MARK_RE = re.compile(r"// >>> %s v(\d+)" % re.escape(MARK))


# ── the JavaScript we inject ─────────────────────────────────────────────────

_STORE = """
// Delivery receipts.  Baileys reports ack progress through `messages.update`
// (update.status) and `message-receipt.update`; the stock bridge drops both unless
// they carry a poll vote.  We keep the last N outbound ids with the highest ack state
// seen, so a caller can prove a message actually left - and how far it got - instead
// of trusting the HTTP 200 that /send returns the instant Baileys accepts the bytes.
const OLIVAW_RECEIPT_MAX = parseInt(process.env.OLIVAW_RECEIPT_MAX || '2000', 10);
const OLIVAW_STATUS = { ERROR: 0, PENDING: 1, SERVER_ACK: 2, DELIVERY_ACK: 3, READ: 4, PLAYED: 5 };
const OLIVAW_STATUS_NAME = ['error', 'pending', 'server_ack', 'delivery_ack', 'read', 'played'];
const olivawReceipts = new Map();

// Baileys has shipped both the numeric enum and its string name over the years.
function olivawStatusCode(status) {
  if (status === null || status === undefined) return null;
  if (typeof status === 'number') return Number.isFinite(status) ? status : null;
  const name = String(status).toUpperCase();
  return Object.prototype.hasOwnProperty.call(OLIVAW_STATUS, name) ? OLIVAW_STATUS[name] : null;
}

function olivawTrim() {
  // Map iteration is insertion order, so this evicts oldest-first and keeps memory flat
  // under sustained sending, same discipline as the outbound id tracker.
  while (olivawReceipts.size > OLIVAW_RECEIPT_MAX) {
    olivawReceipts.delete(olivawReceipts.keys().next().value);
  }
}

function olivawEntry(id, chatId) {
  let entry = olivawReceipts.get(id);
  if (!entry) {
    entry = {
      id, chatId: chatId || '', status: null, statusName: null,
      sentAt: Date.now(), serverAckAt: null, deliveredAt: null, readAt: null,
      updatedAt: Date.now(), error: null,
    };
    olivawReceipts.set(id, entry);
    olivawTrim();
  }
  if (chatId && !entry.chatId) entry.chatId = chatId;
  return entry;
}

function olivawApplyStatus(entry, code) {
  if (code === null || code === undefined) return;
  const now = Date.now();
  if (code === OLIVAW_STATUS.ERROR) entry.error = 'whatsapp reported ERROR for this message';
  // Ack state only ever moves forward: a late PENDING must not erase a READ.
  if (entry.status === null || code > entry.status) {
    entry.status = code;
    entry.statusName = OLIVAW_STATUS_NAME[code] || String(code);
  }
  if (code >= OLIVAW_STATUS.SERVER_ACK && !entry.serverAckAt) entry.serverAckAt = now;
  if (code >= OLIVAW_STATUS.DELIVERY_ACK && !entry.deliveredAt) entry.deliveredAt = now;
  if (code >= OLIVAW_STATUS.READ && !entry.readAt) entry.readAt = now;
  entry.updatedAt = now;
}

// Hooked into trackSentMessageId so every send path registers - /send, /send-media,
// /send-poll, /send-location - without touching each handler separately.
function olivawSeedOutbound(sent) {
  const id = sent && sent.key && sent.key.id;
  if (!id) return;
  const entry = olivawEntry(id, (sent.key && sent.key.remoteJid) || '');
  const code = olivawStatusCode(sent.status);
  olivawApplyStatus(entry, code === null ? OLIVAW_STATUS.PENDING : code);
}

function olivawRecordStatus(key, update) {
  const id = key && key.id;
  // fromMe === false is somebody else's message; their ack state is not ours to track.
  if (!id || (key && key.fromMe === false)) return;
  const code = olivawStatusCode(update && update.status);
  if (code === null) return;
  olivawApplyStatus(olivawEntry(id, (key && key.remoteJid) || ''), code);
}

// Groups - and some 1:1 paths - report progress here instead: a per-recipient receipt
// carrying read/played timestamps rather than a status enum.
function olivawRecordReceipt(key, receipt) {
  const id = key && key.id;
  if (!id || !receipt) return;
  const entry = olivawEntry(id, (key && key.remoteJid) || '');
  if (receipt.receiptTimestamp) olivawApplyStatus(entry, OLIVAW_STATUS.DELIVERY_ACK);
  if (receipt.readTimestamp) olivawApplyStatus(entry, OLIVAW_STATUS.READ);
  if (receipt.playedTimestamp) olivawApplyStatus(entry, OLIVAW_STATUS.PLAYED);
}
"""

_SEED = """
  olivawSeedOutbound(sent);
"""

_RECORD_STATUS = """
      olivawRecordStatus(key, update);
"""

_RECEIPT_HANDLER = """
  sock.ev.on('message-receipt.update', (updates) => {
    for (const entry of updates || []) {
      try {
        olivawRecordReceipt(entry && entry.key, entry && entry.receipt);
      } catch (err) {
        console.warn('[bridge] receipt update failed:', err.message);
      }
    }
  });

"""

_ENDPOINT = """
// Ack state for specific outbound ids.  `unknown` is meaningful on its own: a genuine
// send always registers through trackSentMessageId, so an id the bridge has never heard
// of was never actually sent by this bridge (or predates a restart - the store is
// in-memory by design, matching the rest of the bridge's state).
app.get('/receipts', (req, res) => {
  const raw = String(req.query.ids || '').trim();
  const base = {
    patch: 'olivaw-receipts v1',
    connection: connectionState,
    tracked: olivawReceipts.size,
  };
  if (!raw) return res.json({ ...base, receipts: {}, unknown: [] });

  const ids = raw.split(',').map(s => s.trim()).filter(Boolean);
  const receipts = {};
  const unknown = [];
  for (const id of ids) {
    const entry = olivawReceipts.get(id);
    if (entry) receipts[id] = entry;
    else unknown.push(id);
  }
  res.json({ ...base, receipts, unknown });
});

"""


def _block(js, indent=""):
    """Wrap a hunk in its markers so it can be found and removed exactly."""
    body = js.strip("\n")
    lines = [indent + BEGIN]
    lines.extend((indent + ln) if ln.strip() else "" for ln in body.split("\n"))
    lines.append(indent + END)
    return "\n".join(lines) + "\n"


# (name, anchor, where, indent, js) - `where` is "after" or "before" the anchor text.
HUNKS = (
    (
        "store",
        "const messageStore = createBoundedMessageStore(512);\n",
        "after",
        "",
        _STORE,
    ),
    (
        "seed",
        "function trackSentMessageId(sent) {\n  rememberSentId(sent?.key?.id);\n",
        "after",
        "  ",
        _SEED,
    ),
    (
        "record-status",
        "  sock.ev.on('messages.update', async (updates) => {\n"
        "    for (const { key, update } of updates || []) {\n",
        "after",
        "      ",
        _RECORD_STATUS,
    ),
    (
        "receipt-handler",
        "  sock.ev.on('messages.upsert', async ({ messages, type }) => {\n",
        "before",
        "  ",
        _RECEIPT_HANDLER,
    ),
    (
        "endpoint",
        "// Health check\napp.get('/health', (req, res) => {\n",
        "before",
        "",
        _ENDPOINT,
    ),
)


# ── locating the bridge ──────────────────────────────────────────────────────

_REL = os.path.join("scripts", "whatsapp-bridge", "bridge.js")


def _candidates(hermes_exe=None):
    seen = []

    env = os.environ.get("OLIVAW_BRIDGE_JS") or os.environ.get("HERMES_BRIDGE_JS")
    if env:
        seen.append(env)

    # From the hermes launcher: <root>/venv/Scripts/hermes -> walk up for the checkout.
    exe = hermes_exe
    if not exe:
        exe = shutil.which("hermes") or ""
    if exe:
        d = os.path.dirname(os.path.abspath(exe))
        for _ in range(5):
            seen.append(os.path.join(d, _REL))
            parent = os.path.dirname(d)
            if parent == d:
                break
            d = parent

    home = os.path.expanduser("~")
    for base in (
        os.environ.get("HERMES_HOME", ""),
        os.path.join(home, "AppData", "Local", "hermes", "hermes-agent"),
        os.path.join(home, ".local", "share", "hermes", "hermes-agent"),
        os.path.join(home, "hermes-agent"),
    ):
        if base:
            seen.append(os.path.join(base, _REL))
            seen.append(os.path.join(base, "hermes-agent", _REL))

    out = []
    for p in seen:
        p = os.path.abspath(os.path.expanduser(p))
        if p not in out:
            out.append(p)
    return out


def bridge_path(hermes_exe=None):
    """Absolute path to the bridge Hermes actually runs, or "" if it is not installed."""
    for p in _candidates(hermes_exe):
        if os.path.isfile(p):
            return p
    return ""


def _side_dir():
    """Our own bookkeeping, deliberately outside Hermes' git checkout.

    `hermes update` runs `git stash push --include-untracked`, so anything we leave beside
    bridge.js gets dragged through a stash/restore cycle for no benefit. HERMES_HOME is
    stable, per-profile, and not under version control.
    """
    home = (os.environ.get("HERMES_HOME")
            or (os.path.join(os.environ["LOCALAPPDATA"], "hermes")
                if os.environ.get("LOCALAPPDATA")
                and os.path.isdir(os.path.join(os.environ["LOCALAPPDATA"], "hermes"))
                else os.path.join(os.path.expanduser("~"), ".hermes")))
    d = os.path.join(home, "olivaw-wa")
    try:
        os.makedirs(d, exist_ok=True)
    except OSError:
        pass
    return d


def _stamp_path(path):
    return os.path.join(_side_dir(), "receipts-stamp.json")


def _backup_path(path):
    return os.path.join(_side_dir(), "bridge.js.upstream")


# Git leaves these behind when `hermes update` cannot re-apply its own stash cleanly.
_CONFLICT_RE = re.compile(r"^(<{7}|={7}|>{7})", re.M)


# ── read / apply / remove ────────────────────────────────────────────────────


def _read(path):
    """Return (text_with_LF_endings, original_eol).

    The bridge is LF in git but a Windows checkout hands us CRLF, and every anchor in
    this module is written with LF. Rather than duplicate each anchor, normalise on the
    way in and restore the file's own endings on the way out - so the patch never shows
    up as a whole-file diff to `hermes update`.
    """
    with io.open(path, encoding="utf-8", errors="replace", newline="") as fh:
        raw = fh.read()
    eol = "\r\n" if "\r\n" in raw else "\n"
    return raw.replace("\r\n", "\n"), eol


def _write(path, text, eol="\n"):
    if eol != "\n":
        text = text.replace("\n", eol)
    with io.open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)


def strip(text):
    """Remove every olivaw block, any version. Safe on unpatched text."""
    return _BLOCK_RE.sub("", text)


def status(path=None, hermes_exe=None):
    """What state is the installed bridge in? Never writes."""
    path = path or bridge_path(hermes_exe)
    if not path or not os.path.isfile(path):
        return {"ok": False, "state": "no_bridge", "path": path or "",
                "detail": "El puente de WhatsApp de Hermes no está instalado."}

    text, _eol = _read(path)
    if _CONFLICT_RE.search(text):
        return {"ok": False, "state": "conflicted", "path": path,
                "patch_version": PATCH_VERSION,
                "detail": "bridge.js tiene marcas de conflicto de git: `hermes update` no "
                          "pudo reaplicar su propio stash. El puente no arranca así."}
    found = _ANY_MARK_RE.findall(text)
    versions = sorted({int(v) for v in found})
    clean = strip(text)

    missing = []
    ambiguous = []
    for name, anchor, _where, _indent, _js in HUNKS:
        n = clean.count(anchor)
        if n == 0:
            missing.append(name)
        elif n > 1:
            ambiguous.append(name)

    if versions and versions == [PATCH_VERSION] and len(found) == len(HUNKS):
        state = "applied"
    elif versions:
        state = "stale" if versions != [PATCH_VERSION] else "partial"
    elif missing or ambiguous:
        state = "anchors_moved"
    else:
        state = "absent"

    return {
        "ok": state in ("applied", "absent"),
        "state": state,
        "path": path,
        "patch_version": PATCH_VERSION,
        "found_versions": versions,
        "hunks_present": len(found),
        "hunks_expected": len(HUNKS),
        "missing_anchors": missing,
        "ambiguous_anchors": ambiguous,
    }


def apply(path=None, hermes_exe=None, log=None):
    """Apply the patch. Idempotent; refuses rather than half-patching."""
    def say(m):
        if log:
            log(m)

    path = path or bridge_path(hermes_exe)
    st = status(path)
    if st["state"] == "no_bridge":
        return dict(st, applied=False)
    if st["state"] == "applied":
        _write_stamp(path)
        return dict(st, applied=True, changed=False,
                    detail="El puente ya sabe confirmar entregas.")
    if st["state"] == "conflicted":
        healed = _heal_conflict(path, say)
        if not healed:
            return dict(st, applied=False, changed=False)
        st = status(path)
        if st["state"] not in ("absent", "applied"):
            return dict(st, applied=False, changed=False)
    if st["state"] == "anchors_moved":
        say("wa_patch: anchors moved (missing=%s ambiguous=%s) - not touching bridge.js"
            % (st["missing_anchors"], st["ambiguous_anchors"]))
        return dict(st, applied=False, changed=False,
                    detail="El puente de Hermes cambió; el parche necesita revisión.")

    text, eol = _read(path)
    out = strip(text)  # lift any older/partial version out first
    for name, anchor, where, indent, js in HUNKS:
        if out.count(anchor) != 1:
            return dict(status(path), applied=False, changed=False,
                        detail="Ancla '%s' no encontrada de forma única." % name)
        block = _block(js, indent)
        out = out.replace(
            anchor,
            (anchor + block) if where == "after" else (block + anchor),
            1,
        )

    backup = _backup_path(path)
    if not os.path.exists(backup):
        shutil.copy2(path, backup)
    _write(path, out, eol)
    _write_stamp(path)
    say("wa_patch: applied v%d to %s" % (PATCH_VERSION, path))
    after = status(path)
    return dict(after, applied=after["state"] == "applied", changed=True,
                detail="El puente ahora confirma entregas.")


def _heal_conflict(path, say):
    """Take bridge.js back to the committed upstream version, then let apply() re-patch.

    A conflict here always has the same shape: git could not merge our purely additive
    blocks back onto a bridge.js that upstream also changed. Our side is reproducible from
    this module, so discarding it and re-deriving loses nothing - whereas leaving conflict
    markers in place leaves Hermes with a bridge that will not even parse.

    Only ever touches this one file, and only when the conflict actually involves us.
    """
    import subprocess

    text, _eol = _read(path)
    if MARK not in text:
        say("wa_patch: bridge.js is conflicted but not by us - leaving it alone")
        return False

    repo = os.path.dirname(path)
    try:
        inside = subprocess.run(["git", "rev-parse", "--is-inside-work-tree"], cwd=repo,
                                capture_output=True, text=True, timeout=20)
        if inside.returncode != 0 or "true" not in inside.stdout:
            say("wa_patch: bridge.js is conflicted and not under git - manual fix needed")
            return False
        r = subprocess.run(["git", "checkout", "--", os.path.basename(path)], cwd=repo,
                           capture_output=True, text=True, timeout=40)
    except Exception as e:  # noqa: BLE001
        say("wa_patch: could not restore bridge.js from git: %s" % e)
        return False
    if r.returncode != 0:
        say("wa_patch: git refused to restore bridge.js: %s" % (r.stderr or "")[:200])
        return False
    say("wa_patch: bridge.js had conflict markers; restored it from git and re-patching")
    return True


def remove(path=None, hermes_exe=None):
    """Take the patch back out, leaving the file as upstream ships it."""
    path = path or bridge_path(hermes_exe)
    if not path or not os.path.isfile(path):
        return {"ok": False, "detail": "No bridge.js"}
    text, eol = _read(path)
    out = strip(text)
    changed = out != text
    if changed:
        _write(path, out, eol)
    stamp = _stamp_path(path)
    if os.path.exists(stamp):
        try:
            os.remove(stamp)
        except OSError:
            pass
    return {"ok": True, "changed": changed, "path": path}


def _write_stamp(path):
    try:
        stt = os.stat(path)
        with io.open(_stamp_path(path), "w", encoding="utf-8") as fh:
            json.dump({"version": PATCH_VERSION, "size": stt.st_size,
                       "mtime": int(stt.st_mtime), "at": int(time.time())}, fh)
    except OSError:
        pass


def _stamp_matches(path):
    try:
        with io.open(_stamp_path(path), encoding="utf-8") as fh:
            s = json.load(fh)
        stt = os.stat(path)
        return (s.get("version") == PATCH_VERSION
                and s.get("size") == stt.st_size
                and s.get("mtime") == int(stt.st_mtime))
    except Exception:
        return False


def ensure(hermes_exe=None, log=None):
    """Cheap guard for hot paths: re-apply when `hermes update` overwrites the bridge.

    The stamp records size+mtime at the moment we patched, so the common case is two
    stat() calls and no file read at all.
    """
    path = bridge_path(hermes_exe)
    if not path:
        return {"ok": False, "state": "no_bridge", "changed": False}
    if _stamp_matches(path):
        return {"ok": True, "state": "applied", "changed": False, "path": path}
    return apply(path, log=log)


if __name__ == "__main__":  # pragma: no cover - operator entry point
    import sys

    cmd = (sys.argv[1] if len(sys.argv) > 1 else "status").lower()
    fn = {"status": status, "apply": apply, "remove": remove, "ensure": ensure}.get(cmd)
    if not fn:
        print("usage: wa_patch.py [status|apply|remove|ensure]")
        raise SystemExit(2)
    result = fn(log=lambda m: print(m)) if cmd in ("apply", "ensure") else fn()
    print(json.dumps(result, indent=2, ensure_ascii=False))
    raise SystemExit(0 if result.get("ok") else 1)
