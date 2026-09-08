r"""The Hermes plugin that turns each extra number into a real platform.

Hermes keeps one adapter per ``Platform`` enum value (``self.adapters[platform]``), so a
second WhatsApp number needs a second platform value. Hermes' own extension point provides
exactly that: ``platform_registry.register(PlatformEntry(name=...))``, and
``Platform._missing_()`` mints an enum member for any name the registry knows.

So this module writes a small plugin into the agent's own Hermes profile, and that plugin
registers one platform per row in numbers.py.

Why a generated plugin and not a patch
--------------------------------------
Compare with wa_patch.py, which edits Hermes' own ``bridge.js`` by text anchors and has to
be re-applied after every ``hermes update`` because the update git-pulls over it. This does
not: user plugins live under ``$HERMES_HOME/plugins/``, which is the owner's data
directory, not the vendored checkout. An update cannot take it away.

The one coupling that remains is an IMPORT of Hermes' bundled WhatsApp adapter class. That
is far safer than a text patch - it either resolves or it does not - but it is still a
dependency on somebody else's internals, so the plugin is written to FAIL LOUDLY: if the
class ever moves it logs the reason and registers nothing. An extra number that stops
existing quietly, while the wizard still lists it, is the outcome worth spending code to
avoid.

What the shim actually does
---------------------------
``WhatsAppAdapter`` hardcodes its identity in exactly one line::

    super().__init__(config, Platform.WHATSAPP)      # adapter.py

and ``whatsapp_common.py`` - the whole behaviour mixin - never mentions the platform at
all. So re-stamping ``self.platform`` after construction is the entire adaptation. Port and
session come from ``config.extra``, which the adapter already reads.

The plugin body is STATIC: it reads the number registry at runtime rather than being
regenerated per number. Adding a number is then a JSON write plus a gateway restart, and
the code the owner is running stays one reviewable file.
"""

import hashlib
import io
import os

from . import hermes_ctl

PLUGIN_KEY = "olivaw-extra-whatsapp"
PLUGIN_VERSION = "1.0.0"

_MANIFEST = u"""name: {key}
label: Números de WhatsApp adicionales (Olivaw)
kind: platform
version: {version}
description: >
  Registra un adaptador de WhatsApp por cada número extra que el agente atiende, para que
  un solo agente - una sola personalidad, unas solas instrucciones - responda en varias
  líneas a la vez. Cada número tiene su propio puente, su propia sesión y su propia lista
  de permitidos. Generado por Olivaw; la lista de números vive en olivaw-numbers.json.
author: Olivaw
"""

# NOTE: this file is written into the owner's Hermes profile and executed inside the
# gateway. It is kept deliberately small and dependency-free for that reason.
_PLUGIN = u'''"""Extra WhatsApp numbers for one Olivaw agent. GENERATED - do not edit.

Every number in olivaw-numbers.json (next to this profile's config.yaml) becomes its own
Hermes platform, so one agent answers several lines with one set of instructions.

Regenerate with Olivaw rather than editing: the wizard rewrites this file whenever its
content changes.
"""

import json
import logging
import os

log = logging.getLogger("olivaw.numbers")

REGISTRY_NAME = "{registry}"
PREFIX = "{prefix}"


def _home():
    """This profile's Hermes home, as the running gateway sees it.

    Resolved through Hermes' own helper so a multiplexed / profile-scoped gateway gets the
    profile it is currently acting for, not the default one.
    """
    try:
        from hermes_constants import get_hermes_home
        return str(get_hermes_home())
    except Exception:
        env = os.environ.get("HERMES_HOME")
        if env:
            return env
        local = os.environ.get("LOCALAPPDATA")
        if local and os.path.isdir(os.path.join(local, "hermes")):
            return os.path.join(local, "hermes")
        return os.path.join(os.path.expanduser("~"), ".hermes")


def _numbers():
    path = os.path.join(_home(), REGISTRY_NAME)
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh) or {{}}
    except (OSError, ValueError):
        return []
    rows = data.get("numbers")
    if not isinstance(rows, list):
        return []
    out = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        slug = str(row.get("slug") or "")
        # Mirror the wizard's own validation. This file is read by a long-running gateway
        # and a malformed slug would become a platform name, an env var name and a path.
        if not slug.isalnum() or not slug.islower() or not slug[:1].isalpha():
            log.warning("olivaw numbers: ignoring row with unusable slug %r", slug)
            continue
        if not row.get("port") or not row.get("session"):
            log.warning("olivaw numbers: %s has no port or session; ignoring", slug)
            continue
        out.append(row)
    return out


def _adapter_class():
    """Hermes' bundled WhatsApp adapter.

    The single point of coupling to Hermes internals. Raises rather than returning None so
    the caller reports WHY nothing registered - a number that silently vanishes while the
    wizard still lists it is the failure worth being loud about.
    """
    from plugins.platforms.whatsapp.adapter import WhatsAppAdapter
    return WhatsAppAdapter


def _factory_for(platform_name):
    def _build(config):
        from gateway.config import Platform
        adapter = _adapter_class()(config)
        # The adapter's constructor stamps Platform.WHATSAPP - the one line in it that
        # names a platform. Re-stamp so the gateway files this instance under its own key,
        # routes its inbound messages to its own session, and sends replies back out of
        # the number they arrived on. Nothing else in the adapter reads self.platform
        # during construction, so doing it here is safe.
        adapter.platform = Platform(platform_name)
        return adapter
    return _build


def _connected_for(row):
    def _is_connected(config):
        # "Configured" means a phone is really paired, not that a box was ticked: Baileys
        # writes creds.json as soon as its bridge starts, long before anyone scans a QR.
        try:
            with open(os.path.join(row["session"], "creds.json"), encoding="utf-8") as fh:
                creds = json.load(fh)
        except (OSError, ValueError):
            return False
        if not isinstance(creds, dict):
            return False
        if creds.get("registered") is True:
            return True
        me = creds.get("me")
        return isinstance(me, dict) and bool(str(me.get("id") or "").strip())
    return _is_connected


def register(ctx):
    rows = _numbers()
    if not rows:
        return
    try:
        _adapter_class()
    except Exception as e:
        # Hermes moved the adapter. Say so with the number names, because from the owner's
        # side the symptom is "my second line stopped answering" with nothing in between.
        log.error(
            "olivaw numbers: cannot load Hermes' WhatsApp adapter (%s). "
            "These extra numbers will NOT answer until Olivaw is updated: %s",
            e, ", ".join(str(r.get("label") or r.get("slug")) for r in rows),
        )
        return
    for row in rows:
        slug = row["slug"]
        name = row.get("platform") or (PREFIX + slug)
        up = slug.upper()
        ctx.register_platform(
            name=name,
            label=str(row.get("label") or slug),
            adapter_factory=_factory_for(name),
            check_fn=lambda: True,
            is_connected=_connected_for(row),
            allowed_users_env="WHATSAPP_%s_ALLOWED_USERS" % up,
            allow_all_env="WHATSAPP_%s_ALLOW_ALL_USERS" % up,
            cron_deliver_env_var="WHATSAPP_%s_HOME_CHANNEL" % up,
            max_message_length=4096,
            emoji="\\N{{SPEECH BALLOON}}",
            install_hint="Lo gestiona Olivaw, en el panel de WhatsApp.",
        )
        log.info("olivaw numbers: registered %s (port %s)", name, row.get("port"))
'''


def plugin_dir(profile=None, home=None):
    from . import numbers
    base = home or numbers.profile_home(profile)
    return os.path.join(base, "plugins", PLUGIN_KEY)


def render():
    from . import numbers
    return _PLUGIN.format(registry=numbers.REGISTRY_NAME, prefix=numbers.PLATFORM_PREFIX)


def render_manifest():
    return _MANIFEST.format(key=PLUGIN_KEY, version=PLUGIN_VERSION)


def _same(path, wanted):
    try:
        with io.open(path, encoding="utf-8") as fh:
            return fh.read() == wanted
    except OSError:
        return False


def install(profile=None, home=None, log=None):
    """Write the plugin. Rewrites only when the content actually differs."""
    d = plugin_dir(profile, home)
    init_path = os.path.join(d, "__init__.py")
    man_path = os.path.join(d, "plugin.yaml")
    body, manifest = render(), render_manifest()
    if _same(init_path, body) and _same(man_path, manifest):
        return {"ok": True, "changed": False, "path": d, "detail": "El plugin ya está al día."}
    try:
        os.makedirs(d, exist_ok=True)
        for path, text in ((init_path, body), (man_path, manifest)):
            with io.open(path, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(text)
    except OSError as e:
        return {"ok": False, "changed": False, "path": d,
                "detail": "No se pudo escribir el plugin: %s" % e}
    if log:
        log("numbers: wrote the extra-numbers plugin to %s" % d)
    return {"ok": True, "changed": True, "path": d,
            "sha": hashlib.sha256(body.encode("utf-8")).hexdigest()[:12]}


def enabled(profile=None, hermes=None):
    """Is the plugin in this profile's `plugins.enabled` allow-list?

    User plugins are opt-in by design - Hermes treats them as untrusted code - so writing
    the files is only half the job.
    """
    r = hermes_ctl.config_get("plugins.enabled", hermes, profile)
    return PLUGIN_KEY in (r or "")


def enable(profile=None, hermes=None, log=None):
    """Add it to plugins.enabled, without granting it the right to replace built-in tools.

    `plugins.enabled` is a YAML LIST, and `hermes config set` coerces a scalar - it would
    write a string where a list belongs. `hermes plugins enable` is the supported path and
    edits the list properly. `--no-allow-tool-override` both makes it non-interactive (the
    wizard has no terminal to prompt in) and DECLINES the privileged capability to replace
    built-in tools like shell_exec: this plugin only registers platforms and has no
    business holding it.
    """
    if enabled(profile, hermes):
        return {"ok": True, "changed": False}
    r = hermes_ctl._run(["plugins", "enable", PLUGIN_KEY, "--no-allow-tool-override"],
                        hermes, timeout=90, profile=profile)
    if not r["ok"]:
        # Older Hermes builds may not have the flag; retry without it. Non-interactive
        # stdin makes the prompt read EOF, which declines - the same answer we want.
        r = hermes_ctl._run(["plugins", "enable", PLUGIN_KEY], hermes, timeout=90,
                            profile=profile)
    ok = bool(r["ok"]) or enabled(profile, hermes)
    if log and ok:
        log("numbers: enabled the %s plugin for %s" % (PLUGIN_KEY, profile or "default"))
    return {"ok": ok, "changed": ok, "detail": (r["out"] or r["err"])[:300]}


def ensure(profile=None, hermes=None, log=None):
    res = install(profile, log=log)
    if not res.get("ok"):
        return res
    res["enabled"] = enable(profile, hermes, log=log)
    res["ok"] = bool(res["enabled"].get("ok"))
    if not res["ok"]:
        res["detail"] = ("El plugin está escrito pero Hermes no lo ha activado, así que los "
                         "números adicionales todavía no responden. Detalle: %s"
                         % res["enabled"].get("detail", ""))
    return res


def remove(profile=None, home=None, log=None):
    """Take the plugin away when the last extra number is gone."""
    import shutil
    d = plugin_dir(profile, home)
    if not os.path.isdir(d):
        return {"ok": True, "changed": False, "reason": "not-installed"}
    try:
        shutil.rmtree(d)
    except OSError as e:
        return {"ok": False, "changed": False, "detail": str(e)}
    if log:
        log("numbers: removed the extra-numbers plugin (no extra numbers left)")
    return {"ok": True, "changed": True, "removed": True, "path": d}


def status(profile=None, hermes=None):
    d = plugin_dir(profile)
    installed = os.path.isfile(os.path.join(d, "__init__.py"))
    return {
        "installed": installed,
        "current": installed and _same(os.path.join(d, "__init__.py"), render()),
        "enabled": enabled(profile, hermes),
        "path": d,
        "key": PLUGIN_KEY,
    }
