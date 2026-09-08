r"""How is this agent reachable, right now — one answer, for every surface.

The complaint this exists for: the owner could not see whether WhatsApp was connected. The
information existed, but scattered across four modules with four shapes, reachable only by
opening the setup flow, and each panel asked its own question its own way. So the UI showed
buttons and no state, and "is it working?" was answered by trying it.

This module is the single reader. Every panel that wants to say something about how an
agent is reached asks here, so two places cannot disagree about the same machine.

Two rules it follows
--------------------
* **Never collapse "not set up" into "broken".** Configured, listening, and answering are
  three different states with three different next actions - a gateway that has not
  restarted yet is not a failure, and telling the owner it is sends them to repair
  something that was about to work by itself.
* **Say where the answer came from.** A status the owner cannot trace is one they will
  re-check by hand anyway. Telegram's state is measured against Telegram; a WhatsApp
  number's against its own paired session; the brain's against the bridge that is running.

`fast=True` skips everything that leaves the machine, so a panel can paint immediately and
then refine. Nothing here mutates anything.
"""

import os

from . import hermes_ctl
from .procutil import which


def _safe(fn, default):
    try:
        return fn()
    except Exception:  # noqa: BLE001
        return default


def brain(profile=None, install_dir=None, port=None):
    """Which brain answers for this agent, and whether it can actually run here."""
    from . import rescue
    st = _safe(lambda: rescue.engine_status(install_dir, profile=profile, port=port), None)
    if st is None:
        return {"engine": "claude", "label": "Claude Code", "available": False,
                "source": "unknown", "detail": "No pude comprobar el cerebro."}
    return st


def bridge(port, install_dir=None):
    """The agent's own brain bridge: is it up, and on what code."""
    from .procutil import http_json
    if not port:
        return {"up": False, "detail": "Este agente no tiene puerto asignado."}
    ok, data, _ = http_json("http://127.0.0.1:%d/status" % int(port), timeout=4)
    if not ok or not isinstance(data, dict):
        ok2, _d, _s = http_json("http://127.0.0.1:%d/health" % int(port), timeout=3)
        return {"up": bool(ok2), "port": port,
                "detail": ("Responde, pero es una versión antigua." if ok2 else
                           "El puente de este agente no está corriendo.")}
    return {"up": True, "port": port, "version": data.get("version"),
            "engine": data.get("engine"), "code_sha": (data.get("code_sha") or "")[:10],
            "inflight": data.get("inflight"), "idle_seconds": data.get("idle_seconds"),
            "detail": "El puente está corriendo (v%s)." % data.get("version", "?")}


def telegram(profile=None, hermes=None, fast=False):
    """Telegram, measured against Telegram itself unless `fast`.

    `fast` reads only the profile's .env - enough to say "configured" or "not configured"
    without a round trip, so a panel can paint before the network answers.
    """
    from . import telegram_health
    prof = profile or "default"
    if fast:
        env = _safe(lambda: telegram_health._read_env(
            hermes_ctl.env_path(hermes, None if prof == "default" else prof)), {})
        tok = (env.get("TELEGRAM_BOT_TOKEN") or "").strip()
        owner = (env.get("TELEGRAM_ALLOWED_USERS") or "").strip()
        return {"state": "checking" if tok else "no_token",
                "has_token": bool(tok), "has_owner": bool(owner),
                "owner_locked": bool(owner), "ok": False,
                "detail": ("Comprobando con Telegram…" if tok else
                           "Este agente no tiene un bot de Telegram configurado.")}
    st = _safe(lambda: telegram_health.check(prof, hermes or which("hermes")), None)
    if st is None:
        return {"state": "unknown", "ok": False,
                "detail": "No pude comprobar Telegram."}
    st["owner_locked"] = bool(st.get("has_owner"))
    return st


def whatsapp(profile=None, hermes=None):
    """Every WhatsApp number this agent answers, and whether each one is really linked."""
    from . import numbers
    rows = _safe(lambda: numbers.listing(profile, hermes), [])
    linked = [r for r in rows if r.get("linked")]
    enabled = bool(rows and rows[0].get("enabled"))
    # The receipt patch is global to Hermes, but it decides whether ANY number can prove a
    # delivery, so it belongs in the WhatsApp panel rather than buried in a log.
    def _patch():
        from . import wa_patch
        return wa_patch.status(hermes_exe=hermes)
    patch = _safe(_patch, {})
    return {
        "enabled": enabled,
        "numbers": rows,
        "count": len(rows),
        "linked_count": len(linked),
        "max": getattr(numbers, "MAX_NUMBERS", 6),
        "can_add": len(rows) < getattr(numbers, "MAX_NUMBERS", 6),
        "main": next((r["slug"] for r in rows if r.get("main")), None),
        "receipts": (patch or {}).get("state", "unknown"),
        "detail": _wa_detail(enabled, rows, linked),
    }


def _wa_detail(enabled, rows, linked):
    if not enabled and not linked:
        return ("WhatsApp no está activado en este agente. Puedes conectar tu número y, "
                "si quieres, más de uno.")
    if not linked:
        return ("Está activado pero ningún número está vinculado todavía: falta escanear "
                "el código QR con el teléfono.")
    if len(linked) == len(rows):
        return ("%d número%s conectado%s." % (len(linked), "" if len(linked) == 1 else "s",
                                              "" if len(linked) == 1 else "s"))
    pending = [r["label"] for r in rows if not r.get("linked")]
    return ("%d de %d conectados. Falta vincular: %s."
            % (len(linked), len(rows), ", ".join(pending)))


def talk(profile=None, hermes=None, install_dir=None):
    from . import talk as talk_mod
    return _safe(lambda: talk_mod.status(profile, hermes, install_dir),
                 {"ok": False, "enabled": False, "ready": False,
                  "detail": "No pude comprobarlo."})


def gateway(profile=None, hermes=None):
    hp = hermes or which("hermes")
    if not hp:
        return {"running": None, "detail": "Hermes no está en el PATH."}
    st = _safe(lambda: hermes_ctl.gateway_status(hp,
                                                 profile=None if (profile in (None, "default"))
                                                 else profile), {})
    run = st.get("running")
    return {"running": run,
            "detail": ("El motor de este agente está corriendo." if run else
                       "El motor de este agente está detenido, así que no recibe mensajes."
                       if run is False else "No pude comprobar el motor.")}


def snapshot(profile=None, hermes=None, install_dir=None, port=None, fast=False):
    """Everything a "how is this agent connected" panel needs, in one call."""
    hp = hermes or which("hermes")
    out = {
        "ok": True,
        "profile": profile or "default",
        "fast": bool(fast),
        "gateway": gateway(profile, hp),
        "brain": brain(profile, install_dir, port),
        "telegram": telegram(profile, hp, fast=fast),
        "whatsapp": whatsapp(profile, hp),
        "talk": talk(profile, hp, install_dir),
    }
    if port:
        out["bridge"] = bridge(port, install_dir)
    out["channels"] = _headline(out)
    return out


def _headline(snap):
    """One line per channel, for a summary card: (icon, label, state, text).

    `state` is one of ok | warn | off | checking, so the UI colours it without re-deriving
    a verdict from the details - which is how two panels start disagreeing.
    """
    rows = []
    tg = snap.get("telegram") or {}
    tg_state = tg.get("state")
    rows.append(("✈️", "Telegram",
                 "ok" if tg.get("ok") else
                 "checking" if tg_state == "checking" else
                 "off" if tg_state == "no_token" else "warn",
                 tg.get("detail", "")))
    wa = snap.get("whatsapp") or {}
    rows.append(("💬", "WhatsApp",
                 "ok" if wa.get("linked_count") else
                 "off" if not wa.get("enabled") else "warn",
                 wa.get("detail", "")))
    tk = snap.get("talk") or {}
    rows.append(("🖥️", "Desde esta pantalla",
                 "ok" if tk.get("ready") else
                 "off" if not tk.get("enabled") else "warn",
                 tk.get("detail", "")))
    br = snap.get("brain") or {}
    rows.append(("🧠", br.get("label") or "Cerebro",
                 "ok" if br.get("available") else "warn",
                 br.get("detail", "")))
    return [{"icon": i, "label": l, "state": s, "detail": d} for i, l, s, d in rows]
