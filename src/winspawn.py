"""Start child processes without a console window flashing on the owner's screen.

Everything long-lived in Olivaw runs under `pythonw.exe`, which has no console. When a
process with no console starts a CONSOLE program - `cmd /c daneel.bat`, `hermes.exe`,
`claude.cmd`, `git`, `netstat`, `ffmpeg` - Windows has none to lend it, so it ALLOCATES A
NEW ONE and shows its window. With Windows Terminal as the default console host that is a
real window that takes focus.

Measured on this machine (2026-09-02), with nobody talking to any agent:

    08:39:59  cmd.exe   ppid=<supervisor>  cmd /c ...daneel.bat gateway status
    08:39:59  conhost.exe + OpenConsole.exe -Embedding      <- a Windows Terminal window
    08:40:00  hermes.exe -> python.exe -> python.exe
    08:40:01  WMIC.exe + conhost      (Hermes' own PID lookup)
    08:40:02  WMIC.exe + conhost

...every 60 seconds, forever, because the supervisor asks Hermes whether each agent's
gateway is alive. Three consoles per check, ~2.5s of stolen focus, all invisible in the
log because nothing was wrong.

The flag that fixes it is CREATE_NO_WINDOW, and the distinction from DETACHED_PROCESS
matters:

  * DETACHED_PROCESS gives the child NO console. A console program then allocates one
    itself the moment it writes to stdout - and THAT one is visible. So DETACHED is the
    wrong flag for a console program, and Windows ignores CREATE_NO_WINDOW when the two
    are combined.
  * CREATE_NO_WINDOW gives the child a console that is created hidden. Grandchildren
    inherit it, which is why suppressing our own spawn also silences the `wmic` calls
    made deep inside Hermes' CLI.

So: `subprocess.run(cmd, **quiet(capture_output=True))` for anything a background process
starts. GUI programs (Chrome, Obsidian) do not need it, and one place must NOT have it -
`wizard/channels.py` opens a console the owner is meant to read.
"""

import os

CREATE_NO_WINDOW = 0x08000000
CREATE_NEW_PROCESS_GROUP = 0x00000200
IS_WIN = os.name == "nt"


def quiet(**kwargs):
    """subprocess kwargs with the console hidden on Windows; untouched everywhere else.

    Merges into any `creationflags` already passed instead of replacing them, so a caller
    that also wants its own process group keeps it.
    """
    if IS_WIN:
        kwargs["creationflags"] = int(kwargs.get("creationflags") or 0) | CREATE_NO_WINDOW
    return kwargs
