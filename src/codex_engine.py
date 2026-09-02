r"""
Codex engine — the same brain contract, spoken to OpenAI's Codex CLI.

Olivaw's bridge turns an OpenAI-compatible HTTP request into one run of a coding CLI used as a
pure reasoner: prompt in, one decision out. Claude Code was the only CLI that could play that
part; this module lets `codex exec` play it too, so the choice of brain is the owner's rather
than ours. Everything above this line — Hermes, the tool loop, sessions, the wizard, the SOS
console, the nightly routines — is unchanged.

The contract this module must honour, borrowed exactly from the Claude path:

    run(prompt, ...) -> (text, usage, session_id)

Facts about `codex exec` that shaped the code (verified against codex-cli 0.150.1, not assumed):

  * **Exit code 0 means nothing.** A run whose every request 401s still exits 0, having printed
    only error events. Success is therefore defined as "an agent_message came back", never as
    "the process exited cleanly". This is the single most important thing here.
  * stdout is JSONL *mixed with plain log lines* ("Reading additional input from stdin...", Rust
    tracing lines). Every line that is not JSON is skipped.
  * `-c key=value` overrides are validated against a known key list — `--strict-config` rejects
    unknown ones. `tools.web_search`, `sandbox_mode`, `approval_policy`, `mcp_servers` and
    `model_reasoning_effort` are real; `tools.shell` is NOT.
  * The tools are FEATURES, not config fields: `codex features list` shows `shell_tool`, and
    `--disable shell_tool` turns it off. That is the real equivalent of Claude's `--tools ""`,
    and it is what makes this a pure reasoner rather than an agent with a sandbox around it. The
    read-only sandbox stays as the backstop for `unified_exec`, which this CLI will not disable.
  * There is no `--append-system-prompt`. The runtime contract is prepended to the prompt on
    stdin, inside a marked block.
  * `codex exec resume <thread_id>` continues a session, but accepts a SMALLER option set than
    `codex exec` (no `-s`, no `-C`). Both paths therefore express the sandbox through `-c
    sandbox_mode=` and the working directory through the child's cwd — one option list, two
    subcommands.
  * The session id to resume is the `thread_id` from the `thread.started` event.

Model names are deliberately NOT invented here. With nothing configured, no `-m` is passed and
Codex uses whatever the owner's account and config already prefer; that keeps working when the
model line-up changes. Claude tier names (sonnet/opus/fable) can reach us from a subagent's
`model` field, and they are translated or dropped — never handed to Codex as a model id.
"""

import json
import os
import re
import shutil
import subprocess
import tempfile
import time

IS_WIN = os.name == "nt"

# Where the CLI lives. The env var is written by the wizard; PATH is the fallback.
CODEX_CMD = os.environ.get("OLIVAW_CODEX", "").strip()

# Empty means "let Codex decide" — the right default, and one that cannot go stale.
DEFAULT_MODEL = os.environ.get("OLIVAW_CODEX_MODEL", "").strip()

# Claude tiers a subagent may request. They mean nothing to Codex, so each maps to an
# optional configured model and otherwise falls back to the default (usually none).
_CLAUDE_TIERS = ("fable", "opus", "haiku", "sonnet")
_PASSTHROUGH = ("claude-code", "claude", "default", "codex", "")

# Claude's effort dial → Codex's reasoning effort. Unknown values land on medium rather
# than being passed through: a bad value fails the whole turn at the API.
_EFFORT = {"xhigh": "xhigh", "high": "high", "medium": "medium", "low": "low",
           "minimal": "minimal", "none": "low", "": "medium"}

# Codex's tools are FEATURES, not config fields: `-c tools.shell=false` is rejected, while
# `--disable shell_tool` really does turn the shell off (checked with `codex features list`).
# Turning them off is what makes the brain a pure reasoner, exactly like Claude's `--tools ""` -
# the runtime performs every action, so the brain having its own tools is a liability, not a help.
# `unified_exec` is deliberately in the list even though this CLI refuses to disable it: if a
# later version allows it, we want it off, and until then the read-only sandbox covers it.
TOOL_FEATURES = ("shell_tool", "unified_exec", "apps", "browser_use", "computer_use",
                 "web_search", "code_mode_host", "tool_suggest")

# Flipped off for the life of the process if the CLI ever rejects those flags (a renamed or
# removed feature). Losing the isolation is bad; losing the brain entirely is worse.
_FEATURES_USABLE = True

_FLAG_PROBLEM = re.compile(
    r"unknown configuration field|unknown feature|unrecognized|not a (?:valid|known) feature|"
    r"invalid value for|error loading config|unexpected argument|unknown option",
    re.I,
)


def flags_rejected(text):
    """Does this failure look like the CLI refusing our isolation flags (rather than a real
    problem with the turn)? Used to decide whether retrying without them is worth a shot."""
    return bool(_FLAG_PROBLEM.search(str(text or "")))


def disable_feature_flags(log=None):
    """Stop sending the feature flags for the rest of this process."""
    global _FEATURES_USABLE
    if _FEATURES_USABLE:
        _FEATURES_USABLE = False
        if log:
            log.warning("codex rejected the tool-disabling feature flags; continuing WITHOUT them "
                        "(the read-only sandbox still prevents any change). Check `codex features "
                        "list` against TOOL_FEATURES in codex_engine.py.")
    return _FEATURES_USABLE


def features_enabled():
    return _FEATURES_USABLE


def tool_off_flags():
    """`--disable <feature>` for every tool the reasoner must not have."""
    if not _FEATURES_USABLE:
        return []
    out = []
    for f in TOOL_FEATURES:
        out += ["--disable", f]
    return out


# Prepended to the prompt, since Codex has no --append-system-prompt. Marked as the
# runtime's own framing so it reads as setup rather than as conversation content.
_SYSTEM_HEADER = "<runtime_contract note=\"This is your operating contract, from the runtime itself.\">"
_SYSTEM_FOOTER = "</runtime_contract>"


def exe_candidates():
    """Launchable paths for the Codex CLI, best first.

    Same Windows trap as the Claude shim: CreateProcess can only launch .cmd/.exe/.bat, so the
    extensionless `codex` (a sh script) and `codex.ps1` would raise WinError 193.
    """
    out, seen = [], []
    base = os.path.dirname(CODEX_CMD) if CODEX_CMD else ""
    for c in (CODEX_CMD,
              os.path.join(base, "codex.cmd") if base else None,
              os.path.join(base, "codex.exe") if base else None,
              shutil.which("codex.cmd"), shutil.which("codex.exe"),
              None if IS_WIN else shutil.which("codex")):
        if not c or c in seen:
            continue
        seen.append(c)
        if IS_WIN and os.path.splitext(c)[1].lower() not in (".cmd", ".exe", ".bat"):
            continue
        if os.path.isfile(c) and os.path.getsize(c) > 0:
            out.append(c)
    return out


def resolve_exe():
    cands = exe_candidates()
    return cands[0] if cands else ""


def available():
    return bool(resolve_exe())


def map_effort(effort):
    return _EFFORT.get((effort or "").strip().lower(), "medium")


def map_model(model):
    """Translate whatever the router asked for into a Codex model id (or nothing).

    A Claude tier name reaching `-m` would fail the turn, so tiers are mapped through
    optional env config and otherwise dropped.
    """
    m = (model or "").strip()
    low = m.lower()
    if low in _PASSTHROUGH:
        return DEFAULT_MODEL
    for tier in _CLAUDE_TIERS:
        if tier in low:
            return os.environ.get("OLIVAW_CODEX_MODEL_" + tier.upper(), "").strip() or DEFAULT_MODEL
    return m            # an explicit Codex model id passes through untouched


def _config_flags(effort):
    """Isolation and behaviour, expressed as -c overrides so `exec` and `exec resume`
    can share one option list (resume accepts neither -s nor -C)."""
    return [
        # The brain reasons; the runtime acts. The tools are off (see tool_off_flags); this is
        # the backstop for anything that survives that - notably unified_exec, which this CLI
        # will not let us disable. Read-only means no writes, no installs, no network.
        #
        # NOT loosened for image generation, deliberately. Codex's built-in `image_gen` writes
        # to $CODEX_HOME/generated_images/, and whether this sandbox governs a first-party tool
        # (as opposed to the shell) is undocumented and untested here - no codex on the machine
        # this was written on. If it turns out to be blocked, the fix is a narrow writable_roots
        # entry for that one directory, NOT switching the whole brain to workspace-write.
        "-c", 'sandbox_mode="read-only"',
        "-c", 'approval_policy="never"',
        # Mirror the Claude path's --strict-mcp-config: the owner's own MCP servers are
        # not the brain's tools, and must never be mistaken for the runtime's catalog.
        "-c", "mcp_servers={}",
        "-c", "tools.web_search=false",
        "-c", "hide_agent_reasoning=true",
        "-c", 'model_reasoning_effort="%s"' % map_effort(effort),
    ]


def build_cmd(exe, model=None, effort=None, resume=None, persist=False, image_paths=None,
              out_file=None):
    cmd = [exe, "exec"]
    if resume:
        cmd += ["resume", str(resume)]
    cmd += ["--json", "--skip-git-repo-check"]
    cmd += tool_off_flags()
    cmd += _config_flags(effort)
    m = map_model(model)
    if m:
        cmd += ["-m", m]
    if not persist and not resume:
        cmd += ["--ephemeral"]          # nothing to resume later, so leave no session file
    for p in (image_paths or []):
        if p and os.path.isfile(p):
            cmd += ["-i", p]
    if out_file:
        cmd += ["-o", out_file]
    cmd += ["-"]                        # the prompt itself arrives on stdin
    return cmd


def parse_events(stdout):
    """Read the JSONL stream. Pure function, so the real event shapes can be unit-tested.

    Returns {text, thread_id, usage, errors, turn_completed, messages}.
    """
    text_parts, errors = [], []
    thread_id, usage, turn_completed = "", None, False
    for raw in (stdout or "").splitlines():
        line = raw.strip()
        if not line.startswith("{"):
            continue                    # tracing/log noise shares this stream
        try:
            d = json.loads(line)
        except Exception:  # noqa: BLE001
            continue
        if not isinstance(d, dict):
            continue
        t = d.get("type")
        if t == "thread.started":
            thread_id = str(d.get("thread_id") or "") or thread_id
        elif t == "turn.completed":
            turn_completed = True
            u = d.get("usage") or {}
            if isinstance(u, dict):
                usage = u
        elif t == "turn.failed":
            err = d.get("error") or {}
            errors.append(str(err.get("message") if isinstance(err, dict) else err))
        elif t == "error":
            errors.append(str(d.get("message") or ""))
        elif t == "item.completed":
            item = d.get("item") or {}
            if not isinstance(item, dict):
                continue
            if item.get("type") == "agent_message":
                txt = item.get("text") or ""
                if txt.strip():
                    text_parts.append(txt)
            elif item.get("type") == "error":
                errors.append(str(item.get("message") or ""))
    return {"text": text_parts[-1] if text_parts else "",
            "messages": text_parts,
            "thread_id": thread_id,
            "usage": usage or {},
            "errors": [e for e in errors if e],
            "turn_completed": turn_completed}


def _best_error(events, stderr, returncode):
    """The most useful line to show when a run produced no answer.

    Reconnect notices dominate the error list on an auth failure, and the real cause is the
    tail of them — so prefer a non-transient message, and fall back to the last one.
    """
    errs = events.get("errors") or []
    solid = [e for e in errs if not e.lower().startswith("reconnecting")]
    msg = (solid[-1] if solid else (errs[-1] if errs else "")).strip()
    if not msg:
        msg = (stderr or "").strip().splitlines()[-1] if (stderr or "").strip() else ""
    if not msg:
        msg = "codex exited %s without producing an answer" % returncode
    low = msg.lower()
    if "401" in msg or "invalid_api_key" in low or "unauthorized" in low:
        msg += "  (Codex no tiene sesión: ejecuta `codex login` una vez)"
    return msg[:2000]


_LAUNCH_ERRNOS = (2, 193, 216)


def _spawn(cmd, prompt, timeout, cwd, attempts=3):
    """Run it, retrying over the other launchers if the shim is being rewritten mid-update
    (the same npm race that cost the Claude path whole turns)."""
    last = None
    for i in range(attempts):
        exe = cmd[0] if i == 0 else None
        if i > 0:
            cands = exe_candidates()
            if not cands:
                time.sleep(1.5)
                cands = exe_candidates()
            if not cands:
                raise last or RuntimeError("codex CLI not found on disk")
            exe = cands[min(i - 1, len(cands) - 1)]
        try:
            return subprocess.run([exe] + cmd[1:], input=(prompt or "").encode("utf-8"),
                                  capture_output=True, timeout=timeout, cwd=cwd, shell=False)
        except OSError as e:  # noqa: PERF203 - retrying is the point
            if getattr(e, "winerror", None) not in _LAUNCH_ERRNOS and e.errno != 2:
                raise
            last = e
            time.sleep(0.7 * (i + 1))
    raise last


def run(prompt, system=None, model=None, effort=None, resume=None, persist=False,
        image_paths=None, workspace=None, timeout=1500, log=None):
    """One reasoning turn on Codex. Returns (text, usage, session_id).

    Raises RuntimeError when no answer came back — which, given exit code 0 on failure, is the
    only honest definition of failure available.
    """
    exe = resolve_exe()
    if not exe:
        raise RuntimeError("Codex CLI not found. Install it with: npm install -g @openai/codex")
    cwd = workspace or os.getcwd()
    os.makedirs(cwd, exist_ok=True)

    body = prompt or ""
    if system:
        body = "%s\n%s\n%s\n\n%s" % (_SYSTEM_HEADER, str(system).strip(), _SYSTEM_FOOTER, body)

    start = time.time()
    attempts = 2 if features_enabled() else 1
    for attempt in range(attempts):
        fd, out_file = tempfile.mkstemp(prefix="olivaw-codex-", suffix=".txt")
        os.close(fd)
        cmd = build_cmd(exe, model=model, effort=effort, resume=resume, persist=persist,
                        image_paths=image_paths, out_file=out_file)
        try:
            proc = _spawn(cmd, body, timeout, cwd)
            stdout = proc.stdout.decode("utf-8", errors="replace")
            stderr = proc.stderr.decode("utf-8", errors="replace")
            events = parse_events(stdout)
            text = events["text"]
            if not text.strip():
                # -o carries the last message even when the stream shape shifts; cheap insurance.
                try:
                    with open(out_file, encoding="utf-8", errors="replace") as fh:
                        text = fh.read()
                except Exception:  # noqa: BLE001
                    text = ""
        finally:
            try:
                os.unlink(out_file)
            except Exception:  # noqa: BLE001
                pass
        if (text or "").strip():
            break
        detail = _best_error(events, stderr, proc.returncode)
        # A rejected isolation flag must not cost the owner their agent: drop the flags and
        # try once more, rather than reporting a dead brain.
        if attempt == 0 and attempts == 2 and flags_rejected(detail + " " + (stderr or "")):
            disable_feature_flags(log)
            continue
        raise RuntimeError("codex: " + detail)

    u = events.get("usage") or {}
    usage = {"prompt_tokens": int(u.get("input_tokens", 0) or 0)
                              + int(u.get("cached_input_tokens", 0) or 0),
             "completion_tokens": int(u.get("output_tokens", 0) or 0)}
    usage["total_tokens"] = usage["prompt_tokens"] + usage["completion_tokens"]
    if log:
        log.info("codex ok in %.1fs (model=%s/%s, out_tokens=%s, in=%s cached=%s%s%s)",
                 time.time() - start, map_model(model) or "default", map_effort(effort),
                 usage["completion_tokens"], u.get("input_tokens", 0),
                 u.get("cached_input_tokens", 0), ", resumed" if resume else "",
                 "" if features_enabled() else ", TOOLS NOT DISABLED (sandbox only)")
    return text, usage, (events.get("thread_id") or None)


def console_flags(allow_fix):
    """Flags for the SOS console, where the two modes are the whole point.

    diagnose: identical to a bridge turn - no tools, read-only sandbox. That is what lets the
      console promise the owner that nothing on the machine can change.
    fix: the explicit opt-in. Tools ON and the sandbox bypassed, because repairing an install
      means editing files under it and restarting processes.
    """
    if allow_fix:
        return ["--dangerously-bypass-approvals-and-sandbox"]
    return tool_off_flags() + ["-c", 'sandbox_mode="read-only"',
                               "-c", 'approval_policy="never"']


# ── used by the wizard, kept here so there is one source of truth per engine ──
_VERSION_RE = re.compile(r"([0-9]+\.[0-9]+\.[0-9]+)")


def version():
    exe = resolve_exe()
    if not exe:
        return ""
    try:
        r = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=45,
                           encoding="utf-8", errors="replace")
        out = ((r.stdout or "") + " " + (r.stderr or "")).strip()
        m = _VERSION_RE.search(out)
        return (m.group(1) if m else out.splitlines()[0] if out else "")
    except Exception:  # noqa: BLE001
        return ""


def login_status():
    """Is Codex signed in? `codex login status` prints 'Not logged in' when it is not."""
    exe = resolve_exe()
    if not exe:
        return {"ok": False, "signed_in": False, "found": False,
                "detail": "Codex todavía no está instalado."}
    try:
        r = subprocess.run([exe, "login", "status"], capture_output=True, text=True, timeout=60,
                           encoding="utf-8", errors="replace")
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "signed_in": False, "found": True,
                "detail": "Codex no respondió: %s" % e}
    blob = ((r.stdout or "") + " " + (r.stderr or "")).lower()
    signed = ("not logged in" not in blob and "not signed" not in blob
              and "logged out" not in blob and r.returncode == 0)
    if not signed and os.environ.get("CODEX_API_KEY", "").strip():
        signed = True                    # an API key in the environment is a valid login too
    return {"ok": signed, "signed_in": signed, "found": True,
            "detail": ("Sesión de Codex activa." if signed else
                       "Aún no has iniciado sesión en Codex. Pulsa «Iniciar sesión».")}
