r"""One console at a time, running the code that is on disk now.

The console is started by double-clicking an icon, and nothing stopped a second one from
starting beside the first. Each takes a random port and keeps serving whatever Python it
loaded the day it started - so a window left open since Tuesday went on answering with
Tuesday's routes, and an owner who updated on Friday met «ruta desconocida» on a page
whose JavaScript had come fresh off the disk a second earlier. The UI was new and the
server behind it was four days old.

So, on start:

  * **A live console of the SAME version** is not replaced - its address is opened
    instead. Two consoles are never better than one, and the owner wanted a window, not
    a process.
  * **A live console of a DIFFERENT version** is retired first. That is the whole point:
    after an update, opening the console means getting the new one.
  * **A note that no longer matches anything alive** is ignored. A pid file outlives a
    power cut, and a pid is reused by whatever the machine starts next; this only
    believes one that still answers as a console.

The note holds a pid, a port and a version. No token: that is minted per process and
stays in it, so one console cannot be driven by a stale link somebody else is holding.
"""

import io
import json
import os
import time
import urllib.error
import urllib.request

NOTE = "console.json"


def _path(install_dir):
    return os.path.join(install_dir or ".", NOTE)


def read(install_dir):
    try:
        with io.open(_path(install_dir), encoding="utf-8") as fh:
            got = json.load(fh)
        return got if isinstance(got, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def write(install_dir, port, version):
    try:
        tmp = "%s.%d.tmp" % (_path(install_dir), os.getpid())
        with io.open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"pid": os.getpid(), "port": int(port),
                       "version": str(version), "since": int(time.time())}, fh)
        os.replace(tmp, _path(install_dir))
    except OSError:
        pass


def clear(install_dir, only_mine=True):
    """This console is no longer serving. The address it served on is kept.

    The note answers two questions and only one of them dies with the process: "who is
    serving and can I reach them" is about a living console, while "where does this
    install put its console" outlives it. A window somebody pinned needs the second
    one, and deleting the whole note on a clean exit threw it away - so a console that
    closed properly was the one whose address was lost, and a crash kept it.

    A note with no pid is nobody: alive() still has to get an answer on the port, and
    take_over() still has to be told it is the same version, before anything is reused.
    """
    got = read(install_dir)
    if only_mine and got.get("pid") not in (os.getpid(), None):
        return
    port = 0
    try:
        port = int(got.get("port") or 0)
    except (TypeError, ValueError):
        port = 0
    try:
        if port:
            tmp = "%s.%d.tmp" % (_path(install_dir), os.getpid())
            with io.open(tmp, "w", encoding="utf-8") as fh:
                json.dump({"port": port}, fh)
            os.replace(tmp, _path(install_dir))
        else:
            os.remove(_path(install_dir))
    except OSError:
        pass


def alive(note, timeout=1.5):
    """Is that port still a console, and which version is it?

    Asked over HTTP rather than by looking up the pid: a pid is reused, a port can be
    taken by something else entirely, and the only answer that matters here is whether
    the thing listening is one of ours.
    """
    port = int(note.get("port") or 0)
    if not port:
        return None
    try:
        with urllib.request.urlopen(
                "http://127.0.0.1:%d/who" % port, timeout=timeout) as r:
            got = json.loads(r.read().decode("utf-8", "replace"))
        return got if got.get("app") == "olivaw-console" else None
    except (urllib.error.URLError, OSError, ValueError, TimeoutError):
        return None


def retire(note):
    """Ask the older console to go. Returns True when it is gone."""
    pid = int(note.get("pid") or 0)
    if not pid:
        return False
    try:
        if os.name == "nt":
            import subprocess

            from winspawn import quiet
            # quiet(), or retiring a console flashes a console - which is precisely
            # what the owner is not supposed to see and what tools/test_nowindow.py
            # exists to stop.
            subprocess.run(["taskkill", "/PID", str(pid), "/F"],
                           capture_output=True, check=False, **quiet())
        else:
            import signal
            os.kill(pid, signal.SIGTERM)
    except Exception:  # noqa: BLE001
        return False
    for _ in range(20):
        if alive(note, timeout=0.4) is None:
            return True
        time.sleep(0.1)
    return alive(note, timeout=0.4) is None


def take_over(install_dir, version):
    """What a starting console should do about the one already running.

    Returns ("open", url) to hand the owner the window that already exists, or
    ("start", "") to go ahead and be the console.
    """
    note = read(install_dir)
    if not note:
        return ("start", "")
    live = alive(note)
    if live is None:
        return ("start", "")
    if str(live.get("version") or "") == str(version):
        return ("open", "http://127.0.0.1:%d/" % int(note["port"]))
    retire(note)
    return ("start", "")
