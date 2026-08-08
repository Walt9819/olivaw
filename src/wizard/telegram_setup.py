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


def capture_owner(token):
    """
    getUpdates -> find the most recent person who wrote to the bot. That person is
    the OWNER: the only account allowed to command the agent. In a private chat the
    user id and chat id are the same; we lock on the user id.
    """
    ok, data = _call(token, "getUpdates", {"timeout": 0, "limit": 20}, timeout=25)
    if not ok:
        return {"ok": False,
                "detail": "No pudimos leer los mensajes. Si configuraste un webhook, "
                          "quítalo. Detalle: "
                          + (data.get("description") if isinstance(data, dict) else "")}
    updates = data.get("result", []) or []
    picked = None
    for upd in reversed(updates):
        msg = upd.get("message") or upd.get("edited_message")
        if msg and msg.get("from") and not msg["from"].get("is_bot"):
            picked = msg
            break
    if not picked:
        return {"ok": False, "waiting": True,
                "detail": "Aún no veo tu mensaje. Abre el bot en Telegram, pulsa "
                          "«Start» (o escríbele «hola») y vuelve a probar."}
    frm = picked["from"]
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
