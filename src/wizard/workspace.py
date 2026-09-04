"""Where the agent works - and why that is worth one question.

Until now this was a text box called "Carpeta de trabajo del agente", buried in an
"advanced options" fold on the Telegram step. That is the wrong place for it twice over: an
owner who does not know what a working directory is will never open that fold, and the one
who does know will not think to look for it under Telegram.

It matters more than its old placement suggested. This is the agent's WORKING directory -
the folder its brain opens by default, where it reads and writes the files a task involves:
what the owner asks it to produce, what it downloads, what it organises. It is not where
the agent's own configuration or credentials live; Hermes keeps those under its own home,
and nothing here moves them.

That distinction is the point of asking. The working directory is the owner's territory:
it is what she opens to see the agent's output, what she backs up, and what she may want
pointed at material she already has. Which makes these worth saying before she commits to a
path, rather than after something has gone wrong:

  * a cloud-synced folder (OneDrive, Dropbox, Drive, iCloud) will produce conflicted copies
    when the agent and the owner touch the same file, which is what those clients handle
    worst;
  * a path inside Olivaw's own install directory is destroyed by the next update;
  * a network or removable drive disappears mid-task;
  * a folder that already has files in it will end up mixed with the agent's.

None of these are refusals. They are told to the owner in plain language, and she decides.
"""

import os
import shutil
import subprocess
import sys
from winspawn import quiet

IS_WIN = sys.platform.startswith("win")
IS_MAC = sys.platform == "darwin"

# Folder names cloud clients use. Matched case-insensitively against path parts, so
# "C:/Users/x/OneDrive - Acme/agent" is caught as well as a plain "OneDrive".
_SYNC_HINTS = ("onedrive", "dropbox", "google drive", "googledrive", "gdrive",
               "icloud", "icloudrive", "creative cloud", "mega", "pcloud", "sync.com",
               "yandexdisk", "nextcloud", "owncloud", "box sync", "tresorit")


def _home():
    return os.path.expanduser("~")


def suggest(agent_name="", install_dir=""):
    """A sensible default, named after the agent so two agents never share a home.

    With one exception that matters more than the naming: if an agent is ALREADY working
    somewhere on this machine, suggest that. Re-running the wizard on an existing install
    must not quietly repoint it at an empty folder - the old files would still be on disk,
    but the agent would no longer be opening the directory that holds them.
    """
    existing = legacy_default()
    if _looks_like_workspace(existing):
        return existing

    slug = "".join(c for c in (agent_name or "").strip().lower()
                   if c.isalnum() or c in " -_").strip().replace(" ", "-")
    base = os.path.join(_home(), "Documents") if os.path.isdir(
        os.path.join(_home(), "Documents")) else _home()
    name = ("%s-workspace" % slug) if slug else "hermes-workspace"
    return os.path.join(base, name)


def legacy_default():
    """Where agents created before this question existed keep their files."""
    return os.environ.get("CLAUDE_BRIDGE_WORKSPACE",
                          os.path.join(_home(), "hermes-workspace"))


def _free_bytes(path):
    probe = path
    while probe and not os.path.isdir(probe):
        parent = os.path.dirname(probe)
        if parent == probe:
            return None
        probe = parent
    try:
        return shutil.disk_usage(probe).free
    except Exception:  # noqa: BLE001
        return None


def _human(n):
    if n is None:
        return ""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return "%.0f %s" % (n, unit)
        n /= 1024.0
    return ""


def _writable(path):
    """Can we actually create things here? Tested by doing it, not by reading permissions."""
    probe = path
    while probe and not os.path.isdir(probe):
        parent = os.path.dirname(probe)
        if parent == probe:
            return False
        probe = parent
    test = os.path.join(probe, ".olivaw-write-test")
    try:
        with open(test, "w", encoding="utf-8") as fh:
            fh.write("x")
        os.remove(test)
        return True
    except OSError:
        return False


def inspect(path, install_dir=""):
    """Everything the owner should know about this folder before choosing it.

    Warnings are advisory: `ok` stays true unless the path genuinely cannot be used.
    """
    raw = (path or "").strip().strip('"')
    if not raw:
        return {"ok": False, "path": "", "detail": "Elige una carpeta o usa la recomendada."}

    resolved = os.path.abspath(os.path.expandvars(os.path.expanduser(raw)))
    exists = os.path.isdir(resolved)
    warnings = []
    blocking = None

    if os.path.isfile(resolved):
        blocking = "Eso es un archivo, no una carpeta."

    parts = [p.lower() for p in resolved.replace("\\", "/").split("/") if p]
    synced = next((h for h in _SYNC_HINTS if any(h in p for p in parts)), None)
    if synced:
        warnings.append(
            "Está dentro de una carpeta que se sincroniza en la nube. Tus archivos se "
            "respaldan solos, pero si el agente y tú editáis el mismo a la vez pueden "
            "aparecer copias en conflicto. Funciona; solo tenlo en cuenta.")

    if install_dir:
        inst = os.path.abspath(install_dir)
        try:
            inside = os.path.commonpath([resolved, inst]) == inst
        except ValueError:      # different drives
            inside = False
        if inside:
            blocking = ("Esa carpeta está dentro de la instalación de Olivaw y se "
                        "sobrescribe en cada actualización. Elige otra.")

    drive = os.path.splitdrive(resolved)[0]
    if resolved.startswith("\\\\") or (IS_WIN and drive and drive.upper() not in ("C:",)):
        warnings.append(
            "Parece una unidad de red o externa. Si se desconecta, el agente pierde "
            "acceso a sus archivos a mitad de una tarea.")

    parent = resolved if exists else os.path.dirname(resolved)
    if not os.path.isdir(parent):
        blocking = "La carpeta que la contiene no existe todavía."
    elif not _writable(resolved):
        blocking = "No hay permiso para escribir ahí."

    entries = []
    if exists:
        try:
            entries = os.listdir(resolved)
        except OSError:
            entries = []
        if entries and not _looks_like_workspace(resolved):
            warnings.append(
                "La carpeta ya tiene %d elemento(s). El agente añadirá los suyos junto a "
                "ellos; nada se borra, pero se mezclarán." % len(entries))

    free = _free_bytes(resolved)
    if free is not None and free < 2 * 1024 ** 3:
        warnings.append("Queda poco espacio en el disco (%s libres)." % _human(free))

    return {
        "ok": blocking is None,
        "path": resolved,
        "exists": exists,
        "reused": exists and _looks_like_workspace(resolved),
        "entries": len(entries),
        "free": _human(free),
        "warnings": warnings,
        "detail": blocking or "",
    }


def _looks_like_workspace(path):
    """Signs an agent already works here, rather than this being unrelated material.

    CLAUDE.md / AGENTS.md are the standing-instruction files a brain reads from its working
    directory; `vault` is the note store owners commonly keep alongside. Any of them means
    "an agent has been here", which changes the advice from "these files will get mixed"
    to "it will carry on with what is already there".
    """
    marks = ("CLAUDE.md", "vault", "AGENTS.md")
    return any(os.path.exists(os.path.join(path, m)) for m in marks)


def create(path, install_dir=""):
    """Make the folder, after checking it. Returns the same shape as inspect()."""
    info = inspect(path, install_dir)
    if not info["ok"]:
        return info
    try:
        os.makedirs(info["path"], exist_ok=True)
    except OSError as e:
        return dict(info, ok=False, detail="No se pudo crear la carpeta: %s" % e)
    info["exists"] = True
    return info


# ── the native folder picker ─────────────────────────────────────────────────
# Typing a path is a fine fallback but a poor default: an owner who does not know what a
# working directory is also does not know how to write one. The wizard server runs on the
# owner's own machine, so it can open the real folder chooser.

_PS_PICKER = r"""
Add-Type -AssemblyName System.Windows.Forms | Out-Null
$d = New-Object System.Windows.Forms.FolderBrowserDialog
$d.Description = "Elige la carpeta donde vivira tu agente"
$d.ShowNewFolderButton = $true
if (Test-Path -LiteralPath $env:OLIVAW_PICK_START) { $d.SelectedPath = $env:OLIVAW_PICK_START }
$owner = New-Object System.Windows.Forms.Form
$owner.TopMost = $true
if ($d.ShowDialog($owner) -eq [System.Windows.Forms.DialogResult]::OK) { Write-Output $d.SelectedPath }
$owner.Dispose()
"""


def picker_available():
    if IS_WIN:
        return bool(shutil.which("powershell") or shutil.which("powershell.exe"))
    if IS_MAC:
        return bool(shutil.which("osascript"))
    return bool(shutil.which("zenity") or shutil.which("kdialog"))


def pick(start="", timeout=180):
    """Open the OS folder chooser and return what was selected.

    Cancelling is a normal outcome, not an error - it returns ok:false with no detail so the
    UI simply keeps whatever was already there.
    """
    start = os.path.abspath(os.path.expanduser(start)) if start else _home()
    try:
        if IS_WIN:
            exe = shutil.which("powershell") or "powershell.exe"
            env = dict(os.environ, OLIVAW_PICK_START=start)
            # quiet() suppresses the powershell console window; the picker itself is a
            # WPF dialog created by the script, so the owner still sees it.
            p = subprocess.run([exe, "-NoProfile", "-STA", "-NonInteractive",
                                "-ExecutionPolicy", "Bypass", "-Command", _PS_PICKER],
                               **quiet(capture_output=True, text=True,
                                       timeout=timeout, env=env))
            out = (p.stdout or "").strip()
        elif IS_MAC:
            script = ('set f to choose folder with prompt "Elige la carpeta donde vivirá '
                      'tu agente" default location POSIX file "%s"\n'
                      'return POSIX path of f' % start)
            p = subprocess.run(["osascript", "-e", script],
                               **quiet(capture_output=True, text=True, timeout=timeout))
            out = (p.stdout or "").strip()
        else:
            tool = shutil.which("zenity")
            if tool:
                p = subprocess.run([tool, "--file-selection", "--directory",
                                    "--filename", start + os.sep],
                                   **quiet(capture_output=True, text=True,
                                           timeout=timeout))
            else:
                tool = shutil.which("kdialog")
                if not tool:
                    return {"ok": False, "cancelled": False,
                            "detail": "Escribe la ruta a mano: este sistema no tiene un "
                                      "selector de carpetas."}
                p = subprocess.run([tool, "--getexistingdirectory", start],
                                   **quiet(capture_output=True, text=True,
                                           timeout=timeout))
            out = (p.stdout or "").strip()
    except subprocess.TimeoutExpired:
        return {"ok": False, "cancelled": True, "detail": ""}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "cancelled": False,
                "detail": "No se pudo abrir el selector: %s" % str(e)[:150]}

    if not out:
        return {"ok": False, "cancelled": True, "detail": ""}
    return {"ok": True, "cancelled": False, "path": os.path.abspath(out)}
