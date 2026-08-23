r"""
Obsidian: make sure the long-term memory is a vault someone can actually open.

The agent writes its notes as plain markdown, so its memory works with or without Obsidian. But
"works" is not the point — the owner has to be able to read, correct and trust what the agent
remembers, and that means the folder must be a real vault, registered in Obsidian, not just a
directory full of .md files nobody ever opens.

That gap is easy to miss and this module exists because it happened here: Obsidian was installed
and had never been launched, so `%APPDATA%\obsidian` did not exist and the vault had no
`.obsidian/` folder. Everything looked fine from the agent's side.

Three things are checked separately, because they fail separately:

  * the app is installed (and if not, it can be installed with winget — on a button, never on its
    own: installing software is the owner's call);
  * the folder is registered in Obsidian's vault list, so it shows up when the app opens;
  * Obsidian has really opened it at least once — proven by `.obsidian/workspace.json`, which
    only Obsidian writes. Nothing else is evidence.

Everything written here is additive and reversible: a vault entry in obsidian.json (backed up
first), an empty `.obsidian/app.json`, and the agent's own folders.
"""

import io
import json
import os
import secrets
import subprocess
import threading
import time
import urllib.parse

from . import selfcare
from .procutil import IS_WIN, run, which

WINGET_ID = "Obsidian.Obsidian"

# The folders the routines write into - one definition, in selfcare, so the prompts and the
# checklist can never disagree about the layout.
AGENT_SUBDIRS = selfcare.MEMORY_SUBDIRS

_INDEX_SEED = """# 90-Agent — memoria de Olivaw

Esta carpeta es la memoria del agente, separada de tus carpetas temáticas.

- `journal/` — una entrada por mes: qué pasó cada día que valga la pena recordar.
- `memory/` — conocimiento duradero del agente: cómo trabajar contigo, criterios, mañas del entorno.
- `reviews/` — el repaso semanal: qué funcionó, qué no, qué cambió.
- `proposals/` — lo que el agente propone construir. Nada se construye sin tu sí.

## Notas

<!-- El agente añade aquí los enlaces a lo que escribe. -->
"""

_LEARNING_SEED = """# Aprendizaje sobre las propuestas

Lo que las respuestas del dueño me han enseñado. Lo leo ANTES de proponer algo nuevo.

## Reglas que ya aprendí

<!-- Una línea por regla, con la propuesta que la originó. -->

## No volver a proponer

<!-- Ideas rechazadas. No se reproponen, ni con otro nombre. -->
"""


def _exe_candidates():
    local = os.environ.get("LOCALAPPDATA", "")
    pf = os.environ.get("PROGRAMFILES", r"C:\Program Files")
    home = os.path.expanduser("~")
    if IS_WIN:
        return [os.path.join(local, "Programs", "obsidian", "Obsidian.exe"),
                os.path.join(local, "Obsidian", "Obsidian.exe"),
                os.path.join(pf, "Obsidian", "Obsidian.exe")]
    return ["/Applications/Obsidian.app/Contents/MacOS/Obsidian",
            os.path.join(home, "Applications", "Obsidian.app", "Contents", "MacOS", "Obsidian"),
            "/usr/bin/obsidian", "/snap/bin/obsidian"]


def exe_path():
    for p in _exe_candidates():
        if p and os.path.isfile(p):
            return p
    return which("obsidian") or ""


def config_dir():
    if IS_WIN:
        return os.path.join(os.environ.get("APPDATA", ""), "obsidian")
    home = os.path.expanduser("~")
    mac = os.path.join(home, "Library", "Application Support", "obsidian")
    return mac if os.path.isdir(mac) else os.path.join(home, ".config", "obsidian")


def _config_file():
    return os.path.join(config_dir(), "obsidian.json")


def _read_config():
    try:
        with io.open(_config_file(), encoding="utf-8", errors="replace") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def registered_vaults():
    vaults = (_read_config().get("vaults") or {})
    out = []
    if isinstance(vaults, dict):
        for vid, v in vaults.items():
            if isinstance(v, dict) and v.get("path"):
                out.append({"id": vid, "path": v.get("path"), "open": bool(v.get("open"))})
    return out


def _same_path(a, b):
    try:
        return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))
    except Exception:  # noqa: BLE001
        return False


def _count_notes(root, cap=6000):
    n = 0
    for dirpath, dirnames, files in os.walk(root):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        n += sum(1 for f in files if f.lower().endswith(".md"))
        if n >= cap:
            break
    return n


# ── installing (explicit, on a button) ──────────────────────────────────────
_JOB = {"state": "idle", "log": "", "started": 0.0}
_LOCK = threading.Lock()


def _install_worker(wg):
    cmd = [wg, "install", "--id", WINGET_ID, "-e", "--source", "winget",
           "--accept-package-agreements", "--accept-source-agreements", "--silent"]
    r = run(cmd, timeout=900)
    tail = ((r.get("out") or "") + "\n" + (r.get("err") or "")).strip()
    with _LOCK:
        _JOB["log"] = tail[-1200:]
        _JOB["state"] = "done" if exe_path() else "failed"


def install():
    """Install Obsidian with winget. Started only from an explicit action, and reported back by
    polling `status()` — a winget install takes minutes and must not hold an HTTP request open."""
    if exe_path():
        return {"ok": True, "already": True, "detail": "Obsidian ya está instalado."}
    with _LOCK:
        if _JOB["state"] == "running":
            return {"ok": True, "state": "running", "detail": "La instalación ya está en marcha."}
        wg = which("winget")
        if not wg:
            return {"ok": False, "detail": "Este equipo no tiene winget. Instálalo desde "
                                           "obsidian.md y vuelve a comprobar."}
        _JOB.update({"state": "running", "log": "", "started": time.time()})
    threading.Thread(target=_install_worker, args=(wg,), daemon=True).start()
    return {"ok": True, "state": "running",
            "detail": "Instalando Obsidian… puede tardar un par de minutos."}


def install_state():
    with _LOCK:
        st = dict(_JOB)
    if st["state"] == "running" and st["started"] and time.time() - st["started"] > 960:
        st["state"] = "failed"
        st["log"] = (st["log"] or "") + "\n(se agotó el tiempo de espera)"
    return st


# ── preparing the vault ─────────────────────────────────────────────────────
def prepare(vault=None):
    """Turn the notes folder into a vault Obsidian knows about, and give the agent its shelves.

    Additive only: nothing is deleted, obsidian.json is backed up before being touched, and the
    result says exactly what changed so it can be undone by hand."""
    ws = selfcare.workspace_dir()
    vault = vault or selfcare.vault_dir(ws) or os.path.join(ws, "vault")
    changed, problems = [], []

    try:
        if not os.path.isdir(vault):
            os.makedirs(vault, exist_ok=True)
            changed.append("Creé la carpeta del vault: %s" % vault)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "detail": "No pude crear el vault: %s" % e}

    # `.obsidian/app.json` marks the folder as a vault. alwaysUpdateLinks matters here: the agent
    # writes [[wikilinks]] constantly, and a renamed note must not silently break them.
    dot = os.path.join(vault, ".obsidian")
    try:
        os.makedirs(dot, exist_ok=True)
        app = os.path.join(dot, "app.json")
        if not os.path.isfile(app):
            with io.open(app, "w", encoding="utf-8", newline="") as fh:
                json.dump({"alwaysUpdateLinks": True}, fh, indent=2)
            changed.append("Marqué la carpeta como vault de Obsidian (.obsidian/app.json)")
    except Exception as e:  # noqa: BLE001
        problems.append("No pude crear .obsidian: %s" % e)

    # The agent's own shelves.
    mem = os.path.join(vault, selfcare.AGENT_DIR)
    try:
        for sub in AGENT_SUBDIRS:
            d = os.path.join(mem, sub)
            if not os.path.isdir(d):
                os.makedirs(d, exist_ok=True)
                changed.append("Creé %s/%s/" % (selfcare.AGENT_DIR, sub))
        idx = os.path.join(mem, "_Index.md")
        if not os.path.isfile(idx):
            with io.open(idx, "w", encoding="utf-8", newline="") as fh:
                fh.write(_INDEX_SEED)
            changed.append("Creé %s/_Index.md" % selfcare.AGENT_DIR)
        learn = os.path.join(mem, "proposals", "_Aprendizaje.md")
        if not os.path.isfile(learn):
            with io.open(learn, "w", encoding="utf-8", newline="") as fh:
                fh.write(_LEARNING_SEED)
            changed.append("Creé el registro de aprendizaje de las propuestas")
    except Exception as e:  # noqa: BLE001
        problems.append("No pude preparar %s: %s" % (selfcare.AGENT_DIR, e))

    # Register the vault so it appears when Obsidian opens.
    try:
        if not any(_same_path(v["path"], vault) for v in registered_vaults()):
            cfg_dir = config_dir()
            os.makedirs(cfg_dir, exist_ok=True)
            cfg = _read_config()
            path = _config_file()
            if os.path.isfile(path):
                try:
                    bak = path + ".olivaw.bak"
                    if not os.path.isfile(bak):
                        with io.open(path, encoding="utf-8", errors="replace") as src, \
                                io.open(bak, "w", encoding="utf-8", newline="") as dst:
                            dst.write(src.read())
                except Exception:  # noqa: BLE001
                    pass
            vaults = cfg.get("vaults")
            if not isinstance(vaults, dict):
                vaults = {}
            vaults[secrets.token_hex(8)] = {
                "path": os.path.abspath(vault),
                "ts": int(time.time() * 1000),
                # Only claim "open" when no other vault does, so we never steal the app's
                # startup vault from the owner.
                "open": not any(v.get("open") for v in vaults.values() if isinstance(v, dict)),
            }
            cfg["vaults"] = vaults
            with io.open(path, "w", encoding="utf-8", newline="") as fh:
                json.dump(cfg, fh, indent=2)
            changed.append("Registré el vault en Obsidian")
    except Exception as e:  # noqa: BLE001
        problems.append("No pude registrar el vault en Obsidian: %s" % e)

    st = status()
    return {"ok": not problems, "changed": changed, "problems": problems, "status": st}


def open_vault(vault=None):
    """Launch Obsidian on the vault. This is also the only way to prove it works: Obsidian
    writes `.obsidian/workspace.json` when it really opens a folder."""
    exe = exe_path()
    ws = selfcare.workspace_dir()
    vault = vault or selfcare.vault_dir(ws)
    if not exe:
        return {"ok": False, "detail": "Obsidian no está instalado en este equipo."}
    if not vault or not os.path.isdir(vault):
        return {"ok": False, "detail": "Todavía no hay vault que abrir."}
    uri = "obsidian://open?path=" + urllib.parse.quote(os.path.abspath(vault), safe="")
    try:
        kwargs = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL,
                  "stdin": subprocess.DEVNULL}
        if IS_WIN:
            kwargs["creationflags"] = 0x00000008     # DETACHED_PROCESS
        subprocess.Popen([exe, uri], **kwargs)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "detail": "No pude abrir Obsidian: %s" % e}
    marker = os.path.join(vault, ".obsidian", "workspace.json")
    t0 = time.time()
    while time.time() - t0 < 30:
        if os.path.isfile(marker):
            return {"ok": True, "opened": True,
                    "detail": "Obsidian abrió el vault. La memoria ya es consultable."}
        time.sleep(1)
    return {"ok": True, "opened": False,
            "detail": "Obsidian se está abriendo. Si te pide elegir carpeta, escoge el vault: "
                      + vault}


def status():
    ws = selfcare.workspace_dir()
    vault = selfcare.vault_dir(ws)
    exe = exe_path()
    dot = os.path.join(vault, ".obsidian") if vault else ""
    # Only Obsidian writes these; app.json alone proves nothing because we write that ourselves.
    opened = bool(dot and (os.path.isfile(os.path.join(dot, "workspace.json"))
                           or os.path.isfile(os.path.join(dot, "workspace"))))
    reg = [v for v in registered_vaults() if vault and _same_path(v["path"], vault)]
    mem = os.path.join(vault, selfcare.AGENT_DIR) if vault else ""
    out = {
        "ok": True,
        "installed": bool(exe), "exe": exe,
        "winget": bool(which("winget")),
        "config_dir": config_dir(), "config_exists": os.path.isfile(_config_file()),
        "workspace": ws, "vault": vault,
        "vault_exists": bool(vault and os.path.isdir(vault)),
        "is_vault": bool(dot and os.path.isdir(dot)),
        "registered": bool(reg), "opened": opened,
        "vaults_known": len(registered_vaults()),
        "notes": _count_notes(vault) if vault and os.path.isdir(vault) else 0,
        "agent_memory": mem,
        "agent_dirs": {s: bool(mem and os.path.isdir(os.path.join(mem, s)))
                       for s in AGENT_SUBDIRS} if mem else {},
        "install_job": install_state(),
    }
    steps = [
        {"key": "installed", "label": "Obsidian instalado", "ok": out["installed"]},
        {"key": "vault", "label": "Carpeta de la memoria creada", "ok": out["vault_exists"]},
        {"key": "registered", "label": "Vault registrado en Obsidian", "ok": out["registered"]},
        {"key": "opened", "label": "Abierto al menos una vez (comprobado)", "ok": out["opened"]},
    ]
    out["steps"] = steps
    out["healthy"] = all(s["ok"] for s in steps)
    missing = [s["label"] for s in steps if not s["ok"]]
    out["detail"] = ("La memoria larga está en Obsidian y es consultable."
                     if out["healthy"] else "Falta: " + ", ".join(missing))
    return out
