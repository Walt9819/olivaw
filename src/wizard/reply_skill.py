r"""Teach every agent that it can answer ONE message in particular.

The phone draws a reply as a reply - the quoted message in a strip above the answer, and
tapping it goes there. Both directions use the same shape, because there is no "in reply
to" field in the protocol and the quote IS the protocol. That cuts a particular way: the
app has understood an agent's replies since the day it could draw them, and no agent had
been told it could write one.

So this is the smallest kind of skill - a convention and three lines of example - and it
is installed the same way as the others, per profile, rewritten when it changes.

Two things it is careful about:

* **It does not ask for a reply every time.** An agent that quotes every answer turns a
  conversation into a stack of nested boxes. The skill says when it earns its place:
  several things were asked at once, or the answer arrives long after the question.
* **It is not about the phone.** A quote reads perfectly well on Telegram and in the
  console, and an agent cannot reliably tell which surface it is speaking through, so
  being told "do this on the phone" would be advice it cannot act on.
"""

import os

try:
    from . import agents_registry
except ImportError:  # running from src/ rather than as a package
    from wizard import agents_registry

SKILL_NAME = "responder-a-un-mensaje"
SKILL_VERSION = "1.0.0"

SKILL = """---
name: %(name)s
description: "Responder a UN mensaje en concreto cuando hay varios en juego: citarlo arriba de tu respuesta para que quede claro a cuál contestas."
version: %(version)s
author: Olivaw
license: MIT
metadata:
  hermes:
    tags: [conversacion, responder, citar]
---

# Puedes responder a un mensaje en concreto

Cuando contestas, tu respuesta va al final de la conversación y se entiende que es para
lo último que se dijo. Casi siempre eso basta. Pero a veces no: te preguntaron tres cosas
seguidas, o traes la respuesta de algo que se preguntó hace rato y entremedias se habló
de otra cosa.

En esos casos, **empieza tu respuesta citando el mensaje al que contestas**:

```
> **Walt:** ¿cuánto costó al final el servidor?

Ochenta y seis euros con IVA.
```

La primera línea es `>`, el nombre de quien lo dijo entre `**`, dos puntos, y lo que dijo
en una sola línea. Después una línea en blanco, y luego tu respuesta normal.

En el teléfono eso se dibuja como una respuesta de verdad: el mensaje citado aparece en
un recuadro encima del tuyo, y quien lo toca va a parar a ese mensaje en la conversación.
Es la misma forma que usa el dueño cuando te responde a ti, así que ya la has visto.

## Cuándo sí y cuándo no

**Sí** cuando hay más de una cosa en el aire y tu respuesta sería ambigua sin la cita, o
cuando contestas algo que quedó atrás.

**No** en una conversación normal de ida y vuelta. Si citas todo, cada mensaje acaba
siendo una caja dentro de otra caja y se lee peor que sin nada. La cita se gana su sitio;
no es el formato por defecto.

## Detalles

- **Una sola línea** para lo citado. Si el mensaje era largo, córtalo y termina en `…`:
  igual se encuentra el original.
- **El nombre** es el de quien escribió el mensaje que citas, tal y como aparece en la
  conversación. Si te citas a ti mismo, pon tu propio nombre.
- **Solo al principio.** Una cita en medio de tu respuesta es una cita normal, no una
  respuesta a un mensaje, y así se lee.
"""


def _home():
    return agents_registry._hermes_home()


def skill_dir(profile=None, home=None):
    root = home or _home()
    if profile and profile != "default":
        return os.path.join(root, "profiles", profile, "skills", SKILL_NAME)
    return os.path.join(root, "skills", SKILL_NAME)


def render_skill():
    return SKILL % {"name": SKILL_NAME, "version": SKILL_VERSION}


def install_skill(profile=None, home=None, log=None):
    """Write it, unless the same text is already there."""
    d = skill_dir(profile, home)
    path = os.path.join(d, "SKILL.md")
    wanted = render_skill()
    try:
        with open(path, encoding="utf-8") as fh:
            if fh.read() == wanted:
                return {"ok": True, "changed": False, "path": path}
    except OSError:
        pass
    try:
        os.makedirs(d, exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(wanted)
    except OSError as e:
        return {"ok": False, "changed": False, "path": path, "detail": str(e)}
    if log:
        log("replies: skill -> %s" % path)
    return {"ok": True, "changed": True, "path": path}


def ensure_all(profiles=None, log=None, install_dir=None):
    """Every agent on this machine, because any of them can be talked to from a phone."""
    out = []
    if profiles is None:
        # Registered agents plus whatever is on disk, and always the default one, which
        # has no registry row of its own.
        profiles = sorted(agents_registry.known_profiles(install_dir) | {"default"})
    for prof in profiles:
        try:
            r = install_skill(prof, log=log)
        except Exception as e:  # noqa: BLE001
            r = {"ok": False, "changed": False, "detail": str(e)}
        r["profile"] = prof
        out.append(r)
    return out
