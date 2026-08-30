"""What the owner wants to be told about, and what her own reasons mean.

The escalation script stays fixed at call time: the agent picks a key from a closed list and
controls nothing else. This module is the one place that list can change, and it changes only
when a person edits it in the wizard - never at the model's initiative.

Two things live here that the script deliberately does not do:

  * VALIDATION. A custom reason with no description is worthless - the description is the
    only thing that teaches the agent when to use it, so it is required, and required to be
    a sentence rather than a word.
  * THE SKILL REWRITE. Adding a reason the agent never hears about would be theatre, so
    saving preferences regenerates SKILL.md with the owner's actual reasons and her own
    wording in it.

Reading is done by tools/escalate_owner.py itself (stdlib only, no imports from here), so the
emergency path never depends on the wizard being importable.
"""

import contextlib
import io
import json
import os
import re
import sys
import time
import unicodedata

_TOOLS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools")
if _TOOLS not in sys.path:
    sys.path.insert(0, _TOOLS)

import escalate_owner as E  # noqa: E402


@contextlib.contextmanager
def _at(home):
    """Read and write the preferences of a specific profile, not whichever is default."""
    if not home:
        yield
        return
    prev = os.environ.get("OLIVAW_ESCALATION_HOME")
    os.environ["OLIVAW_ESCALATION_HOME"] = home
    try:
        yield
    finally:
        if prev is None:
            os.environ.pop("OLIVAW_ESCALATION_HOME", None)
        else:
            os.environ["OLIVAW_ESCALATION_HOME"] = prev


MAX_CUSTOM = 12
KEY_RE = re.compile(r"^[a-z][a-z0-9_]{1,30}$")
MIN_DESCRIPTION = 12
MAX_DESCRIPTION = 400


def _slug(text):
    """A CLI-safe key from a Spanish label: 'Pide cita urgente' -> 'pide_cita_urgente'."""
    norm = unicodedata.normalize("NFKD", str(text or ""))
    ascii_only = "".join(c for c in norm if not unicodedata.combining(c))
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", ascii_only).strip("_").lower()
    slug = re.sub(r"_{2,}", "_", slug)[:31]
    if slug and not slug[0].isalpha():
        slug = "r_" + slug
    return slug


def catalog():
    """Every built-in reason with its label, priority and what it means, for the UI."""
    hints = E.REASON_HINTS
    return [{"key": k, "label": E.REASONS[k][0], "priority": E.REASONS[k][1],
             "icon": E.REASONS[k][2], "description": hints.get(k, "")}
            for k in sorted(E.REASONS, key=lambda k: (E.REASONS[k][1] != "alta", k))]


def load(home=None):
    """Current preferences, in the shape the UI wants (never `None` for reasons)."""
    with _at(home):
        return _load_inner()


def _load_inner():
    p = E.load_prefs()
    configured = p["reasons"] is not None
    return {
        "enabled": p["enabled"],
        "configured": configured,
        # Not configured yet means everything is on - show it that way rather than
        # presenting an empty checklist that looks like "nothing will reach you".
        "reasons": p["reasons"] if configured else sorted(E.REASONS),
        "custom": p["custom"],
        "path": E.prefs_path(),
    }


def _clean_custom(raw, seen):
    """Validate one owner-defined reason. Returns (entry, error)."""
    label = re.sub(r"\s+", " ", str(raw.get("label") or "")).strip()
    desc = re.sub(r"\s+", " ", str(raw.get("description") or "")).strip()
    priority = raw.get("priority") if raw.get("priority") in ("alta", "media") else "media"
    key = str(raw.get("key") or "").strip().lower() or _slug(label)

    if not label:
        return None, "Cada motivo tuyo necesita un nombre corto."
    if len(label) > 60:
        return None, "El nombre «%s…» es demasiado largo (máx. 60)." % label[:20]
    if not desc:
        return None, ("Describe cuándo debe usarse «%s». Sin esa descripción el agente "
                      "no sabe reconocerlo." % label)
    if len(desc) < MIN_DESCRIPTION:
        return None, ("La descripción de «%s» es demasiado corta. Explícalo como se lo "
                      "explicarías a alguien nuevo." % label)
    if len(desc) > MAX_DESCRIPTION:
        desc = desc[:MAX_DESCRIPTION].rstrip() + "…"
    if not KEY_RE.match(key):
        return None, "No pude generar un identificador válido para «%s»." % label
    if key in E.REASONS:
        return None, ("«%s» choca con un motivo que ya viene incluido. Usa otro nombre."
                      % label)
    if key in seen:
        return None, "Tienes dos motivos con el mismo nombre («%s»)." % label
    seen.add(key)
    return {"key": key, "label": label, "priority": priority, "description": desc}, None


def save(enabled=True, reasons=None, custom=None, regenerate_skill=True, log=None,
         home=None):
    """Persist the owner's choices and teach the agent about them.

    `reasons` is the list of keys she wants to hear about - built-in or her own. An empty
    list with `enabled` true is accepted but called out, because it means nothing will ever
    reach her, which is almost never what somebody intends.
    """
    with _at(home):
        return _save_inner(enabled, reasons, custom, regenerate_skill, log, home)


def _save_inner(enabled, reasons, custom, regenerate_skill, log, home):
    seen = set()
    clean_custom = []
    auto_on = []
    for raw in (custom or []):
        if not isinstance(raw, dict):
            continue
        entry, err = _clean_custom(raw, seen)
        if err:
            return {"ok": False, "detail": err}
        clean_custom.append(entry)
        # The key is derived from the label here, so the browser cannot have sent it in
        # `reasons` for a reason she just typed. She added it because she wants to hear
        # about it, so it is on unless she explicitly unticks it.
        if raw.get("selected", True):
            auto_on.append(entry["key"])
        if len(clean_custom) > MAX_CUSTOM:
            return {"ok": False,
                    "detail": "Máximo %d motivos propios; agrupa los parecidos." % MAX_CUSTOM}

    known = set(E.REASONS) | {c["key"] for c in clean_custom}
    picked = [r for r in (reasons or []) if r in known]
    for key in auto_on:
        if key not in picked:
            picked.append(key)

    data = {
        "enabled": bool(enabled),
        "reasons": picked,
        "custom": clean_custom,
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }

    path = E.prefs_path()
    tmp = path + ".tmp"
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with io.open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except OSError as e:
        return {"ok": False, "detail": "No se pudieron guardar las preferencias: %s" % e}

    skill = None
    if regenerate_skill:
        try:
            from . import wa_setup
            skill = wa_setup.install_skill(hermes_home=home, force=True, log=log)
        except Exception as e:  # noqa: BLE001
            skill = {"ok": False, "detail": str(e)[:200]}

    if not data["enabled"]:
        detail = "Guardado. No recibirás avisos por Telegram."
    elif not picked:
        detail = ("Guardado, pero no seleccionaste ningún motivo: así no te llegará "
                  "ningún aviso. Marca al menos los importantes.")
    else:
        own = len(clean_custom)
        detail = "Guardado. Te avisaremos por %d motivo(s)%s." % (
            len(picked), (", %d tuyo(s)" % own) if own else "")

    return {"ok": True, "detail": detail, "saved": data,
            "skill_updated": bool(skill and skill.get("ok")),
            "warning": (data["enabled"] and not picked)}


def summary_for_skill(home=None):
    """The lines the SKILL.md needs: what is on, and what the owner's own reasons mean."""
    with _at(home):
        return _summary_inner()


def _summary_inner():
    prefs = E.load_prefs()
    table = E.effective_reasons(prefs)
    hints = E.reason_hints(prefs)
    active, muted, own = [], [], []
    for key in sorted(table, key=lambda k: (table[k][1] != "alta", k)):
        label, priority, _icon = table[key]
        row = {"key": key, "label": label, "priority": priority,
               "description": hints.get(key, ""), "custom": key not in E.REASONS}
        (muted if E.is_muted(key, prefs) else active).append(row)
        if row["custom"]:
            own.append(row)
    return {"enabled": prefs.get("enabled", True), "active": active,
            "muted": muted, "custom": own}
