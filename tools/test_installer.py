r"""The Windows installer has to survive two things: a path with a space in it, and failing.

A real install failed on a real machine and reached us as a photograph showing the banner,
"[ok] Cerebro: Codex", then "La instalacion fallo (codigo 1)" - and nothing else. Pressing
"Copiar detalles" copied that same nothing. The cause was undiagnosable because of how the
window ran the work: as a child process with

    -RedirectStandardOutput <log>  -RedirectStandardError <log>.err

and it only ever tailed the FIRST of those. In PowerShell every terminating error - every
`throw` in this script - goes to stderr. So the one piece of text that said what went wrong
was written to a file nothing read, nothing displayed and nothing copied.

Then, testing that, the second bug fell out. `Start-Process -ArgumentList` joins an array
with spaces and does NOT quote the elements, so a path containing a space is split in two.
Measured end to end: passing "...\Juan Perez\Olivaw" installed into "...\Juan", and the
leftover "Perez\Olivaw" bound POSITIONALLY to -BotToken. A non-empty BotToken means
"headless" to this script, so it skipped the wizard and went off to reconfigure Hermes from
a garbage token. Every user whose Windows name contains a space - Juan Perez, Maria Garcia -
got that.

What this suite pins:
  * the child reports its own terminating errors on STDOUT, where the window can see them;
  * the window tails stderr as well, as the backstop for what a trap cannot catch;
  * "Copiar detalles" carries the transcript AND the machine facts AND both log paths;
  * every value handed to the child is quoted, proven by actually launching a child with a
    spaced path and asking it what it received;
  * a stray positional argument is a loud error, never silent junk in -BotToken.

Run: python tools/test_installer.py     (Windows only; skips elsewhere)
"""

import io
import os
import platform
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PS1 = os.path.join(ROOT, "install", "install-windows.ps1")

PASSED, FAILED = [], []


def check(name, cond, extra=""):
    (PASSED if cond else FAILED).append(name)
    print(("  ok   " if cond else "  FAIL ") + name +
          (("\n       " + str(extra)) if (extra and not cond) else ""))


def section(t):
    print("\n=== %s ===" % t)


def powershell(script, *args):
    """Run a PowerShell snippet from a temp file (no quoting games in the command line)."""
    fd, path = tempfile.mkstemp(suffix=".ps1")
    os.close(fd)
    with io.open(path, "w", encoding="utf-8", newline="\r\n") as fh:
        fh.write(script)
    try:
        p = subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
                            "-File", path] + list(args),
                           capture_output=True, text=True, timeout=180,
                           encoding="utf-8", errors="replace")
        return p.returncode, (p.stdout or ""), (p.stderr or "")
    finally:
        os.remove(path)


def main():
    src = io.open(PS1, encoding="utf-8", errors="replace").read()

    section("the child says what went wrong, on the stream the window reads")
    check("there is a script-scope trap", "\ntrap {" in src, "no trap")
    check("it prints the message", "$_.Exception.Message" in src)
    check("and where it happened", "PositionMessage" in src)
    check("and which step it died on, so 'before 1/5' is answerable",
          "$script:CurrentStep" in src and "function Step($m){ $script:CurrentStep" in src)
    check("it re-throws, so the exit code and stderr are unchanged",
          "\n  break\n}" in src, "trap must end in break")

    section("the window no longer throws stderr away")
    check("it tails the .err file too", '$errFile = $logFile + ".err"' in src)
    check("with its own read position, so nothing is re-printed",
          "$state.errpos" in src and "errpos = 0" in src)
    check("and what it reads goes into the copied transcript too",
          "$state.log += $echunk" in src)

    section("'Copiar detalles' is worth pasting to somebody")
    for fact in ("windows   :", "powershell:", "64-bit    :", "registro  :", "errores   :"):
        check("it reports %r" % fact.strip(" :"), fact in src)
    check("it still carries the transcript itself", "$state.log + $facts" in src)

    section("a stray argument cannot masquerade as a bot token")
    # This is the bug that turned a split path into "reconfigure Hermes from garbage".
    check("positional binding is off",
          "[CmdletBinding(PositionalBinding=$false)]" in src, "still positional")
    check("and a non-empty BotToken is still what selects headless mode",
          "IsNullOrWhiteSpace($BotToken)" in src)

    section("every value handed to the child is quoted")
    check("there is a quoting helper", "$qarg = { param($v)" in src)
    check("trailing backslashes are trimmed (they would escape the closing quote)",
          'TrimEnd(\'\\\')' in src or "TrimEnd('\\')" in src, "no TrimEnd")
    for name in ("$SelfPath", "$InstallDir", "$Workspace", "$Repo", "$Lang"):
        check("%s goes through it" % name, "(& $qarg %s)" % name in src,
              "unquoted %s" % name)

    section("the download-and-double-click files")
    # These exist so nobody has to open PowerShell or a Terminal and paste a command. Both
    # are wrappers: the logic stays in install/, fetched from main at run time, so a file
    # somebody downloaded months ago still installs the current version.
    cmd_path = os.path.join(ROOT, "install", "Olivaw-Instalar.cmd")
    check("there is a Windows one", os.path.isfile(cmd_path))
    raw = open(cmd_path, "rb").read()
    check("it is CRLF - a batch file with LF endings misparses",
          raw.count(b"\n") > 0 and raw.count(b"\n") == raw.count(b"\r\n"),
          "mixed line endings: %d LF vs %d CRLF" % (raw.count(b"\n"), raw.count(b"\r\n")))
    check("and has no BOM, which cmd.exe would try to execute",
          not raw.startswith(b"\xef\xbb\xbf"))
    check("it is pure ASCII, so it survives any console codepage",
          all(b < 128 for b in raw), "non-ascii byte present")
    cmd = raw.decode("ascii")
    check("it fetches the real installer from main",
          "install/install-windows.ps1" in cmd and "raw.githubusercontent.com" in cmd)
    check("the URL is overridable, so a fork (and this test) can point elsewhere",
          '%OLIVAW_PS1%' in cmd)
    check("it bypasses the execution policy, or a default Windows blocks it",
          "-ExecutionPolicy Bypass" in cmd)
    check("a failure pauses instead of closing the window on the reason",
          "pause" in cmd and "errorlevel 1" in cmd)
    check("and points at 'Ejecutar como administrador', the fix we now know about",
          "administrador" in cmd)
    # Deliberately NOT self-elevating: on a standard account UAC asks for a DIFFERENT
    # account and the install would land in that admin's profile.
    check("it does not silently elevate itself", "-Verb RunAs" not in cmd)
    check("and says why in the file, for whoever edits it next",
          "cuenta de usuario estandar" in cmd)

    mac = os.path.join(ROOT, "install", "install-macos.command")
    check("the Mac installer is still there to wrap", os.path.isfile(mac))
    sys.path.insert(0, os.path.join(ROOT, "tools"))
    import release as rel                                    # noqa: E402
    os.makedirs(rel.DIST, exist_ok=True)
    built = rel.build_launchers()
    check("release.py builds both assets", len(built) == 2, built)
    zp = [p for p in built if p.endswith(".zip")][0]
    import stat, zipfile as zf                               # noqa: E402
    with zf.ZipFile(zp) as z:
        info = z.infolist()[0]
        mode = info.external_attr >> 16
        # The whole point of shipping a zip: a .command downloaded raw from a browser has
        # no execute bit, so double-clicking opens a text editor instead of installing.
        check("the Mac zip preserves the execute bit", bool(mode & stat.S_IXUSR),
              oct(mode))
        check("it unpacks under a name a person can recognise",
              info.filename == "Olivaw-Instalar.command", info.filename)
        check("and it really is the installer", z.read(info.filename).startswith(b"#!/bin/bash"))

    section("running without administrator rights is a handled case, not a mystery")
    # The reported failure turned out to be exactly this, and the window said only
    # "codigo 1". Elevation is now stated in every transcript, and the fix is a button.
    check("elevation is detected", "function Is-Admin" in src)
    check("and reported in the log, so it is in every pasted transcript",
          "Permisos: administrador." in src and "Permisos: usuario normal" in src)
    check("a failed run offers to retry elevated",
          "Reintentar como administrador" in src)
    check("but only when it would actually help",
          "if (-not (Is-Admin)) {" in src and "$again.Visible = $true" in src)
    check("the retry really elevates", "-Verb RunAs" in src)
    check("and a refused UAC prompt is reported rather than leaving a dead button",
          "No se pudo abrir como administrador" in src)
    check("both launches share ONE quoting helper, so they cannot drift",
          src.count("$qarg = { param($v)") == 1 and src.count("(& $qarg ") >= 8,
          "qarg defs: %d uses: %d" % (src.count("$qarg = { param($v)"),
                                      src.count("(& $qarg ")))

    if platform.system() != "Windows":
        print("\n  skip (the behavioural checks need PowerShell)")
    else:
        section("the parse still holds")
        code, out, err = powershell(
            "param([string]$Path)\n"
            "$errs = $null\n"
            "$null = [System.Management.Automation.Language.Parser]::ParseFile("
            "$Path, [ref]$null, [ref]$errs)\n"
            "if ($errs -and $errs.Count) { $errs | ForEach-Object { $_.Message }; exit 1 }\n"
            "'clean'\n", PS1)
        check("install-windows.ps1 parses", code == 0 and "clean" in out, out + err)

        section("a child really does receive a path with a space in it")
        # The mechanism, not the string: launch a child the way the window does and ask it
        # what it got. Without the quoting this comes back truncated at the space.
        spaced = os.path.join(tempfile.gettempdir(), "Juan Perez", "Olivaw")
        code, out, err = powershell(
            "param([string]$Spaced)\n"
            "$child = Join-Path $env:TEMP 'olv-argecho.ps1'\n"
            "@'\n"
            "[CmdletBinding(PositionalBinding=$false)]\n"
            "param([string]$InstallDir = '', [string]$Workspace = '', [string]$BotToken = '')\n"
            "\"DIR=[$InstallDir]\"\n"
            "\"WS=[$Workspace]\"\n"
            "\"TOKEN=[$BotToken]\"\n"
            "'@ | Set-Content -Encoding utf8 $child\n"
            "$q = { param($v) '\"' + (\"$v\".TrimEnd('\\') ) + '\"' }\n"
            "$log = Join-Path $env:TEMP 'olv-argecho.out'\n"
            "$errf = $log + '.err'\n"
            "Remove-Item $log,$errf -ErrorAction SilentlyContinue\n"
            "$a = @('-NoProfile','-ExecutionPolicy','Bypass','-File',(& $q $child),\n"
            "       '-InstallDir',(& $q $Spaced),'-Workspace',(& $q $Spaced))\n"
            "$p = Start-Process powershell.exe -ArgumentList $a -RedirectStandardOutput $log "
            "-RedirectStandardError $errf -NoNewWindow -PassThru\n"
            "$null = $p.Handle\n"
            "$p.WaitForExit(60000) | Out-Null\n"
            "Get-Content $log -Raw\n"
            "'EXIT=' + $p.ExitCode\n"
            "Remove-Item $child,$log,$errf -ErrorAction SilentlyContinue\n", spaced)
        check("the child ran at all", "EXIT=0" in out, out + err)
        check("it received the WHOLE install dir, space included",
              ("DIR=[%s]" % spaced) in out, out.strip() + " | wanted " + spaced)
        check("and the whole workspace", ("WS=[%s]" % spaced) in out, out.strip())
        check("and nothing leaked into the bot token, which would mean headless mode",
              "TOKEN=[]" in out, out.strip())

        section("and without the quoting it really does break (the bug, reproduced)")
        code, out, err = powershell(
            "param([string]$Spaced)\n"
            "$child = Join-Path $env:TEMP 'olv-argecho2.ps1'\n"
            "@'\n"
            "param([string]$InstallDir = '', [string]$Workspace = '', [string]$BotToken = '')\n"
            "\"DIR=[$InstallDir]\"\n"
            "\"TOKEN=[$BotToken]\"\n"
            "'@ | Set-Content -Encoding utf8 $child\n"
            "$log = Join-Path $env:TEMP 'olv-argecho2.out'\n"
            "Remove-Item $log -ErrorAction SilentlyContinue\n"
            "$a = @('-NoProfile','-ExecutionPolicy','Bypass','-File',$child,\n"
            "       '-InstallDir',$Spaced,'-Workspace',$Spaced)\n"
            "$p = Start-Process powershell.exe -ArgumentList $a -RedirectStandardOutput $log "
            "-NoNewWindow -PassThru\n"
            "$null = $p.Handle\n"
            "$p.WaitForExit(60000) | Out-Null\n"
            "Get-Content $log -Raw\n"
            "Remove-Item $child,$log -ErrorAction SilentlyContinue\n", spaced)
        check("unquoted, the install dir is truncated at the space",
              ("DIR=[%s]" % spaced) not in out, out.strip())
        check("and the leftover lands in the bot token - the headless flip",
              "TOKEN=[]" not in out, out.strip())

    print("\n%d passed, %d failed" % (len(PASSED), len(FAILED)))
    for f in FAILED:
        print("  - " + f)
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
