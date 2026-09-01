r"""An agent whose conversation never ends is an agent that empties its owner's quota.

Hermes starts a new profile on "never restart the conversation" (session_reset.mode = none
since July 2026) and "summarise at half the window" (compression.threshold = 0.50). Olivaw
advertises a 1M window, so an agent created by the wizard dragged up to half a million
tokens of thread into every single turn and nothing anywhere said so. The owner's own agent
did not, because he had configured it by hand, long ago - which is exactly why the problem
was invisible from inside the product.

Three things have to hold, and each has a section here:

  1. we read the policy a profile is actually running under, without a YAML library and
     without nine subprocess launches;
  2. a profile that has never been configured gets one, and a profile that HAS been - even
     to say "never restart" - is left alone;
  3. a written policy is not a live policy: the gateway reads it once at boot, so something
     has to restart it, and that something must never be the agent answering a question.

The last section checks our mirrored copy of Hermes' own numbers against the Hermes on this
machine, so this suite fails the day they move rather than the day an owner's bill does.

Run: python tools/test_context_policy.py
"""

import io
import json
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from wizard import context_policy as cp  # noqa: E402

PASSED, FAILED = [], []


def check(name, cond, extra=""):
    (PASSED if cond else FAILED).append(name)
    print(("  ok   " if cond else "  FAIL ") + name +
          (("\n       " + str(extra)) if (extra and not cond) else ""))


def section(t):
    print("\n=== %s ===" % t)


class FakeCtl:
    """Stands in for the hermes CLI: records every `config set` and answers status."""

    def __init__(self, path, gateway_running=True, fail=()):
        self.path = path
        self.sets = []
        self.gateway_running = gateway_running
        self.restarts = 0
        self.fail = set(fail)

    def config_path(self, hermes=None, profile=None):
        return self.path

    def config_set(self, key, value, hermes=None, profile=None):
        self.sets.append((key, value))
        if key in self.fail:
            return {"ok": False, "detail": "nope"}
        return {"ok": True, "detail": "set"}

    def gateway_status(self, hermes=None, profile=None):
        return {"ok": True, "running": self.gateway_running}

    def gateway_restart_safe(self, hermes=None, profile=None, timeout=90):
        self.restarts += 1
        return {"ok": True, "detail": "restarted"}

    def available(self, hermes=None):
        return True


FRESH = """\
model:
  default: claude-code
  provider: custom
  base_url: http://127.0.0.1:8792/v1
  context_length: 1000000
_config_version: 33
"""

CONFIGURED = """\
model:
  default: claude-code
  context_length: 1000000
session_reset:
  mode: both
  idle_minutes: 150
  at_hour: 4
compression:
  enabled: true
  threshold: 0.1
  target_ratio: 0.2
  protect_last_n: 20
  protect_first_n: 3
"""

OPTED_OUT = """\
model:
  context_length: 1000000
session_reset:
  mode: none
"""


def write(tmp, text, name="config.yaml"):
    p = os.path.join(tmp, name)
    io.open(p, "w", encoding="utf-8", newline="\n").write(text)
    return p


def main():
    tmp = tempfile.mkdtemp(prefix="ctxpol-")
    real_ctl = cp.hermes_ctl
    try:
        section("reading a profile Olivaw has never touched - the case that cost money")
        st = cp.read(path=write(tmp, FRESH))
        check("it is recognised as unconfigured", st["configured"] is False)
        check("which means: never restarts", st["policy"]["mode"] == "none", st["policy"])
        check("and summarises only at half the window",
              st["policy"]["compact_at"] == 0.50, st["policy"])
        check("half a million tokens, spelled out",
              st["trigger_tokens"] == 500_000, st["trigger_tokens"])
        check("the summary says so in the owner's language",
              "no se reinicia" in st["summary"] and "500.000" in st["summary"], st["summary"])

        section("reading a profile that has one")
        st = cp.read(path=write(tmp, CONFIGURED, "c2.yaml"))
        check("recognised as configured", st["configured"] is True)
        check("2.5 hours idle", st["policy"]["idle_minutes"] == 150)
        check("both idle and daily", st["policy"]["mode"] == "both")
        check("summarises at 10%", st["policy"]["compact_at"] == 0.1)
        check("= 100k tokens", st["trigger_tokens"] == 100_000, st["trigger_tokens"])
        check("and it maps onto a preset the owner can recognise",
              st["preset"] == "equilibrado", st["preset"])
        check("keys absent from the file fall back to Hermes' default, not to ours",
              st["policy"]["notify"] is True)

        section("the mini YAML reader only claims what it can actually parse")
        weird = """\
session_reset:
  mode: "both"      # con comillas y comentario
  idle_minutes: 200
  nested:
    something: 1
  at_hour: 5
compression:
  enabled: false
other_top_level: 3
"""
        blk = cp.read_block(weird, "session_reset")
        check("quotes and trailing comments are stripped", blk["mode"] == "both", blk)
        check("numbers become numbers", blk["idle_minutes"] == 200, blk)
        check("a nested mapping is skipped, not guessed at", "nested" not in blk, blk)
        check("keys after the nested block are still read", blk["at_hour"] == 5, blk)
        check("it stops at the next top-level key", "other_top_level" not in blk, blk)
        check("booleans survive", cp.read_block(weird, "compression")["enabled"] is False)
        check("an absent block is None, not empty", cp.read_block(weird, "nope") is None)
        check("'empty block' and 'absent block' stay distinguishable",
              cp.read_block("session_reset:\nmodel:\n  a: 1\n", "session_reset") == {})

        section("values the owner types are bounded, and she is told")
        p, notes = cp.normalize({"idle_minutes": 1, "at_hour": 99, "compact_at": 5})
        check("a 1-minute window is raised to the floor", p["idle_minutes"] == 15, p)
        check("an impossible hour is clamped", p["at_hour"] == 23, p)
        check("a threshold above 1 is clamped", p["compact_at"] == 0.90, p)
        check("and each correction is explained", len(notes) >= 3, notes)
        p, notes = cp.normalize({"mode": "sometimes"})
        check("an unknown mode falls back rather than reaching Hermes",
              p["mode"] == "both" and notes, (p, notes))
        p, _ = cp.normalize({"idle_minutes": "no"})
        check("a non-number does not raise", p["idle_minutes"] == cp.DEFAULTS["idle_minutes"])
        check("normalize never returns a key Hermes does not know",
              set(p) == set(cp.DEFAULTS), set(p) ^ set(cp.DEFAULTS))

        section("what the percentage really means, including Hermes' own floors")
        check("10% of 1M is 100k", cp.trigger_tokens(0.10, 1_000_000) == 100_000)
        check("but nothing triggers under 64k, whatever the percentage says",
              cp.trigger_tokens(0.03, 1_000_000) == 64_000,
              cp.trigger_tokens(0.03, 1_000_000))
        check("a window under 512k is raised to 75% by Hermes, so we say 75% too",
              cp.trigger_tokens(0.10, 200_000) == 150_000,
              cp.trigger_tokens(0.10, 200_000))
        check("and a window so small the floor would swallow it triggers at 85%",
              cp.trigger_tokens(0.10, 64_000) == int(64_000 * 0.85),
              cp.trigger_tokens(0.10, 64_000))
        check("an unknown window yields no claim at all",
              cp.trigger_tokens(0.10, None) is None)

        section("every preset is a real, legal policy")
        for pr in cp.PRESETS:
            got = cp.preset_policy(pr["id"])
            norm, notes = cp.normalize(got)
            check("preset '%s' survives validation unchanged" % pr["id"],
                  norm == got and not notes, (pr["id"], notes))
            check("preset '%s' round-trips to its own name" % pr["id"],
                  cp.preset_of(got) == pr["id"], cp.preset_of(got))
        check("Olivaw's default IS the recommended preset",
              cp.preset_of(cp.normalize(cp.DEFAULTS)[0]) == "equilibrado")
        check("and it is what the owner's own agent runs: 2.5h + 10%",
              cp.DEFAULTS["idle_minutes"] == 150 and cp.DEFAULTS["compact_at"] == 0.10)

        section("writing: a fresh profile gets every key spelled out")
        ctl = FakeCtl(write(tmp, FRESH, "w1.yaml"))
        cp.hermes_ctl = ctl
        res = cp.apply(cp.DEFAULTS, profile="daneel")
        keys = dict(ctl.sets)
        check("the write succeeds", res["ok"], res)
        check("all nine keys are written, not just the ones that differ",
              len(ctl.sets) == 9, ctl.sets)
        check("mode is set to both", keys.get("session_reset.mode") == "both", keys)
        check("idle is 150 minutes", keys.get("session_reset.idle_minutes") == "150", keys)
        check("the threshold is written as a plain decimal Hermes will parse as a float",
              keys.get("compression.threshold") == "0.1", keys)
        check("booleans are written the way YAML wants them",
              keys.get("compression.enabled") == "true", keys)
        check("nothing is written by hand into the file itself",
              io.open(ctl.path, encoding="utf-8").read() == FRESH)

        section("writing: an already-configured profile only gets the difference")
        ctl = FakeCtl(write(tmp, CONFIGURED, "w2.yaml"))
        cp.hermes_ctl = ctl
        res = cp.apply(dict(cp.DEFAULTS, idle_minutes=480), profile="daneel")
        check("one key changed, one key written", len(ctl.sets) == 1, ctl.sets)
        check("and it is the right one",
              ctl.sets[0] == ("session_reset.idle_minutes", "480"), ctl.sets)
        ctl.sets = []
        cp.apply(cp.DEFAULTS, profile="daneel")
        check("re-applying an identical policy writes nothing at all", not ctl.sets, ctl.sets)

        section("a failed write is reported as failed")
        ctl = FakeCtl(write(tmp, FRESH, "w3.yaml"), fail=("session_reset.mode",))
        cp.hermes_ctl = ctl
        res = cp.apply(cp.DEFAULTS, profile="daneel")
        check("ok is False", res["ok"] is False, res)
        check("the failing key is named", res["failed"] == ["session_reset.mode"], res)
        check("and the message does not claim success",
              "No se pudo" in res["detail"], res["detail"])

        section("ensure(): configure once, then never again")
        ctl = FakeCtl(write(tmp, FRESH, "e1.yaml"))
        cp.hermes_ctl = ctl
        r = cp.ensure(profile="daneel", restart=False)
        check("an unconfigured profile is configured", r["changed"] is True, r)
        check("nine keys written", len(ctl.sets) == 9, len(ctl.sets))

        ctl = FakeCtl(write(tmp, CONFIGURED, "e2.yaml"))
        cp.hermes_ctl = ctl
        r = cp.ensure(profile="daneel", restart=False)
        check("a configured profile is left alone", r["changed"] is False, r)
        check("nothing is written", not ctl.sets, ctl.sets)

        # The one that matters most: turning restarts OFF is a choice, and a supervisor that
        # starts twice a day must not keep undoing it.
        ctl = FakeCtl(write(tmp, OPTED_OUT, "e3.yaml"))
        cp.hermes_ctl = ctl
        r = cp.ensure(profile="daneel", restart=False)
        check("'never restart', chosen deliberately, survives ensure()",
              r["changed"] is False and not ctl.sets, (r, ctl.sets))
        for _ in range(20):
            cp.ensure(profile="daneel", restart=False)
        check("and survives twenty more startups", not ctl.sets, ctl.sets)

        section("a written policy is not a live policy")
        ctl = FakeCtl(write(tmp, FRESH, "a1.yaml"), gateway_running=True)
        cp.hermes_ctl = ctl
        cp.ensure(profile="daneel", restart=True)
        check("configuring a live gateway restarts it, or the change does nothing",
              ctl.restarts == 1, ctl.restarts)
        ctl = FakeCtl(write(tmp, FRESH, "a2.yaml"), gateway_running=False)
        cp.hermes_ctl = ctl
        r = cp.ensure(profile="daneel", restart=True)
        check("a gateway that is down is not started just to be restarted",
              ctl.restarts == 0, ctl.restarts)
        check("and that is said plainly, not reported as a failure",
              r["activation"]["ok"] and not r["activation"]["restarted"], r["activation"])

        section("the agent hands the restart to the supervisor instead of killing its turn")
        home = os.path.join(tmp, "home")
        os.makedirs(home, exist_ok=True)
        check("nothing is pending on a clean machine", cp.pending(home) == [])
        cp.mark_pending("daneel", home)
        check("a change leaves a note", cp.pending(home) == ["daneel"], cp.pending(home))
        cp.mark_pending("daneel", home)
        check("marking twice does not queue it twice", cp.pending(home) == ["daneel"])
        check("the note is a real file the supervisor can find",
              os.path.isfile(cp.pending_path(home)), cp.pending_path(home))
        cp.clear_pending("daneel", home)
        check("and it is cleared once done", cp.pending(home) == [])

        section("a gateway that will not come back is not restarted forever")
        cp.mark_pending("daneel", home)
        tries = [cp.note_activation_failure("daneel", home) for _ in range(3)]
        check("failures are counted", tries == [1, 2, 3], tries)
        check("after three it stops being offered", cp.pending(home) == [], cp.pending(home))
        cp.clear_pending("daneel", home)
        cp.mark_pending("blanca", home)
        cp.note_activation_failure("blanca", home)
        check("a failure also backs off before the next attempt",
              cp.pending(home) == [], cp.pending(home))
        data = json.load(io.open(cp.pending_path(home), encoding="utf-8"))
        data["blanca"]["next"] = 0
        io.open(cp.pending_path(home), "w", encoding="utf-8").write(json.dumps(data))
        check("...and is offered again once the wait has passed",
              cp.pending(home) == ["blanca"], cp.pending(home))
        check("a corrupt note file is ignored, not fatal",
              (io.open(cp.pending_path(home), "w", encoding="utf-8").write("{{{ not json"),
               cp.pending(home) == [])[1])

        section("the skill the agent reads")
        skill = cp.render_skill("daneel")
        check("it names this machine's real script path",
              os.path.join("src", "tools", "conversation_policy.py") in skill, skill[:200])
        check("it targets the right profile, so one agent cannot reconfigure another",
              "--profile daneel" in skill)
        check("the default agent gets no stray --profile flag",
              "--profile" not in cp.render_skill(None))
        check("it never tells the agent to run pythonw, which eats stdout",
              "pythonw" not in skill.lower())
        # The interpreter on Windows is under "Program Files". An unquoted path splits at
        # the space and the agent's shell reports that C:\Program does not exist - a skill
        # whose every command line fails, with no hint as to why.
        cmds = [ln.strip() for ln in skill.splitlines()
                if "conversation_policy.py" in ln and not ln.startswith("#")]
        check("the skill actually contains command lines", len(cmds) >= 3, cmds)
        bad = [c for c in cmds if not c.startswith('"')]
        check("every command line quotes the interpreter, spaces and all", not bad, bad)
        check("and quotes the script path too",
              all('" "' in c for c in cmds), cmds)
        check("it forbids the agent restarting its own gateway",
              "No reinicies el gateway" in skill)
        check("and it explains what actually happens instead",
              "supervisor" in skill and "reposo" in skill)
        got = cp.install_skill("daneel", home=os.path.join(tmp, "sk"))
        check("installing writes it", got["ok"] and got["changed"], got)
        again = cp.install_skill("daneel", home=os.path.join(tmp, "sk"))
        check("installing again rewrites nothing", again["ok"] and not again["changed"])
    finally:
        cp.hermes_ctl = real_ctl

    section("the command line the skill actually tells the agent to run")
    script = os.path.join(ROOT, "src", "tools", "conversation_policy.py")
    env = dict(os.environ, HERMES_HOME=os.path.join(tmp, "cli-home"))
    os.makedirs(env["HERMES_HOME"], exist_ok=True)
    io.open(os.path.join(env["HERMES_HOME"], "config.yaml"), "w",
            encoding="utf-8").write(CONFIGURED)

    def run(*args):
        return subprocess.run([sys.executable, script] + list(args), capture_output=True,
                              text=True, timeout=120, env=env, encoding="utf-8",
                              errors="replace")

    p = run("--list-presets")
    check("--list-presets works on its own, with no other flag",
          p.returncode == 0 and "equilibrado" in p.stdout, (p.returncode, p.stderr[-300:]))
    p = run("--help")
    check("--help works", p.returncode == 0 and "conversation_policy" in p.stdout)
    p = run()
    check("no arguments reports the current policy rather than changing it",
          p.returncode == 0 and "2.5 h" in p.stdout, p.stdout[:300] + p.stderr[-200:])
    p = run("--json")
    check("--json is machine-readable", p.returncode == 0 and json.loads(p.stdout)["configured"])
    p = run("--preset", "inventado")
    check("an unknown preset is a usage error, not a silent default",
          p.returncode == 2, (p.returncode, p.stdout[:200]))
    p = run("--idle-minutes", "99999")
    check("an out-of-range value does not crash the agent's shell",
          p.returncode in (0, 1), (p.returncode, p.stderr[-300:]))

    section("Hermes on THIS machine still behaves the way we assume it does")
    hermes_src = None
    for base in (os.environ.get("LOCALAPPDATA", ""), os.path.expanduser("~")):
        cand = os.path.join(base, "hermes", "hermes-agent")
        if os.path.isdir(cand):
            hermes_src = cand
            break
    if not hermes_src:
        print("  skip (no Hermes checkout found)")
    else:
        cfg = io.open(os.path.join(hermes_src, "hermes_cli", "config.py"),
                      encoding="utf-8", errors="replace").read()
        gwc = io.open(os.path.join(hermes_src, "gateway", "config.py"),
                      encoding="utf-8", errors="replace").read()
        comp = io.open(os.path.join(hermes_src, "agent", "context_compressor.py"),
                       encoding="utf-8", errors="replace").read()
        meta = io.open(os.path.join(hermes_src, "agent", "model_metadata.py"),
                       encoding="utf-8", errors="replace").read()
        check("session_reset still defaults to 'none' - the whole reason this exists",
              'mode: str = "none"' in gwc)
        check("its idle default is still 1440",
              "idle_minutes: int = 1440" in gwc)
        check("compression.threshold still defaults to 0.50",
              '"threshold": 0.50' in cfg)
        check("the 512K small-window limit is unchanged",
              "_SMALL_CTX_WINDOW_LIMIT = %s" % "{:,}".format(cp.SMALL_WINDOW).replace(",", "_") in comp)
        check("the 64k trigger floor is unchanged",
              "MINIMUM_CONTEXT_LENGTH = %s" % "{:,}".format(cp.MIN_TRIGGER_TOKENS)
              .replace(",", "_") in meta)
        check("the 85% degenerate-window ratio is unchanged",
              "_MIN_CTX_TRIGGER_RATIO = %s" % cp.MIN_CTX_RATIO in comp)
        check("session_reset is still read from config.yaml by the gateway",
              'yaml_cfg.get("session_reset")' in gwc)
        check("...and still only at gateway start, which is why we restart it",
              "def get_reset_policy" in gwc)

        section("this machine, right now")
        for prof in (None, "daneel"):
            st = cp.read(profile=prof)
            if not st["ok"]:
                continue
            print("       %-8s configured=%-5s %s"
                  % (prof or "default", st["configured"], st["summary"]))
        main_state = cp.read(profile=None)
        check("the owner's own agent reads as configured, and we agree with its numbers",
              main_state["ok"] and main_state["configured"], main_state["path"])

    shutil.rmtree(tmp, ignore_errors=True)
    print("\n%d passed, %d failed" % (len(PASSED), len(FAILED)))
    for f in FAILED:
        print("  - " + f)
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
