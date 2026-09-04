r"""An agent that believes it cannot browse will not browse.

Asked to use "the Claude Code Chrome extension", an agent replied that it could not,
because it is Hermes and not Claude Code. That answer is correct about the extension and
wrong about the agent: Hermes' core toolset carries twelve browser tools, every messaging
toolset inherits them, and the bridge log on this machine shows `browser_navigate` calls
going out for weeks. The owner concluded his agent had no browser at all.

Nothing was broken, so nothing could be fixed by testing behaviour. What this suite pins
instead is the two things that actually decide the outcome:

  1. the SKILL — it must say "you can browse", must handle the extension question by name,
     and must tell the agent not to open a window on someone's screen unasked;
  2. the SWITCH — enabling a real browser must never write config pointing at an endpoint
     that isn't there, because a profile aimed at a dead CDP port fails every browser call
     instead of falling back to the headless one that works;
  3. the SPLIT — every agent must get its OWN port and its OWN user-data directory. One
     endpoint shared by all of them was one Chrome window shared by all of them, and
     agent-browser attaches to that window's ACTIVE page: two agents browsing at once were
     two agents driving one tab. Measured before the split, with two sessions on one
     endpoint, the second agent's page vanished under the first one's. So this suite pins
     that two agents never resolve to the same port, that an install already collapsed
     onto 9222 migrates the extras off it, and that the main agent keeps its seat.

Plus a section asserting the claims in the skill against the Hermes installed here, so
this fails the day Hermes drops browser tools from a messaging toolset rather than the day
an owner is told his agent is blind.

Run: python tools/test_browser.py
"""

import io
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from wizard import browser_setup as bs  # noqa: E402

PASSED, FAILED = [], []


def check(name, cond, extra=""):
    (PASSED if cond else FAILED).append(name)
    print(("  ok   " if cond else "  FAIL ") + name +
          (("\n       " + str(extra)) if (extra and not cond) else ""))


def section(t):
    print("\n=== %s ===" % t)


class FakeCtl:
    def __init__(self, value="", ok=True):
        self.value, self.ok, self.sets = value, ok, []

    def config_get(self, key, hermes=None, profile=None):
        return self.value

    def config_set(self, key, value, hermes=None, profile=None):
        self.sets.append((key, value))
        if self.ok:
            self.value = value
        return {"ok": self.ok, "detail": "" if self.ok else "denied"}


class Configs:
    """A stand-in for hermes_ctl that writes real config.yaml files, one per agent.

    The port split has to be tested THROUGH the files rather than around them: which ports
    are already taken is read straight out of Hermes' YAML (one `hermes config get`
    subprocess per agent would cost seconds on a button press), so an in-memory fake would
    happily pass while the regex that does the reading was wrong.
    """

    def __init__(self, home):
        self.home = home

    def path(self, profile=None):
        if not profile or profile == "default":
            return os.path.join(self.home, "config.yaml")
        return os.path.join(self.home, "profiles", profile, "config.yaml")

    def config_get(self, key, hermes=None, profile=None):
        try:
            text = io.open(self.path(profile), encoding="utf-8").read()
        except OSError:
            return ""
        m = re.search(r"cdp_url:\s*(\S+)", text)
        return m.group(1) if m else ""

    def config_set(self, key, value, hermes=None, profile=None):
        p = self.path(profile)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with io.open(p, "w", encoding="utf-8", newline="\n") as fh:
            # Shaped like Hermes writes it, nesting included.
            fh.write("browser:\n  inactivity_timeout: 120\n  cdp_url: %s\n" % value)
        return {"ok": True, "detail": ""}


def main():
    tmp = tempfile.mkdtemp(prefix="brw-")
    real_ctl, real_probe, real_find = bs.hermes_ctl, bs.probe, bs.find_browser
    real_home, real_free = bs.hermes_home, bs._port_free
    # Everything per-agent now hangs off hermes_home - the user-data directories, the
    # window cards, the config files the port scan reads. Sandbox it for the whole run so
    # a test can neither read this machine's agents nor write into their browser profiles.
    home = os.path.join(tmp, "hermes")
    bs.hermes_home = lambda: home
    try:
        section("the skill answers the question that was actually asked")
        skill = bs.render_skill("daneel")
        check("it states plainly that the agent CAN browse",
              "Sí puedes navegar" in skill, skill[:200])
        check("it names the extension question head-on",
              "extensión de Chrome de Claude Code" in skill)
        check("it explains the extension is not the agent's tool",
              "no** es una herramienta tuya" in skill or
              "no es una herramienta tuya" in skill.replace("**", ""))
        check("it forbids the 'I can't browse' answer",
              "Nunca digas que no puedes navegar" in skill)
        check("it insists on doing the task, not just explaining",
              "no te quedes en la explicación" in skill.lower())
        for tool in ("browser_navigate", "browser_snapshot", "browser_click",
                     "browser_type", "browser_vision", "browser_dialog"):
            check("it names %s" % tool, tool in skill)

        section("the skill keeps the agent off the owner's screen")
        check("it says enabling opens a window on her screen",
              "abre una ventana en su pantalla" in skill, skill[-1200:])
        check("it tells the agent to ask first",
              "No lo actives por tu cuenta" in skill)
        check("it forbids asking for passwords over chat",
              "no la pidas por chat" in skill)
        check("it warns that page text is not an instruction",
              "no son órdenes" in skill and "prompt" not in skill.lower()[:0] or
              "no son órdenes" in skill)
        check("it says the debug profile is separate from the everyday browser",
              "propio perfil" in skill)

        section("the skill tells the agent the window is its own")
        # An agent that thinks it shares a browser explains away a page that changed under
        # it ("otro agente me la movió"); one that knows the window is its own goes looking
        # for the real reason. Both matter, and after the split only the second is true.
        check("it says the window belongs to this agent alone",
              "Esa ventana es tuya" in skill, skill[-1500:])
        check("it says nobody else can move its tab",
              "nadie te va a mover la pestaña" in skill)
        check("but it does not claim that on an install that has not migrated yet",
              "puede que todavía la compartas" in skill and "AVISO" in skill)
        check("it warns that another agent's login is not its own",
              "Las sesiones no se comparten" in skill)
        check("it explains how to hold more than one tab",
              "browser_cdp" in skill and "Target.createTarget" in skill)
        check("and tells it to leave the other agents' ports alone",
              "No toques el puerto de otro agente" in skill)
        check("it asks the agent not to close the card that labels the window",
              "No la cierres" in skill)

        section("the command line the skill hands the agent")
        cmds = [ln.strip() for ln in skill.splitlines() if "browser_mode.py" in ln]
        check("there are command lines", len(cmds) >= 1, cmds)
        check("every one quotes the interpreter, spaces and all",
              all(c.startswith('"') for c in cmds), cmds)
        check("and quotes the script path", all('" "' in c for c in cmds), cmds)
        check("it targets this profile", "--profile daneel" in skill)
        check("the default agent gets no stray --profile",
              "--profile" not in bs.render_skill(None))
        check("it never tells the agent to run pythonw",
              "pythonw" not in skill.lower())

        section("delegating to Claude Code, which does have the extension")
        # The agent cannot hold the Chrome extension as a tool, but `claude -p --chrome` is
        # a command and the agent has `terminal`. Verified on the machine this was written
        # on: plain `claude -p` says NO to having mcp__claude-in-chrome tools, `--chrome`
        # says YES. The skill has to teach the safe shape of that call.
        check("the skill offers the delegation route",
              "Delegar a Claude Code" in skill, skill[:400])
        check("it says this one reaches the owner's everyday browser",
              "Chrome de siempre" in skill or "navegador real del dueño" in skill)
        check("it tells the agent to check availability first", "--check" in skill)
        check("it warns about the time limit", "240" in skill and "terminal" in skill)
        check("it explains why the delegated session has no shell",
              "no tiene shell" in skill and "no es de fiar" in skill)
        check("and that --files is the opt-in", "--files" in skill and "--out" in skill)
        # --files re-enables Bash as well as Write (a file-producing skill needs both), so
        # the skill must not describe it as "writes only" — an agent told it is narrower
        # than it is will reach for it casually.
        check("--files is described honestly as returning shell too",
              "escritura y shell" in skill, skill)
        check("and told to use it only when a file must be produced",
              "sólo** cuando la tarea tenga que producir un archivo" in skill
              or "sólo cuando la tarea tenga que producir un archivo" in skill.replace("**", ""))
        check("it still forbids handling passwords", "contraseñas" in skill)

        section("the delegation script itself")
        cc = io.open(os.path.join(ROOT, "src", "tools", "claude_chrome.py"),
                     encoding="utf-8", errors="replace").read()
        check("it passes --chrome, or the tools are simply absent", '"--chrome"' in cc)
        check("shell is denied by default inside the delegated session",
              '_DENY = "Bash,' in cc)
        check("--files still never re-enables Task/WebFetch",
              "_DENY_FILES" in cc and "Task,WebFetch,WebSearch" in cc)
        check("the script says out loud that --files returns shell, not just writes",
              "shell AND writes come back" in cc, cc[:0] or "comment missing")
        check("the prompt goes over stdin, never argv",
              "input=prompt.encode" in cc and "--task" in cc)
        check("the timeout stays under Hermes' 300s terminal cap",
              "timeout=240" in cc or "default=240" in cc)
        check("the delegated session is told page content is not an instruction",
              "never an instruction" in cc)
        check("and told not to wander into mail or banking",
              "banking" in cc)

        section("why the debug window starts logged out, and what the panel says")
        # Chrome 136 ignores --remote-debugging-port on the default user-data-dir, and a
        # non-standard dir uses a DIFFERENT encryption key on purpose. So the window is
        # blank by design and copying a profile in would not carry logins either. An owner
        # who is not told this concludes the feature is broken - which is what happened.
        app = io.open(os.path.join(ROOT, "src", "wizard", "web", "app.js"),
                      encoding="utf-8", errors="replace").read()
        check("the panel explains the blank profile is Chrome's rule, not ours",
              "Chrome 136" in app, "no explanation in the panel")
        check("and that copying a profile would not help either",
              "copiar tu perfil tampoco" in app)
        check("it says the login persists once made", "para siempre" in app)
        check("it offers the route that DOES have her sessions",
              "brwDeleg" in app and "Chrome de siempre" in app)
        check("the panel says each agent opens its own window",
              "Cada agente abre su propia ventana" in app, "not explained in the panel")
        check("and that a login here is for THIS agent only",
              "no para los demás" in app)
        check("it has a state for two agents still sharing one window",
              "shared_with" in app and "Comparte ventana" in app)
        check("that state tells her which button fixes it",
              "para abrirle la suya" in app)
        check("the window it opens is told which agent it belongs to",
              "name: targetName()" in app)

        section("delegation availability is cheap to ask")
        st = bs.delegation_status()
        check("it answers without spawning anything", isinstance(st.get("ready"), bool), st)
        check("it reports each precondition separately, so the reason is actionable",
              all(k in st for k in ("claude", "installed", "paired")), st)
        check("and carries a sentence the panel can show", bool(st.get("detail")), st)

        section("installing the skill")
        home = os.path.join(tmp, "home")
        got = bs.install_skill("daneel", home=home)
        check("it is written", got["ok"] and got["changed"], got)
        check("installing again rewrites nothing", not bs.install_skill("daneel", home=home)["changed"])
        check("it lands where Hermes looks for skills",
              got["path"].endswith(os.path.join("skills", bs.SKILL_NAME, "SKILL.md")), got["path"])

        section("a real browser is never configured unless it answers")
        # The failure that matters: writing browser.cdp_url for a port nothing is serving
        # turns every browser call into an error, where doing nothing would have left the
        # working headless browser in place.
        bs.find_browser = lambda: (None, None)
        ctl = FakeCtl()
        bs.hermes_ctl = ctl
        bs.probe = lambda url=None, timeout=1.0: {"ok": False, "browser": "", "detail": "no"}
        res = bs.enable(profile="daneel")
        check("with no browser installed it refuses", res["ok"] is False, res)
        check("and writes NO config at all", not ctl.sets, ctl.sets)
        check("and says so in plain language", "No encontré" in res["detail"], res)

        bs.find_browser = lambda: ("Chrome", os.path.join(tmp, "chrome.exe"))
        launched = {"n": 0}

        def fake_popen(args, **kw):
            launched["n"] += 1
            class P:
                pass
            return P()
        real_popen = bs.subprocess.Popen
        bs.subprocess.Popen = fake_popen
        try:
            ctl = FakeCtl()
            bs.hermes_ctl = ctl
            res = bs.enable(profile="daneel")
            check("a browser that never comes up is not written either",
                  res["ok"] is False and not ctl.sets, (res, ctl.sets))
            check("it was at least attempted", launched["n"] == 1, launched)

            # now the endpoint answers
            bs.probe = lambda url=None, timeout=1.0: {"ok": True, "browser": "Chrome/141",
                                                      "detail": "Chrome/141"}
            ctl = FakeCtl()
            bs.hermes_ctl = ctl
            res = bs.enable(profile="daneel")
            check("a live endpoint IS written", res["ok"] and ctl.sets, (res, ctl.sets))
            check("under the key Hermes actually reads",
                  ctl.sets[0][0] == "browser.cdp_url", ctl.sets)
            check("pointing at loopback, not a public host",
                  ctl.sets[0][1].startswith("http://127.0.0.1:"), ctl.sets)
            check("and not at the main agent's port, which is not this agent's",
                  ctl.sets[0][1] != "http://127.0.0.1:9222", ctl.sets)
            before = launched["n"]
            bs.enable(profile="daneel")
            check("a second enable does not open a second window",
                  launched["n"] == before, launched)

            section("the debug browser never uses the owner's everyday profile")
            args_seen = []
            bs.subprocess.Popen = lambda args, **kw: (args_seen.append(args) or type("P", (), {})())
            bs.probe = lambda url=None, timeout=1.0: {"ok": False, "browser": "", "detail": "no"}
            bs.launch()
            flat = " ".join(args_seen[0]) if args_seen else ""
            check("--user-data-dir is always passed", "--user-data-dir=" in flat, flat)
            check("and it is Hermes' own debug dir, not the default profile",
                  bs.data_dir() in flat, flat)
            check("remote debugging is on the loopback default port",
                  "--remote-debugging-port=9222" in flat, flat)
        finally:
            bs.subprocess.Popen = real_popen

        # ── the split: one window per agent ──────────────────────────────────
        # The failure this section exists for, measured on this machine before the split:
        # two agent-browser sessions on ONE endpoint, agent B opens a page, and a moment
        # later that tab is showing agent A's page instead. B's navigation is simply gone.
        # agent-browser attaches to the window's ACTIVE page, so one endpoint is one tab no
        # matter how many agents point at it.
        section("each agent gets its own window, not a share of one")
        bs._port_free = lambda port: True           # deterministic: no real ports involved
        cfg = Configs(home)
        bs.hermes_ctl = cfg
        live_ports = set()

        def port_probe(url=None, timeout=1.0):
            if bs._port_of(url or "") in live_ports:
                return {"ok": True, "browser": "Chrome/152", "detail": "Chrome/152"}
            return {"ok": False, "browser": "", "detail": "nothing there"}

        opened = []

        def open_popen(args, **kw):
            opened.append(list(args))
            for a in args:
                if a.startswith("--remote-debugging-port="):
                    live_ports.add(int(a.split("=", 1)[1]))
            return type("P", (), {})()

        bs.probe = port_probe
        bs.subprocess.Popen = open_popen
        try:
            main_a = bs.enable(profile=None, name="Agente principal")
            extra = bs.enable(profile="daneel", name="Daneel")
            third = bs.enable(profile="giskard", name="Giskard")
            check("the main agent keeps the well-known port",
                  main_a["port"] == 9222, main_a)
            check("the second agent is given a port of its own",
                  extra["port"] == 9223, extra)
            check("and the third another one still",
                  third["port"] == 9224, third)
            check("no two agents ever resolve to the same endpoint",
                  len({main_a["cdp_url"], extra["cdp_url"], third["cdp_url"]}) == 3,
                  [main_a["cdp_url"], extra["cdp_url"], third["cdp_url"]])
            check("each one opened its own window",
                  len(opened) == 3, len(opened))
            check("every agent drives its own user-data directory",
                  len({main_a["data_dir"], extra["data_dir"], third["data_dir"]}) == 3,
                  [main_a["data_dir"], extra["data_dir"], third["data_dir"]])
            check("the main agent's profile directory is where it always was",
                  main_a["data_dir"] == os.path.join(home, "chrome-debug"), main_a)
            check("an extra agent's lives under its own profile home",
                  extra["data_dir"] == os.path.join(home, "profiles", "daneel",
                                                    "chrome-debug"), extra)
            flats = [" ".join(a) for a in opened]
            check("each window is launched on the port it was assigned",
                  all("--remote-debugging-port=%d" % p in f for p, f in
                      zip((9222, 9223, 9224), flats)), flats)
            check("and with the directory that belongs to it",
                  all(("--user-data-dir=" + d) in f for d, f in zip(
                      (main_a["data_dir"], extra["data_dir"], third["data_dir"]), flats)),
                  flats)
            check("asking again gives an agent the SAME port, so its window survives",
                  bs.port_for("daneel") == extra["port"], bs.port_for("daneel"))

            section("two blank Chrome windows are indistinguishable, so each says whose it is")
            card = io.open(bs.card_path("daneel"), encoding="utf-8").read()
            check("the card names the agent whose window it is", "Daneel" in card, card[:200])
            check("it says the other agents cannot touch this tab",
                  "ninguno te va a cambiar la pestaña" in card, card)
            check("and warns that a login here does not sign the others in",
                  "no se comparten" in card, card)
            check("it is self-contained — no network, no CDN",
                  "http://" not in card and "https://" not in card, card)
            # A card naming a port other than the window it sits in is worse than none.
            check("it names the port this very window was launched on",
                  str(extra["port"]) in card, card)
            args = opened[1]
            blank, cardarg = args.index("about:blank"), [
                i for i, a in enumerate(args) if a.startswith("file:///")][0]
            # Verified against agent-browser 0.26: it attaches to the window's ACTIVE page,
            # which is the LAST url Chrome was given. Card first, blank last, so the agent
            # navigates the blank one and the card survives its first browsing task.
            check("the card is opened first and a blank tab last, so the agent takes the blank",
                  cardarg < blank, args)

            section("an install where every agent shares one window migrates off it")
            for prof in (None, "daneel"):
                cfg.config_set(bs.CONFIG_KEY, "http://127.0.0.1:9222", profile=prof)
            check("the extra agent is told it is sharing",
                  bs.status("daneel")["shared_with"] == ["default"], bs.status("daneel"))
            check("and so is the main agent, from its own panel",
                  bs.status(None)["shared_with"] == ["daneel"], bs.status(None))
            check("the main agent keeps the window it already has",
                  bs.port_for(None) == 9222)
            check("the extra one is moved off it", bs.port_for("daneel") != 9222)
            moved = bs.enable(profile="daneel", name="Daneel")
            check("enabling actually writes the new endpoint",
                  moved["ok"] and moved["port"] != 9222, moved)
            check("and the warning is gone afterwards",
                  bs.status("daneel")["shared_with"] == [], bs.status("daneel"))
            check("without dragging the main agent anywhere",
                  bs.status(None)["shared_with"] == [] and
                  bs.status(None)["port"] == 9222, bs.status(None))
        finally:
            bs.subprocess.Popen = real_popen
            bs._port_free = real_free

        section("a port someone else is already serving is not offered to an agent")
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.bind(("127.0.0.1", 0))
            sock.listen(1)
            busy = sock.getsockname()[1]
            check("a listening port reads as taken", bs._port_free(busy) is False, busy)
        finally:
            sock.close()
        check("and a free one reads as free", bs._port_free(busy) is True, busy)

        section("turning it back off")
        ctl = FakeCtl(value="http://127.0.0.1:9222")
        bs.hermes_ctl = ctl
        res = bs.disable(profile="daneel")
        check("the key is cleared", res["ok"] and ctl.sets == [("browser.cdp_url", "")], ctl.sets)
        check("and the mode is reported as headless", res["mode"] == "headless")

        section("status tells the truth in each state")
        bs.probe = lambda url=None, timeout=1.0: {"ok": True, "browser": "Chrome/141",
                                                  "detail": "Chrome/141"}
        bs.hermes_ctl = FakeCtl(value="http://127.0.0.1:9222")
        st = bs.status(profile="daneel")
        check("configured + live = connected", st["mode"] == "cdp" and st["connected"], st)
        bs.probe = lambda url=None, timeout=1.0: {"ok": False, "browser": "", "detail": "gone"}
        st = bs.status(profile="daneel")
        check("configured + dead = cdp but NOT connected",
              st["mode"] == "cdp" and not st["connected"], st)
        bs.hermes_ctl = FakeCtl(value="")
        st = bs.status(profile="daneel")
        check("unconfigured = headless", st["mode"] == "headless" and not st["connected"], st)
        bs.hermes_ctl = FakeCtl(value="not set")
        st = bs.status(profile="daneel")
        check("a CLI that prints 'not set' is not mistaken for a URL",
              st["mode"] == "headless", st)

        section("a squatter on 9222 is not mistaken for a browser")
        bs.probe = real_probe
        check("an endpoint with no Browser field is rejected",
              bs.probe("http://127.0.0.1:1")["ok"] is False)
    finally:
        bs.hermes_ctl, bs.probe, bs.find_browser = real_ctl, real_probe, real_find
        bs.hermes_home, bs._port_free = real_home, real_free

    section("the CLI the skill documents")
    script = os.path.join(ROOT, "src", "tools", "browser_mode.py")

    def run(*args):
        return subprocess.run([sys.executable, script] + list(args), capture_output=True,
                              text=True, timeout=180, encoding="utf-8", errors="replace")

    p = run("--help")
    check("--help works", p.returncode == 0 and "browser_mode" in p.stdout, p.stderr[-300:])
    p = run("status", "--json")
    check("status --json is machine-readable and does not crash on accents",
          p.returncode == 0 and json.loads(p.stdout)["ok"], p.stdout[:200] + p.stderr[-300:])
    p = run("status")
    check("status prints Spanish without a UnicodeEncodeError",
          p.returncode == 0 and "navegador" in p.stdout.lower(),
          p.stdout[:200] + p.stderr[-300:])
    p = run("nonsense")
    check("an unknown action is a usage error, not a silent default", p.returncode == 2)

    section("Hermes on THIS machine still gives messaging agents a browser")
    hermes_src = None
    for base in (os.environ.get("LOCALAPPDATA", ""), os.path.expanduser("~")):
        cand = os.path.join(base, "hermes", "hermes-agent")
        if os.path.isdir(cand):
            hermes_src = cand
            break
    if not hermes_src:
        print("  skip (no Hermes checkout found)")
    else:
        ts = io.open(os.path.join(hermes_src, "toolsets.py"),
                     encoding="utf-8", errors="replace").read()
        core = ts[ts.find("_HERMES_CORE_TOOLS = ["):]
        core = core[:core.find("]")]
        for tool in ("browser_navigate", "browser_snapshot", "browser_click",
                     "browser_type", "browser_cdp"):
            check("%s is in Hermes' CORE toolset" % tool, '"%s"' % tool in core, core[:200])
        for platform_key in ("hermes-telegram", "hermes-whatsapp"):
            i = ts.find('"%s": {' % platform_key)
            check("%s inherits the core tools (so it can browse)" % platform_key,
                  i > 0 and "_HERMES_CORE_TOOLS" in ts[i:i + 400], platform_key)
        bt = io.open(os.path.join(hermes_src, "tools", "browser_tool.py"),
                     encoding="utf-8", errors="replace").read()
        check("browser.cdp_url is still the persistent key we write",
              'browser_cfg.get("cdp_url"' in bt)
        check("BROWSER_CDP_URL env still takes precedence over it",
              'os.environ.get("BROWSER_CDP_URL"' in bt)
        check("private/internal URLs are still blocked by default",
              '"allow_private_urls": False' in io.open(
                  os.path.join(hermes_src, "hermes_cli", "config.py"),
                  encoding="utf-8", errors="replace").read())

        section("the bridge still keeps MCP off, which is why extensions cannot work")
        cb = io.open(os.path.join(ROOT, "src", "claude_bridge.py"),
                     encoding="utf-8", errors="replace").read()
        check("--strict-mcp-config is still passed", '"--strict-mcp-config"' in cb)
        check("and the MCP config is still the empty one", "EMPTY_MCP" in cb)
        check("the skill's explanation matches that reality",
              "Hermes, no Claude Code" in bs.render_skill(None))

        section("this machine, right now")
        st = bs.status()
        print("       mode=%s  browser_found=%s (%s)  connected=%s"
              % (st["mode"], st["browser_found"], st["browser_label"], st["connected"]))

    shutil.rmtree(tmp, ignore_errors=True)
    print("\n%d passed, %d failed" % (len(PASSED), len(FAILED)))
    for f in FAILED:
        print("  - " + f)
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
