#!/usr/bin/env python3
"""
smtp_send.py — send an email over SMTP. Stdlib only.

Hermes has no native email, so this small tool gives an agent outbound email. The
wizard writes the SMTP_* credentials into the agent's profile .env, so when Hermes runs
this via its terminal tool the values are already in the environment. The agent can call:

    python smtp_send.py --to dest@example.com --subject "Hola" --body "Mensaje"
    python smtp_send.py --to a@x.com --subject S --body B --attach /path/report.pdf

Config (env vars, or --flags to override):
    SMTP_HOST   e.g. smtp.gmail.com
    SMTP_PORT   587 (STARTTLS) or 465 (SSL)
    SMTP_USER   full email / username
    SMTP_PASS   password or app-password
    SMTP_FROM   from address (defaults to SMTP_USER)
    SMTP_SECURE starttls | ssl   (default: starttls for 465->ssl otherwise)
"""
import argparse
import mimetypes
import os
import smtplib
import ssl
import sys
from email.message import EmailMessage


def _cfg(args):
    host = args.host or os.environ.get("SMTP_HOST", "")
    port = int(args.port or os.environ.get("SMTP_PORT", "587"))
    user = args.user or os.environ.get("SMTP_USER", "")
    pw = args.password or os.environ.get("SMTP_PASS", "")
    frm = args.sender or os.environ.get("SMTP_FROM", "") or user
    secure = (args.secure or os.environ.get("SMTP_SECURE", "")
              or ("ssl" if port == 465 else "starttls")).lower()
    return host, port, user, pw, frm, secure


def send(to, subject, body, args, attachments=None):
    host, port, user, pw, frm, secure = _cfg(args)
    if not host or not user or not pw:
        return False, "Faltan credenciales SMTP (SMTP_HOST / SMTP_USER / SMTP_PASS)."
    msg = EmailMessage()
    msg["From"] = frm
    msg["To"] = to
    msg["Subject"] = subject or "(sin asunto)"
    msg.set_content(body or "")
    for path in (attachments or []):
        if not os.path.isfile(path):
            return False, "Adjunto no encontrado: %s" % path
        ctype, _ = mimetypes.guess_type(path)
        maintype, subtype = (ctype or "application/octet-stream").split("/", 1)
        with open(path, "rb") as fh:
            msg.add_attachment(fh.read(), maintype=maintype, subtype=subtype,
                               filename=os.path.basename(path))
    try:
        ctx = ssl.create_default_context()
        if secure == "ssl":
            with smtplib.SMTP_SSL(host, port, context=ctx, timeout=30) as s:
                s.login(user, pw)
                s.send_message(msg)
        else:
            with smtplib.SMTP(host, port, timeout=30) as s:
                s.ehlo()
                s.starttls(context=ctx)
                s.login(user, pw)
                s.send_message(msg)
        return True, "Correo enviado a %s." % to
    except Exception as e:  # noqa: BLE001
        return False, "Error SMTP: %s" % e


def main():
    ap = argparse.ArgumentParser(description="Send an email via SMTP.")
    ap.add_argument("--to", required=True)
    ap.add_argument("--subject", default="")
    ap.add_argument("--body", default="")
    ap.add_argument("--attach", action="append", default=[])
    ap.add_argument("--host"); ap.add_argument("--port", type=int)
    ap.add_argument("--user"); ap.add_argument("--password")
    ap.add_argument("--sender"); ap.add_argument("--secure", choices=["starttls", "ssl"])
    a = ap.parse_args()
    ok, detail = send(a.to, a.subject, a.body, a, a.attach)
    print(detail)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
