"""Prove a WhatsApp message actually left, instead of assuming it did.

Hermes' bridge answers `{"success": true}` the instant Baileys accepts the bytes for
transmission. That is not delivery, and it is not even "WhatsApp has it" - it is "the
socket took it". This module asks the patched bridge (see wizard/wa_patch.py) what
WhatsApp actually said afterwards, and grades the result.

The ladder, in the order the owner asked for it:

    delivered   the recipient's device acknowledged it          <- what we aim for
    sent        Meta's servers took it, no device ack in time   <- fallback, counts as done
    pending     the bridge has the id, WhatsApp has said nothing
    unknown     the bridge never saw this id -> it was NOT sent
    failed      WhatsApp reported an explicit error
    unverifiable  no bridge / no receipt patch / bridge offline

The fallback is the important part. A recipient with a phone that is off will never
send a device ack, and blocking on one would report failure for a message that is sitting
safely on Meta's servers and will arrive the moment they come back online. So we wait a
bounded time for the device, then accept the server ack as proof the message is out of
our hands.

`unknown` is a real answer, not a gap: every send path in the bridge registers through
trackSentMessageId, so an id the bridge has never heard of was never sent by it.
"""

import json
import time
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_PORT = 3000
DEFAULT_HOST = "127.0.0.1"

# How long to hold out for the recipient's device before falling back to the server ack.
DELIVERY_WAIT = 25.0
POLL_INTERVAL = 1.0

# Verdicts, worst to best. Ordered so a batch can be graded by its weakest message.
RANK = ("unverifiable", "failed", "unknown", "pending", "sent", "delivered")

_CONFIRMED = ("sent", "delivered")


class BridgeUnreachable(Exception):
    """The bridge is not answering on localhost."""


class PatchMissing(Exception):
    """The bridge is running, but without the receipt patch."""


def _get(url, timeout=8.0):
    req = urllib.request.Request(url, headers={"User-Agent": "olivaw-wa-verify"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def _receipts_url(ids, host, port):
    joined = ",".join(urllib.parse.quote(str(i), safe="") for i in ids)
    return "http://%s:%d/receipts?ids=%s" % (host, port, joined)


def bridge_health(host=DEFAULT_HOST, port=DEFAULT_PORT, timeout=5.0):
    """Connection state of the bridge, plus whether it can prove deliveries at all."""
    out = {"reachable": False, "connection": None, "patched": False, "tracked": None}
    try:
        h = _get("http://%s:%d/health" % (host, port), timeout=timeout)
        out["reachable"] = True
        out["connection"] = h.get("status")
    except Exception:
        return out
    try:
        r = _get("http://%s:%d/receipts" % (host, port), timeout=timeout)
        out["patched"] = bool(r.get("patch"))
        out["tracked"] = r.get("tracked")
        out["patch"] = r.get("patch")
    except urllib.error.HTTPError as e:
        # 404 is the signal that this bridge predates the patch.
        out["patched"] = False
        out["http_status"] = e.code
    except Exception:
        pass
    return out


def _grade_one(entry):
    """Grade a single receipt entry into a verdict."""
    if entry is None:
        return "unknown"
    if entry.get("error"):
        return "failed"
    status = entry.get("status")
    if status is None:
        return "pending"
    if status == 0:
        return "failed"
    if status >= 3:          # DELIVERY_ACK / READ / PLAYED
        return "delivered"
    if status == 2:          # SERVER_ACK - Meta has it, recipient does not yet
        return "sent"
    return "pending"         # PENDING


def verify(message_ids,
           chat_id=None,
           host=DEFAULT_HOST,
           port=DEFAULT_PORT,
           delivery_wait=DELIVERY_WAIT,
           poll_interval=POLL_INTERVAL,
           log=None):
    """Poll the bridge until every id is delivered, or the wait runs out.

    Returns a dict with an overall `verdict` (the weakest of the per-message verdicts),
    `confirmed` (True when that verdict is `sent` or `delivered`), and the raw per-id
    detail so a caller can explain itself.
    """
    def say(m):
        if log:
            log(m)

    ids = [str(i) for i in (message_ids or []) if i]
    if not ids:
        return {"verdict": "unknown", "confirmed": False, "messages": {},
                "detail": "No message ids to verify - nothing proves anything was sent."}

    health = bridge_health(host, port)
    if not health["reachable"]:
        return {"verdict": "unverifiable", "confirmed": False, "messages": {},
                "bridge": health,
                "detail": "El puente de WhatsApp no responde en %s:%d." % (host, port)}
    if not health["patched"]:
        return {"verdict": "unverifiable", "confirmed": False, "messages": {},
                "bridge": health,
                "detail": "El puente no tiene el parche de confirmaciones; "
                          "no se puede comprobar la entrega."}

    deadline = time.time() + max(0.0, float(delivery_wait))
    per_id = {}
    raw = {}
    polls = 0

    while True:
        polls += 1
        try:
            data = _get(_receipts_url(ids, host, port))
        except Exception as e:
            return {"verdict": "unverifiable", "confirmed": False, "messages": per_id,
                    "bridge": health, "polls": polls,
                    "detail": "No se pudo leer /receipts: %s" % e}

        raw = data.get("receipts") or {}
        per_id = {i: _grade_one(raw.get(i)) for i in ids}

        # Everyone home, or somebody definitively failed - either way, stop early.
        if all(v == "delivered" for v in per_id.values()):
            break
        if any(v == "failed" for v in per_id.values()):
            break
        if time.time() >= deadline:
            say("verify: delivery wait elapsed after %.0fs, falling back to server ack"
                % float(delivery_wait))
            break
        time.sleep(max(0.2, float(poll_interval)))

    verdict = min(per_id.values(), key=RANK.index) if per_id else "unknown"
    confirmed = verdict in _CONFIRMED

    return {
        "verdict": verdict,
        "confirmed": confirmed,
        "messages": per_id,
        "receipts": raw,
        "chat_id": chat_id,
        "bridge": health,
        "polls": polls,
        "waited_for_delivery_s": round(float(delivery_wait), 1),
        "detail": _explain(verdict, per_id),
    }


def _explain(verdict, per_id):
    n = len(per_id)
    if verdict == "delivered":
        return "Entregado en el dispositivo del destinatario (%d/%d)." % (
            sum(1 for v in per_id.values() if v == "delivered"), n)
    if verdict == "sent":
        return ("Aceptado por los servidores de WhatsApp. Todavía sin acuse del "
                "dispositivo (el teléfono puede estar apagado); llegará solo.")
    if verdict == "pending":
        return ("WhatsApp aún no ha acusado nada. El mensaje salió del puente pero "
                "no hay prueba de que haya llegado a los servidores.")
    if verdict == "unknown":
        missing = [k for k, v in per_id.items() if v == "unknown"]
        return ("El puente no conoce %d de %d id(s) (%s). No se enviaron por este "
                "puente." % (len(missing), n, ", ".join(missing[:3])))
    if verdict == "failed":
        return "WhatsApp devolvió un error para al menos un mensaje."
    return "No se pudo verificar."


def send_and_verify(chat_id, text,
                    host=DEFAULT_HOST,
                    port=DEFAULT_PORT,
                    delivery_wait=DELIVERY_WAIT,
                    log=None):
    """Send through the bridge and return the send result joined to a real verdict.

    Callers get one object that never says "sent" on the strength of an HTTP 200 alone.
    """
    payload = json.dumps({"chatId": chat_id, "message": text}).encode("utf-8")
    req = urllib.request.Request(
        "http://%s:%d/send" % (host, port), data=payload,
        headers={"Content-Type": "application/json", "User-Agent": "olivaw-wa-send"})
    try:
        with urllib.request.urlopen(req, timeout=45) as r:
            sent = json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")[:300]
        return {"verdict": "failed", "confirmed": False,
                "detail": "El puente rechazó el envío (%s): %s" % (e.code, body)}
    except Exception as e:
        return {"verdict": "unverifiable", "confirmed": False,
                "detail": "No se pudo hablar con el puente: %s" % e}

    ids = sent.get("messageIds") or ([sent["messageId"]] if sent.get("messageId") else [])
    result = verify(ids, chat_id=chat_id, host=host, port=port,
                    delivery_wait=delivery_wait, log=log)
    result["send_response"] = sent
    result["message_ids"] = ids
    return result


def main(argv=None):  # pragma: no cover - operator/agent entry point
    import argparse

    p = argparse.ArgumentParser(
        prog="whatsapp_delivery",
        description="Check whether WhatsApp messages actually got delivered.")
    p.add_argument("--ids", default="",
                   help="Comma-separated message ids returned by the send.")
    p.add_argument("--chat", default="", help="Chat id, for the report only.")
    p.add_argument("--send", default="",
                   help="Text to send first, then verify (needs --chat).")
    p.add_argument("--wait", type=float, default=DELIVERY_WAIT,
                   help="Seconds to wait for the recipient's device before "
                        "falling back to the server ack (default %(default)s).")
    p.add_argument("--port", type=int, default=DEFAULT_PORT)
    p.add_argument("--host", default=DEFAULT_HOST)
    p.add_argument("--health", action="store_true",
                   help="Just report bridge and patch state.")
    p.add_argument("--json", action="store_true")
    a = p.parse_args(argv)

    if a.health:
        h = bridge_health(a.host, a.port)
        print(json.dumps(h, ensure_ascii=False, indent=2) if a.json else
              "bridge reachable=%s connection=%s receipts=%s tracked=%s"
              % (h["reachable"], h["connection"], h["patched"], h["tracked"]))
        return 0 if (h["reachable"] and h["patched"]) else 1

    if a.send:
        if not a.chat:
            print("--send needs --chat")
            return 2
        r = send_and_verify(a.chat, a.send, host=a.host, port=a.port,
                            delivery_wait=a.wait)
    else:
        ids = [s.strip() for s in a.ids.split(",") if s.strip()]
        if not ids:
            print("nothing to verify: pass --ids or --send")
            return 2
        r = verify(ids, chat_id=a.chat or None, host=a.host, port=a.port,
                   delivery_wait=a.wait)

    if a.json:
        print(json.dumps(r, ensure_ascii=False, indent=2))
    else:
        print("%s  %s" % (r["verdict"].upper(), r["detail"]))
    # 0 = it really went out, 1 = it did not, 2 = we could not tell.
    return 0 if r["confirmed"] else (2 if r["verdict"] == "unverifiable" else 1)


if __name__ == "__main__":  # pragma: no cover
    import sys

    sys.exit(main())
