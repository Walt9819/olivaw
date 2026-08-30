"""Wire the WhatsApp reliability kit into a live Hermes install.

Three things have to be true before the agent can be trusted with clients on WhatsApp:

  1. the bridge records delivery receipts        -> wa_patch.ensure()
  2. the agent can read those receipts back      -> whatsapp_delivery.py
  3. the agent can hand a conversation to a human -> tools/escalate_owner.py

(1) is a patch to Hermes' own file and is re-applied after every `hermes update`.
(2) and (3) are scripts in this repo; the agent learns about them through a SKILL.md
written into Hermes' skills directory with the real absolute paths baked in, because the
install location differs per machine and a skill with a wrong path is worse than none.

The skill is rewritten whenever the paths or the template change, and is otherwise left
alone so an owner who edits it keeps their edits until the next version bump.
"""

import io
import os
import re

from . import wa_patch

SKILL_NAME = "whatsapp-clientes"
SKILL_VERSION = "1.0.0"

# Bumping SKILL_VERSION is what authorises overwriting an edited skill file.
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
{python} "{verify}" --ids <MESSAGE_ID> --json
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
{python} "{verify}" --chat <JID> --send "texto" --json
```

## 2. Cuando haga falta una persona, llama al script

No redactes tú el aviso ni elijas por dónde mandarlo. Hay un script fijo que se encarga
de escribirlo, mandarlo, reintentarlo y dejar constancia:

```bash
{python} "{escalate}" --reason <MOTIVO> \
  --summary "una línea de qué pasa" \
  --contact "+52..." --contact-name "Nombre" \
  --excerpt "lo que escribió el cliente, textual" --json
```

Motivos válidos (elige el más cercano; `--list-reasons` los imprime):

`angry` · `human_requested` · `complaint` · `legal` · `medical_urgent` ·
`payment_issue` · `refund` · `repeated` · `vip` · `data_request` ·
`agent_stuck` · `other` (este exige `--summary`)

Códigos de salida: `0` el dueño ya lo tiene, confirmado · `3` quedó guardado y se
reintentará, **todavía no confirmado** · `2` mal uso.

### Cuándo llamarlo

Llámalo en cuanto ocurra, sin pedir permiso y sin esperar a terminar la conversación:

- el cliente se molesta, reclama o sube el tono → `angry`
- pide hablar con una persona, un humano, el dueño o el doctor → `human_requested`
- menciona abogado, demanda, denuncia o Profeco → `legal`
- describe algo que suena a urgencia médica → `medical_urgent`
- reclama un cobro, un cargo o un pago que no cuadra → `payment_issue`
- pide reembolso o cancelar → `refund`
- ya escribió varias veces lo mismo sin solución → `repeated`
- **no sabes qué responder** → `agent_stuck` (esto no es fallar; es lo correcto)

Ante la duda, escala. Un aviso de más cuesta diez segundos de la atención del dueño; uno
de menos cuesta un cliente.

### Qué decirle al cliente

Si el script salió con `0`, puedes decirle que ya avisaste a una persona. Si salió con
`3`, dile solo que lo estás pasando a una persona — no prometas que ya está avisada,
porque todavía no está confirmado.

Nunca inventes tiempos de respuesta que el dueño no te haya dado.

## Si el puente no confirma entregas

Comprueba el estado con:

```bash
{python} "{verify}" --health
```

Si dice `receipts=False`, el parche no está puesto (o `hermes update` lo quitó). Se
vuelve a poner con:

```bash
{python} "{patch}" ensure
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


def render_skill():
    src = _repo_src()
    return (_FRONTMATTER.format(name=SKILL_NAME, version=SKILL_VERSION)
            + _BODY.format(
                python=_python(),
                verify=os.path.join(src, "whatsapp_delivery.py"),
                escalate=os.path.join(src, "tools", "escalate_owner.py"),
                patch=os.path.join(src, "wizard", "wa_patch.py"),
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
    """Write the skill, unless an equal-or-newer one is already installed."""
    d = skill_dir(hermes_home)
    path = os.path.join(d, "SKILL.md")
    have = _installed_version(path)
    if have == SKILL_VERSION and not force:
        return {"ok": True, "changed": False, "path": path, "version": have,
                "detail": "La habilidad ya está instalada."}
    try:
        os.makedirs(d, exist_ok=True)
        with io.open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(render_skill())
    except OSError as e:
        return {"ok": False, "changed": False, "path": path,
                "detail": "No se pudo escribir la habilidad: %s" % e}
    if log:
        log("wa_setup: skill %s v%s -> %s" % (SKILL_NAME, SKILL_VERSION, path))
    return {"ok": True, "changed": True, "path": path, "version": SKILL_VERSION,
            "previous": have, "detail": "Habilidad instalada."}


def ensure(hermes_exe=None, hermes_home=None, log=None):
    """Idempotent, cheap, safe to call on every start. Returns a combined report."""
    patch = wa_patch.ensure(hermes_exe=hermes_exe, log=log)
    skill = install_skill(hermes_home, log=log)
    return {
        "ok": bool(patch.get("ok")) and bool(skill.get("ok")),
        "patch": patch,
        "skill": skill,
    }


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
