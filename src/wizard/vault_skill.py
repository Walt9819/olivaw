r"""Teach every agent that the owner's passwords exist, are usable, and are not askable.

The tool is only half the answer. Without this, an agent in front of a login wall does
what it has always done: it asks the owner for the password in the conversation - and
that one message is the whole leak. It goes to the model provider, into their logs, into
the transcript on disk, and into every later summary of that conversation.

So the skill says three things, in this order of importance:

  1. **Never ask for a password here.** Not once, not "just this time", and if the owner
     offers one anyway, say no and point her at the console. An agent that accepts a
     pasted password has already lost it.
  2. **You have keys, and here is how to spend them.** With the exact commands, because a
     skill that describes a capability without the invocation is a skill that gets
     half-remembered and then improvised around.
  3. **What a refusal means.** The guards refuse for good reasons - wrong site, wrong
     kind of box, too many tries - and each of those is a thing the agent should REPORT,
     not retry. A wrong-site refusal in particular is the phishing case, and it deserves
     to be told to the owner rather than worked around.

Installed per profile and rewritten when it changes, the same way as the others, with the
agent's own slug baked in: `--agent` is a confirmation and the tool takes the identity
from the workspace, so an agent that reads its own name here gets a clearer error on the
day it runs the command from somewhere else.
"""

import os

try:
    from . import agents_registry
except ImportError:  # running from src/ rather than as a package
    from wizard import agents_registry

SKILL_NAME = "usar-las-llaves"
SKILL_VERSION = "1.0.0"

SKILL = """---
name: %(name)s
description: "Entrar en una cuenta (Google, GitHub, la que sea) usando las llaves que la dueña guardó en este equipo, sin pedirle nunca la contraseña por el chat."
version: %(version)s
author: Olivaw
license: MIT
metadata:
  hermes:
    tags: [credenciales, acceso, navegador, seguridad]
---

# Las llaves que la dueña dejó para ti

Tu navegador se desloguea. Caduca la sesión de Google, un sitio te echa por la noche, y
te quedas delante de un formulario de acceso sin poder pasar.

Para eso hay llaves guardadas en este equipo. **Tú las usas; no las lees.** El valor va
del llavero del sistema al campo de la página, por dentro de esta máquina. No pasa por
esta conversación, ni por el proveedor del modelo, ni por ningún registro.

## Nunca pidas una contraseña por aquí

Esto es lo primero y no tiene excepciones:

* **No le pidas nunca una contraseña, un código ni un token a la dueña en el chat.**
* Si te la ofrece igualmente, **dile que no** y explica por qué: lo que se escribe aquí
  sale de este equipo y queda guardado. Dile que la añada en la consola, en
  **«Llaves y contraseñas»**, y que tú la usarás sin verla.
* Si ya te la mandó antes de que pudieras evitarlo: no la repitas, no la guardes en un
  archivo, no la escribas en tus notas. Díselo y pídele que la cambie.

## Cómo entrar en un sitio

```bash
python "%(tools)s/credentials.py" list
python "%(tools)s/credentials.py" fill google username
python "%(tools)s/credentials.py" fill google password --enter
python "%(tools)s/credentials.py" code github --enter
```

El orden normal: abre el sitio en tu navegador, mira la página, y cuando el formulario
esté delante escribe cada campo con `fill`. Tú decides cuándo y dónde; el dato lo pone
la herramienta. Si hay varios campos y se confunde, pásale `--selector` con el CSS del
que quieras.

`list` te dice qué llaves tienes, de qué cuenta son y qué guardan. Nunca te dice el
valor, y no hay ninguna orden que lo haga.

## Cuando se niega

Las negativas son información, no obstáculos que rodear:

* **«Esa llave es de X y la pestaña está en Y»** — la página donde estás no es el sitio
  de la llave. Casi siempre eso significa que algo te llevó a un sitio que imita al
  bueno. **Para, y díselo a la dueña.** No busques otra forma de escribirla.
* **«El campo no es de contraseña»** — ese formulario no es un acceso normal. Mismo
  trato: para y cuéntalo.
* **«Ya se usó N veces en la última hora»** — el acceso no está funcionando. Reintentar
  bloquea la cuenta. Cuenta lo que ves en la página y espera instrucciones.
* **«No tienes una llave para X»** — díselo a la dueña y que la añada en la consola.
  Eso sí puedes pedirlo: que la guarde, no que te la diga.

Cada uso queda anotado — qué llave, en qué página, cuándo, si salió bien — y la dueña
lo ve en la consola. Eso es lo que hace que esto sea razonable: no que prometas no
mirar, sino que no puedas, y que todo lo que uses quede escrito.
"""


def render_skill(install_dir=None):
    tools = os.path.join(install_dir or agents_registry.install_root(),
                         "src", "tools")
    return SKILL % {"name": SKILL_NAME, "version": SKILL_VERSION,
                    "tools": tools.replace("\\", "/")}


def skill_dir(profile=None, home=None):
    from . import reply_skill
    return os.path.join(os.path.dirname(reply_skill.skill_dir(profile, home)),
                        SKILL_NAME)


def install_skill(profile=None, home=None, log=None, install_dir=None):
    """Write it, unless the same text is already there."""
    d = skill_dir(profile, home)
    path = os.path.join(d, "SKILL.md")
    wanted = render_skill(install_dir)
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
        log("credentials: skill -> %s" % path)
    return {"ok": True, "changed": True, "path": path}


def ensure_all(profiles=None, log=None, install_dir=None):
    """Every agent, because any of them can end up in front of a login form."""
    out = []
    if profiles is None:
        profiles = sorted(agents_registry.known_profiles(install_dir) | {"default"})
    for prof in profiles:
        try:
            r = install_skill(prof, log=log, install_dir=install_dir)
        except Exception as e:  # noqa: BLE001
            r = {"ok": False, "changed": False, "detail": str(e)}
        r["profile"] = prof
        out.append(r)
    return out
