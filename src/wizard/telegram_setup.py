"""
Telegram bot wiring — everything that CAN be automated after the user gets a token.

Reality check: you cannot create a bot programmatically. The token must come from a
short manual chat with @BotFather. But once the user pastes it, this module does the
rest automatically: validate it, capture the owner's chat id, brand the bot, and send
a confirmation message — so the flow feels one-tap even though BotFather is manual.
"""

from .procutil import http_json

API = "https://api.telegram.org/bot{token}/{method}"


def _call(token, method, params=None, timeout=25):
    url = API.format(token=token.strip(), method=method)
    ok, data, status = http_json(url, data=(params or {}), method="POST",
                                 timeout=timeout)
    if isinstance(data, dict) and "ok" in data:
        return data.get("ok", False), data
    return ok, {"ok": False, "description": str(data)[:300]}


def validate(token):
    """getMe -> confirm the token is real and return the bot's identity."""
    ok, data = _call(token, "getMe", timeout=15)
    if not ok:
        return {"ok": False,
                "detail": "Ese token no funcionó. Cópialo completo desde BotFather.",
                "error": (data.get("description") if isinstance(data, dict) else "")}
    r = data.get("result", {})
    return {"ok": True, "bot_id": r.get("id"),
            "username": r.get("username"), "name": r.get("first_name"),
            "detail": "Bot válido: @%s" % r.get("username", "?")}


def capture_owner(token, code=None):
    """
    getUpdates -> identify the OWNER (the only account allowed to command the agent).

    Security: a plain "most recent sender wins" is hijackable — a stranger who messages the bot
    during the setup window could be captured as owner. So the wizard shows a one-time CODE and
    the operator must send exactly that code; we bind ownership ONLY to the sender whose message
    text equals the code. Without a code we fall back to "single distinct sender only" and refuse
    if more than one person has written (ambiguous), never silently picking the latest.
    """
    ok, data = _call(token, "getUpdates", {"timeout": 0, "limit": 50}, timeout=25)
    if not ok:
        return {"ok": False,
                "detail": "No pudimos leer los mensajes. Si configuraste un webhook, "
                          "quítalo. Detalle: "
                          + (data.get("description") if isinstance(data, dict) else "")}
    updates = data.get("result", []) or []
    humans = {}   # user_id -> (msg, matched_code_bool)
    for upd in updates:
        msg = upd.get("message") or upd.get("edited_message")
        frm = msg.get("from") if msg else None
        if not frm or frm.get("is_bot"):
            continue
        text = (msg.get("text") or "").strip()
        matched = bool(code) and text == str(code).strip()
        # prefer a code-matching message; otherwise keep the latest per sender
        prev = humans.get(frm["id"])
        if matched or not prev or not prev[1]:
            humans[frm["id"]] = (msg, matched)

    def _valid_id(x):
        try:
            return int(x) > 0
        except (TypeError, ValueError):
            return False

    picked = None
    if code:
        matches = [m for (m, ok2) in humans.values() if ok2]
        if not matches:
            return {"ok": False, "waiting": True,
                    "detail": "Aún no veo tu código. Abre el bot en Telegram y envíale exactamente: %s" % code}
        if len({m["from"]["id"] for m in matches}) > 1:
            return {"ok": False,
                    "detail": "Más de una cuenta envió el código. Usa un código nuevo y no lo compartas."}
        picked = matches[0]
    else:
        if not humans:
            return {"ok": False, "waiting": True,
                    "detail": "Aún no veo tu mensaje. Abre el bot en Telegram, pulsa «Start» y vuelve a probar."}
        if len(humans) > 1:
            return {"ok": False,
                    "detail": "Varias personas escribieron al bot. Reinicia el bot o usa un código de verificación "
                              "para asegurar que TÚ quedes como dueño."}
        picked = next(iter(humans.values()))[0]

    frm = picked["from"]
    if not _valid_id(frm.get("id")):
        return {"ok": False, "detail": "No pude leer un id de usuario válido."}
    chat = picked.get("chat", {})
    name = " ".join(x for x in [frm.get("first_name"), frm.get("last_name")] if x)
    return {"ok": True,
            "owner_id": frm.get("id"),
            "chat_id": chat.get("id", frm.get("id")),
            "username": frm.get("username"),
            "name": name or frm.get("username") or str(frm.get("id")),
            "detail": "¡Listo! Serás el dueño del agente: %s (id %s)."
                      % (name or frm.get("username") or "tú", frm.get("id"))}


def brand(token, name=None, short_desc=None, description=None, commands=None):
    """Best-effort cosmetic setup. Failures here are non-fatal."""
    results = {}
    if name:
        ok, d = _call(token, "setMyName", {"name": name[:64]})
        results["name"] = ok
    if short_desc:
        ok, d = _call(token, "setMyShortDescription",
                      {"short_description": short_desc[:120]})
        results["short_description"] = ok
    if description:
        ok, d = _call(token, "setMyDescription", {"description": description[:512]})
        results["description"] = ok
    if commands:
        ok, d = _call(token, "setMyCommands", {"commands": commands})
        results["commands"] = ok
    return {"ok": True, "results": results, "detail": "Bot personalizado."}


def test_send(token, chat_id, text):
    ok, data = _call(token, "sendMessage",
                     {"chat_id": chat_id, "text": text}, timeout=20)
    if not ok:
        return {"ok": False,
                "detail": "No se pudo enviar el mensaje de prueba. Detalle: "
                          + (data.get("description") if isinstance(data, dict) else "")}
    return {"ok": True, "detail": "¡Mensaje enviado! Revisa tu Telegram."}
