#!/usr/bin/env python3
r"""Hand a WhatsApp conversation to the owner - deterministically.

The agent decides THAT a situation needs a human. It decides nothing else. Which reasons
exist, how urgent each one is, how the alert is worded, where it goes, how many times it
is retried, and what happens when every channel fails are all fixed here, in code, so the
outcome does not vary with the model's mood, phrasing, or memory of the instructions.

The guarantee is "the owner never misses one", and it is built in four layers:

  1. LEDGER FIRST. The escalation is appended to an on-disk journal before any network
     call. A crash, a power cut, or a dead internet connection still leaves the record.
  2. VERIFIED DELIVERY. Telegram's API returns the created Message; we require its
     message_id. An HTTP 200 with no message is not treated as delivered.
  3. RETRIES with backoff, honouring Telegram's own retry_after on 429.
  4. AN OUTBOX. Anything not confirmed stays in a pending file. It is retried by the next
     invocation and is meant to be surfaced by the wizard and the nightly routine, so an
     escalation that failed at 02:00 is still in front of the owner at breakfast.

Exit codes are the contract for the caller:
    0  the owner has it, confirmed by the delivering service
    3  recorded and queued, but NOT confirmed - tell the client a human was notified only
       with care, and say so in your reply to the owner later
    2  bad usage (unknown reason code, missing required text)

Stdlib only, and no imports from the rest of Olivaw, on purpose: the emergency path must
not be able to fail because of a package layout change.
"""

import argparse
import hashlib
import io
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

TELEGRAM_API = "https://api.telegram.org/bot{token}/{method}"
MAX_TELEGRAM = 4096
EXCERPT_LIMIT = 700
SEND_ATTEMPTS = 4
DEDUP_WINDOW_S = 300


# ── the fixed taxonomy ───────────────────────────────────────────────────────
# Adding a reason is a code change and a deliberate one. The model may only choose from
# what is here; it cannot invent a category, a priority, or a headline.

REASONS = {
    "angry": ("Cliente molesto", "alta", "\U0001F534"),
    "human_requested": ("Pide hablar con una persona", "alta", "\U0001F64B"),
    "complaint": ("Queja formal", "alta", "\U0001F4E3"),
    "legal": ("Amenaza legal", "alta", "⚖️"),
    "medical_urgent": ("Posible urgencia médica", "alta", "\U0001F691"),
    "payment_issue": ("Problema de cobro o pago", "alta", "\U0001F4B3"),
    "refund": ("Pide reembolso o cancelar", "media", "↩️"),
    "repeated": ("Insiste sin recibir solución", "media", "\U0001F501"),
    "vip": ("Contacto importante", "media", "⭐"),
    "data_request": ("Pide sus datos o privacidad", "media", "\U0001F510"),
    "agent_stuck": ("El agente no sabe qué responder", "media", "\U0001F914"),
    "other": ("Requiere atención", "media", "\U0001F4CC"),
}

PRIORITY_MARK = {"alta": "‼️", "media": ""}


def _now():
    return datetime.now(timezone.utc).astimezone()


def _hermes_home():
    env = os.environ.get("HERMES_HOME")
    if env:
        return env
    local = os.environ.get("LOCALAPPDATA")
    if local and os.path.isdir(os.path.join(local, "hermes")):
        return os.path.join(local, "hermes")
    return os.path.join(os.path.expanduser("~"), ".hermes")


def _state_dir():
    d = os.path.join(_hermes_home(), "escalations")
    try:
        os.makedirs(d, exist_ok=True)
    except OSError:
        d = os.path.join(os.path.expanduser("~"), ".olivaw-escalations")
        os.makedirs(d, exist_ok=True)
    return d


LEDGER = lambda: os.path.join(_state_dir(), "escalations.jsonl")          # noqa: E731
OUTBOX = lambda: os.path.join(_state_dir(), "pending.jsonl")              # noqa: E731


def _read_env():
    """Hermes' .env, read directly. The gateway may not be running when we escalate."""
    out = {}
    path = os.path.join(_hermes_home(), ".env")
    try:
        with io.open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip().strip('"').strip("'")
    except OSError:
        pass
    for k in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_HOME_CHANNEL", "TELEGRAM_ALLOWED_USERS"):
        if os.environ.get(k):
            out[k] = os.environ[k]
    return out


def _http_json(url, data=None, timeout=20):
    body = None
    headers = {"User-Agent": "olivaw-escalation", "Accept": "application/json"}
    if data is not None:
        body = urllib.parse.urlencode(data).encode("utf-8")
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    req = urllib.request.Request(url, data=body, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return True, json.loads(r.read().decode("utf-8", "replace")), r.status
    except urllib.error.HTTPError as e:
        raw = ""
        try:
            raw = e.read().decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            pass
        try:
            return False, json.loads(raw), e.code
        except Exception:  # noqa: BLE001
            return False, {"description": raw or str(e)}, e.code
    except Exception as e:  # noqa: BLE001
        return False, {"description": str(e)}, 0


# ── the alert itself ─────────────────────────────────────────────────────────

def _clean(text, limit):
    t = re.sub(r"\s+", " ", str(text or "")).strip()
    return t if len(t) <= limit else t[: limit - 1].rstrip() + "…"


def _wa_link(contact):
    digits = re.sub(r"\D", "", str(contact or ""))
    return "https://wa.me/%s" % digits if len(digits) >= 8 else ""


def compose(rec):
    """The alert text. Fixed layout - the model supplies facts, never formatting."""
    label, priority, icon = REASONS[rec["reason"]]
    mark = PRIORITY_MARK.get(priority, "")
    lines = [
        "%s %sATENCIÓN REQUERIDA — %s" % (icon, (mark + " ") if mark else "", label),
        "",
    ]
    who = rec.get("contact_name") or ""
    num = rec.get("contact") or ""
    both = " · ".join([p for p in (who, num) if p]) or "(sin identificar)"
    lines.append("Cliente:  %s" % both)
    lines.append("Motivo:   %s (prioridad %s)" % (label, priority))
    if rec.get("summary"):
        lines.append("Resumen:  %s" % rec["summary"])
    if rec.get("excerpt"):
        lines += ["", "Dijo:", "«%s»" % rec["excerpt"]]
    link = rec.get("chat_link") or _wa_link(num)
    if link:
        lines += ["", "Abrir:    %s" % link]
    lines.append("Cuándo:   %s" % rec["local_time"])
    lines.append("ID:       %s" % rec["id"])
    text = "\n".join(lines)
    return text if len(text) <= MAX_TELEGRAM else text[: MAX_TELEGRAM - 2] + "…"


# ── ledger + outbox ──────────────────────────────────────────────────────────

def _append(path, rec):
    try:
        with io.open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return True
    except OSError:
        return False


def _load(path):
    out = []
    try:
        with io.open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    try:
                        out.append(json.loads(line))
                    except ValueError:
                        continue
    except OSError:
        pass
    return out


def _rewrite(path, records):
    tmp = path + ".tmp"
    try:
        with io.open(tmp, "w", encoding="utf-8") as fh:
            for r in records:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
        os.replace(tmp, path)
        return True
    except OSError:
        return False


def _recent_duplicate(fingerprint, window=DEDUP_WINDOW_S):
    cutoff = time.time() - window
    for rec in reversed(_load(LEDGER())[-80:]):
        if rec.get("ts", 0) < cutoff:
            break
        if rec.get("fingerprint") == fingerprint and rec.get("delivered"):
            return rec
    return None


# ── delivery ─────────────────────────────────────────────────────────────────

def deliver_telegram(text, env, log=print):
    """Send to the owner and require Telegram's own record of the message as proof."""
    token = (env.get("TELEGRAM_BOT_TOKEN") or "").strip()
    chat = (env.get("TELEGRAM_HOME_CHANNEL")
            or (env.get("TELEGRAM_ALLOWED_USERS") or "").split(",")[0]).strip()
    if not token:
        return {"ok": False, "channel": "telegram", "error": "no_token"}
    if not chat:
        return {"ok": False, "channel": "telegram", "error": "no_owner_chat"}

    url = TELEGRAM_API.format(token=token, method="sendMessage")
    last = ""
    for attempt in range(1, SEND_ATTEMPTS + 1):
        ok, data, status = _http_json(
            url, {"chat_id": chat, "text": text, "disable_web_page_preview": "true"})
        # Proof is the returned Message, not the status line.
        if ok and isinstance(data, dict) and data.get("ok"):
            msg = data.get("result") or {}
            if msg.get("message_id"):
                return {"ok": True, "channel": "telegram",
                        "message_id": msg["message_id"],
                        "chat_id": (msg.get("chat") or {}).get("id"),
                        "date": msg.get("date"), "attempts": attempt}
            last = "telegram accepted the call but returned no message"
        else:
            last = str((data or {}).get("description") or data)[:200]
            # 401/403 will never succeed by retrying; anything else might.
            if status in (401, 403):
                return {"ok": False, "channel": "telegram", "error": last,
                        "status": status, "attempts": attempt, "fatal": True}
            if status == 429:
                wait = float(((data or {}).get("parameters") or {}).get("retry_after", 5))
                log("  telegram rate-limited, waiting %.0fs" % wait)
                time.sleep(min(wait, 30))
                continue
        if attempt < SEND_ATTEMPTS:
            time.sleep(min(2 ** attempt, 15))
    return {"ok": False, "channel": "telegram", "error": last or "unknown",
            "attempts": SEND_ATTEMPTS}


def deliver_hermes_cli(text, log=print):
    """Second channel: let Hermes route it, in case the direct API path is the broken one."""
    import shutil
    import subprocess

    exe = shutil.which("hermes")
    if not exe:
        return {"ok": False, "channel": "hermes-cli", "error": "hermes not on PATH"}
    try:
        p = subprocess.run([exe, "send", "--to", "telegram", text],
                           capture_output=True, text=True, timeout=60)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "channel": "hermes-cli", "error": str(e)[:200]}
    if p.returncode == 0:
        # `hermes send` exiting 0 is weaker evidence than a Message object; say so.
        return {"ok": True, "channel": "hermes-cli", "proof": "exit_code_only"}
    return {"ok": False, "channel": "hermes-cli",
            "error": (p.stderr or p.stdout or "")[:200]}


# ── the operation ────────────────────────────────────────────────────────────

def escalate(reason, summary="", contact="", contact_name="", excerpt="",
             chat_link="", force=False, retry_pending=True, log=print):
    if reason not in REASONS:
        return {"ok": False, "code": 2,
                "error": "reason must be one of: %s" % ", ".join(sorted(REASONS))}
    if reason == "other" and not summary.strip():
        return {"ok": False, "code": 2,
                "error": "reason 'other' requires --summary explaining what is happening"}

    now = _now()
    label, priority, _icon = REASONS[reason]
    payload = {
        "reason": reason, "reason_label": label, "priority": priority,
        "summary": _clean(summary, 400),
        "contact": _clean(contact, 60),
        "contact_name": _clean(contact_name, 80),
        "excerpt": _clean(excerpt, EXCERPT_LIMIT),
        "chat_link": _clean(chat_link, 300),
    }
    fingerprint = hashlib.sha256(
        "|".join([payload["reason"], payload["contact"], payload["summary"]])
        .encode("utf-8")).hexdigest()[:16]

    if not force:
        dup = _recent_duplicate(fingerprint)
        if dup:
            log("  duplicate of %s delivered %ds ago - not resending"
                % (dup.get("id"), int(time.time() - dup.get("ts", 0))))
            return {"ok": True, "code": 0, "duplicate_of": dup.get("id"),
                    "delivered": True, "channel": dup.get("channel"),
                    "note": "Ya se avisó al dueño de esto hace un momento."}

    rec = dict(payload)
    rec.update({
        "id": "esc-%s-%s" % (now.strftime("%Y%m%d-%H%M%S"), fingerprint[:4]),
        "ts": time.time(),
        "local_time": now.strftime("%d/%m/%Y %H:%M"),
        "fingerprint": fingerprint,
        "delivered": False,
        "channel": None,
    })

    text = compose(rec)
    rec["text"] = text

    # Layer 1: on disk before anything can go wrong on the network.
    if not _append(LEDGER(), rec):
        log("  WARNING: could not write the ledger at %s" % LEDGER())

    env = _read_env()
    results = []
    outcome = deliver_telegram(text, env, log=log)
    results.append(outcome)
    if not outcome.get("ok"):
        log("  telegram failed (%s); trying hermes send" % outcome.get("error"))
        outcome = deliver_hermes_cli(text, log=log)
        results.append(outcome)

    rec["delivered"] = bool(outcome.get("ok"))
    rec["channel"] = outcome.get("channel")
    rec["proof"] = {k: v for k, v in outcome.items() if k != "ok"}
    rec["attempts"] = results

    _rewrite(LEDGER(), [r for r in _load(LEDGER()) if r.get("id") != rec["id"]] + [rec])

    if not rec["delivered"]:
        _append(OUTBOX(), rec)
        log("  NOT confirmed - left in the outbox at %s" % OUTBOX())

    flushed = _flush_outbox(env, skip_id=rec["id"], log=log) if retry_pending else 0

    return {
        "ok": True,
        "code": 0 if rec["delivered"] else 3,
        "id": rec["id"],
        "reason": reason,
        "priority": priority,
        "delivered": rec["delivered"],
        "channel": rec["channel"],
        "proof": rec["proof"],
        "outbox_flushed": flushed,
        "outbox_remaining": len(_load(OUTBOX())),
        "ledger": LEDGER(),
    }


def _flush_outbox(env, skip_id=None, log=print):
    """Retry escalations that never reached the owner. Called on every escalation."""
    pending = _load(OUTBOX())
    if not pending:
        return 0
    still, sent = [], 0
    for rec in pending:
        if rec.get("id") == skip_id:
            still.append(rec)
            continue
        out = deliver_telegram(rec.get("text") or compose(rec), env, log=log)
        if out.get("ok"):
            sent += 1
            log("  outbox: delivered %s (was stuck)" % rec.get("id"))
        else:
            still.append(rec)
    _rewrite(OUTBOX(), still)
    return sent


def main(argv=None):
    p = argparse.ArgumentParser(
        prog="escalate_owner",
        description="Alert the owner about a WhatsApp conversation that needs a human.")
    # Not `required=True`: --list-reasons has to work on its own, and argparse would
    # refuse the whole call before we ever got to it.
    p.add_argument("--reason", choices=sorted(REASONS),
                   help="Fixed category. Pick the closest; use 'other' only with --summary.")
    p.add_argument("--summary", default="", help="One line: what is happening.")
    p.add_argument("--contact", default="", help="Client phone or WhatsApp id.")
    p.add_argument("--contact-name", default="", help="Client name, if known.")
    p.add_argument("--excerpt", default="", help="What the client actually wrote, verbatim.")
    p.add_argument("--chat-link", default="", help="Link to open the chat.")
    p.add_argument("--force", action="store_true",
                   help="Send even if an identical alert went out in the last 5 minutes.")
    p.add_argument("--json", action="store_true", help="Machine-readable output.")
    p.add_argument("--list-reasons", action="store_true", help="Print the taxonomy and exit.")
    a = p.parse_args(argv)

    if a.list_reasons:
        for k in sorted(REASONS):
            label, pri, icon = REASONS[k]
            print("%-16s %s %-34s prioridad %s" % (k, icon, label, pri))
        return 0

    if not a.reason:
        p.error("--reason is required (or use --list-reasons to see the options)")

    quiet = a.json
    log = (lambda m: None) if quiet else print
    r = escalate(reason=a.reason, summary=a.summary, contact=a.contact,
                 contact_name=a.contact_name, excerpt=a.excerpt,
                 chat_link=a.chat_link, force=a.force, log=log)

    if a.json:
        print(json.dumps(r, ensure_ascii=False, indent=2))
    elif not r.get("ok"):
        print("ERROR: %s" % r.get("error"))
    elif r.get("duplicate_of"):
        print("Ya avisado (%s). No se repite." % r["duplicate_of"])
    elif r["delivered"]:
        print("Avisado al dueño por %s. ID %s." % (r["channel"], r["id"]))
    else:
        print("NO confirmado. Guardado como %s; se reintentará." % r["id"])
    return r.get("code", 1)


if __name__ == "__main__":
    sys.exit(main())
