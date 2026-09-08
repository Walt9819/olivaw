r"""One agent, several WhatsApp numbers, one set of instructions.

The ask: a business with a sales line and a support line wants the SAME agent answering
both - same persona, same CLAUDE.md, same skills - not two agents configured alike that
drift apart the first time somebody edits one.

Hermes keeps one adapter per ``Platform`` enum value (``self.adapters[platform]``), so the
unit of "one WhatsApp number" is (profile, platform-value). The adapter itself is already
per-instance - it reads ``bridge_port`` and ``session_path`` out of ``config.extra`` - so
what was missing was a second KEY, and Hermes provides one: a plugin may register a
platform under any name and ``Platform._missing_()`` mints an enum member for it.

This suite pins the parts where being wrong is expensive:

  * a number is (slug, platform, port, session) or it is nothing - two rows sharing a
    session are two bridges fighting over one WhatsApp identity;
  * the FIRST number never changes, so nobody has to re-pair anything;
  * every extra number is silenced for customers, because an unknown platform falls
    through to Hermes' global display defaults where tool_progress is "all";
  * the generated plugin is valid Python that fails LOUDLY rather than silently
    registering nothing;
  * a one-number agent reads exactly the skill it read before.

Run: python tools/test_numbers.py
"""

import ast
import io
import json
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
sys.path.insert(0, SRC)

PASSED, FAILED = [], []


def check(name, cond, extra=""):
    (PASSED if cond else FAILED).append(name)
    print(("  ok   " if cond else "  FAIL ") + name +
          (("\n       " + str(extra)) if (extra and not cond) else ""))


def section(t):
    print("\n=== %s ===" % t)


def _pair(path, registered=True):
    """Write the session a real pairing leaves behind."""
    os.makedirs(path, exist_ok=True)
    creds = {"registered": True, "me": {"id": "5215512345678:9@s.whatsapp.net"}} \
        if registered else {"registered": False, "noiseKey": {"private": "x"}}
    io.open(os.path.join(path, "creds.json"), "w", encoding="utf-8").write(json.dumps(creds))


def main():
    tmp = tempfile.mkdtemp(prefix="numbers-")
    old_home = os.environ.get("HERMES_HOME")
    os.environ["HERMES_HOME"] = tmp
    try:
        from wizard import numbers as N
        from wizard import display_policy as D
        from wizard import wa_plugin as P
        from wizard import wa_setup

        section("adding a number")
        r = N.add("Ventas", allowed_users="+52 155 1234 5678")
        check("it saves", r.get("ok"), r)
        row = r.get("number") or {}
        check("it gets its own platform value", row.get("platform") == "whatsapp_ventas", row)
        check("its own bridge port, not the first number's",
              row.get("port") not in (None, N.BUILTIN_PORT), row)
        check("its own session directory", "whatsapp-ventas" in (row.get("session") or ""), row)
        check("the allowlist is normalised, not stored as typed",
              row.get("allowed_users") == "+52,155,1234,5678" or
              row.get("allowed_users") == "+5215512345678", row.get("allowed_users"))
        r2 = N.add("Soporte")
        check("a second one gets a different port",
              r2["number"]["port"] != row["port"], (row["port"], r2["number"]["port"]))

        section("the first number is untouched")
        rows = N.listing()
        first = rows[0]
        check("it is still the plain 'whatsapp' platform",
              first["platform"] == "whatsapp", first)
        check("still on port 3000", first["port"] == 3000, first)
        check("and it is listed first, so the UI order is stable",
              first["builtin"] is True and len(rows) == 3, rows)
        check("no config key is ever planned for it",
              not any(".whatsapp." in k for k, _v in N.config_plan()),
              [k for k, _v in N.config_plan()])

        section("two numbers can never share what only one can own")
        base = dict(row, slug="otro")
        for why, bad in (
            ("another number's port", dict(base, port=r2["number"]["port"],
                                           session=os.path.join(tmp, "s1"))),
            ("the first number's port", dict(base, port=N.BUILTIN_PORT,
                                             session=os.path.join(tmp, "s2"))),
            ("another number's session", dict(base, port=3099,
                                              session=row["session"])),
            ("the first number's session", dict(base, port=3098,
                                                session=N.builtin_session_paths()[0])),
            ("a slug that is not a slug", dict(base, slug="../../x", port=3097,
                                               session=os.path.join(tmp, "s3"))),
            ("a port that is not a number", dict(base, port="tres mil",
                                                 session=os.path.join(tmp, "s4"))),
        ):
            problems = N.conflicts(bad)
            check("rejected: " + why, bool(problems), "it was ACCEPTED")
        check("the message names the culprit, in Spanish that agrees",
              "la carpeta de sesión" in N.describe(
                  N.conflicts(dict(base, port=3099, session=row["session"]))),
              N.describe(N.conflicts(dict(base, port=3099, session=row["session"]))))
        check("a row already on disk can still be edited in place",
              not N.conflicts(row), N.conflicts(row))

        # Not just the predicate - the WRITE has to refuse. A registry that has been
        # hand-edited (or restored from an older backup) can already hold a row squatting on
        # the session directory the next number would be given, and saving on top of it is
        # how two bridges end up sharing one WhatsApp identity.
        saved = N.load()
        squatter = {"slug": "otro", "label": "Otro", "platform": "whatsapp_otro",
                    "port": 3090, "session": N.session_path("contable"), "main": False}
        N.save({"numbers": saved["numbers"] + [squatter]})
        try:
            N.add("Contable")
            check("add() REFUSES to write a colliding row", False, "it was saved")
        except N.Conflict as e:
            check("add() REFUSES to write a colliding row", True, e)
        check("and the registry is left as it was",
              N.get("contable") is None, N.extras())
        N.save(saved)

        section("the ceiling is stated, not silently hit")
        while True:
            res = N.add("Linea")
            if not res.get("ok"):
                break
        check("adding past the ceiling fails", not res.get("ok"), res)
        check("and the reason is the real one, not 'error'",
              "sesión de WhatsApp" in res.get("detail", ""), res.get("detail"))
        check("the ceiling is where MAX_NUMBERS says",
              len(N.listing()) == N.MAX_NUMBERS, len(N.listing()))

        section("pairing is per number")
        _pair(row["session"])
        check("the paired one reads as linked", N.linked(row))
        check("an unpaired one does not", not N.linked(r2["number"]))
        _pair(r2["number"]["session"], registered=False)
        check("a bridge that started but was never scanned is NOT linked",
              not N.linked(r2["number"]), "creds.json alone must not count")
        listed = {x["slug"]: x for x in N.listing()}
        check("the main number reports its OWN pairing, not another's",
              listed["principal"]["linked"] is False,
              "it borrowed ventas' linked state")

        section("every extra number is silenced for customers")
        # Hermes has no per-platform display defaults for a name it does not know, so an
        # unlisted platform falls through to the global ones, where tool_progress is "all".
        plats = D.enabled_platforms(env={"WHATSAPP_ENABLED": "1"})
        for slug in ("ventas", "soporte"):
            check("whatsapp_%s is in the silence list" % slug,
                  "whatsapp_%s" % slug in plats, plats)
        cfg = os.path.join(tmp, "config.yaml")
        io.open(cfg, "w", encoding="utf-8").write("model:\n  default: claude-code\n")
        todo = D.plan(platforms=plats, env={"WHATSAPP_ENABLED": "1"}, path=cfg)
        check("so each of them gets the full set of quiet keys",
              len(todo) == len(plats) * len(D.QUIET), (len(todo), len(plats)))
        io.open(cfg, "w", encoding="utf-8").write(
            "display:\n  platforms:\n    whatsapp_ventas:\n      tool_progress: all\n")
        todo = D.plan(platforms=plats, env={"WHATSAPP_ENABLED": "1"}, path=cfg)
        keys = [k for k, _v in todo if "whatsapp_ventas" in k]
        check("a key the owner set on an EXTRA number is respected too",
              not any(k.endswith(".tool_progress") for k in keys), keys)
        check("...and the ones she never touched are still written",
              any(k.endswith(".streaming") for k in keys), keys)
        # written() with no explicit platform list is the path the supervisor takes. If it
        # only reads the fixed CUSTOMER_PLATFORMS it reports "nothing set" for every extra
        # number, so every pass re-issues all seven writes AND overwrites the owner's own
        # choice for that number.
        have = D.written(path=cfg)
        check("reading config back covers the extra numbers by default",
              "whatsapp_ventas" in have, sorted(have))
        check("and it sees the key she set there",
              "tool_progress" in (have.get("whatsapp_ventas") or set()), have)

        section("what Hermes writes for each number")
        plan = dict(N.config_plan())
        for slug, port in (("ventas", row["port"]),):
            base_key = "platforms.whatsapp_%s" % slug
            check("%s is enabled" % slug, plan.get(base_key + ".enabled") == "true", plan)
            check("with its own port",
                  plan.get(base_key + ".extra.bridge_port") == str(port), plan)
            check("its own session path",
                  "whatsapp-%s" % slug in plan.get(base_key + ".extra.session_path", ""), plan)
            # dm_policy is the ADAPTER's own gate; the env allowlist below is the GATEWAY's.
            check("an allowlist at the adapter, not Hermes' 'pairing' default",
                  plan.get(base_key + ".extra.dm_policy") == "allowlist", plan)
            check("and groups off - a customer line is not a group bot",
                  plan.get(base_key + ".extra.group_policy") == "disabled", plan)
        env = N.env_plan()
        check("each number has its own allowlist variable",
              "WHATSAPP_VENTAS_ALLOWED_USERS" in env, sorted(env))
        check("and allow-all is written OFF, not merely left absent",
              env.get("WHATSAPP_VENTAS_ALLOW_ALL_USERS") == "0", env)
        check("no key is planned for a platform Hermes would reject",
              all(k.split(".")[1].startswith("whatsapp_") for k, _v in N.config_plan()))

        section("platform_toolsets is deliberately NOT written")
        # Hermes auto-generates hermes-<platform> for a plugin platform from
        # _HERMES_CORE_TOOLS, and the real hermes-whatsapp toolset IS that same list. So an
        # extra number inherits a tool surface identical to the first number's. If upstream
        # ever changes one without the other, this test is the alarm.
        core, wa = _hermes_toolsets()
        if core is None:
            print("  ..   (Hermes not installed here; equivalence unchecked)")
        else:
            check("hermes-whatsapp is still exactly _HERMES_CORE_TOOLS", core == wa,
                  "they have diverged - extra numbers now need an explicit "
                  "platform_toolsets entry")
        check("and we write no toolset key at all",
              not any("toolset" in k for k, _v in N.config_plan()))

        section("the generated plugin")
        res = P.install(home=tmp)
        check("it is written", res.get("ok"), res)
        init = os.path.join(res["path"], "__init__.py")
        body = io.open(init, encoding="utf-8").read()
        ast.parse(body)
        check("it is valid Python", True)
        check("it is pure ASCII, so no encoding can corrupt it",
              all(ord(c) < 128 for c in body))
        check("it declares itself a platform plugin",
              "kind: platform" in io.open(os.path.join(res["path"], "plugin.yaml"),
                                          encoding="utf-8").read())
        check("it re-stamps the adapter's platform identity",
              "adapter.platform = Platform(platform_name)" in body, body[:0])
        check("it declares a per-number allowlist env var",
              'allowed_users_env="WHATSAPP_%s_ALLOWED_USERS" % up' in body)
        check("it reads the registry at runtime, not at generation time",
              "olivaw-numbers.json" in body and "whatsapp_ventas" not in body,
              "the number list must not be baked into the code")
        check("it fails LOUDLY if Hermes moves the adapter",
              "will NOT answer until Olivaw is updated" in body, body[:0])
        check("a malformed slug in the registry is ignored, not turned into a path",
              "unusable slug" in body)
        check("writing it again changes nothing", not P.install(home=tmp)["changed"])
        io.open(init, "w", encoding="utf-8").write("# tampered\n")
        check("but a changed file IS rewritten", P.install(home=tmp)["changed"])

        section("the plugin's own logic, exercised")
        mod = _load_generated(init)
        check("it finds the registry through the profile home",
              len(mod._numbers()) == len([x for x in N.listing() if not x["builtin"]]),
              mod._numbers())
        bad = N.load()
        bad["numbers"].append({"slug": "../evil", "port": 3050, "session": tmp})
        N.save(bad)
        check("and refuses a slug it cannot trust",
              all(r_["slug"] != "../evil" for r_ in mod._numbers()), mod._numbers())
        bad["numbers"] = [r_ for r_ in bad["numbers"] if r_["slug"] != "../evil"]
        bad["numbers"].append({"slug": "sinpuerto", "session": tmp})
        N.save(bad)
        check("and a row with no port, which would collide with 3000",
              all(r_["slug"] != "sinpuerto" for r_ in mod._numbers()), mod._numbers())

        section("removing a number keeps the phone paired")
        N.save({"numbers": [dict(row), dict(r2["number"])]})
        gone = N.remove("soporte")
        check("the row is gone", gone.get("ok") and N.get("soporte") is None, gone)
        check("the session is KEPT by default - unlinking would cost a re-pair",
              gone.get("session_kept") is True and
              os.path.isfile(os.path.join(r2["number"]["session"], "creds.json")), gone)
        check("removing something that is not there is an honest error",
              not N.remove("soporte").get("ok"))
        check("and the plugin comes away when the last number does",
              P.remove(home=tmp).get("changed") is True)

        section("the skill tells the agent which line it is on")
        skill = wa_setup.render_skill(tmp)
        body = skill.split("---", 2)[2]
        check("it lists the lines", "atiende varias" in body, body[:200])
        check("with a row per number",
              body.count("| `principal`") == 1 and body.count("| `ventas`") == 1, body[:0])
        check("it tells the agent to name the line when verifying a delivery",
              "--number <clave>" in body)
        check("and explains the cost of getting it wrong",
              "unknown" in body and "otra" in body)
        check("it says replies need no choice at all",
              "salen" in body and "solas" in body, body[:0])

        section("a one-number agent reads exactly what it read before")
        N.save({"numbers": []})
        solo = wa_setup.render_skill(tmp)
        sbody = solo.split("---", 2)[2]
        check("no section about several lines", "atiende varias" not in sbody)
        check("no --number flag anywhere", "--number" not in sbody)
        check("and no leftover template placeholder",
              "{" not in sbody.replace("{python}", ""), sbody[:0])

        section("the supervisor reconciles it")
        src = io.open(os.path.join(SRC, "launcher.py"), encoding="utf-8").read()
        check("_ensure_numbers exists", "def _ensure_numbers(" in src)
        startup = src[src.index("    _reconcile_extras(cfg, state)"):]
        startup = startup[:startup.index("    while True:")]
        check("and the startup sequence calls it", "\n    _ensure_numbers()" in startup,
              startup)
        check("before the display policy, so a new number is silenced from its first message",
              startup.index("_ensure_numbers()") <
              startup.index("_ensure_display_policy()"), startup)
        check("an agent with no extra numbers is skipped entirely",
              "if not _numbers.extras(prof):" in src)
        check("a newly registered number queues the gateway restart that activates it",
              '_skill_needs_reload(key, "numbers")' in src)

        section("the tools can be pointed at one number")
        # Exercised, not grepped: the flag existing in --help proves nothing about whether
        # the port it resolves is actually used.
        N.save({"numbers": [dict(row), dict(r2["number"])]})
        import whatsapp_delivery as WD
        check("a slug resolves to that number's bridge port",
              WD.port_for_number("ventas") == row["port"],
              (WD.port_for_number("ventas"), row["port"]))
        check("the main number is port 3000, with or without a name",
              WD.port_for_number("") == WD.DEFAULT_PORT ==
              WD.port_for_number("principal"))
        check("a name this agent does not have resolves to nothing",
              WD.port_for_number("inventado") is None)
        # And the CLI must ACT on it. Asking the wrong bridge returns "unknown", which the
        # agent is told means "it was NOT sent" - about a message that went out fine on
        # another line. So an unrecognised name has to stop the run, not fall through to
        # the default port.
        code = WD.main(["--number", "inventado", "--ids", "ABC", "--json"])
        check("so the CLI refuses it instead of asking the main bridge", code == 2, code)
        used = {}
        real_health = WD.bridge_health
        WD.bridge_health = lambda host=None, port=None, timeout=5.0: (
            used.update(port=port) or {"reachable": False, "connection": None,
                                       "patched": False, "tracked": None})
        try:
            WD.main(["--number", "ventas", "--health"])
        finally:
            WD.bridge_health = real_health
        check("and a real name reaches THAT number's bridge",
              used.get("port") == row["port"], used)

        section("the escalation resolves a LID through any number's session")
        from tools import escalate_owner as E
        os.environ["OLIVAW_ESCALATION_HOME"] = tmp
        try:
            # The mapping lives in the SECOND number's session, because that is the line
            # this customer wrote to. Reading only the first number's session would drop
            # the phone number out of the owner's alert.
            io.open(os.path.join(row["session"], "lid-mapping-5219998887777.json"),
                    "w", encoding="utf-8").write('"271828182845904"')
            check("a LID paired on an extra line still resolves to a phone",
                  E.canonical_phone("271828182845904@lid") == "5219998887777",
                  E.canonical_phone("271828182845904@lid"))
            check("one nothing proves still resolves to nothing",
                  E.canonical_phone("999999999999999@lid") == "")
            check("the alert names the line the client used",
                  E.line_label("ventas") == "Ventas", E.line_label("ventas"))
            N.save({"numbers": []})
            check("but says nothing about lines on a one-line agent",
                  E.line_label("ventas") == "", E.line_label("ventas"))
        finally:
            os.environ.pop("OLIVAW_ESCALATION_HOME", None)
    finally:
        if old_home is None:
            os.environ.pop("HERMES_HOME", None)
        else:
            os.environ["HERMES_HOME"] = old_home
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n%d passed, %d failed" % (len(PASSED), len(FAILED)))
    for f in FAILED:
        print("  - " + f)
    return 1 if FAILED else 0


def _hermes_toolsets():
    """(_HERMES_CORE_TOOLS, hermes-whatsapp tools) from the installed Hermes, or (None, None)."""
    import re
    for base in (os.environ.get("LOCALAPPDATA", ""), os.path.expanduser("~")):
        for tail in (("hermes", "hermes-agent"), (".hermes", "hermes-agent")):
            p = os.path.join(base, *tail, "toolsets.py")
            if not os.path.isfile(p):
                continue
            text = io.open(p, encoding="utf-8", errors="replace").read()
            m = re.search(r'"hermes-whatsapp":\s*\{(.*?)\}', text, re.S)
            if not m:
                return None, None
            return "_HERMES_CORE_TOOLS", ("_HERMES_CORE_TOOLS"
                                          if "_HERMES_CORE_TOOLS" in m.group(1) else "other")
    return None, None


def _load_generated(path):
    """Import the generated plugin as a module so its logic can be run, not just grepped."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("olivaw_generated_numbers", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


if __name__ == "__main__":
    sys.exit(main())
