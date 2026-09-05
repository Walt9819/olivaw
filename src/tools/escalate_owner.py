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
    4  recorded, but the owner switched this reason off in the wizard. Not a failure, and
       NOT a notification either - never tell the client a person was alerted
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

# The stdlib-only rule above is why this does not import winspawn.quiet like the rest of
# Olivaw does: the flag is one integer, and the emergency path is not worth a dependency.
# Same reason as everywhere else - `hermes send` is a console program, and a windowless
# parent makes Windows allocate (and show) a console for it. See src/winspawn.py.
_QUIET = {"creationflags": 0x08000000} if os.name == "nt" else {}   # CREATE_NO_WINDOW

# Everything this prints is Spanish with emoji, and the agent reads it back through a pipe.
# A piped stdout on Windows defaults to cp1252, where "dueño" alone raises
# UnicodeEncodeError - so `--list-reasons` would hand the agent a traceback instead of its
# escalation reasons. PYTHONIOENCODING only helps when someone remembered to set it.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):  # pragma: no cover
        pass

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
CUSTOM_ICON = "\U0001F4CC"

# What each built-in reason means, in the words the agent needs to recognise it. The owner's
# own reasons carry their own description, written in the wizard - that description is the
# only thing that teaches the agent when to use it, so it is required there.
REASON_HINTS = {
    "angry": "el cliente se molesta, reclama o sube el tono",
    "human_requested": "pide hablar con una persona, un humano, el dueño o el doctor",
    "complaint": "presenta una queja formal sobre el servicio",
    "legal": "menciona abogado, demanda, denuncia o Profeco",
    "medical_urgent": "describe algo que suena a urgencia médica",
    "payment_issue": "reclama un cobro, un cargo o un pago que no cuadra",
    "refund": "pide reembolso o cancelar",
    "repeated": "ya escribió varias veces lo mismo sin solución",
    "vip": "es un contacto marcado como importante",
    "data_request": "pide sus datos personales o habla de privacidad",
    "agent_stuck": "no sabes qué responder (esto no es fallar; es lo correcto)",
    "other": "algo que necesita al dueño y no encaja en los demás",
}


# ── owner preferences ────────────────────────────────────────────────────────
# The taxonomy stays fixed at call time - the model still cannot invent a category. What the
# owner controls, once, from the wizard, is WHICH reasons reach her and what her own extra
# reasons mean. Absent a preferences file everything is on, so a fresh install never silently
# swallows an escalation.

def prefs_path():
    return os.path.join(_state_dir(), "preferences.json")


def load_prefs():
    try:
        with io.open(prefs_path(), encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {"enabled": True, "reasons": None, "custom": []}
    if not isinstance(data, dict):
        return {"enabled": True, "reasons": None, "custom": []}
    reasons = data.get("reasons")
    return {
        "enabled": bool(data.get("enabled", True)),
        # None means "not configured yet" -> everything is on.
        "reasons": list(reasons) if isinstance(reasons, list) else None,
        "custom": [c for c in (data.get("custom") or []) if isinstance(c, dict) and c.get("key")],
    }


def effective_reasons(prefs=None):
    """The full taxonomy: built-ins plus the owner's own, as {key: (label, priority, icon)}."""
    prefs = prefs if prefs is not None else load_prefs()
    out = dict(REASONS)
    for c in prefs.get("custom") or []:
        key = str(c.get("key") or "").strip()
        if not key or key in REASONS:
            continue
        priority = c.get("priority") if c.get("priority") in ("alta", "media") else "media"
        out[key] = (str(c.get("label") or key), priority, CUSTOM_ICON)
    return out


def reason_hints(prefs=None):
    prefs = prefs if prefs is not None else load_prefs()
    out = dict(REASON_HINTS)
    for c in prefs.get("custom") or []:
        key = str(c.get("key") or "").strip()
        if key:
            out[key] = str(c.get("description") or "").strip()
    return out


def is_muted(reason, prefs=None):
    """True when the owner asked not to be told about this. Never a delivery failure."""
    prefs = prefs if prefs is not None else load_prefs()
    if not prefs.get("enabled", True):
        return True
    selected = prefs.get("reasons")
    if selected is None:            # not configured -> everything reaches her
        return False
    return reason not in selected


def _now():
    return datetime.now(timezone.utc).astimezone()


def _hermes_home():
    # Named Hermes profiles keep their own state under <home>/profiles/<name>, and the
    # wizard sets this when it is acting on one, so a second agent's escalation settings
    # never leak into the owner's main one.
    override = os.environ.get("OLIVAW_ESCALATION_HOME")
    if override:
        return override
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


# ── who the customer actually is ─────────────────────────────────────────────
# WhatsApp no longer hands out a phone number for everyone who writes. Many senders arrive
# as a LID - "267383306489914@lid" - an identity that is deliberately NOT their number.
# Stripping the non-digits out of one, which is what this used to do, produces a perfectly
# well-formed https://wa.me/267383306489914 pointing at a stranger, printed in an alert
# that says it is the customer. An owner clicking that opens a chat with the wrong person
# and says something about somebody else's business.
#
# So: a number goes in the alert only when the paired session PROVES it. Baileys writes the
# mapping into its own session directory as `lid-mapping-<phone>.json` (holding the LID) and
# `lid-mapping-<lid>_reverse.json` (holding the phone). Both are read; neither is guessed.
# When nothing proves a number, the alert says so and omits the link. An owner who has to
# open the chat herself has lost thirty seconds. An owner who messages the wrong person has
# lost more than that.

_LID_SUFFIX = "@lid"
_NOT_A_PERSON = ("@g.us", "@broadcast", "@newsletter")   # group, status, channel


def session_dirs(home=None):
    """Both places Hermes may keep the paired session, newest layout first.

    Hermes picks between them with get_hermes_dir("platforms/whatsapp/session",
    "whatsapp/session"): the legacy path wins only when it already holds something. Looking
    in one of them is how a perfectly good install reads as "this LID cannot be resolved",
    which here means an owner alert quietly loses the customer's phone number.
    """
    home = home or _hermes_home()
    return [os.path.join(home, "platforms", "whatsapp", "session"),
            os.path.join(home, "whatsapp", "session")]


def session_dir(home=None):
    """The one that exists, preferring a populated legacy directory as Hermes does."""
    dirs = session_dirs(home)
    for d in reversed(dirs):          # legacy first, matching Hermes' own preference
        try:
            if os.listdir(d):
                return d
        except OSError:
            continue
    return dirs[0]


def _digits(value):
    return re.sub(r"\D", "", str(value or ""))


def _lid_in_dir(lid, d):
    """Look this LID up in one session directory. '' when it is not there."""
    try:
        with io.open(os.path.join(d, "lid-mapping-%s_reverse.json" % lid),
                     encoding="utf-8") as fh:
            phone = _digits(json.load(fh))
        if 8 <= len(phone) <= 15:
            return phone
    except (OSError, ValueError):
        pass
    # The forward direction, written as lid-mapping-<phone>.json holding the LID.
    try:
        names = os.listdir(d)
    except OSError:
        return ""
    for name in names:
        m = re.match(r"^lid-mapping-(\d{8,15})\.json$", name)
        if not m:
            continue
        try:
            with io.open(os.path.join(d, name), encoding="utf-8") as fh:
                if _digits(json.load(fh)) == lid:
                    return m.group(1)
        except (OSError, ValueError):
            continue
    return ""


def resolve_lid(lid, home=None):
    """A LID's real phone number, from the paired session's own mapping files. '' if unproven.

    Both candidate session directories are searched rather than only the one Hermes would
    pick: an install caught mid-migration between the two layouts has mappings in either,
    and the cost of missing one is a customer's phone number silently absent from an alert.
    """
    lid = _digits(lid)
    if not lid:
        return ""
    for d in session_dirs(home):
        phone = _lid_in_dir(lid, d)
        if phone:
            return phone
    return ""


def canonical_phone(contact, home=None):
    """The customer's own number as plain digits, or '' when nothing here proves one.

    Never returns a group, a broadcast, or the digits of an unresolved LID.
    """
    raw = str(contact or "").strip()
    if not raw:
        return ""
    low = raw.lower()
    if any(low.endswith(s) for s in _NOT_A_PERSON):
        return ""
    if low.endswith(_LID_SUFFIX):
        return resolve_lid(low[:-len(_LID_SUFFIX)], home)
    if "@" in low:
        # s.whatsapp.net / c.us carry the real number; anything else is an id we do not know.
        host = low.rsplit("@", 1)[1]
        if host not in ("s.whatsapp.net", "c.us"):
            return ""
        raw = low.rsplit("@", 1)[0].split(":", 1)[0]      # strip a :device suffix
    digits = _digits(raw)
    return digits if 8 <= len(digits) <= 15 else ""


def _wa_link(contact, home=None):
    phone = canonical_phone(contact, home)
    return "https://wa.me/%s" % phone if phone else ""


def safe_chat_link(link, phone):
    """A caller-supplied deep link, kept only if it agrees with the number we proved.

    `chat_link` reaches this tool from the agent, i.e. from a model that has just been
    reading a customer's messages. A wa.me link it composed from a LID - or from a number
    a message asked it to use - would otherwise be printed to the owner over the top of
    the one number this file actually verified. Anything that does not match is dropped;
    a non-wa.me link is left alone, since it is not claiming to be a phone number.
    """
    link = (link or "").strip()
    if not link:
        return ""
    low = link.lower()
    if "wa.me/" not in low and "api.whatsapp.com" not in low:
        return link
    digits = _digits(low.split("wa.me/")[-1].split("?")[0]) if "wa.me/" in low else \
        _digits((low.split("phone=")[-1].split("&")[0]) if "phone=" in low else "")
    return link if (phone and digits == phone) else ""


def compose(rec):
    """The alert text. Fixed layout - the model supplies facts, never formatting."""
    # Read from the record, not the table: a reason the owner defined herself is not in
    # REASONS, and an old ledger row must still render after she renames one.
    label = rec.get("reason_label") or rec["reason"]
    priority = rec.get("priority") or "media"
    icon = rec.get("icon") or CUSTOM_ICON
    mark = PRIORITY_MARK.get(priority, "")
    lines = [
        "%s %sATENCIÓN REQUERIDA — %s" % (icon, (mark + " ") if mark else "", label),
        "",
    ]
    who = rec.get("contact_name") or ""
    # `phone` is only ever a number the session proved. `contact` may be a LID, which is
    # not a number and must never be shown as one.
    num = rec.get("phone") or ""
    both = " · ".join([p for p in (who, "+" + num if num else "") if p]) or "(sin identificar)"
    lines.append("Cliente:  %s" % both)
    lines.append("Motivo:   %s (prioridad %s)" % (label, priority))
    if rec.get("summary"):
        lines.append("Resumen:  %s" % rec["summary"])
    if rec.get("excerpt"):
        lines += ["", "Dijo:", "«%s»" % rec["excerpt"]]
    link = rec.get("chat_link") or (("https://wa.me/%s" % num) if num else "")
    if link:
        lines += ["", "Abrir:    %s" % link]
    elif rec.get("contact"):
        # No number, and saying nothing would read as "we do not know who this is" when in
        # fact the conversation is sitting right there in her WhatsApp.
        lines += ["", "Sin número verificable: WhatsApp identificó a esta persona con un",
                  "  id interno, no con su teléfono. Ábrela desde la conversación en tu",
                  "  WhatsApp%s." % ((" (%s)" % who) if who else "")]
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
                           capture_output=True, text=True, timeout=60, **_QUIET)
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
    prefs = load_prefs()
    table = effective_reasons(prefs)
    if reason not in table:
        return {"ok": False, "code": 2,
                "error": "reason must be one of: %s" % ", ".join(sorted(table))}
    if reason == "other" and not summary.strip():
        return {"ok": False, "code": 2,
                "error": "reason 'other' requires --summary explaining what is happening"}

    now = _now()
    label, priority, icon = table[reason]
    payload = {
        "reason": reason, "reason_label": label, "priority": priority,
        "summary": _clean(summary, 400),
        "contact": _clean(contact, 60),
        "contact_name": _clean(contact_name, 80),
        "excerpt": _clean(excerpt, EXCERPT_LIMIT),
        # Dropped unless it agrees with the number below - see safe_chat_link.
        "chat_link": safe_chat_link(_clean(chat_link, 300), canonical_phone(contact)),
        # Resolved ONCE, here, and stored on the ledger row: a LID that the session can map
        # today may be unmappable later, and an old row must still render the number it was
        # actually sent with. Empty means "no number was ever proven", which the alert says
        # out loud rather than papering over with digits that are not a phone.
        "phone": canonical_phone(contact),
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
        "icon": icon,
        "delivered": False,
        "channel": None,
    })

    text = compose(rec)
    rec["text"] = text

    # Layer 1: on disk before anything can go wrong on the network.
    if not _append(LEDGER(), rec):
        log("  WARNING: could not write the ledger at %s" % LEDGER())

    # The owner said she does not want to hear about this one. That is a decision, not a
    # failure: it is still on the ledger, it just does not ring her phone. Distinct from
    # code 3 so the agent never tells a client "a person has been notified".
    if is_muted(reason, prefs):
        rec["muted"] = True
        _rewrite(LEDGER(), [r for r in _load(LEDGER()) if r.get("id") != rec["id"]] + [rec])
        log("  '%s' is switched off in the owner's preferences - recorded, not sent" % reason)
        return {"ok": True, "code": 4, "id": rec["id"], "reason": reason,
                "priority": priority, "delivered": False, "muted": True, "channel": None,
                "detail": "El dueño desactivó los avisos para «%s». Queda registrado." % label,
                "ledger": LEDGER()}

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
    # Deliberately no `choices` and no `required`: argparse fixes both at parse time, but
    # the valid set depends on --home (a named profile has its own reasons) and
    # --list-reasons must work with no --reason at all. Validated by hand below instead,
    # against the profile actually asked for.
    p.add_argument("--reason",
                   help="Fixed category. Pick the closest; use 'other' only with --summary. "
                        "See --list-reasons.")
    p.add_argument("--summary", default="", help="One line: what is happening.")
    p.add_argument("--contact", default="", help="Client phone or WhatsApp id.")
    p.add_argument("--contact-name", default="", help="Client name, if known.")
    p.add_argument("--excerpt", default="", help="What the client actually wrote, verbatim.")
    p.add_argument("--chat-link", default="", help="Link to open the chat.")
    p.add_argument("--force", action="store_true",
                   help="Send even if an identical alert went out in the last 5 minutes.")
    p.add_argument("--home", default="",
                   help="Hermes profile home, when this agent is not the default one.")
    p.add_argument("--json", action="store_true", help="Machine-readable output.")
    p.add_argument("--list-reasons", action="store_true", help="Print the taxonomy and exit.")
    a = p.parse_args(argv)
    if a.home:
        os.environ["OLIVAW_ESCALATION_HOME"] = a.home
    table = effective_reasons()

    if a.list_reasons:
        prefs = load_prefs()
        hints = reason_hints(prefs)
        if not prefs.get("enabled", True):
            print("(Los avisos al dueño están DESACTIVADOS en el asistente.)")
            print("")
        for k in sorted(table):
            label, pri, icon = table[k]
            state = "off" if is_muted(k, prefs) else " on"
            own = "  (tuyo)" if k not in REASONS else ""
            print("%s %-16s %s %-32s prioridad %-6s%s" % (state, k, icon, label, pri, own))
            if hints.get(k):
                print("      %s" % hints[k])
        return 0

    if not a.reason:
        p.error("--reason is required (or use --list-reasons to see the options)")
    if a.reason not in table:
        p.error("unknown --reason '%s'. Valid: %s" % (a.reason, ", ".join(sorted(table))))

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
    elif r.get("muted"):
        print(r["detail"])
    elif r["delivered"]:
        print("Avisado al dueño por %s. ID %s." % (r["channel"], r["id"]))
    else:
        print("NO confirmado. Guardado como %s; se reintentará." % r["id"])
    return r.get("code", 1)


if __name__ == "__main__":
    sys.exit(main())
