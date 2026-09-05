r"""The Telegram token paste, which is where setup fails most often - and repeatedly.

A bot token is copied out of a chat message, usually on a phone. That copy routinely brings
along an invisible character (zero-width space, BOM, a directional mark, a soft hyphen) or
the sentence around it. `str.strip()` and JavaScript's `trim()` remove whitespace only, so
the junk went straight into the URL and urllib raised

    'ascii' codec can't encode character '​' in position 55

which the wizard reported as "Ese token no funcionó. Cópialo completo desde BotFather." -
advice that reproduces the identical character. That is why it failed again on the next
attempt, and on other people's machines.

Worse than the message: the raw paste was written into `.env` verbatim, so a token that
looks perfectly correct in an editor could authenticate nowhere, permanently.

No network here on purpose: every case is a pure string recovery, so this runs anywhere and
cannot be flaky.

Run: python tools/test_telegram_token.py
"""

import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from wizard.telegram_setup import TOKEN_RE, clean_token  # noqa: E402
from wizard.config_writer import clean_token as cw_clean  # noqa: E402

PASSED, FAILED = [], []

# Shaped exactly like a real one; not a real token.
TOK = "8106530142:AAHl5JTv8MvsbEXAMPLEnotarealtoken00"


def check(name, cond, extra=""):
    (PASSED if cond else FAILED).append(name)
    print(("  ok   " if cond else "  FAIL ") + name +
          (("\n       " + str(extra)) if (extra and not cond) else ""))


def section(t):
    print("\n=== %s ===" % t)


def recovers(name, pasted, expect=TOK):
    got, notes = clean_token(pasted)
    check(name, got == expect, "got %r (notes=%s)" % (got, notes))
    return got


def main():
    section("a clean paste is left exactly alone")
    got, notes = clean_token(TOK)
    check("returned unchanged", got == TOK)
    check("and nothing is reported as repaired", notes == [], notes)

    section("whitespace, which strip() already handled")
    recovers("trailing newline", TOK + "\n")
    recovers("surrounding spaces", "   " + TOK + "   ")
    recovers("tab and CRLF", "\t" + TOK + "\r\n")
    recovers("non-breaking space", TOK + " ")

    section("invisible characters - the actual cause")
    recovers("zero-width space at the end", TOK + "​")
    recovers("zero-width space in the middle", TOK[:12] + "​" + TOK[12:])
    recovers("byte-order mark in front", "﻿" + TOK)
    recovers("left-to-right mark", "‎" + TOK)
    recovers("right-to-left mark", TOK + "‏")
    recovers("soft hyphen inside", TOK[:10] + "­" + TOK[10:])
    recovers("word joiner", TOK + "⁠")
    _, notes = clean_token(TOK + "​")
    check("the repair is reported, not done silently",
          any("invisible" in n for n in notes), notes)

    section("copying more than the token")
    recovers("the whole BotFather sentence",
             "Use this token to access the HTTP API:\n" + TOK)
    recovers("the full BotFather message",
             "Done! Congratulations on your new bot. You will find it at t.me/x_bot.\n\n"
             "Use this token to access the HTTP API:\n" + TOK + "\n\n"
             "Keep your token secure and store it safely, it can be used by anyone to "
             "control your bot.")
    recovers("a 'token:' label", "token: " + TOK)
    recovers("wrapped in quotes", '"' + TOK + '"')
    recovers("wrapped in smart quotes", "“" + TOK + "”")
    recovers("wrapped in backticks", "`" + TOK + "`")

    section("a token broken across lines")
    recovers("newline mid-token", TOK[:20] + "\n" + TOK[20:])
    recovers("space mid-token", TOK[:20] + " " + TOK[20:])
    _, notes = clean_token(TOK[:20] + "\n" + TOK[20:])
    check("and that repair is named too",
          any("saltos" in n for n in notes), notes)

    section("things that are genuinely not a token")
    for bad, why in (("", "empty"), ("hola", "a word"), ("12345", "just digits"),
                     ("123456789", "digits with no secret"),
                     ("abc:def", "too short on both sides"),
                     ("123456789:short", "secret too short")):
        got, _ = clean_token(bad)
        check("refuses %s" % why, got == "", "got %r" % got)

    section("the shape it insists on")
    check("digits before the colon are required",
          TOKEN_RE.fullmatch("abcdefghij:" + TOK.split(":")[1]) is None)
    check("at least 20 characters after the colon",
          TOKEN_RE.fullmatch("123456789:" + "a" * 19) is None)
    check("url-safe characters are accepted",
          TOKEN_RE.fullmatch("123456789:" + "a-b_c" + "d" * 20) is not None)
    check("the recovered token is pure ASCII, so it can go in a URL",
          clean_token(TOK + "​")[0].isascii())

    section("what reaches .env is the cleaned token, never the paste")
    for pasted, why in ((TOK + "​", "an invisible character"),
                        ("token: " + TOK, "surrounding text"),
                        (TOK[:20] + "\n" + TOK[20:], "a line break")):
        check("a token with %s is cleaned before being written" % why,
              cw_clean(pasted)[0] == TOK, cw_clean(pasted))
    check("config_writer and telegram_setup use the same cleaner",
          cw_clean is clean_token)

    section("the old failure can no longer reach urllib")
    # The bug was an exception thrown while building the URL. Nothing that survives
    # cleaning can contain a character that would do that again.
    for pasted in (TOK + "​", "﻿" + TOK, "token: " + TOK, "«" + TOK + "»"):
        got, _ = clean_token(pasted)
        check("%r yields a URL-safe token" % pasted[:18],
              got == "" or (got.isascii() and not any(c.isspace() for c in got)))

    test_tls_is_not_the_token()

    print("\n%d passed, %d failed" % (len(PASSED), len(FAILED)))
    for f in FAILED:
        print("  - " + f)
    return 1 if FAILED else 0


def test_tls_is_not_the_token():
    r"""A rejected certificate must never be reported as a rejected token.

    Field report: `CERTIFICATE_VERIFY_FAILED: self-signed certificate in certificate chain`
    on a corporate machine. A proxy was inspecting TLS and presenting its own root, Python
    refused the handshake, and the request never reached Telegram - so Telegram had neither
    accepted nor rejected anything. Both failures arrive at urllib as "could not connect",
    and they need opposite advice: one is fixed by generating a new token, the other only by
    the company installing its root certificate. Guessing sends the owner round a loop.
    """
    from wizard import telegram_health as T

    section("the probe tells a bad certificate from a bad network")
    live = T.tls_probe("api.telegram.org")
    if live["state"] == "ok":
        check("a good chain probes clean", True)
        # Real hosts, because this is exactly the class of failure being classified and a
        # mock would only prove the mock. Skipped when the machine has no network.
        check("a self-signed chain is reported as a certificate problem",
              T.tls_probe("self-signed.badssl.com")["state"] == "tls",
              T.tls_probe("self-signed.badssl.com"))
        check("an expired certificate too",
              T.tls_probe("expired.badssl.com")["state"] == "tls")
        check("a name that does not resolve is a DNS problem, not a certificate one",
              T.tls_probe("nope.invalid.telegram.example")["state"] == "dns")
    else:
        print("  ..   (no network; the live probe cases are skipped)")

    section("and the verdict says so, in words the owner can act on")
    src = io.open(os.path.join(ROOT, "src", "wizard", "telegram_health.py"),
                  encoding="utf-8").read()
    check("there is a distinct state for it", 'state="unreachable_tls"' in src)
    check("it is terminal - waiting cannot fix a certificate",
          '"unreachable_tls"' in src.split("TERMINAL = ", 1)[1].split(")", 1)[0])
    check("it says Telegram never saw the token",
          "Telegram NO ha visto el token" in src)
    check("it names the actual remedy: the company's root certificate",
          "Entidades de certificación raíz de confianza" in src)
    check("and the alternative: exempt api.telegram.org from inspection",
          "excluyendo api.telegram.org" in src)
    check("a configured proxy is reported when present",
          "def proxies_configured(" in src and "Este equipo tiene un proxy configurado" in src)
    check("but its value - which can hold a password - is never printed",
          "os.environ.get(v)" in src and "os.environ[v]" not in src)

    section("no bypass, anywhere, ever")
    # The remedy for an inspected connection is never to stop verifying it. Checked across
    # the whole tree, not just this file, so the property is the codebase's and not one
    # module's.
    import re
    offenders = []
    for base, _dirs, files in os.walk(os.path.join(ROOT, "src")):
        if "__pycache__" in base:
            continue
        for f in files:
            if not f.endswith(".py"):
                continue
            p = os.path.join(base, f)
            body = io.open(p, encoding="utf-8", errors="replace").read()
            if re.search(r"_create_unverified_context|CERT_NONE|check_hostname\s*=\s*False",
                         body):
                offenders.append(os.path.relpath(p, ROOT))
    check("nothing in src/ can turn certificate verification off", not offenders, offenders)


if __name__ == "__main__":
    sys.exit(main())
