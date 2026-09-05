r"""What a customer is allowed to see, and who a customer actually is.

Three failures reported from a live Windows install running Codex + WhatsApp, all of them
about the channel where CLIENTS write rather than the owner:

  display_policy   A WhatsApp customer was shown tool progress, terminal output and an
                   internal note about conversation compression. Nothing had gone wrong -
                   Hermes files WhatsApp under its TIER_MEDIUM display defaults, which have
                   tool progress ON, and Olivaw had never written a single display key. So
                   every agent it ever created leaked by default.
  wa_setup         The skill that tells an agent to VERIFY a delivery before claiming one,
                   and how to reach a human, was only ever installed into the DEFAULT
                   profile. A customer-facing bot is an extra agent by construction - its
                   own profile, its own number - so the agent that needed the skill was
                   precisely the one that never got it. Confirmed on disk before the fix.
  escalate_owner   WhatsApp identifies many senders by a LID ("2673...@lid"), which is not
                   a phone number. Stripping the digits out of one produced a perfectly
                   well-formed https://wa.me/2673... in an alert captioned "Cliente" -
                   pointing at a stranger.

Run: python tools/test_customer_channels.py
"""

import io
import json
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
sys.path.insert(0, SRC)

from wizard import display_policy as D     # noqa: E402
from wizard import hermes_ctl              # noqa: E402
from wizard import wa_setup                # noqa: E402
from tools import escalate_owner as E      # noqa: E402

PASSED, FAILED = [], []


def check(name, cond, extra=""):
    (PASSED if cond else FAILED).append(name)
    print(("  ok   " if cond else "  FAIL ") + name +
          (("\n       " + str(extra)) if (extra and not cond) else ""))


def section(t):
    print("\n=== %s ===" % t)


# Hermes' own list of settings that accept a per-platform override
# (gateway/display_config.OVERRIDEABLE_KEYS). Pinned here so that inventing a key - which
# would be written, ignored, and believed - fails this suite instead of failing silently in
# somebody's chat. Verified against the installed Hermes when one is present.
HERMES_OVERRIDEABLE = {
    "tool_progress", "tool_progress_grouping", "show_reasoning", "reasoning_style",
    "tool_preview_length", "streaming", "interim_assistant_messages",
    "long_running_notifications", "busy_ack_detail", "busy_steer_ack_enabled",
    "cleanup_progress",
}


def _installed_hermes_keys():
    """The real list, if Hermes is on this machine. Absent, the pinned copy stands."""
    for base in (os.environ.get("LOCALAPPDATA", ""), os.path.expanduser("~")):
        for tail in (("hermes", "hermes-agent"), (".hermes", "hermes-agent")):
            p = os.path.join(base, *tail, "gateway", "display_config.py")
            if os.path.isfile(p):
                try:
                    text = io.open(p, encoding="utf-8").read()
                except OSError:
                    return None
                block = text.split("_GLOBAL_DEFAULTS", 1)[-1].split("}", 1)[0]
                import re
                return set(re.findall(r'^\s*"([a-z_]+)":', block, re.M))
    return None


def test_display_policy():
    section("a customer channel shows the answer, and nothing else")
    real = _installed_hermes_keys()
    if real:
        check("the pinned key list still matches the installed Hermes",
              {k for k, _ in D.QUIET} <= real,
              "Hermes accepts %s; we would write %s" % (sorted(real), sorted(k for k, _ in D.QUIET)))
    else:
        print("  ..   (Hermes not installed here; using the pinned key list)")
    for key, _v in D.QUIET:
        check("`%s` is a setting Hermes actually accepts per platform" % key,
              key in HERMES_OVERRIDEABLE)
    check("tool progress is turned off, not merely reduced",
          dict(D.QUIET).get("tool_progress") == "off", dict(D.QUIET))
    check("mid-turn commentary is off - this is what carried the compaction notice",
          dict(D.QUIET).get("interim_assistant_messages") is False)
    check("half-finished sentences never reach a customer",
          dict(D.QUIET).get("streaming") is False)
    check("telegram is NOT quieted - it is the owner's own window",
          "telegram" not in D.CUSTOMER_PLATFORMS, D.CUSTOMER_PLATFORMS)
    check("whatsapp is", "whatsapp" in D.CUSTOMER_PLATFORMS)

    section("only channels that are actually on")
    tmp = tempfile.mkdtemp(prefix="disp-")
    try:
        cfg = os.path.join(tmp, "config.yaml")
        io.open(cfg, "w", encoding="utf-8").write("model:\n  default: claude-code\n")
        none = D.plan(None, platforms=D.enabled_platforms(None, env={"TELEGRAM_BOT_TOKEN": "x"}),
                      env={"TELEGRAM_BOT_TOKEN": "x"}, path=cfg)
        check("a Telegram-only agent is left completely alone", none == [], none)
        env = {"WHATSAPP_ENABLED": "1"}
        plats = D.enabled_platforms(None, env=env)
        check("WHATSAPP_ENABLED=1 makes it a customer channel", plats == ["whatsapp"], plats)
        check("WHATSAPP_ENABLED=0 does not",
              D.enabled_platforms(None, env={"WHATSAPP_ENABLED": "0"}) == [])
        todo = D.plan(None, platforms=plats, env=env, path=cfg)
        check("a fresh WhatsApp agent needs every setting written",
              len(todo) == len(D.QUIET), todo)
        check("and every one of them is scoped to whatsapp only",
              all(k.startswith("display.platforms.whatsapp.") for k, _ in todo), todo)

        section("a choice the owner made herself is never overwritten")
        io.open(cfg, "w", encoding="utf-8").write(
            "display:\n  platforms:\n    whatsapp:\n      tool_progress: all\n")
        todo = D.plan(None, platforms=["whatsapp"], env=env, path=cfg)
        keys = [k.rsplit(".", 1)[1] for k, _ in todo]
        check("the key she set is left exactly as it is", "tool_progress" not in keys, keys)
        check("the ones she never touched are still filled in",
              "streaming" in keys and "busy_ack_detail" in keys, keys)

        section("writing it, then writing it again")
        # Both hermes_ctl.config_set AND config_file are redirected at the temp file, so
        # apply() reads back exactly what it wrote. Without the config_file redirect the
        # second run would re-read the machine's REAL config and the idempotency check
        # below would pass for the wrong reason.
        calls = []
        real_set, real_file = hermes_ctl.config_set, D.config_file

        def fake_set(key, value, hermes=None, profile=None):
            calls.append((key, value))
            _plat, leaf = key.split(".")[2], key.split(".")[3]
            text = io.open(cfg, encoding="utf-8").read()
            if "display:\n" not in text:
                text += "display:\n  platforms:\n"
            if "    %s:\n" % _plat not in text:
                text += "    %s:\n" % _plat
            text += "      %s: %s\n" % (leaf, value)
            io.open(cfg, "w", encoding="utf-8").write(text)
            return {"ok": True, "detail": ""}

        hermes_ctl.config_set = fake_set
        D.config_file = lambda hermes=None, profile=None: cfg
        try:
            io.open(cfg, "w", encoding="utf-8").write("model:\n  default: claude-code\n")
            first = D.apply(None, platforms=["whatsapp"])
            n_first = len(calls)
            second = D.apply(None, platforms=["whatsapp"])
            n_second = len(calls) - n_first
        finally:
            hermes_ctl.config_set, D.config_file = real_set, real_file
        check("the first run writes every setting",
              first.get("changed") and n_first == len(D.QUIET), (first, n_first))
        check("the second run writes NOTHING - the settings are read back, not re-sent",
              n_second == 0 and not second.get("changed"), (second, n_second))
        check("`off` goes over as the string Hermes expects, not a bare boolean",
              ("display.platforms.whatsapp.tool_progress", "off") in calls, calls)
        check("booleans go over as lowercase words",
              all(v in ("off", "true", "false") for _k, v in calls), calls)
        check("and the file it read back really does contain them",
              "tool_progress: off" in io.open(cfg, encoding="utf-8").read())
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    section("the supervisor actually runs it")
    src = io.open(os.path.join(SRC, "launcher.py"), encoding="utf-8").read()
    check("_ensure_display_policy exists", "def _ensure_display_policy(" in src)
    # The CALL, not the definition. Searching the whole file for the bare name matches the
    # `def` line and so passes on a launcher that never calls it - which is exactly what a
    # revert check caught this assertion doing.
    startup = src[src.index("    _reconcile_extras(cfg, state)"):]
    startup = startup[:startup.index("    while True:")]
    check("and the startup sequence calls it, before the keep-alive loop",
          "\n    _ensure_display_policy()" in startup, startup)
    check("a change queues the gateway restart that makes it real",
          "_skill_needs_reload(r[\"profile\"], \"display policy\")" in src)
    ch = io.open(os.path.join(SRC, "wizard", "channels.py"), encoding="utf-8").read()
    check("and turning WhatsApp on applies it immediately, not at the next boot",
          "_quiet_customer_channels(" in ch and ch.count("_quiet_customer_channels(") >= 4, ch.count("_quiet_customer_channels("))


def test_skill_reaches_the_agent_that_needs_it():
    section("the client-handling skill goes where the clients are")
    tmp = tempfile.mkdtemp(prefix="waskill-")
    old_home = os.environ.get("HERMES_HOME")
    os.environ["HERMES_HOME"] = tmp

    def pair(prof, layout="platforms", creds=None):
        """Write the session a real pairing leaves behind."""
        d = os.path.join(wa_setup.profile_home(prof),
                         *( ("platforms", "whatsapp", "session") if layout == "platforms"
                            else ("whatsapp", "session") ))
        os.makedirs(d, exist_ok=True)
        io.open(os.path.join(d, "creds.json"), "w", encoding="utf-8").write(
            json.dumps(creds if creds is not None
                       else {"registered": True, "me": {"id": "5215512345678:7@s.whatsapp.net"}}))
        return d

    try:
        # Three extra agents: one with a phone really paired, one with the flag ticked but
        # the QR never scanned, one on Telegram only.
        for prof, env in (("clientes", "WHATSAPP_ENABLED=1\n"),
                          ("apuntado", "WHATSAPP_ENABLED=1\n"),
                          ("interno", "TELEGRAM_BOT_TOKEN=x\n")):
            d = os.path.join(tmp, "profiles", prof)
            os.makedirs(d)
            io.open(os.path.join(d, ".env"), "w", encoding="utf-8").write(env)
        pair("clientes")

        section("the flag is not the same thing as a linked phone")
        check("the flag is read from the profile's own .env",
              wa_setup.whatsapp_on("clientes") and wa_setup.whatsapp_on("apuntado")
              and not wa_setup.whatsapp_on("interno"))
        check("a paired session counts as linked", wa_setup.whatsapp_linked("clientes"))
        check("the flag alone does NOT - the QR was never scanned",
              not wa_setup.whatsapp_linked("apuntado"))
        check("neither does a bridge that started but was never paired",
              not wa_setup.whatsapp_linked("apuntado"))
        # Baileys writes creds.json the moment the bridge starts, long before anyone scans.
        pair("apuntado", creds={"registered": False, "noiseKey": {"private": "x"}})
        check("an unscanned creds.json is still not linked",
              not wa_setup.whatsapp_linked("apuntado"), "creds.json alone must not count")
        check("the legacy session layout is found too",
              (pair("interno", layout="legacy") and wa_setup.whatsapp_linked("interno")))
        shutil.rmtree(os.path.join(wa_setup.profile_home("interno"), "whatsapp"))
        check("...and removing it makes it unlinked again",
              not wa_setup.whatsapp_linked("interno"))
        os.makedirs(os.path.join(tmp, "profiles", "nube"))
        io.open(os.path.join(tmp, "profiles", "nube", ".env"), "w", encoding="utf-8").write(
            "WHATSAPP_CLOUD_ACCESS_TOKEN=EAA...\nWHATSAPP_CLOUD_PHONE_NUMBER_ID=123456\n")
        check("WhatsApp Cloud counts as linked on its credentials, having no QR",
              wa_setup.whatsapp_linked("nube"))
        io.open(os.path.join(tmp, "profiles", "nube", ".env"), "w", encoding="utf-8").write(
            "WHATSAPP_CLOUD_ACCESS_TOKEN=EAA...\n")
        check("...but half of them is not a link",
              not wa_setup.whatsapp_linked("nube"))
        shutil.rmtree(os.path.join(tmp, "profiles", "nube"))

        agents = [{"slug": "clientes", "profile": "clientes", "port": 8792},
                  {"slug": "apuntado", "profile": "apuntado", "port": 8796},
                  {"slug": "interno", "profile": "interno", "port": 8794}]
        res = wa_setup.ensure_all(agents=agents, hermes_exe="")
        by = {s["profile"]: s for s in res["skills"]}
        want = os.path.join(tmp, "profiles", "clientes", "skills",
                            wa_setup.SKILL_NAME, "SKILL.md")
        landed = os.path.isfile(want)
        check("the client-facing agent gets the skill, under ITS OWN profile", landed, want)
        check("and the report says so", by.get("clientes", {}).get("changed") is True, by)
        if not landed:
            # Everything below reads that file. Say so once instead of raising a traceback
            # over the remaining assertions.
            check("(the rest of this section cannot run)", False, "no skill was written")
            return
        def has_skill(prof):
            return os.path.isfile(os.path.join(tmp, "profiles", prof, "skills",
                                               wa_setup.SKILL_NAME, "SKILL.md"))

        check("the Telegram-only agent does not carry instructions for a channel it lacks",
              not has_skill("interno"))
        check("nor does the one that ticked the box but never scanned the QR",
              not has_skill("apuntado"), "an agent with no WhatsApp would offer to use it")
        check("and that is reported as a reason, not a failure",
              by.get("interno", {}).get("reason") == "no-whatsapp-linked", by)
        check("the report says plainly which agents are linked",
              by["clientes"]["linked"] is True and by["apuntado"]["linked"] is False, by)

        section("what the skill it installs actually requires")
        body = io.open(want, encoding="utf-8").read()
        check("it names the delivery-verification tool",
              "whatsapp_delivery.py" in body, body[:200])
        check("it names the escalation tool", "escalate_owner.py" in body)
        check("it distinguishes delivered from merely sent", "delivered" in body and "pending" in body)

        section("the supervisor installs it per agent, not just once")
        src = io.open(os.path.join(SRC, "launcher.py"), encoding="utf-8").read()
        check("_ensure_whatsapp walks every registered agent",
              "_wa.ensure_all(agents=_load_extra_agents()" in src, src[:0])
        check("and it no longer calls the single-profile ensure()",
              "r = _wa.ensure()" not in src)
        check("a newly taught agent gets the gateway restart that makes it visible",
              '_skill_needs_reload(skill["profile"], "whatsapp")' in src)
        # Caught in a live log: a removal reports changed=True too, and was announced as
        # "serves clients - installed the client-handling skill" one line after the truthful
        # "removed ... (no WhatsApp linked)". The action was right; the log read as a bug.
        check("a removal is not announced as an installation",
              'if skill.get("removed"):' in src and
              "took the" in src.split('if skill.get("removed"):', 1)[1][:300], src[:0])
        check("and the old misleading wording is gone",
              "serves clients - installed the " not in src)

        section("re-running changes nothing")
        again = wa_setup.ensure_all(agents=agents, hermes_exe="")
        check("second run is a no-op",
              all(not s.get("changed") for s in again["skills"]), again["skills"])

        section("scanning the QR later is what earns the skill")
        pair("apuntado")
        r = wa_setup.ensure_all(agents=agents, hermes_exe="")
        by = {s["profile"]: s for s in r["skills"]}
        check("once a phone is really linked, the skill arrives", has_skill("apuntado"))
        check("and it is reported as a change", by["apuntado"].get("changed") is True, by)
        check("the already-linked agent is untouched",
              not by["clientes"].get("changed"), by["clientes"])

        section("unlinking takes it away again")
        # The owner's actual complaint: an agent that still talks about WhatsApp after the
        # WhatsApp is gone reads as a malfunction. The skill follows the session both ways.
        shutil.rmtree(os.path.join(wa_setup.profile_home("apuntado"), "platforms"))
        r = wa_setup.ensure_all(agents=agents, hermes_exe="")
        by = {s["profile"]: s for s in r["skills"]}
        check("the skill is removed", not has_skill("apuntado"), "it is still there")
        check("and the removal is reported", by["apuntado"].get("removed") is True, by)
        check("the still-linked agent keeps its own", has_skill("clientes"))
        check("removing twice is not an error",
              wa_setup.remove_skill(wa_setup.profile_home("apuntado"))["ok"])

        section("a skill somebody wrote by hand is never deleted")
        d = os.path.join(wa_setup.profile_home("interno"), "skills", wa_setup.SKILL_NAME)
        os.makedirs(d, exist_ok=True)
        mine = "---\nname: %s\nauthor: Blanca\n---\nmis propias notas\n" % wa_setup.SKILL_NAME
        io.open(os.path.join(d, "SKILL.md"), "w", encoding="utf-8").write(mine)
        r = wa_setup.remove_skill(wa_setup.profile_home("interno"))
        check("it is left exactly as it was",
              io.open(os.path.join(d, "SKILL.md"), encoding="utf-8").read() == mine)
        check("and the reason is reported", r.get("reason") == "not-ours", r)
    finally:
        if old_home is None:
            os.environ.pop("HERMES_HOME", None)
        else:
            os.environ["HERMES_HOME"] = old_home
        shutil.rmtree(tmp, ignore_errors=True)


def test_who_the_customer_is():
    section("a LID is not a phone number")
    tmp = tempfile.mkdtemp(prefix="lid-")
    os.environ["OLIVAW_ESCALATION_HOME"] = tmp
    try:
        sess = os.path.join(tmp, "whatsapp", "session")
        os.makedirs(sess)
        # Exactly the format Baileys writes (scripts/whatsapp-bridge/allowlist.js).
        io.open(os.path.join(sess, "lid-mapping-5215512345678.json"), "w").write('"267383306489914"')
        io.open(os.path.join(sess, "lid-mapping-449900000000_reverse.json"), "w").write('"5215599999999"')

        table = [
            ("a plain number", "5215512345678", "5215512345678"),
            ("a number typed with + and spaces", "+52 155 1234 5678", "5215512345678"),
            ("an individual JID", "5215512345678@s.whatsapp.net", "5215512345678"),
            ("a legacy c.us JID", "5215512345678@c.us", "5215512345678"),
            ("a JID with a device suffix", "5215512345678:12@s.whatsapp.net", "5215512345678"),
            ("a LID the session can map (forward file)", "267383306489914@lid", "5215512345678"),
            ("a LID the session can map (reverse file)", "449900000000@lid", "5215599999999"),
            ("a LID nothing proves", "999999999999999@lid", ""),
            ("a group", "120363012345678901@g.us", ""),
            ("a status broadcast", "status@broadcast", ""),
            ("a channel", "120363999@newsletter", ""),
            ("something that is not an id at all", "el cliente", ""),
        ]
        for name, contact, want in table:
            got = E.canonical_phone(contact)
            check("%s -> %s" % (name, want or "(no number)"), got == want,
                  "got %r" % got)

        section("the alert never prints an id as though it were a number")
        # Driven through escalate() itself so the record is built the way the real path
        # builds it. Composing a record by hand here would test only compose(), and the
        # number is resolved one layer above that.
        def alert_for(contact, chat_link=""):
            r = E.escalate("human_requested", summary="quiere hablar con Blanca",
                           contact=contact, contact_name="Ana",
                           excerpt="necesito hablar con alguien", chat_link=chat_link,
                           force=True, retry_pending=False, log=lambda *a: None)
            rows = [json.loads(l) for l in
                    io.open(E.LEDGER(), encoding="utf-8").read().splitlines() if l.strip()]
            return rows[-1], r

        row, _ = alert_for("999999999999999@lid")
        text = row["text"]
        check("the LID digits appear nowhere in the alert",
              "999999999999999" not in text, text)
        check("no wa.me link is invented", "wa.me" not in text, text)
        check("the ledger row records that no number was proven",
              row.get("phone") == "", row.get("phone"))
        check("the owner is told why, and where to look instead",
              "Sin número verificable" in text, text)
        check("the customer's name is still there", "Ana" in text)

        section("a number the session DID prove is used, in full")
        row2, _ = alert_for("267383306489914@lid")
        text2 = row2["text"]
        check("the ledger row carries the resolved number",
              row2.get("phone") == "5215512345678", row2.get("phone"))
        check("the resolved number is shown", "5215512345678" in text2, text2)
        check("and the deep link points at it",
              "https://wa.me/5215512345678" in text2, text2)
        check("the LID itself is never shown", "267383306489914" not in text2, text2)

        section("a group chat is never turned into a personal phone number")
        row3, _ = alert_for("120363012345678901@g.us")
        check("no number, no link",
              row3.get("phone") == "" and "wa.me" not in row3["text"], row3.get("phone"))

        section("a link the model invented cannot reach the owner")
        row4, _ = alert_for("999999999999999@lid",
                            chat_link="https://wa.me/5215500000000")
        check("the made-up deep link is dropped before it is stored",
              row4.get("chat_link") == "" and "5215500000000" not in row4["text"], row4)

        section("a link the model supplied cannot override the one we proved")
        check("a wa.me link for a different number is dropped",
              E.safe_chat_link("https://wa.me/999999999999999", "5215512345678") == "")
        check("a matching one is kept",
              E.safe_chat_link("https://wa.me/5215512345678", "5215512345678") ==
              "https://wa.me/5215512345678")
        check("when no number was proven, no wa.me link survives at all",
              E.safe_chat_link("https://wa.me/999999999999999", "") == "")
        check("an api.whatsapp.com link is checked the same way",
              E.safe_chat_link("https://api.whatsapp.com/send?phone=999999999999999",
                               "5215512345678") == "")
        check("a link that is not claiming to be a phone number is left alone",
              E.safe_chat_link("https://example.com/ticket/9", "5215512345678") ==
              "https://example.com/ticket/9")

        section("escalate() resolves the number once and stores it")
        src = io.open(os.path.join(SRC, "tools", "escalate_owner.py"), encoding="utf-8").read()
        check("the ledger row carries the proven phone",
              '"phone": canonical_phone(contact)' in src)
        check("and the caller's chat_link is sanitised before it is stored",
              "safe_chat_link(_clean(chat_link" in src)
        check("compose reads the proven phone, never the raw contact",
              'num = rec.get("phone")' in src)
    finally:
        os.environ.pop("OLIVAW_ESCALATION_HOME", None)
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    test_display_policy()
    test_skill_reaches_the_agent_that_needs_it()
    test_who_the_customer_is()
    print("\n%d passed, %d failed" % (len(PASSED), len(FAILED)))
    for f in FAILED:
        print("  - " + f)
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
