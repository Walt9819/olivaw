"""
Self-care: the agent's sleep and its weekly retrospective.

Two scheduled routines, modelled on something humans do and software usually does not:

  * NIGHTLY CONSOLIDATION ("sleep"), early morning while nothing else is running. The day's
    conversations are short-term memory: plenty of it, mostly noise, and it will be gone from
    working context tomorrow. The job re-reads the day, keeps only what will still be true or
    useful in a month, and writes that into the vault - linked into the notes that already
    exist, so it is reachable later without carrying it around all day.

  * WEEKLY RETROSPECTIVE, Sunday morning. The agent reads back its own week, separates what
    landed well from what the owner had to repeat, correct or chase, and turns the difference
    into concrete changes: its own working notes, a missing vault note, a skill worth having -
    or a proposal for the owner when the fix is not the agent's call.

Scheduling goes through Hermes' own cron (`hermes cron`), not a second scheduler: the owner
already has jobs there, the runs are durable, and each run lands in the session store like any
other conversation - so the retrospective can read its own history.

Everything the jobs may write is inside the workspace and the vault. They are told, explicitly,
never to touch the Olivaw installation or its code.
"""

import os
import re
import subprocess

from .procutil import run, which

_HERE = os.path.dirname(os.path.abspath(__file__))
INSTALL_DIR = os.path.dirname(os.path.dirname(_HERE))

# Job names are the identity we match on (Hermes cron has no tags), so they must stay stable.
DAILY_NAME = "Olivaw · consolidación nocturna"
WEEKLY_NAME = "Olivaw · repaso semanal"
DEFAULT_DAILY = "30 4 * * *"        # 04:30 every day - the quiet hour
DEFAULT_WEEKLY = "15 5 * * 0"       # Sunday 05:15, after the last nightly run of the week

# Where the agent's own memory lives inside the vault. Kept apart from the owner's topical
# folders on purpose: business knowledge belongs in 50-Company/20-Clients/..., while the
# journal and the retrospectives are the agent's own bookkeeping.
AGENT_DIR = "90-Agent"


def workspace_dir():
    """The agent's workspace (its cwd for tools), from the bridge config or the default."""
    try:
        import json
        with open(os.path.join(INSTALL_DIR, "updater.config.json"), encoding="utf-8") as fh:
            env = (json.load(fh) or {}).get("env") or {}
        ws = env.get("CLAUDE_BRIDGE_WORKSPACE")
        if ws and os.path.isdir(ws):
            return ws
    except Exception:  # noqa: BLE001
        pass
    guess = os.path.join(os.path.expanduser("~"), "hermes-workspace")
    return guess if os.path.isdir(guess) else os.path.expanduser("~")


def vault_dir(ws=None):
    """The Obsidian vault, if there is one. Without it the jobs still work — they just keep
    their notes in the workspace instead."""
    ws = ws or workspace_dir()
    for name in ("vault", "Vault", "notes"):
        p = os.path.join(ws, name)
        if os.path.isdir(p):
            return p
    return ""


def _tmp(ws):
    return os.path.join(ws, "tmp", "olivaw-selfcare")


def daily_prompt(ws=None, vault=None):
    ws = ws or workspace_dir()
    vault = vault if vault is not None else vault_dir(ws)
    mem = os.path.join(vault, AGENT_DIR) if vault else os.path.join(ws, "agent-memory")
    tmp = _tmp(ws)
    return f"""Es tu CONSOLIDACIÓN NOCTURNA (tu "sueño"): releer el día y quedarte con lo que valga la
pena recordar. Nadie espera respuesta: trabaja tranquilo, pero TERMINA. Más vale una nota corta
escrita que un análisis perfecto sin escribir.

REGLA DE ORO: primero lo barato, y con presupuesto. Máximo ~8 lecturas antes de empezar a escribir.
Para leer estos archivos usa SIEMPRE la terminal (head, sed, grep). NO uses search_files sobre la
carpeta de exportación: falla con archivos grandes.

1) MIRA EL DÍA (barato):
   hermes sessions list | head -25
   hermes sessions export --newer-than 26h --only user-prompts --format md --yes "{tmp}/prompts.md"
   sed -n '1,400p' "{tmp}/prompts.md"
   Con los títulos y lo que pidió el dueño ya sabes de qué se trató el día.
   SOLO si una conversación parece importante y no la entiendes con eso, exporta esa y lee un tramo:
   hermes sessions export --session-id <ID> --format md --yes "{tmp}/one"
   sed -n '1,250p' "{tmp}/one/"*.md
   No exportes el día completo: son megabytes y no te caben.

2) DECIDE QUÉ IMPORTA. Quédate solo con lo que seguirá siendo cierto o útil dentro de un mes:
   decisiones tomadas (y por qué), compromisos y fechas, hechos nuevos del negocio/clientes/producto,
   preferencias y criterios del dueño (cómo le gusta que se hagan las cosas), y los hilos abiertos.
   Descarta saludos, pruebas, tanteos y lo que ya quedó cerrado.

3) ESCRIBE EN LA MEMORIA LARGA — el vault: {vault or ws}
   a) SIEMPRE, aunque el día haya sido flojo: una entrada en {mem}/journal/AAAA-MM.md
      (encabezado con la fecha + 3-8 viñetas; si no hubo nada relevante, escríbelo así en una línea).
   b) Lo que sea conocimiento duradero, a la nota que le toca:
      - del negocio → la carpeta temática que ya existe (50-Company, 20-Clients, 30-*, 10-Meetings…),
        con el mismo estilo que las notas vecinas;
      - tuyo (cómo trabajar con este dueño, criterios, mañas del entorno) → {mem}/memory/<tema>.md.
      Antes de crear una nota nueva, comprueba con grep si ya existe una del tema: ACTUALIZAR es
      mejor que duplicar.
   c) Enlaza con [[wikilinks]] a las notas relacionadas y añade cada nota nueva al _Index.md que
      corresponda ({mem}/_Index.md para las tuyas). Una nota sin enlaces es una nota perdida.
   d) Máximo 5 notas por noche y ~40 líneas nuevas por nota. Hechos concretos, fechas y nombres —
      escribe para que TÚ lo entiendas de un vistazo dentro de tres meses.
   e) Nunca credenciales, tokens ni contraseñas en el vault.

4) LÍMITES: escribe solo dentro de {ws} (incluido el vault). No toques NUNCA la instalación de Olivaw
   ni su código. No borres notas: solo añadir o corregir. Al final borra "{tmp}".

5) AVISA AL DUEÑO — y esto llega a su canal principal (Telegram), en su teléfono. Escribe como un
   mensaje, no como un informe:
   - Solo lo RELEVANTE para él: lo que decidió y quedó guardado, los compromisos con fecha, y lo que
     necesita de su lado. Nada de rutas de archivos, ni "tarea completada", ni contabilidad interna.
   - Máximo 6 líneas cortas, sin encabezados ni tablas. Si hay algo que él debe hacer o confirmar,
     que sea la última línea y que se entienda de un vistazo.
   - Si la noche no dio nada que valga la pena: UNA sola línea diciéndolo (por ejemplo
     "🌙 Noche tranquila: nada nuevo que guardar."). No inventes contenido para tener algo que decir.
   - Si algo se te quedó a medias o falló, dilo en una línea: un aviso honesto vale más que un
     resumen limpio."""


def weekly_prompt(ws=None, vault=None):
    ws = ws or workspace_dir()
    vault = vault if vault is not None else vault_dir(ws)
    mem = os.path.join(vault, AGENT_DIR) if vault else os.path.join(ws, "agent-memory")
    tmp = _tmp(ws)
    return f"""Es tu REPASO SEMANAL: mirar tu propia semana y mejorar de verdad. Sé honesto contigo
mismo — un repaso donde todo salió bien no sirve para nada. Nadie espera respuesta inmediata, pero
TERMINA: escribe el repaso aunque la evidencia sea parcial.

REGLA DE ORO: primero lo barato, con presupuesto (~10 lecturas). Usa la terminal (grep, sed, head)
para leer los archivos grandes; NO uses search_files sobre la carpeta de exportación.

1) LEE LA SEMANA:
   - Empieza por lo que ya destilaste: {mem}/journal/ (las entradas de la semana) y
     {mem}/reviews/ (el repaso anterior: ¿cumpliste lo que te propusiste?).
   - Lo que pidió el dueño, en sus palabras:
     hermes sessions export --newer-than 7d --only user-prompts --format md --yes "{tmp}/week-prompts.md"
   - La señal de satisfacción, sin leerlo todo:
     grep -i -n "gracias\\|perfecto\\|justo eso\\|excelente\\|muy bien" "{tmp}/week-prompts.md" | head -30
     grep -i -n "no es\\|no era\\|otra vez\\|insisto\\|te dije\\|sigue mal\\|no funciona\\|de nuevo" "{tmp}/week-prompts.md" | head -30

2) EVALÚA CON EVIDENCIA, no de memoria. Cuenta los casos y cita el ejemplo concreto:
   - DÓNDE FUNCIONÓ: qué salió a la primera y con buena reacción.
   - DÓNDE NO: dónde el dueño tuvo que repetir, corregir, insistir o esperar; qué necesitó varios
     intentos; qué quedó a medias.
   Si una misma queja aparece dos veces o más, es un patrón, no un accidente.

3) CONVIERTE CADA PATRÓN EN UNA ACCIÓN, y hazla ahora si te toca:
   a) Es CÓMO trabajas → añade una o dos líneas concretas a tu {ws}/CLAUDE.md (aditivo; copia .bak
      antes de tocarlo).
   b) Te faltó CONOCIMIENTO → crea o completa la nota del vault que te habría hecho falta, enlazada.
   c) Es un FLUJO repetitivo → deja un script o una skill documentada para no improvisarlo cada vez.
   d) Necesita permiso, dinero, accesos o una decisión de negocio → NO lo hagas: anótalo como
      propuesta para el dueño.

4) LÍMITES: no toques el código ni la instalación de Olivaw. Los cambios a tu CLAUDE.md: pequeños,
   aditivos y reversibles. Al final borra "{tmp}".

5) ESCRIBE el repaso en {mem}/reviews/AAAA-Www.md con cuatro apartados: qué funcionó (con ejemplos),
   qué no (con ejemplos), qué cambié esta semana, qué propongo. Enlázalo desde {mem}/_Index.md.

6) AVISA AL DUEÑO — llega a su canal principal (Telegram), en su teléfono. Como mensaje, no como
   informe: 2-3 aciertos, 2-3 fallos (con el ejemplo real, en media línea), lo que YA cambiaste, y
   como máximo 2 preguntas o propuestas que necesiten su decisión — al final y numeradas, para que
   pueda contestar "1" o "2". Máximo 12 líneas cortas, sin encabezados ni tablas. El repaso completo
   queda en el vault; el mensaje es el resumen que se lee de pie."""


JOBS = {
    "daily": {"name": DAILY_NAME, "schedule": DEFAULT_DAILY, "prompt": daily_prompt,
              "label": "Consolidación nocturna (sueño)",
              "what": "Cada madrugada relee el día, se queda con lo que importa y lo guarda "
                      "enlazado en el vault."},
    "weekly": {"name": WEEKLY_NAME, "schedule": DEFAULT_WEEKLY, "prompt": weekly_prompt,
               "label": "Repaso semanal (mejora propia)",
               "what": "Cada domingo relee su semana, mide dónde acertó y dónde no, y aplica o "
                       "propone mejoras."},
}

_ID_RE = re.compile(r"^\s{2}([0-9a-f]{6,})\s+\[(\w+)\]", re.M)


def _parse_cron_list(text):
    """`hermes cron list` prints a box per job; pull out what we need to identify ours."""
    jobs = []
    blocks = re.split(r"\n(?=\s{2}[0-9a-f]{6,}\s+\[)", text or "")
    for b in blocks:
        m = _ID_RE.search(b)
        if not m:
            continue
        job = {"id": m.group(1), "state": m.group(2)}
        for key, field in (("Name", "name"), ("Schedule", "schedule"),
                           ("Next run", "next_run"), ("Last run", "last_run")):
            mm = re.search(r"%s:\s*(.+)" % re.escape(key), b)
            if mm:
                job[field] = mm.group(1).strip()
        jobs.append(job)
    return jobs


def list_jobs():
    hp = which("hermes")
    if not hp:
        return {"ok": False, "detail": "No encontré el comando hermes en este equipo."}
    r = run([hp, "cron", "list"], timeout=60)
    if r.get("code") not in (0, None) and not (r.get("out") or ""):
        return {"ok": False, "detail": (r.get("err") or "No pude leer los trabajos programados.")[:300]}
    return {"ok": True, "jobs": _parse_cron_list(r.get("out") or "")}


def status():
    ws = workspace_dir()
    vault = vault_dir(ws)
    out = {"ok": True, "workspace": ws, "vault": vault,
           "agent_memory": os.path.join(vault, AGENT_DIR) if vault else "",
           "hermes": bool(which("hermes")), "jobs": {}}
    listing = list_jobs()
    if not listing.get("ok"):
        out["ok"] = False
        out["detail"] = listing.get("detail")
        return out
    by_name = {(j.get("name") or ""): j for j in listing["jobs"]}
    for key, spec in JOBS.items():
        found = by_name.get(spec["name"])
        out["jobs"][key] = {
            "label": spec["label"], "what": spec["what"],
            "installed": bool(found), "default_schedule": spec["schedule"],
            **({"id": found["id"], "schedule": found.get("schedule", ""),
                "state": found.get("state", ""), "next_run": found.get("next_run", ""),
                "last_run": found.get("last_run", "")} if found else {}),
        }
    return out


def _remove_by_name(hp, name):
    listing = _parse_cron_list(run([hp, "cron", "list"], timeout=60).get("out") or "")
    removed = 0
    for j in listing:
        if (j.get("name") or "") == name:
            run([hp, "cron", "remove", j["id"]], timeout=60)
            removed += 1
    return removed


def _deliver_target(explicit=None):
    """Where the routine's closing summary goes. `origin` is right for jobs created from a chat;
    one created here has no origin, so prefer telegram when it is configured."""
    if explicit:
        return explicit
    local = os.environ.get("LOCALAPPDATA", "")
    home = os.path.expanduser("~")
    # Order matters only for speed; the first file that shows a Telegram setup wins. The first
    # entry is the one this actually lives in on Windows - missing it is why the routines were
    # created with `origin` and their summaries never left the cron session.
    env_paths = [os.path.join(local, "hermes", ".env"),
                 os.path.join(home, ".hermes", ".env"),
                 os.path.join(local, "hermes", "hermes-agent", ".env"),
                 os.path.join(home, ".hermes", "profiles", "default", ".env"),
                 os.path.join(local, "hermes", "profiles", "default", ".env")]
    for p in env_paths:
        try:
            with open(p, encoding="utf-8", errors="replace") as fh:
                body = fh.read()
            if re.search(r"^\s*TELEGRAM_(BOT_TOKEN|HOME_CHANNEL|ALLOWED_USERS)\s*=\s*\S",
                         body, re.M):
                return "telegram"
        except Exception:  # noqa: BLE001
            continue
    return "origin"


def install(keys=("daily", "weekly"), schedules=None, deliver=None):
    """Create (or re-create) the routines. Idempotent: an existing job with the same name is
    replaced, so pressing the button twice cannot leave duplicates."""
    hp = which("hermes")
    if not hp:
        return {"ok": False, "detail": "No encontré el comando hermes en este equipo."}
    ws = workspace_dir()
    vault = vault_dir(ws)
    target = _deliver_target(deliver)
    schedules = schedules or {}
    results = {}
    for key in keys:
        spec = JOBS.get(key)
        if not spec:
            continue
        sched = (schedules.get(key) or spec["schedule"]).strip()
        if not re.match(r"^[\d*/,\- ]+$", sched):
            results[key] = {"ok": False, "detail": "Horario inválido: %s" % sched}
            continue
        prompt = spec["prompt"](ws, vault)
        _remove_by_name(hp, spec["name"])
        cmd = [hp, "cron", "create", sched, prompt, "--name", spec["name"],
               "--deliver", target, "--workdir", ws]
        r = run(cmd, timeout=120)
        ok = r.get("code") in (0, None)
        if not ok and "deliver" in ((r.get("err") or "") + (r.get("out") or "")).lower():
            cmd[cmd.index("--deliver") + 1] = "origin"      # fall back if the target is unknown
            r = run(cmd, timeout=120)
            ok = r.get("code") in (0, None)
        results[key] = {"ok": ok, "schedule": sched, "deliver": target,
                        "detail": ((r.get("out") or "") + (r.get("err") or "")).strip()[:300]}
    st = status()
    return {"ok": all(v.get("ok") for v in results.values()) if results else False,
            "results": results, "status": st}


def run_now(key):
    """Ask Hermes to run the routine on the next scheduler tick (the 'test it' button)."""
    spec = JOBS.get(key)
    hp = which("hermes")
    if not spec or not hp:
        return {"ok": False, "detail": "Rutina desconocida o hermes no disponible."}
    listing = _parse_cron_list(run([hp, "cron", "list"], timeout=60).get("out") or "")
    job = next((j for j in listing if (j.get("name") or "") == spec["name"]), None)
    if not job:
        return {"ok": False, "detail": "Esa rutina todavía no está instalada."}
    # `hermes cron run` BLOCKS for the whole run (minutes), so fire and forget: the UI only
    # needs to know it was queued, and the routine reports to the owner's channel when done.
    try:
        kwargs = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL,
                  "stdin": subprocess.DEVNULL, "cwd": workspace_dir()}
        if os.name == "nt":
            kwargs["creationflags"] = 0x00000008 | 0x08000000   # DETACHED | NO_WINDOW
        subprocess.Popen([hp, "cron", "run", job["id"]], **kwargs)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "detail": "No pude lanzarla: %s" % e}
    return {"ok": True, "job_id": job["id"],
            "detail": "Encolada. Se ejecuta en el próximo tick y te avisa por tu canal al terminar."}


def remove(keys=("daily", "weekly")):
    hp = which("hermes")
    if not hp:
        return {"ok": False, "detail": "No encontré el comando hermes."}
    gone = {k: _remove_by_name(hp, JOBS[k]["name"]) for k in keys if k in JOBS}
    return {"ok": True, "removed": gone, "status": status()}


def preview(key):
    """The exact prompt that will run — worth being able to read before trusting it."""
    spec = JOBS.get(key)
    if not spec:
        return {"ok": False, "detail": "Rutina desconocida."}
    ws = workspace_dir()
    return {"ok": True, "name": spec["name"], "label": spec["label"],
            "schedule": spec["schedule"], "prompt": spec["prompt"](ws, vault_dir(ws))}
