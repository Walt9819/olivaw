"""Wire the WhatsApp reliability kit into a live Hermes install.

Three things have to be true before the agent can be trusted with clients on WhatsApp:

  1. the bridge records delivery receipts        -> wa_patch.ensure()
  2. the agent can read those receipts back      -> whatsapp_delivery.py
  3. the agent can hand a conversation to a human -> tools/escalate_owner.py

(1) is a patch to Hermes' own file and is re-applied after every `hermes update`.
(2) and (3) are scripts in this repo; the agent learns about them through a SKILL.md
written into Hermes' skills directory with the real absolute paths baked in, because the
install location differs per machine and a skill with a wrong path is worse than none.

The skill is generated, not seeded: it is rewritten whenever what it should say differs
from what is on disk. That matters because its body lists the owner's own escalation
reasons, which she can change in the wizard at any time - a skill still promising
notifications for reasons she has since switched off would have the agent telling clients
a person was alerted when nobody was.
"""

import io
import json
import os
import re

from . import wa_patch

SKILL_NAME = "whatsapp-clientes"
SKILL_VERSION = "1.0.0"

_FRONTMATTER = """---
name: {name}
description: "Atención a clientes por WhatsApp: confirmar que un mensaje realmente se entregó, y avisar al dueño cuando una conversación necesita a una persona."
version: {version}
author: Olivaw
license: MIT
metadata:
  hermes:
    tags: [WhatsApp, clientes, entrega, escalamiento]
---
"""

_BODY = r"""
# WhatsApp con clientes

WhatsApp lo usan **los clientes**, no el dueño. Dos reglas, y las dos tienen script propio.

## 1. Nunca digas "ya lo mandé" sin comprobarlo

El puente responde `success: true` en cuanto entrega los bytes al socket. Eso **no** es
entrega. Después de enviar por WhatsApp, comprueba con el id que te devolvió el envío:

```bash
"{python}" "{verify}" --ids <MESSAGE_ID> --json
```

Códigos de salida: `0` salió de verdad · `1` no salió · `2` no se pudo comprobar.

Veredictos:

| veredicto | qué significa | ¿puedes decir que se envió? |
|---|---|---|
| `delivered` | el teléfono del cliente lo acusó | sí |
| `sent` | los servidores de WhatsApp lo tienen; el teléfono aún no responde | sí — llegará solo |
| `pending` | WhatsApp no ha acusado nada todavía | no, aún no |
| `unknown` | el puente nunca vio ese id: **no se envió** | no |
| `failed` | WhatsApp devolvió error | no |
| `unverifiable` | puente caído o sin el parche | no lo afirmes; dilo tal cual |

Si sale `unknown` o `failed`, **vuelve a enviar**. Si sale `unverifiable`, dilo con esas
palabras: no inventes que se entregó. Para enviar y comprobar en un paso:

```bash
"{python}" "{verify}" --chat <JID> --send "texto" --json
```

## 2. Cuando haga falta una persona, llama al script

No redactes tú el aviso ni elijas por dónde mandarlo. Hay un script fijo que se encarga
de escribirlo, mandarlo, reintentarlo y dejar constancia:

```bash
"{python}" "{escalate}"{home_arg} --reason <MOTIVO> \
  --summary "una línea de qué pasa" \
  --contact "+52..." --contact-name "Nombre" \
  --excerpt "lo que escribió el cliente, textual" --json
```

Códigos de salida: `0` la dueña ya lo tiene, confirmado · `3` quedó guardado y se
reintentará, **todavía no confirmado** · `4` quedó registrado pero ella desactivó ese
aviso · `2` mal uso.

### Cuándo llamarlo

Llámalo en cuanto ocurra, sin pedir permiso y sin esperar a terminar la conversación.
Esta lista es la que **ella misma configuró** en el asistente:

{reasons_block}

Ante la duda, escala. Un aviso de más cuesta diez segundos de su atención; uno de menos
cuesta un cliente.

### Qué decirle al cliente

Si el script salió con `0`, puedes decirle que ya avisaste a una persona. Si salió con
`3` o con `4`, dile solo que lo estás pasando a una persona — **no** prometas que ya
está avisada, porque con `3` aún no está confirmado y con `4` ella eligió no recibir
ese aviso.

Nunca inventes tiempos de respuesta que ella no te haya dado.

## Si el puente no confirma entregas

Comprueba el estado con:

```bash
"{python}" "{verify}" --health
```

Si dice `receipts=False`, el parche no está puesto (o `hermes update` lo quitó). Se
vuelve a poner con:

```bash
"{python}" "{patch}" ensure
```
"""


def _python():
    """The interpreter the skill should name.

    sys.executable is right about WHICH Python, but the supervisor runs under pythonw.exe,
    and a skill that told the agent to run `pythonw script.py` would get no stdout back at
    all - the console-less build discards it. Swap it for the console build sitting next to
    it, which is the same interpreter and the same site-packages.
    """
    import sys

    exe = sys.executable or "python"
    if os.path.basename(exe).lower() == "pythonw.exe":
        console = os.path.join(os.path.dirname(exe), "python.exe")
        if os.path.isfile(console):
            return console
    return exe


def skill_dir(hermes_home=None):
    home = hermes_home or _hermes_home()
    return os.path.join(home, "skills", SKILL_NAME)


def _hermes_home():
    env = os.environ.get("HERMES_HOME")
    if env:
        return env
    local = os.environ.get("LOCALAPPDATA")
    if local and os.path.isdir(os.path.join(local, "hermes")):
        return os.path.join(local, "hermes")
    return os.path.join(os.path.expanduser("~"), ".hermes")


def _repo_src():
    """This file lives at <repo>/src/wizard/wa_setup.py."""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _reasons_block(hermes_home=None):
    """Her actual reasons, in her own words, as a table the agent can act on.

    A reason the agent never reads about may as well not exist, so the wizard rewrites this
    every time she saves. Muted reasons are still listed - the agent should keep recognising
    the situation and keep it on the ledger - but flagged, so it never tells a client that a
    person was alerted when nobody was.
    """
    try:
        from . import escalation_prefs
        s = escalation_prefs.summary_for_skill(home=hermes_home)
    except Exception:  # noqa: BLE001
        return ("Usa `--list-reasons` para ver los motivos disponibles y su estado.")

    if not s["enabled"]:
        return ("> Ella ha **desactivado** los avisos por Telegram. Sigue llamando al script\n"
                "> cuando corresponda (queda registrado y ella puede revisarlo), pero saldrá\n"
                "> con `4` y **no** le llegará nada: no le digas al cliente que ya avisaste.")

    lines = []
    if s["active"]:
        lines.append("| motivo | cuándo usarlo | prioridad |")
        lines.append("|---|---|---|")
        for r in s["active"]:
            mark = " ⟵ suyo" if r["custom"] else ""
            lines.append("| `%s`%s | %s | %s |"
                         % (r["key"], mark, r["description"] or r["label"], r["priority"]))
    else:
        lines.append("> No hay ningún motivo activo: no le llegará ningún aviso.")

    if s["muted"]:
        keys = " · ".join("`%s`" % r["key"] for r in s["muted"])
        lines.append("")
        lines.append("Desactivados por ella: %s. Puedes llamarlos igual — queda registrado —"
                     % keys)
        lines.append("pero saldrá con `4` y **no** le llegará aviso.")

    if s["custom"]:
        lines.append("")
        lines.append("Los marcados «suyo» los definió ella; su descripción es literalmente")
        lines.append("lo que quiere que reconozcas. Respétala tal cual.")
    return "\n".join(lines)


def _home_arg(hermes_home=None):
    """Named profiles keep their own preferences, so the command must say which.

    A flag rather than an environment variable: the agent runs this through
    whatever shell it has, and `VAR=x cmd` is not valid on Windows.
    """
    if not hermes_home or os.path.abspath(hermes_home) == os.path.abspath(_hermes_home()):
        return ""
    return ' --home "%s"' % hermes_home


def render_skill(hermes_home=None):
    src = _repo_src()
    return (_FRONTMATTER.format(name=SKILL_NAME, version=SKILL_VERSION)
            + _BODY.format(
                python=_python(),
                verify=os.path.join(src, "whatsapp_delivery.py"),
                escalate=os.path.join(src, "tools", "escalate_owner.py"),
                patch=os.path.join(src, "wizard", "wa_patch.py"),
                reasons_block=_reasons_block(hermes_home),
                home_arg=_home_arg(hermes_home),
            ))


def _installed_version(path):
    try:
        with io.open(path, encoding="utf-8", errors="replace") as fh:
            head = fh.read(2000)
    except OSError:
        return None
    m = re.search(r"^version:\s*(\S+)", head, re.M)
    return m.group(1) if m else None


def install_skill(hermes_home=None, force=False, log=None):
    """Write the skill whenever what it should say differs from what is on disk.

    Compared by CONTENT rather than by version, because the body now depends on the owner's
    escalation preferences: she can change which reasons reach her without any version
    changing, and a skill listing reasons she has since turned off would have the agent
    promising notifications nobody gets. The file is generated - anything hand-written in it
    is replaced.
    """
    d = skill_dir(hermes_home)
    path = os.path.join(d, "SKILL.md")
    have = _installed_version(path)
    wanted = render_skill(hermes_home)
    if not force:
        try:
            with io.open(path, encoding="utf-8") as fh:
                if fh.read() == wanted:
                    return {"ok": True, "changed": False, "path": path, "version": have,
                            "detail": "La habilidad ya está al día."}
        except OSError:
            pass
    try:
        os.makedirs(d, exist_ok=True)
        with io.open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(wanted)
    except OSError as e:
        return {"ok": False, "changed": False, "path": path,
                "detail": "No se pudo escribir la habilidad: %s" % e}
    if log:
        log("wa_setup: skill %s v%s -> %s" % (SKILL_NAME, SKILL_VERSION, path))
    return {"ok": True, "changed": True, "path": path, "version": SKILL_VERSION,
            "previous": have, "detail": "Habilidad instalada."}


def profile_home(profile=None):
    if not profile or profile == "default":
        return _hermes_home()
    return os.path.join(_hermes_home(), "profiles", profile)


def _env_of(profile=None):
    out = {}
    try:
        with io.open(os.path.join(profile_home(profile), ".env"),
                     encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    out[k.strip()] = v.strip().strip('"').strip("'")
    except OSError:
        pass
    return out


def whatsapp_on(profile=None):
    """Is the WhatsApp FLAG set for this profile? Not the same as having a phone linked."""
    val = (_env_of(profile).get("WHATSAPP_ENABLED") or "").lower()
    return bool(val) and val not in ("0", "false", "no", "off")


def session_dirs(profile=None):
    """Both places Hermes may keep the paired session, newest layout first.

    Hermes resolves this with get_hermes_dir("platforms/whatsapp/session",
    "whatsapp/session") - the legacy path wins only when it already has content. Checking
    one of them is how a real install gets read as "never paired".
    """
    home = profile_home(profile)
    return [os.path.join(home, "platforms", "whatsapp", "session"),
            os.path.join(home, "whatsapp", "session")]


def whatsapp_linked(profile=None):
    """Is a phone ACTUALLY paired to this agent - or a Cloud number configured?

    The flag in .env says somebody ticked the box. It says nothing about whether the QR was
    ever scanned, and an agent that has been told how to handle WhatsApp clients while
    having no WhatsApp is worse than one that has not: it will offer to message people, talk
    about verifying deliveries, and reference a channel that does not exist. So the skill
    follows the SESSION, not the flag.

    Baileys writes creds.json as soon as the bridge starts, before anyone scans anything -
    so the file existing proves nothing. `registered` / `me.id` are what pairing sets.
    """
    env = _env_of(profile)
    # WhatsApp Cloud has no QR and no session directory: its credentials ARE the link.
    if (env.get("WHATSAPP_CLOUD_ACCESS_TOKEN") or "").strip() and \
            (env.get("WHATSAPP_CLOUD_PHONE_NUMBER_ID") or "").strip():
        return True
    for d in session_dirs(profile):
        try:
            with io.open(os.path.join(d, "creds.json"), encoding="utf-8") as fh:
                creds = json.load(fh)
        except (OSError, ValueError):
            continue
        if not isinstance(creds, dict):
            continue
        if creds.get("registered") is True:
            return True
        me = creds.get("me")
        if isinstance(me, dict) and (me.get("id") or "").strip():
            return True
    return False


def ensure(hermes_exe=None, hermes_home=None, log=None):
    """Idempotent, cheap, safe to call on every start. Returns a combined report."""
    patch = wa_patch.ensure(hermes_exe=hermes_exe, log=log)
    skill = install_skill(hermes_home, log=log)
    return {
        "ok": bool(patch.get("ok")) and bool(skill.get("ok")),
        "patch": patch,
        "skill": skill,
    }


def remove_skill(hermes_home=None, log=None):
    """Take the skill away from an agent that has no WhatsApp.

    Only ever removes a file this module generated - identified by its own frontmatter - so
    a hand-written skill that happens to share the name is left alone. The directory goes
    too when nothing else is in it.
    """
    d = skill_dir(hermes_home)
    path = os.path.join(d, "SKILL.md")
    try:
        with io.open(path, encoding="utf-8", errors="replace") as fh:
            head = fh.read(400)
    except OSError:
        return {"ok": True, "changed": False, "reason": "not-installed"}
    if "author: Olivaw" not in head or ("name: %s" % SKILL_NAME) not in head:
        return {"ok": True, "changed": False, "reason": "not-ours", "path": path}
    try:
        os.remove(path)
        if not os.listdir(d):
            os.rmdir(d)
    except OSError as e:
        return {"ok": False, "changed": False, "path": path, "detail": str(e)}
    if log:
        log("wa_setup: removed %s from %s (no WhatsApp linked)" % (SKILL_NAME, d))
    return {"ok": True, "changed": True, "removed": True, "path": path}


def ensure_all(agents=None, hermes_exe=None, log=None):
    """The receipt patch once, the client-handling skill in every agent that HAS WhatsApp.

    The patch is global by nature - it instruments Hermes' own Node bridge, of which there
    is one. The skill is not, and treating it as global was a real hole: `ensure()` only
    ever wrote into the DEFAULT profile's skills directory, so an extra agent - which is
    exactly the shape a customer-facing bot takes, its own profile, its own number, its own
    workspace - ran with no idea that it must verify a delivery before claiming one, and no
    idea how to reach a human. Checked on a live machine: the default profile had the
    skill, the extra agent's skills directory did not.

    The condition is a PAIRED SESSION, not the WHATSAPP_ENABLED flag. The flag only records
    that somebody ticked a box; the session records that a phone was actually linked. An
    agent carrying this skill with no WhatsApp behind it is actively harmful - it will offer
    to message people, talk about confirming deliveries, and reference a channel that does
    not exist, which reads to the owner as the agent malfunctioning. So a profile that loses
    its pairing loses the skill again, and a Telegram-only agent never had it.
    """
    out = []
    patch = wa_patch.ensure(hermes_exe=hermes_exe, log=log)
    profiles = [None] + [a.get("profile") or a.get("slug")
                         for a in (agents or []) if (a.get("profile") or a.get("slug"))]
    seen = set()
    for prof in profiles:
        key = prof or "default"
        if key in seen:
            continue
        seen.add(key)
        try:
            if whatsapp_linked(prof):
                r = install_skill(profile_home(prof), log=log)
            else:
                r = remove_skill(profile_home(prof), log=log)
                # WHY it has no skill is always the same answer; WHAT was found on disk
                # (nothing / ours, deleted / somebody else's, left alone) is the detail.
                r["removal"] = r.get("reason")
                r["reason"] = "no-whatsapp-linked"
        except Exception as e:  # noqa: BLE001
            r = {"ok": False, "changed": False, "detail": str(e)}
        r["profile"] = key
        r["linked"] = whatsapp_linked(prof)
        out.append(r)
    return {"patch": patch, "skills": out,
            "ok": bool(patch.get("ok")) and all(s.get("ok") for s in out)}


def status(hermes_exe=None, hermes_home=None):
    """Read-only: what an owner (or the SOS console) needs to see."""
    p = wa_patch.status(hermes_exe=hermes_exe)
    path = os.path.join(skill_dir(hermes_home), "SKILL.md")
    return {
        "bridge_patch": p["state"],
        "bridge_path": p.get("path", ""),
        "skill_installed": os.path.isfile(path),
        "skill_version": _installed_version(path),
        "skill_path": path,
    }


if __name__ == "__main__":  # pragma: no cover
    import json
    import sys

    cmd = (sys.argv[1] if len(sys.argv) > 1 else "status").lower()
    if cmd == "ensure":
        out = ensure(log=lambda m: print(m))
    elif cmd == "skill":
        out = install_skill(force=True, log=lambda m: print(m))
    else:
        out = status()
    print(json.dumps(out, indent=2, ensure_ascii=False))
    sys.exit(0 if out.get("ok", True) else 1)
