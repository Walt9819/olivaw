r"""No child process may flash a console window on the owner's screen.

The bug this pins (measured 2026-09-02, with nobody talking to any agent): the supervisor
runs under pythonw.exe, which has no console, and asked Hermes `gateway status` once a
minute. A process with no console that starts a CONSOLE program makes Windows allocate a
new console AND SHOW ITS WINDOW - so a Windows Terminal window blinked open on the desktop
every 60 seconds, three per check counting Hermes' own wmic calls, for ~2.5s each.

The fix is winspawn.quiet(). It only works if it is used at EVERY spawn, and new spawn
sites are added all the time - so this suite does not test one call, it audits the tree
with ast and fails on any subprocess.run/Popen that cannot be shown to be windowless.

Run: python tools/test_nowindow.py
"""

import ast
import io
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SRC = os.path.join(ROOT, "src")
sys.path.insert(0, SRC)

import winspawn  # noqa: E402

FAILED = []
CHECKS = [0]


def ok(cond, label):
    CHECKS[0] += 1
    if not cond:
        FAILED.append(label)
        print("FAIL " + label)


def eq(got, want, label):
    ok(got == want, "%s (got %r, want %r)" % (label, got, want))


# Files whose spawns are deliberately NOT hidden, with the reason. Anything else in src/
# must be windowless. Keeping this list here means removing a justification is a test
# change someone has to think about, not a silent regression.
EXEMPT = {
    # opens a console the owner is MEANT to read (pairing steps, QR codes)
    os.path.join("wizard", "channels.py"): "deliberately visible console",
    # GUI programs: they need no console, and DETACHED is intentional there
    os.path.join("wizard", "obsidian.py"): "GUI app (Obsidian)",
}

# Spawns that pass a literal creationflags instead of quiet(), justified per file.
INLINE_OK = {
    # documented stdlib-only module: must not import the rest of Olivaw
    os.path.join("tools", "escalate_owner.py"): "_QUIET",
}


def py_files(root):
    for base, dirs, names in os.walk(root):
        dirs[:] = [d for d in dirs if d not in ("__pycache__", "node_modules", "img_cache")]
        for n in sorted(names):
            if n.endswith(".py"):
                yield os.path.join(base, n)


def spawn_calls(tree):
    """Every subprocess.run / subprocess.Popen call node."""
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        if isinstance(f, ast.Attribute) and f.attr in ("run", "Popen", "call",
                                                       "check_output", "check_call"):
            if isinstance(f.value, ast.Name) and f.value.id == "subprocess":
                out.append(node)
    return out


def is_hidden(call, inline_name):
    """True when this call demonstrably suppresses the console window.

    Accepted: **quiet(...), an explicit creationflags=..., or **<inline_name> for the one
    module that may not import winspawn.
    """
    for kw in call.keywords:
        if kw.arg == "creationflags":
            return True
        if kw.arg is None:                      # **something
            v = kw.value
            if isinstance(v, ast.Call) and isinstance(v.func, ast.Name) \
                    and v.func.id == "quiet":
                return True
            if inline_name and isinstance(v, ast.Name) and v.id == inline_name:
                return True
            if isinstance(v, ast.Name) and v.id == "kwargs":
                return True                     # kwargs built with creationflags above
    return False


def test_every_spawn_in_src_is_windowless():
    audited = 0
    for path in py_files(SRC):
        rel = os.path.relpath(path, SRC)
        if rel in EXEMPT:
            continue
        tree = ast.parse(io.open(path, encoding="utf-8").read())
        for call in spawn_calls(tree):
            audited += 1
            ok(is_hidden(call, INLINE_OK.get(rel)),
               "%s:%d spawns a child without hiding its console" % (rel, call.lineno))
    ok(audited >= 20, "audited enough spawn sites (%d)" % audited)
    print("   audited %d spawn sites under src/" % audited)


def test_exempt_files_still_justify_themselves():
    """An exemption is only valid while the file really is the special case claimed."""
    ch = io.open(os.path.join(SRC, "wizard", "channels.py"), encoding="utf-8").read()
    ok("CREATE_NEW_CONSOLE" in ch,
       "channels.py is exempt because it opens a console on purpose")
    esc = io.open(os.path.join(SRC, "tools", "escalate_owner.py"), encoding="utf-8").read()
    ok("Stdlib only" in esc, "escalate_owner.py still declares the stdlib-only rule")
    ok("0x08000000" in esc, "escalate_owner.py inlines CREATE_NO_WINDOW")
    # Grepping for "import winspawn" would match the COMMENT that explains why there is
    # no such import. Ask the parser what the module actually imports.
    olivaw = {"winspawn", "wizard", "launcher", "claude_bridge", "codex_engine"}
    imported = set()
    for node in ast.walk(ast.parse(esc)):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    ok(not (imported & olivaw),
       "escalate_owner.py keeps its no-Olivaw-imports promise (has %r)"
       % sorted(imported & olivaw))


def test_quiet_merges_instead_of_replacing():
    """A caller that wants its own process group must not lose it - or gain a window."""
    kw = winspawn.quiet(creationflags=winspawn.CREATE_NEW_PROCESS_GROUP, timeout=5)
    if os.name == "nt":
        eq(kw["creationflags"],
           winspawn.CREATE_NO_WINDOW | winspawn.CREATE_NEW_PROCESS_GROUP,
           "quiet() ORs into existing creationflags")
    eq(kw["timeout"], 5, "quiet() passes other kwargs through untouched")
    plain = winspawn.quiet()
    if os.name == "nt":
        eq(plain, {"creationflags": winspawn.CREATE_NO_WINDOW}, "quiet() alone sets the flag")
    else:
        eq(plain, {}, "quiet() is a no-op off Windows")
    eq(winspawn.quiet(creationflags=None).get("creationflags"),
       winspawn.CREATE_NO_WINDOW if os.name == "nt" else None,
       "quiet() tolerates creationflags=None")


def test_no_detached_process_on_a_console_program():
    """DETACHED_PROCESS makes Windows IGNORE CREATE_NO_WINDOW, and a console child of a
    detached process opens its own visible console. It must not come back next to quiet()."""
    for rel in (os.path.join("wizard", "selfcare.py"),
                os.path.join("wizard", "wizard_server.py"),
                "launcher.py"):
        s = io.open(os.path.join(SRC, rel), encoding="utf-8").read()
        ok("0x00000008" not in s, "%s no longer detaches a child it wants windowless" % rel)


def test_procutil_really_passes_the_flag():
    """The audit reads source; this one watches the call. procutil is the hot path - every
    hermes_ctl call in the product, including the once-a-minute gateway check."""
    sys.path.insert(0, SRC)
    from wizard import procutil
    seen = {}
    real = procutil.subprocess.run

    class R:
        returncode, stdout, stderr = 0, "", ""

    def fake(cmd, **kw):
        seen.update(kw)
        seen["cmd"] = cmd
        return R()

    procutil.subprocess.run = fake
    try:
        procutil.run(["whoami"])
    finally:
        procutil.subprocess.run = real
    if os.name == "nt":
        ok(bool(seen.get("creationflags", 0) & winspawn.CREATE_NO_WINDOW),
           "procutil.run() hides the console (flags=%r)" % seen.get("creationflags"))
    eq(seen.get("cmd"), ["whoami"], "procutil.run() still passes the command through")
    eq(seen.get("timeout"), 25, "procutil.run() keeps its default timeout")


def test_supervisor_does_not_poll_a_foreign_gateway_every_minute():
    """Hidden is not the same as free: one `gateway status` is 6 processes and ~2.5s."""
    s = io.open(os.path.join(SRC, "launcher.py"), encoding="utf-8").read()
    i = s.index("its gateway is already running outside our supervision")
    window = s[i:i + 900]
    ok("retry_at" in window and "now + 300" in window,
       "an externally-owned gateway is re-checked every 5 minutes, not every 60s")


def test_the_patched_modules_still_import():
    """A misplaced import or an unbalanced paren in this change would take the agent down
    at the next restart, not at test time. Compile every file we touched, for real."""
    touched = ["winspawn.py", "launcher.py", "claude_bridge.py", "codex_engine.py",
               os.path.join("wizard", "procutil.py"), os.path.join("wizard", "rescue.py"),
               os.path.join("wizard", "wa_patch.py"), os.path.join("wizard", "workspace.py"),
               os.path.join("wizard", "wizard_server.py"),
               os.path.join("wizard", "selfcare.py"),
               os.path.join("wizard", "browser_setup.py"),
               os.path.join("tools", "claude_chrome.py"),
               os.path.join("tools", "escalate_owner.py"),
               os.path.join("tts", "synthesize.py")]
    for rel in touched:
        path = os.path.join(SRC, rel)
        r = subprocess.run([sys.executable, "-m", "py_compile", path],
                           capture_output=True, text=True, timeout=90)
        ok(r.returncode == 0, "%s compiles (%s)" % (rel, (r.stderr or "").strip()[:160]))
    # and the two that must work as scripts from their own directory
    for rel, args in ((os.path.join("tools", "claude_chrome.py"), ["--help"]),
                      (os.path.join("tools", "escalate_owner.py"), ["--list-reasons"])):
        r = subprocess.run([sys.executable, os.path.join(SRC, rel)] + args,
                           capture_output=True, timeout=120, cwd=os.path.join(SRC, "tools"))
        out = (r.stdout or b"").decode("utf-8", "replace")
        err = (r.stderr or b"").decode("utf-8", "replace")
        ok("Traceback" not in err,
           "%s %s runs standalone (%s)" % (rel, " ".join(args), err.strip()[-200:]))
        ok(len(out.strip()) > 0, "%s %s printed something" % (rel, " ".join(args)))


def test_winspawn_import_comes_after_any_path_bootstrap():
    """`from winspawn import ...` above the sys.path.insert that makes src/ importable is
    a ModuleNotFoundError - and under pythonw.exe, which has no console, a silent one.

    That is exactly how the Olivaw shortcut broke (2026-09-02): clicking the icon ran
    `pythonw src/wizard/wizard_server.py`, the import at the top died before the bootstrap
    below it, and nothing at all appeared on screen. py_compile did not catch it, because
    compiling a file does not run its imports.
    """
    for path in py_files(SRC):
        rel = os.path.relpath(path, SRC)
        src = io.open(path, encoding="utf-8").read()
        if "winspawn" not in src:
            continue
        tree = ast.parse(src)
        win = [n.lineno for n in ast.walk(tree)
               if isinstance(n, ast.ImportFrom) and (n.module or "") == "winspawn"]
        if not win:
            continue
        boots = []
        for n in ast.walk(tree):
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) \
                    and n.func.attr == "insert" \
                    and isinstance(n.func.value, ast.Attribute) \
                    and n.func.value.attr == "path":
                boots.append(n.lineno)
        if boots:
            ok(min(boots) < min(win),
               "%s bootstraps sys.path (line %d) before importing winspawn (line %d)"
               % (rel, min(boots), min(win)))
        else:
            # No bootstrap at all is only safe for a module that is never run as a script.
            ok('if __name__ == "__main__"' not in src,
               "%s is runnable as a script but has no sys.path bootstrap" % rel)


def test_desktop_repair_finds_a_desktop_that_exists():
    """The self-repair that recreates a lost Olivaw icon has to look where the desktop
    actually is - OneDrive moves it, and ~/Desktop then does not exist."""
    sys.path.insert(0, SRC)
    import launcher
    d = launcher._desktop_dir()
    ok(os.path.isdir(d), "the supervisor resolves a real Desktop folder (%s)" % d)
    if os.name == "nt":
        guess = os.path.join(os.path.expanduser("~"), "Desktop")
        if not os.path.isdir(guess):
            ok(d != guess, "it does not fall back to a ~/Desktop that is not there")
    src = io.open(os.path.join(SRC, "launcher.py"), encoding="utf-8").read()
    ok("desktop = _desktop_dir()" in src,
       "_ensure_app_shortcut uses the resolved desktop, not the guess")


def test_entry_points_actually_run():
    """The check the compile test could not make: EXECUTE each entry point's top level,
    from a directory that is not the repo, the way the shortcut and the agent do."""
    import tempfile
    neutral = tempfile.mkdtemp(prefix="olivaw-probe-")
    entries = ["launcher.py", "claude_bridge.py",
               os.path.join("wizard", "wizard_server.py"),
               os.path.join("wizard", "wa_patch.py"),
               os.path.join("tools", "claude_chrome.py"),
               os.path.join("tools", "escalate_owner.py"),
               os.path.join("tools", "browser_mode.py"),
               os.path.join("tools", "conversation_policy.py")]
    for rel in entries:
        path = os.path.join(SRC, rel)
        if not os.path.isfile(path):
            continue
        # run_name is not "__main__", so the top level runs but main() does not: no server
        # is bound, no browser opens, nothing is sent.
        code = ("import runpy; runpy.run_path(r'%s', run_name='__probe__')" % path)
        r = subprocess.run([sys.executable, "-c", code], capture_output=True,
                           text=True, timeout=180, cwd=neutral)
        ok(r.returncode == 0,
           "%s runs from a foreign cwd (%s)" % (rel, (r.stderr or "").strip()[-200:]))


def main():
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            print("-- " + name)
            fn()
    print("\n%d checks, %d failed" % (CHECKS[0], len(FAILED)))
    for f in FAILED:
        print("  FAILED: " + f)
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
