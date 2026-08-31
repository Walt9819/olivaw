"""
Telegram bot wiring — everything that CAN be automated after the user gets a token.

Reality check: you cannot create a bot programmatically. The token must come from a
short manual chat with @BotFather. But once the user pastes it, this module does the
rest automatically: validate it, capture the owner's chat id, brand the bot, and send
a confirmation message — so the flow feels one-tap even though BotFather is manual.
"""

import re
import unicodedata

from .procutil import http_json

API = "https://api.telegram.org/bot{token}/{method}"

# A bot token is <digits>:<35-ish url-safe chars>. Anchored nowhere on purpose: we search
# for it inside whatever was pasted, because people copy the whole BotFather line.
TOKEN_RE = re.compile(r"(\d{5,}):([A-Za-z0-9_-]{20,})")

# Characters that survive .trim() and .strip() and then destroy the request.
# - Cf (format): zero-width space U+200B, BOM U+FEFF, LRM/RLM U+200E/200F, soft hyphen U+00AD
# - the quote marks a phone keyboard or a chat client substitutes
_QUOTES = "\"'`‘’“”«»"


def clean_token(raw):
    """Recover the real token from whatever the owner actually pasted.

    This is the single most common way setup fails, and the reason it fails REPEATEDLY: a
    copy from BotFather - especially on a phone, or out of Telegram Desktop - routinely
    carries an invisible character (zero-width space, BOM, a directional mark) or the
    surrounding sentence. Both `str.strip()` and JavaScript's `trim()` remove whitespace
    only, so the junk sails through, and urllib then raises deep in the stack with
    "'ascii' codec can't encode character '\\u200b'". The old message told the owner to copy
    the token again from BotFather - which reproduces the identical character, forever.

    Returns (token, notes). `notes` names what had to be removed, so the UI can say the
    paste was repaired instead of pretending nothing happened.
    """
    text = str(raw or "")
    notes = []

    # Strip invisible formatting characters anywhere in the string, not just the ends.
    stripped = "".join(c for c in text if unicodedata.category(c) != "Cf")
    if stripped != text:
        notes.append("caracteres invisibles")
        text = stripped

    # Non-breaking and other exotic spaces, plus ordinary whitespace and quoting.
    text = "".join(" " if (c.isspace() or c == " ") else c for c in text)
    text = text.strip().strip(_QUOTES).strip()

    m = TOKEN_RE.search(text)
    if not m:
        # A token contains no whitespace, so if we still cannot find one, the paste was
        # probably wrapped across lines by a narrow terminal or chat bubble. Closing the
        # gaps is safe precisely because no legitimate token has a space in it.
        squeezed = "".join(text.split())
        m = TOKEN_RE.search(squeezed)
        if not m:
            return "", notes
        notes.append("saltos de línea dentro del token")
        text = squeezed
    token = "%s:%s" % (m.group(1), m.group(2))
    # Anything around the token means they copied more than the token itself.
    if text != token and "texto de más alrededor del token" not in notes:
        notes.append("texto de más alrededor del token")
    return token, notes


def _looks_like_token(token):
    return bool(token) and token.isascii() and TOKEN_RE.fullmatch(token) is not None


def _call(token, method, params=None, timeout=25):
    """Call the Bot API. Never lets a malformed token reach urllib as an exception."""
    clean, _notes = clean_token(token)
    if not _looks_like_token(clean):
        return False, {"ok": False, "description": "malformed token (not sent)"}
    url = API.format(token=clean, method=method)
    ok, data, status = http_json(url, data=(params or {}), method="POST",
                                 timeout=timeout)
    if isinstance(data, dict) and "ok" in data:
        return data.get("ok", False), data
    return ok, {"ok": False, "description": str(data)[:300]}


def validate(token):
    """getMe -> confirm the token is real and return the bot's identity.

    Returns the CLEANED token so the caller stores what actually worked. Writing the raw
    paste into .env is how an install ends up permanently broken with a token that looks
    correct in an editor.
    """
    clean, notes = clean_token(token)
    if not clean:
        return {"ok": False, "token": "",
                "detail": "Eso no parece un token. Debe verse así: "
                          "123456789:AAG… (números, dos puntos, y una clave larga). "
                          "Pega solo esa línea, tal como te la dio BotFather."}

    ok, data = _call(clean, "getMe", timeout=15)
    if not ok:
        desc = (data.get("description") if isinstance(data, dict) else "") or ""
        if "401" in desc or "Unauthorized" in desc or "unauthorized" in desc.lower():
            detail = ("Telegram rechazó ese token. Suele pasar cuando generaste uno nuevo: "
                      "BotFather invalida el anterior. Pide /token otra vez y usa el último.")
        elif "404" in desc or "Not Found" in desc:
            detail = "Ese token no existe. Revisa que sea el del bot correcto."
        elif not desc or "codec" in desc or "control characters" in desc:
            # Never surface a Python encoding error to somebody setting up a chatbot.
            detail = ("No pudimos usar ese texto como token. Pega solo la línea del token, "
                      "sin comillas ni palabras alrededor.")
        else:
            detail = "Telegram no aceptó el token: %s" % desc[:120]
        return {"ok": False, "token": "", "detail": detail}

    r = data.get("result", {})
    detail = "Bot válido: @%s" % r.get("username", "?")
    if notes:
        detail += " (limpiamos %s del texto que pegaste)" % " y ".join(notes)
    return {"ok": True, "bot_id": r.get("id"),
            "username": r.get("username"), "name": r.get("first_name"),
            "token": clean, "cleaned": bool(notes), "notes": notes,
            "detail": detail}


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
