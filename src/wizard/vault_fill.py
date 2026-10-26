r"""Putting a stored value into a box in the agent's own browser, and nowhere else.

This is the half of the vault that touches the outside world. It is short on purpose: the
value is unsealed, written into one input field over the local DevTools socket, and
forgotten. It is never returned to the caller, never formatted into a message, never
logged.

Two guards live here rather than in the store, because both are about the PAGE:

  * **The origin must match the one the credential was saved for.** This is the single
    most valuable line in the feature. An agent reads web pages, and a web page is
    untrusted input: "to continue, sign in at accounts-google.secure-login.example" is
    an instruction some model somewhere will follow. A person can be fooled by that; a
    host comparison cannot. The password for google.com goes into google.com, or it does
    not go anywhere.
  * **A password goes into a password box.** Not because the page cannot read it either
    way - it can - but because a form that asks for the password in a visible field is
    either broken or not a login form, and in both cases stopping is right.

Neither guard can be waived by the agent. There is no --force: the owner changes what a
credential is for, in the console, or it is not changed.
"""

import json
import urllib.error
import urllib.request

try:
    from . import browser_label, browser_setup, vault
except ImportError:  # running from src/ rather than as a package
    from wizard import browser_label, browser_setup, vault

# How long one DevTools call may take. Local, so this is a "something is wrong" bound,
# not a latency budget.
TIMEOUT = 8.0


class Refused(Exception):
    """Said no, with a sentence the agent can act on."""


def _targets(port):
    try:
        raw = urllib.request.urlopen("http://127.0.0.1:%d/json/list" % int(port),
                                     timeout=2.0).read()
        return json.loads(raw.decode("utf-8", "replace"))
    except (urllib.error.URLError, OSError, ValueError, TimeoutError) as e:
        raise Refused(u"No puedo hablar con el navegador de este agente (%s). "
                      u"¿Está encendido su modo navegador?" % str(e)[:80])


def _browser_port(agent):
    """The port this agent's own browser is answering on, right now.

    Not browser_setup.port_for() alone: that is the function that ALLOCATES a port for a
    new window, and when the configured one is not cleanly this agent's it falls through
    to "the first free port". A free port is by definition one with no browser on it, so
    the mistake arrived as a connection error from somewhere deep in the fill instead of
    as a sentence about the browser.

    Typing a password is not a moment for guessing which window is which, so this
    verifies the port is answering as a browser before anything is unsealed, and says so
    plainly when it is not.
    """
    port = browser_setup.port_for(agent if agent != "default" else None)
    try:
        urllib.request.urlopen("http://127.0.0.1:%d/json/version" % int(port),
                               timeout=TIMEOUT).read()
    except Exception:  # noqa: BLE001
        raise Refused(
            u"No encuentro el navegador de este agente (debería estar en el puerto "
            u"%s y ahí no contesta nadie). Ábrelo desde la consola, en «Navegador», "
            u"y vuelve a intentarlo." % port)
    return port


def _page(port, want_url=""):
    """The page to type into: the one asked for, else the frontmost real page."""
    pages = [t for t in _targets(port)
             if t.get("type") == "page" and (t.get("webSocketDebuggerUrl") or "")
             and not str(t.get("url", "")).startswith("devtools://")]
    if not pages:
        raise Refused(u"Ese navegador no tiene ninguna pestaña abierta.")
    if want_url:
        for t in pages:
            if want_url in (t.get("url") or ""):
                return t
        # Asked for a tab that is not open. Falling back to the frontmost one is how a
        # password ends up typed into whatever happened to be in front - caught doing
        # exactly that against a real browser. An agent that named a page meant that
        # page.
        raise Refused(u"No hay ninguna pestaña en «%s». Ábrela y vuelve a intentarlo."
                      % want_url)
    return pages[0]


def host_of(url):
    """The host, lowercased, without port or credentials. '' when there is none."""
    text = str(url or "").strip()
    if "//" in text:
        text = text.split("//", 1)[1]
    text = text.split("/", 1)[0].split("?", 1)[0]
    if "@" in text:
        text = text.rsplit("@", 1)[1]
    return text.split(":", 1)[0].lower()


def same_site(saved, page):
    """Is this page the site the credential was saved for?

    A plain host match, widened one way only: a subdomain of the saved host is the same
    site (accounts.google.com for google.com), and the reverse is not. Everything else -
    a lookalike domain, a different TLD, an IP address - is a different site and gets
    nothing.
    """
    saved, page = host_of(saved), host_of(page)
    if not saved or not page:
        return False
    return page == saved or page.endswith("." + saved)


def _rpc(ws, nid, method, params=None, session=None):
    nid[0] += 1
    msg = {"id": nid[0], "method": method, "params": params or {}}
    if session:
        msg["sessionId"] = session
    ws.send(json.dumps(msg))
    while True:
        raw = ws.recv()
        if raw is None:
            raise Refused(u"El navegador cerró la conexión a media orden.")
        try:
            got = json.loads(raw)
        except ValueError:
            continue
        if got.get("id") != nid[0]:
            continue            # an event, or another call's answer
        if "error" in got:
            raise Refused(u"El navegador rechazó la orden: %s"
                          % str(got["error"].get("message", ""))[:120])
        return got.get("result") or {}


_FIND = r"""
(() => {
  const want = %s;
  const vis = el => {
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width > 1 && r.height > 1 && s.visibility !== 'hidden' && s.display !== 'none';
  };
  const all = [...document.querySelectorAll('input, textarea')].filter(vis)
              .filter(el => !el.disabled && !el.readOnly);
  const pick = want === 'password'
    ? all.find(el => el.type === 'password')
    : all.find(el => ['text','email','tel','number',''].includes(el.type))
      || all.find(el => el.tagName === 'TEXTAREA');
  if (!pick) return {ok: false, why: 'no-field'};
  pick.focus();
  if (pick.value) pick.select();
  return {ok: true, type: pick.type || pick.tagName.toLowerCase(),
          name: (pick.name || pick.id || '').slice(0, 40)};
})()
"""


def fill(install_dir, agent, platform_id, field, approved=False, selector="",
         press_enter=False, page_url=""):
    """Type one stored value into the agent's browser. Returns what happened, never what.

    The order matters: the page is identified and checked BEFORE the value is unsealed,
    so a credential is not even taken out of the keystore for a page it does not belong
    to.
    """
    rows = [r for r in vault.entries(install_dir, agent)
            if r["platform"] == vault._slug(platform_id)]
    if not rows:
        raise Refused(u"Este agente no tiene una llave para «%s». "
                      u"La dueña puede añadirla en la consola." % platform_id)
    row = rows[0]
    if field not in row["fields"]:
        raise Refused(u"Esa llave guarda %s, no «%s»."
                      % (", ".join(row["fields"]) or u"nada", field))

    port = _browser_port(agent)
    target = _page(port, page_url)
    here = target.get("url") or ""
    if row["url"] and not same_site(row["url"], here):
        vault.note_fill(install_dir, agent, platform_id, field, here, False,
                        "otro sitio")
        raise Refused(
            u"Esa llave es de %s y la pestaña está en %s. No la escribo ahí. "
            u"Abre el sitio correcto, o pídele a la dueña que cambie la llave."
            % (host_of(row["url"]), host_of(here) or u"ninguna página"))

    ws = browser_label._WS(target["webSocketDebuggerUrl"], timeout=TIMEOUT)
    nid = [0]
    try:
        if selector:
            js = ("(() => { const el = document.querySelector(%s);"
                  " if (!el) return {ok:false, why:'no-field'};"
                  " el.focus(); if (el.value) el.select();"
                  " return {ok:true, type: el.type || '', name: (el.name||el.id||'')}; })()"
                  % json.dumps(selector))
        else:
            js = _FIND % json.dumps(field)
        got = _rpc(ws, nid, "Runtime.evaluate",
                   {"expression": js, "returnByValue": True})
        found = (got.get("result") or {}).get("value") or {}
        if not found.get("ok"):
            vault.note_fill(install_dir, agent, platform_id, field, here, False,
                            "sin campo")
            raise Refused(u"No encuentro dónde escribirlo en esta página. "
                          u"Abre el formulario de acceso y vuelve a intentarlo.")
        kind = (found.get("type") or "").lower()
        if field == "password" and kind != "password":
            vault.note_fill(install_dir, agent, platform_id, field, here, False,
                            "campo visible")
            raise Refused(u"El campo donde escribiría la contraseña no es de "
                          u"contraseña. Eso no es un formulario de acceso normal, "
                          u"así que no escribo nada.")

        if field == "totp":
            seed = vault.take(install_dir, agent, platform_id, "totp", approved)
            value = vault.totp(seed)
            del seed
        else:
            value = vault.take(install_dir, agent, platform_id, field, approved)

        # insertText and not a key-by-key typing: it is one message, it does not race
        # with the page's own handlers, and a password manager extension does not get a
        # chance to offer to save it.
        _rpc(ws, nid, "Input.insertText", {"text": value})
        del value
        if press_enter:
            for kind_ev in ("keyDown", "keyUp"):
                _rpc(ws, nid, "Input.dispatchKeyEvent",
                     {"type": kind_ev, "key": "Enter", "code": "Enter",
                      "windowsVirtualKeyCode": 13, "nativeVirtualKeyCode": 13})
        vault.note_fill(install_dir, agent, platform_id, field, here, True,
                        found.get("name", ""))
        return {"ok": True, "field": field, "where": here,
                "box": found.get("name", ""), "entered": bool(press_enter)}
    finally:
        try:
            ws.close()
        except Exception:  # noqa: BLE001
            pass
