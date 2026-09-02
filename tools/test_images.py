r"""Three ways to make an image, and the agent was only ever told about the expensive one.

A Claude-Code brain cannot draw. Hermes can, but only after the owner picks a provider and
pastes an API key — so in practice a fresh agent answers that it cannot make images and
that is the end of it. Two free routes existed the whole time and nothing surfaced either:

  * a **Codex** brain has its own `image_gen` (gpt-image-2), billed to the owner's ChatGPT
    subscription, needing no OPENAI_API_KEY;
  * **any** brain can drive Gemini in the agent's own browser window, where the owner
    signed in once.

The subtle thing this suite exists to protect is WHY Codex's own tool is allowed when the
Claude Code Chrome extension was refused one release earlier. It is not a double standard:
a tool the brain can only *call* is dead here, because Hermes owns the catalog and drops
calls it does not recognise. Codex's image tool produces a FILE, and files already have a
way home — the MEDIA: contract. Break that distinction and you either lose the capability
or resurrect the empty-reply bug.

Run: python tools/test_images.py
"""

import io
import json
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from wizard import image_setup as I  # noqa: E402

PASSED, FAILED = [], []


def check(name, cond, extra=""):
    (PASSED if cond else FAILED).append(name)
    print(("  ok   " if cond else "  FAIL ") + name +
          (("\n       " + str(extra)) if (extra and not cond) else ""))


def section(t):
    print("\n=== %s ===" % t)


def make_install(root, engine=None, agents=()):
    os.makedirs(root, exist_ok=True)
    io.open(os.path.join(root, "updater.config.json"), "w", encoding="utf-8").write(
        json.dumps({"repo": "x/y", "env": ({"OLIVAW_ENGINE": engine} if engine else {})}))
    io.open(os.path.join(root, "agents.json"), "w", encoding="utf-8").write(
        json.dumps({"agents": list(agents)}))
    return root


def main():
    tmp = tempfile.mkdtemp(prefix="img-")
    try:
        section("which brain this agent runs on")
        claude_root = make_install(os.path.join(tmp, "claude-install"))
        codex_root = make_install(os.path.join(tmp, "codex-install"), engine="codex")
        mixed = make_install(os.path.join(tmp, "mixed"), agents=[
            {"slug": "daneel", "profile": "daneel", "port": 8792},
            {"slug": "blanca", "profile": "blanca", "port": 8794, "engine": "codex"}])
        check("the default agent's engine comes from updater.config",
              I.engine_of(None, claude_root) == "claude", I.engine_of(None, claude_root))
        check("a Codex install is detected", I.engine_of(None, codex_root) == "codex")
        check("an extra agent can name its own engine",
              I.engine_of("blanca", mixed) == "codex", I.engine_of("blanca", mixed))
        check("and one that does not, inherits Claude",
              I.engine_of("daneel", mixed) == "claude", I.engine_of("daneel", mixed))
        check("an unreadable install never crashes the panel — it assumes Claude",
              I.engine_of(None, os.path.join(tmp, "nope")) == "claude")

        section("the route offered depends on the brain")
        real = I._browser_mode
        I._browser_mode = lambda profile=None: ("headless", False, True)
        try:
            st = I.status(profile=None, install_dir=codex_root)
            check("a Codex agent is told images are already included",
                  st["recommended"] == "codex", st["recommended"])
            codex_route = [r for r in st["routes"] if r["id"] == "codex"][0]
            check("and that route is marked ready with nothing to do",
                  codex_route["available"] and codex_route["ready"], codex_route)
            check("its cost line says no key is needed",
                  "sin clave" in codex_route["cost"], codex_route["cost"])

            st = I.status(profile=None, install_dir=claude_root)
            check("a Claude agent is NOT offered the Codex route",
                  not [r for r in st["routes"] if r["id"] == "codex"][0]["available"])
            check("it is pointed at the free browser route instead",
                  st["recommended"] == "gemini-browser", st["recommended"])
            gem = [r for r in st["routes"] if r["id"] == "gemini-browser"][0]
            check("which is honest that it is not ready yet", gem["ready"] is False, gem)
            check("and says what is missing", "navegador real" in gem["note"], gem["note"])

            I._browser_mode = lambda profile=None: ("cdp", True, True)
            st = I.status(profile=None, install_dir=claude_root)
            check("with the real browser connected it reports ready",
                  [r for r in st["routes"] if r["id"] == "gemini-browser"][0]["ready"] is True)

            I._browser_mode = lambda profile=None: ("headless", False, False)
            st = I.status(profile=None, install_dir=claude_root)
            check("with no browser on the machine it falls back to the paid route",
                  st["recommended"] == "hermes-provider", st["recommended"])
            check("the paid route is never claimed 'ready' — only the owner knows",
                  [r for r in st["routes"] if r["id"] == "hermes-provider"][0]["ready"] is None)
        finally:
            I._browser_mode = real

        section("the Gemini skill talks to tools an Olivaw agent actually has")
        skill = I.render_skill("daneel")
        for tool in ("browser_navigate", "browser_snapshot", "browser_type",
                     "browser_press", "browser_get_images", "browser_vision"):
            check("it uses %s" % tool, tool in skill)
        # The owner's own Claude Code skill for this drives Chrome through MCP tools that
        # an Olivaw agent does not have. Copying it verbatim would be a skill made of
        # tool names nothing here can execute.
        for alien in ("tabs_context_mcp", "computer {", "ToolSearch", "mcp__"):
            check("it does NOT reference the Claude Code tool '%s'" % alien.strip(" {"),
                  alien not in skill)
        check("it sends the result home through the MEDIA contract", "MEDIA:" in skill)
        check("it tells the agent to verify the download, not assume it",
              "0 bytes" in skill or "pesa algo" in skill, skill[-1500:])
        check("a screenshot fallback is labelled as a screenshot, not the image",
              "es una captura" in skill)

        section("the skill protects the owner's browser session")
        check("it refuses to handle her password", "nunca" in skill.lower() and
              "contraseña" in skill)
        check("it stops and asks when Gemini wants a login",
              "para aquí" in skill.lower() or "para aqu" in skill.lower())
        check("it keeps the agent out of the rest of her session",
              "No cierres pestañas ajenas" in skill)
        check("page content is data, not orders", "no órdenes tuyas" in skill)
        check("it checks the browser mode before trying anything",
              "browser_mode.py" in skill and "status" in skill)

        section("the command line the skill hands the agent")
        cmds = [ln.strip() for ln in skill.splitlines() if "browser_mode.py" in ln]
        check("every command quotes the interpreter",
              cmds and all(c.startswith('"') for c in cmds), cmds)
        check("it targets this profile", "--profile daneel" in skill)
        check("the default agent gets no stray --profile",
              "--profile" not in I.render_skill(None))
        check("it never names pythonw", "pythonw" not in skill.lower())

        section("installing")
        home = os.path.join(tmp, "home")
        got = I.install_skill("daneel", home=home)
        check("the skill is written", got["ok"] and got["changed"], got)
        check("a second install rewrites nothing",
              not I.install_skill("daneel", home=home)["changed"])

        section("a Codex agent is NOT given browser instructions it does not need")
        res = I.ensure_all(agents=[{"slug": "blanca", "profile": "blanca"}],
                           install_dir=codex_root)
        blanca = [r for r in res if r["profile"] == "blanca"][0]
        default = [r for r in res if r["profile"] == "default"][0]
        check("the Codex default agent is skipped",
              default.get("reason") == "codex-builtin", default)
        check("and so is a Codex extra agent",
              blanca.get("reason") == "codex-builtin", blanca)
        res = I.ensure_all(agents=[{"slug": "daneel", "profile": "daneel"}],
                           install_dir=mixed)
        check("but a Claude agent does get it",
              [r for r in res if r["profile"] == "daneel"][0].get("reason") != "codex-builtin")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    section("why Codex's own tool is allowed where the Chrome extension was not")
    # The distinction is load-bearing. A brain-side tool that Hermes must EXECUTE is dead
    # (the call is dropped, the user gets an empty reply). One that writes a FILE is fine,
    # because MEDIA: already carries files home. If the runtime clause ever stops saying
    # so, a Codex agent goes back to claiming it cannot make images.
    # Assert the prompt the brain actually receives, not the source that builds it: the
    # clause is assembled from adjacent string literals, so grepping the file would pass
    # on text no model ever sees.
    import subprocess

    def prompt_for(engine):
        env = dict(os.environ, OLIVAW_ENGINE=engine)
        p = subprocess.run(
            [sys.executable, "-c",
             "import sys; sys.path.insert(0, r'%s'); import claude_bridge as B; "
             "print(B.RUNTIME_SYSTEM_PROMPT)" % os.path.join(ROOT, "src")],
            capture_output=True, text=True, env=env, timeout=180,
            encoding="utf-8", errors="replace")
        return p.stdout, p.stderr

    codex_prompt, err = prompt_for("codex")
    claude_prompt, _ = prompt_for("claude")
    check("the bridge imports under both engines", bool(codex_prompt.strip()), err[-300:])
    check("a Codex brain is told it can generate images itself",
          "generate images yourself" in codex_prompt, codex_prompt[-400:])
    check("and that it needs no API key", "needs no API key" in codex_prompt,
          codex_prompt[-400:])
    check("it is told to hand back the real path via MEDIA:",
          "MEDIA:<path>" in codex_prompt)
    check("and forbidden from inventing one", "never invent one" in codex_prompt)
    check("everything else still goes through the runtime",
          "everything else goes through the runtime" in codex_prompt)
    check("a CLAUDE brain is told none of this - it has no such tool",
          "generate images yourself" not in claude_prompt, claude_prompt[-300:])
    check("and the shared contract is otherwise identical",
          claude_prompt.strip() and codex_prompt.startswith(claude_prompt.strip()[:200]))

    ce = io.open(os.path.join(ROOT, "src", "codex_engine.py"),
                 encoding="utf-8", errors="replace").read()
    check("image_gen is NOT in the disabled feature list",
          "image_gen" not in ce.split("TOOL_FEATURES = (")[1].split(")")[0],
          ce.split("TOOL_FEATURES = (")[1].split(")")[0])
    check("the read-only sandbox was NOT loosened for this",
          'sandbox_mode="read-only"' in ce
          and 'sandbox_mode="workspace-write"' not in ce
          and 'sandbox_mode="danger-full-access"' not in ce)
    check("and the open question is written down rather than guessed at",
          "undocumented and untested" in ce)

    print("\n%d passed, %d failed" % (len(PASSED), len(FAILED)))
    for f in FAILED:
        print("  - " + f)
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
