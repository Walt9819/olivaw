r"""How long an agent's conversation lives before it is summarised or started over.

The problem this exists to fix
------------------------------
A Hermes conversation grows forever. Every turn resends the whole thread, so the cost of
turn N is proportional to everything said before it. Two mechanisms bound that, and Hermes
ships with BOTH effectively switched off for a new profile:

  * ``session_reset``  — start a fresh conversation after N minutes idle and/or at an hour
    of the day. Hermes' default is ``mode: none`` (changed from ``both`` in July 2026
    because it surprised CLI users). The conversation therefore never restarts.
  * ``compression``    — summarise the middle of the thread once it crosses
    ``threshold × context_length``. Hermes' default threshold is ``0.50``, and Olivaw
    advertises a 1M window, so the first summary happens at ~500k tokens.

Neither key is in ``DEFAULT_CONFIG``-shaped territory that a profile inherits from the
owner's main config: ``get_config_path()`` is ``$HERMES_HOME/config.yaml``, and for a named
profile HERMES_HOME *is* the profile directory. A profile config is merged with Hermes'
built-in defaults only — never with the main agent's config. So every agent Olivaw created
started life with "never restart, summarise at half a million tokens", regardless of what
the owner had configured for herself, and burned her quota in days.

What this module does
---------------------
Owns one policy, applies it through ``hermes config set`` (never by hand-editing YAML —
the schema moves between versions), and lets it be changed afterwards by the owner in the
wizard or by the agent itself through ``tools/conversation_policy.py``.

It writes the policy **once**, when the profile has none. A profile whose config.yaml
already carries a ``session_reset:`` block has been configured — by the owner, by the
agent, or by an earlier run of this code — and is left alone. "Turn restarts off" is a
legitimate choice and must survive the next supervisor start.

Reading is done by parsing the profile's raw config.yaml rather than shelling out to
``hermes config get`` nine times: on Windows that is nine process launches (~10s) for a
panel that has to feel instant. Only the two blocks below are read, and anything absent
falls back to the Hermes default recorded here.
"""

import io
import json
import os
import re
import time

from . import hermes_ctl

# ── Hermes' own numbers, mirrored so we can predict and explain its behaviour ──
# Kept here rather than imported: Hermes lives in its own venv and may be a different
# version. Every one of these is asserted against the running install by
# tools/test_context_policy.py, which fails loudly if Hermes moves them.
HERMES_DEFAULTS = {
    "mode": "none",          # session_reset.mode
    "idle_minutes": 1440,
    "at_hour": 4,
    "notify": True,
    "compact": True,         # compression.enabled
    "compact_at": 0.50,      # compression.threshold
    "keep_ratio": 0.20,      # compression.target_ratio
    "keep_last": 20,         # compression.protect_last_n
    "keep_first": 3,         # compression.protect_first_n
}

SMALL_WINDOW = 512_000       # under this, Hermes raises the trigger to 75% (raise-only)
SMALL_FLOOR = 0.75
MIN_TRIGGER_TOKENS = 64_000  # a trigger is never lower than this, whatever the percentage
MIN_CTX_RATIO = 0.85         # ...unless that floor would exceed the window itself

# ── the policy Olivaw gives every agent ───────────────────────────────────────
# 150 minutes and 10% are not arbitrary: they are what the owner's own main agent has been
# running on this machine, and the reason it survives a working day on a quota that a
# freshly created agent exhausts in an afternoon.
DEFAULTS = {
    "mode": "both",          # restart on whichever comes first: idle, or the daily hour
    "idle_minutes": 150,     # 2.5 hours with nobody talking
    "at_hour": 4,            # ...and a clean slate at 4am regardless
    "notify": True,          # tell the user, so a fresh start never looks like amnesia
    "compact": True,
    "compact_at": 0.10,      # summarise at 10% of the window -> ~100k tokens at 1M
    "keep_ratio": 0.20,
    "keep_last": 20,
    "keep_first": 3,
}

# olivaw name -> the dotted key `hermes config set` understands
KEYS = {
    "mode": "session_reset.mode",
    "idle_minutes": "session_reset.idle_minutes",
    "at_hour": "session_reset.at_hour",
    "notify": "session_reset.notify",
    "compact": "compression.enabled",
    "compact_at": "compression.threshold",
    "keep_ratio": "compression.target_ratio",
    "keep_last": "compression.protect_last_n",
    "keep_first": "compression.protect_first_n",
}

MODES = ("both", "idle", "daily", "none")

# Bounds. Deliberately wide — the owner is allowed to make choices we would not — but
# closed at both ends, because a typo like `idle_minutes: 1` restarts the conversation
# between two messages and looks exactly like the agent losing its mind.
LIMITS = {
    "idle_minutes": (15, 10080),     # 15 minutes .. 7 days
    "at_hour": (0, 23),
    "compact_at": (0.03, 0.90),
    "keep_ratio": (0.10, 0.80),
    "keep_last": (2, 200),
    "keep_first": (0, 50),
}

# Four ways to say it in the owner's language. `values` is a partial policy laid over
# DEFAULTS, so a preset only has to name what it changes.
PRESETS = [
    {"id": "ahorro", "label": "Ahorra al máximo",
     "note": "Empieza de cero tras 1½ h sin hablar y resume pronto. Lo más barato; "
             "tu agente recuerda menos de la charla del día.",
     "values": {"mode": "both", "idle_minutes": 90, "compact_at": 0.07}},
    {"id": "equilibrado", "label": "Equilibrado (recomendado)",
     "note": "Empieza de cero tras 2½ h sin hablar, y cada madrugada. Resume al llegar "
             "al 10% de su memoria. Es lo que usa el agente principal de esta máquina.",
     "values": {}},
    {"id": "memoria", "label": "Memoria larga",
     "note": "Aguanta 8 h de conversación seguida y resume más tarde. Útil si trabajas "
             "con él todo el día sobre lo mismo. Gasta bastante más.",
     "values": {"mode": "both", "idle_minutes": 480, "compact_at": 0.25}},
    {"id": "nunca", "label": "No cortar nunca",
     "note": "⚠️ La conversación no se reinicia sola. Sólo se resume cuando se llena. "
             "Es lo que más gasta, y es como venía Hermes de fábrica.",
     "values": {"mode": "none", "compact_at": 0.40}},
]


# ── validation ────────────────────────────────────────────────────────────────
def _num(value, lo, hi, cast):
    try:
        v = cast(value)
    except (TypeError, ValueError):
        return None, True
    clamped = max(lo, min(hi, v))
    return clamped, clamped != v


def normalize(policy):
    """Return (clean policy, notes). Never raises; anything unusable falls back.

    Notes are in the owner's language and are shown next to the panel, because silently
    correcting a number she typed is how a setting appears not to save.
    """
    src = dict(DEFAULTS)
    src.update({k: v for k, v in (policy or {}).items() if k in DEFAULTS})
    out, notes = {}, []

    mode = str(src.get("mode", "")).strip().lower()
    if mode not in MODES:
        notes.append("«%s» no es un modo válido; se usa «%s»." % (mode, DEFAULTS["mode"]))
        mode = DEFAULTS["mode"]
    out["mode"] = mode

    out["notify"] = bool(src.get("notify", True))
    out["compact"] = bool(src.get("compact", True))

    for key, cast, label in (("idle_minutes", int, "el tiempo sin hablar"),
                             ("at_hour", int, "la hora de reinicio"),
                             ("compact_at", float, "el punto de resumen"),
                             ("keep_ratio", float, "lo que se conserva"),
                             ("keep_last", int, "los mensajes recientes protegidos"),
                             ("keep_first", int, "los mensajes iniciales protegidos")):
        lo, hi = LIMITS[key]
        val, changed = _num(src.get(key), lo, hi, cast)
        if val is None:
            val, changed = DEFAULTS[key], True
            notes.append("No entendí %s; se deja en %s." % (label, DEFAULTS[key]))
        elif changed:
            notes.append("Ajusté %s al límite permitido (%s)." % (label, val))
        out[key] = val

    if out["mode"] == "none" and out["compact_at"] >= 0.5:
        notes.append("Sin reinicio y resumiendo tan tarde, el gasto sube mucho.")
    return out, notes


def preset_of(policy):
    """Which preset a policy corresponds to, or 'personalizado'."""
    for p in PRESETS:
        want, _ = normalize(dict(DEFAULTS, **p["values"]))
        if all(policy.get(k) == want[k] for k in ("mode", "idle_minutes", "compact_at")):
            return p["id"]
    return "personalizado"


def preset_policy(preset_id):
    for p in PRESETS:
        if p["id"] == preset_id:
            return normalize(dict(DEFAULTS, **p["values"]))[0]
    return normalize(DEFAULTS)[0]


# ── what the numbers actually mean, in tokens ─────────────────────────────────
def trigger_tokens(compact_at, context_length):
    """The conversation size at which Hermes will actually summarise.

    A percentage is not the whole story: Hermes raises the trigger to 75% for windows
    under 512K, never triggers below 64k tokens whatever the percentage says, and falls
    back to 85% of the window when that 64k floor would exceed it. The panel shows this
    number rather than the percentage, because "resume alrededor de 100.000 palabras-token"
    is a thing an owner can reason about and "0.10" is not.
    """
    try:
        ctx = int(context_length or 0)
    except (TypeError, ValueError):
        return None
    if ctx <= 0:
        return None
    pct = max(float(compact_at), SMALL_FLOOR) if ctx < SMALL_WINDOW else float(compact_at)
    floored = max(int(ctx * pct), MIN_TRIGGER_TOKENS)
    if floored >= ctx:
        return max(1, min(int(ctx * MIN_CTX_RATIO), ctx - 1))
    return floored


def describe(policy, context_length=None):
    """One sentence an owner can check against what she believes she asked for."""
    p, _ = normalize(policy)
    hrs = p["idle_minutes"] / 60.0
    when = ("%d min" % p["idle_minutes"]) if p["idle_minutes"] < 60 else (
        "%s h" % (("%.1f" % hrs).rstrip("0").rstrip(".")))
    if p["mode"] == "both":
        first = "Empieza una conversación nueva tras %s sin hablar, y cada día a las %02d:00" \
                % (when, p["at_hour"])
    elif p["mode"] == "idle":
        first = "Empieza una conversación nueva tras %s sin hablar" % when
    elif p["mode"] == "daily":
        first = "Empieza una conversación nueva cada día a las %02d:00" % p["at_hour"]
    else:
        first = "La conversación no se reinicia sola"
    if not p["compact"]:
        return first + ". No resume: la conversación crece sin límite."
    tok = trigger_tokens(p["compact_at"], context_length)
    size = ("unos %s tokens" % "{:,}".format(tok).replace(",", ".")) if tok \
        else "el %d%% de su memoria" % round(p["compact_at"] * 100)
    return "%s. Resume lo hablado al llegar a %s." % (first, size)


# ── reading the profile's own config.yaml ─────────────────────────────────────
_SCALAR = re.compile(r"^(\s+)([A-Za-z_][A-Za-z0-9_]*)\s*:\s*(.*?)\s*$")


def _strip_comment(raw):
    """Drop a trailing ` # comment` without touching a `#` inside quotes."""
    if raw[:1] in ("'", '"'):
        q = raw[0]
        end = raw.find(q, 1)
        return raw[1:end] if end > 0 else raw[1:]
    cut = raw.find(" #")
    return (raw[:cut] if cut >= 0 else raw).strip()


def _scalar(raw):
    raw = _strip_comment(raw)
    low = raw.lower()
    if low in ("true", "yes", "on"):
        return True
    if low in ("false", "no", "off"):
        return False
    if re.match(r"^-?\d+$", raw):
        return int(raw)
    if re.match(r"^-?\d*\.\d+$", raw):
        return float(raw)
    return raw


def read_block(text, name):
    """The immediate scalar children of a top-level `name:` mapping, or None if absent.

    A deliberately small reader for a deliberately small shape — Hermes writes these
    blocks itself with two-space indentation and scalar leaves. Anything more complex
    (a nested mapping, a list) is skipped rather than guessed at, and the caller falls
    back to the Hermes default for that key.
    """
    lines = text.splitlines()
    start = None
    for i, line in enumerate(lines):
        if re.match(r"^%s\s*:\s*(#.*)?$" % re.escape(name), line):
            start = i + 1
            break
    if start is None:
        return None
    out = {}
    for line in lines[start:]:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if not line[:1].isspace():
            break
        m = _SCALAR.match(line)
        if not m:
            continue
        _, key, raw = m.groups()
        if raw == "" or raw.startswith("#"):
            continue          # a nested mapping — not our shape, leave it to Hermes
        out[key] = _scalar(raw)
    return out


def config_file(hermes=None, profile=None):
    """Path to the profile's own config.yaml.

    Asks Hermes when it can (it knows about HERMES_HOME overrides and odd layouts) and
    derives it from the standard layout otherwise, so the panel still works on a machine
    where the CLI is momentarily missing.
    """
    try:
        p = hermes_ctl.config_path(hermes, profile)
        if p and os.path.isfile(p):
            return p
    except Exception:  # noqa: BLE001
        pass
    home = hermes_home()
    if profile and profile != "default":
        return os.path.join(home, "profiles", profile, "config.yaml")
    return os.path.join(home, "config.yaml")


def hermes_home():
    env = os.environ.get("HERMES_HOME")
    if env:
        return env
    local = os.environ.get("LOCALAPPDATA")
    if local and os.path.isdir(os.path.join(local, "hermes")):
        return os.path.join(local, "hermes")
    return os.path.join(os.path.expanduser("~"), ".hermes")


def _read_text(path):
    try:
        with io.open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return None


def read(hermes=None, profile=None, path=None):
    """The policy this profile is actually running under.

    Returns ``{policy, configured, preset, context_length, path, notes, describe}``.
    ``configured`` is False when the profile has no ``session_reset:`` block of its own —
    that is the signal ensure() uses, and the difference between "she chose never to
    restart" and "nobody has ever chosen".
    """
    path = path or config_file(hermes, profile)
    text = _read_text(path)
    sr = read_block(text, "session_reset") if text is not None else None
    comp = read_block(text, "compression") if text is not None else None
    model = read_block(text, "model") if text is not None else None

    raw = dict(HERMES_DEFAULTS)
    for src, mapping in ((sr, {"mode": "mode", "idle_minutes": "idle_minutes",
                               "at_hour": "at_hour", "notify": "notify"}),
                         (comp, {"compact": "enabled", "compact_at": "threshold",
                                 "keep_ratio": "target_ratio", "keep_last": "protect_last_n",
                                 "keep_first": "protect_first_n"})):
        if not src:
            continue
        for ours, theirs in mapping.items():
            if theirs in src:
                raw[ours] = src[theirs]

    policy, notes = normalize(raw)
    ctx = (model or {}).get("context_length")
    return {
        "ok": text is not None,
        "policy": policy,
        "configured": sr is not None,
        "preset": preset_of(policy),
        "context_length": ctx if isinstance(ctx, int) else None,
        "trigger_tokens": trigger_tokens(policy["compact_at"], ctx),
        "path": path,
        "notes": notes,
        "summary": describe(policy, ctx),
    }


# ── writing ───────────────────────────────────────────────────────────────────
def _fmt(value):
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return ("%.4f" % value).rstrip("0").rstrip(".") or "0"
    return str(value)


def apply(policy, hermes=None, profile=None, log=None):
    """Write the policy through Hermes' own CLI, skipping keys already at that value.

    Only what differs is written: a save from the panel is usually one or two
    ``hermes config set`` calls rather than nine, and on Windows each of those is a
    process launch the owner waits for.
    """
    want, notes = normalize(policy)
    state = read(hermes, profile)
    current = state["policy"]
    # A profile with no policy at all gets every key written, even the ones that happen to
    # equal a Hermes default today: HERMES_DEFAULTS above is our copy of their numbers, and
    # an explicit value is the only kind that survives them changing their mind. After that
    # first write, only real changes are sent — each one is a process launch on Windows.
    full = not state["configured"]
    steps, failed = [], []
    for name in ("mode", "idle_minutes", "at_hour", "notify",
                 "compact", "compact_at", "keep_ratio", "keep_last", "keep_first"):
        if not full and current.get(name) == want[name]:
            continue
        res = hermes_ctl.config_set(KEYS[name], _fmt(want[name]), hermes, profile)
        steps.append({"name": KEYS[name], "value": _fmt(want[name]),
                      "ok": bool(res.get("ok")), "detail": res.get("detail", "")})
        if not res.get("ok"):
            failed.append(KEYS[name])
    if log and steps:
        log("context policy (%s): %d key(s) written%s"
            % (profile or "default", len(steps),
               (", %d failed" % len(failed)) if failed else ""))
    return {"ok": not failed, "steps": steps, "policy": want, "notes": notes,
            "written": len(steps), "failed": failed,
            "summary": describe(want, state["context_length"]),
            "detail": ("No se pudo escribir: %s" % ", ".join(failed)) if failed
                      else "Listo. %s" % describe(want, state["context_length"])}


def activate(hermes=None, profile=None, log=None):
    """Make a freshly written policy take effect.

    The gateway reads ``session_reset`` once, into memory, when it starts: a live gateway
    goes on honouring whatever the file said at boot. Writing the keys and stopping there
    looks like success and changes nothing until the next restart — possibly days. So the
    write is followed by a restart of that profile's gateway, and only of one that is
    actually running.
    """
    st = hermes_ctl.gateway_status(hermes, profile)
    if not st.get("running"):
        return {"ok": True, "restarted": False,
                "detail": "El gateway no estaba corriendo; tomará la política al arrancar."}
    r = hermes_ctl.gateway_restart_safe(hermes, profile)
    if log:
        log("context policy (%s): gateway restart -> %s"
            % (profile or "default", "ok" if r.get("ok") else r.get("detail", "falló")))
    return {"ok": bool(r.get("ok")), "restarted": True, "detail": r.get("detail", "")}


# ── deferred activation ───────────────────────────────────────────────────────
# When the AGENT changes its own policy, it cannot restart its own gateway: that kills the
# turn it is in the middle of, so the owner sees the request vanish instead of an answer.
# It leaves a note here instead, and the supervisor performs the restart the next time that
# agent is idle - the same "wait for idle" rule the engine swap already follows.
MAX_ACTIVATION_TRIES = 3
ACTIVATION_RETRY = 300          # seconds between attempts, so a broken gateway is not hammered


def pending_path(home=None):
    return os.path.join(home or hermes_home(), "olivaw-context", "pending.json")


def _pending_load(home=None):
    try:
        with io.open(pending_path(home), encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _pending_save(data, home=None):
    path = pending_path(home)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with io.open(tmp, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(data, indent=2, ensure_ascii=False))
        os.replace(tmp, path)
        return True
    except OSError:
        return False


def mark_pending(profile=None, home=None):
    """Record that this profile's gateway must restart before its new policy is real."""
    data = _pending_load(home)
    key = profile or "default"
    entry = data.get(key) or {}
    entry.setdefault("since", time.time())
    entry["tries"] = 0
    entry["next"] = 0
    data[key] = entry
    _pending_save(data, home)
    return key


def pending(home=None):
    """Profiles waiting for a restart whose retry window has arrived."""
    now = time.time()
    return [k for k, v in _pending_load(home).items()
            if (v or {}).get("tries", 0) < MAX_ACTIVATION_TRIES and (v or {}).get("next", 0) <= now]


def clear_pending(profile=None, home=None):
    data = _pending_load(home)
    if data.pop(profile or "default", None) is None:
        return False
    _pending_save(data, home)
    return True


def note_activation_failure(profile=None, home=None):
    """Back off, and give up after a few tries rather than restarting on every tick."""
    data = _pending_load(home)
    key = profile or "default"
    entry = data.get(key) or {"since": time.time()}
    entry["tries"] = int(entry.get("tries", 0)) + 1
    entry["next"] = time.time() + ACTIVATION_RETRY
    data[key] = entry
    _pending_save(data, home)
    return entry["tries"]


def ensure(hermes=None, profile=None, log=None, restart=True):
    """Give a profile the default policy IF it has never had one.

    Idempotent and one-way: once a ``session_reset:`` block exists the profile is
    considered decided, whatever it says. Turning restarts off is a real choice and must
    not be undone by the next supervisor start.
    """
    state = read(hermes, profile)
    if not state["ok"]:
        return {"ok": False, "changed": False, "reason": "no-config",
                "detail": "No encontré el config.yaml del perfil (%s)." % state["path"]}
    if state["configured"]:
        return {"ok": True, "changed": False, "reason": "already-set",
                "policy": state["policy"], "summary": state["summary"]}
    res = apply(DEFAULTS, hermes, profile, log=log)
    res["changed"] = bool(res.get("written")) and res.get("ok")
    res["reason"] = "applied" if res["changed"] else "failed"
    if res["changed"]:
        if log:
            log("context policy (%s): %s" % (profile or "default", res["summary"]))
        if restart:
            res["activation"] = activate(hermes, profile, log=log)
    return res


def ensure_all(agents=None, hermes=None, log=None, restart=True):
    """Backfill every agent on this machine — the default one and each extra profile.

    Existing installs are the point: agents created before this feature have no policy at
    all, and their owners are the ones already paying for it. Runs once at supervisor
    start, does nothing on a profile that already has a policy, and never raises.
    """
    out = []
    if not hermes_ctl.available(hermes):
        return out
    profiles = [None] + [a.get("profile") or a.get("slug")
                         for a in (agents or []) if (a.get("profile") or a.get("slug"))]
    seen = set()
    for prof in profiles:
        key = prof or "default"
        if key in seen:
            continue
        seen.add(key)
        try:
            r = ensure(hermes, prof, log=log, restart=restart)
        except Exception as e:  # noqa: BLE001
            r = {"ok": False, "changed": False, "reason": "error", "detail": str(e)}
        # The skill goes in regardless of whether the policy needed writing: a profile
        # configured long ago still has an agent that does not know it may change this.
        try:
            r["skill"] = install_skill(prof, log=log)
        except Exception as e:  # noqa: BLE001
            r["skill"] = {"ok": False, "changed": False, "detail": str(e)}
        r["profile"] = key
        out.append(r)
    return out


# ── the skill that tells the agent this knob exists ───────────────────────────
# An agent that does not know it can change this will not change it, and the owner will
# never think to. The skill is generated rather than seeded because it carries absolute
# paths (the install location differs per machine) and the profile's own flag - a skill
# with the wrong profile in it would have one agent reconfiguring another.
SKILL_NAME = "duracion-de-la-conversacion"
SKILL_VERSION = "1.0.0"

_SKILL = u"""---
name: {name}
description: "Cada cuánto empieza de cero la conversación y cuándo se resume: consultarlo y cambiarlo cuando el gasto o la continuidad lo pidan."
version: {version}
author: Olivaw
license: MIT
metadata:
  hermes:
    tags: [contexto, tokens, gasto, conversación]
---

# Duración de la conversación (y lo que cuesta)

Cada mensaje que respondes arrastra **toda la conversación anterior**. Si nunca empieza de
cero, el turno número cien cuesta cien veces lo que el primero, y el saldo del dueño se
acaba en días sin que nadie entienda por qué.

Dos cosas lo acotan, y las dos son ajustables:

* **empezar de cero** — tras X minutos sin hablar, y/o a una hora fija de la madrugada;
* **resumir** — condensar lo hablado al llegar a cierto tamaño, y seguir desde ahí.

## Ver cómo estás ahora

```bash
"{python}" "{script}"{profile}
```

## Cambiarlo

```bash
"{python}" "{script}"{profile} --preset ahorro
"{python}" "{script}"{profile} --idle-minutes 240 --compact-at 0.15
"{python}" "{script}"{profile} --list-presets
```

Preajustes: `ahorro` · `equilibrado` (el de fábrica de Olivaw) · `memoria` · `nunca`.

## Cuándo cambiarlo tú

Propónselo al dueño y hazlo si dice que sí — o hazlo directamente si te lo pidió antes:

* **te quedas sin saldo antes de tiempo** → `--preset ahorro`, o baja `--compact-at`;
* **pierdes el hilo de algo que aún estabais haciendo** → sube `--idle-minutes`, o
  `--preset memoria`;
* **el dueño te habla de temas sueltos, sin relación entre sí** → `ahorro`: arrastrar la
  conversación anterior no le aporta nada y la paga igual;
* **estáis en algo largo y continuo todo el día** → `memoria`, y vuelve a bajarlo al acabar.

No lo cambies a espaldas del dueño más de una vez seguida, y dile siempre en qué queda:
«a partir de ahora empiezo de cero tras 4 h sin hablar».

## Lo que NO debes hacer

**No reinicies el gateway tú.** `hermes gateway restart` corta la conversación en curso:
la pregunta del dueño desaparece y nunca ve tu respuesta. El script ya deja aviso y el
supervisor de Olivaw reinicia solo, en cuanto quedas en reposo — normalmente en minutos.
Díselo así: «queda guardado; se activa en cuanto terminemos».

Si el script sale con `1` no se pudo escribir: dilo, no supongas que quedó hecho.
"""


def skill_dir(home=None, profile=None):
    base = home or profile_home(profile)
    return os.path.join(base, "skills", SKILL_NAME)


def profile_home(profile=None):
    """A named profile's HERMES_HOME. Its skills, config and state all live under it."""
    if not profile or profile == "default":
        return hermes_home()
    return os.path.join(hermes_home(), "profiles", profile)


def _python():
    """The interpreter to name in the skill.

    The supervisor runs under pythonw.exe, whose console-less build discards stdout - a
    skill telling the agent to run `pythonw script.py` would hand it back nothing at all.
    """
    import sys
    exe = sys.executable or "python"
    if os.path.basename(exe).lower() == "pythonw.exe":
        console = os.path.join(os.path.dirname(exe), "python.exe")
        if os.path.isfile(console):
            return console
    return exe


def render_skill(profile=None):
    src = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # <install>/src
    return _SKILL.format(
        name=SKILL_NAME, version=SKILL_VERSION, python=_python(),
        script=os.path.join(src, "tools", "conversation_policy.py"),
        profile=(' --profile %s' % profile) if (profile and profile != "default") else "")


def install_skill(profile=None, home=None, log=None):
    """Write the skill when its content differs from what is on disk. Generated, not seeded."""
    d = skill_dir(home, profile)
    path = os.path.join(d, "SKILL.md")
    wanted = render_skill(profile)
    try:
        with io.open(path, encoding="utf-8") as fh:
            if fh.read() == wanted:
                return {"ok": True, "changed": False, "path": path}
    except OSError:
        pass
    try:
        os.makedirs(d, exist_ok=True)
        with io.open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(wanted)
    except OSError as e:
        return {"ok": False, "changed": False, "path": path, "detail": str(e)}
    if log:
        log("context policy: skill -> %s" % path)
    return {"ok": True, "changed": True, "path": path}
