r"""Talk to your own agent from the browser, not only from Telegram.

Why this needed a real answer rather than a text box
----------------------------------------------------
Olivaw already has a bridge on 127.0.0.1:8790 that speaks OpenAI's chat API, and pointing a
chat box at it would have been two hours' work. It would also have been a lie: that bridge
is the *brain*, reached before Hermes exists. It has no persona, no memory, no skills, no
tools, no conversation. The owner would be talking to a raw model that shares nothing with
the agent they know from Telegram, and every difference would look like a bug.

Hermes ships the right door: the ``api_server`` platform, an OpenAI-compatible HTTP server
that runs a full agent turn - persona, memory, skills, tools, sessions - exactly as a
Telegram message does. So "talk from the UI" means enabling that platform and proxying to
it, and what the owner types reaches the same agent by the same path as everything else.

The security model, which is Hermes' own and worth stating
----------------------------------------------------------
This endpoint dispatches terminal-capable agent work, so it is not a casual thing to open:

* it binds **127.0.0.1 only** (Hermes' ``DEFAULT_HOST``), so nothing off this machine can
  reach it;
* Hermes **refuses to start it without a strong ``API_SERVER_KEY``** - it rejects
  placeholders and anything under 16 characters, with the reasoning written into its own
  source: a guessable key here is remote code execution. We generate 64 hex characters;
* the key lives in the profile's ``.env`` and **never reaches the browser**. The page talks
  to the wizard, which is itself loopback-bound and requires its own one-time token; the
  wizard adds the bearer header server-side.

That is why this module proxies instead of handing the page a key and a port.

One deliberate product choice
-----------------------------
A conversation started here is its OWN conversation, not a continuation of the Telegram
thread. Hermes keys sessions per platform and chat, so joining them would mean forging a
Telegram session id - and it would put whatever the owner types in the browser into the
history the agent replays on Telegram. Separate is both simpler and the honest shape; the
UI says so rather than letting the owner guess.

Files, which this screen could not do at all
--------------------------------------------
Every other channel carries files. On Telegram the owner sends a photo and the agent sees
it; the agent writes a report and Telegram delivers it. Here she could only type, and the
agent - correctly - said it could not send files. Three separate reasons, each fixed here:

* **It did not know where it was.** Hermes' ``api_server`` platform hint says "the
  rendering layer is unknown - assume plain text", which is right for a generic API client
  and wrong for this one. So every turn carries ``system_message`` (Hermes appends it to
  the persona for that turn only, never to the trajectory) saying this screen renders
  images and files and that ``MEDIA:<absolute path>`` is how to hand one over. Without it
  the agent tells the owner it has no way to send her the thing it just made.

* **Inbound: images go IN the turn, everything else goes on DISK.** Hermes accepts
  OpenAI's vision parts and rejects ``file``/``input_file`` parts outright, so a PDF
  cannot be inlined. It does not need to be: this agent runs on the same machine, with
  tools. Every attachment is written into the agent's own workspace (``adjuntos/``) and
  named in the message with its path, so the agent opens it the way it opens anything
  else; images are ALSO inlined as data URLs so it literally sees them. The browser never
  names a path - it uploads and gets an id back, and the id is what ``send`` takes.

* **Outbound: the reply is read for files.** Hermes rewrites ``MEDIA:`` image tags into
  base64 markdown for API callers (``_resolve_media_to_data_urls``) and leaves every other
  one as the raw tag, which is why the old chat showed either a megabyte of base64 or a
  bare Windows path. Both are lifted out of the text here and returned as structured
  files: images the page shows, anything else becomes a download button.

The download rule, since this reads files off disk
--------------------------------------------------
Only a path the AGENT ITSELF wrote into a reply becomes downloadable, and only through the
opaque id minted for it at that moment - the browser can never ask this server for a path
of its own choosing, which is the difference between "deliver what the agent sent" and an
arbitrary-file-read endpoint. On top of that every path is re-checked at download time
against ``_is_secret``: credentials, keys, ``.ssh``/``.aws``, Hermes' own ``.env`` and
pairing store. That mirrors what Hermes applies to the agent's file delivery on every
other channel, and it is the part that matters when a web page the agent read tries to
talk it into attaching ``~/.hermes/.env``.
"""

import base64
import collections
import io
import json
import os
import re
import secrets
import socket
import urllib.error
import urllib.parse
import urllib.request

from . import hermes_ctl

PLATFORM = "api_server"
KEY_ENV = "API_SERVER_KEY"
DEFAULT_PORT = 8642          # Hermes' own default
PORT_SPAN = 40
HOST = "127.0.0.1"
KEY_BYTES = 32               # 64 hex chars, far above Hermes' 16-char floor

_SESSION_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")

# ── files ────────────────────────────────────────────────────────────────────
ATTACH_DIR = "adjuntos"          # inside the agent's own workspace, in its own language
MAX_UPLOAD_BYTES = 25 * 1024 * 1024
# Hermes rejects a request body over 10MB (api_server.MAX_REQUEST_BYTES), and base64 adds
# a third. 4MB of image is 5.4MB on the wire and leaves room for the conversation; a
# bigger image still reaches the agent - as a file on disk, which its own tools can open.
MAX_INLINE_IMAGE_BYTES = 4 * 1024 * 1024
MAX_DOWNLOAD_BYTES = 25 * 1024 * 1024
MAX_FILES_PER_TURN = 10
GRANT_LIMIT = 400                # ids kept alive; the oldest fall off the end

_IMAGE_MIME = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
               ".gif": "image/gif", ".webp": "image/webp", ".bmp": "image/bmp"}
_MIME = {".pdf": "application/pdf", ".txt": "text/plain; charset=utf-8",
         ".md": "text/markdown; charset=utf-8", ".csv": "text/csv; charset=utf-8",
         ".json": "application/json", ".xml": "application/xml",
         ".html": "text/html; charset=utf-8", ".htm": "text/html; charset=utf-8",
         ".zip": "application/zip", ".mp3": "audio/mpeg", ".ogg": "audio/ogg",
         ".wav": "audio/wav", ".mp4": "video/mp4", ".svg": "image/svg+xml",
         ".doc": "application/msword", ".xls": "application/vnd.ms-excel",
         ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
         ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
         ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation"}

# What the agent is told, for this turn only, about where it is talking. Hermes appends
# `system_message` to the persona and keeps it out of the trajectory, so this costs one
# short paragraph per turn and nothing permanent. Without it the platform hint for
# `api_server` stands - "the rendering layer is unknown, assume plain text" - and the agent
# answers that it cannot send files, which is the bug.
# Note what it does NOT say: that this screen renders Markdown. It does not - the log
# escapes what it is given, by design - and Hermes' own api_server hint already tells the
# agent to write plain text. Only the part that was wrong is corrected.
TURN_BRIEF = (
    "Estás en la consola de Olivaw, hablando con la persona que te administra desde su "
    "navegador. Sigue escribiendo en texto normal, pero esta pantalla sí muestra "
    "imágenes y entrega archivos: para darle un archivo escribe MEDIA: seguido de la "
    "ruta absoluta (por ejemplo MEDIA:C:\\Users\\ana\\informe.pdf) y la consola lo "
    "convierte en una imagen visible o en un botón de descarga, y quita esa línea del "
    "texto. Nunca digas que no puedes enviar archivos, y no pegues el contenido de uno "
    "cuando puedes entregarlo así."
)

# Credentials never leave through this door, however the path got into the reply. The same
# ground Hermes' own validate_media_delivery_path covers for every other channel.
_SECRET_NAMES = frozenset((
    ".env", ".env.local", ".netrc", "auth.json", "credentials", "credentials.json",
    ".anthropic_oauth.json", "google_token.json", "token.json", "tokens.json",
    "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519", "shadow", "sam", "security",
))
_SECRET_EXT = frozenset((
    ".key", ".pem", ".pfx", ".p12", ".jks", ".keystore", ".ppk", ".asc", ".gpg", ".kdbx",
))
_SECRET_DIRS = frozenset((
    ".ssh", ".aws", ".gnupg", ".azure", ".kube", ".docker", "gcloud",
    "pairing", "credentials", "windows", "system32",
))
# A report called "tokens de la clinica.pdf" is a document. A file called tokens.json is a
# credential store. So the word only disqualifies a file that is itself configuration.
_SECRET_WORDS = ("secret", "password", "passwd", "token", "credential", "apikey", "api_key")
_CONFIGISH = frozenset((".json", ".yaml", ".yml", ".ini", ".cfg", ".conf", ".toml", ".env"))

_WIN_RESERVED = frozenset(
    ["con", "prn", "aux", "nul"] + ["com%d" % i for i in range(1, 10)]
    + ["lpt%d" % i for i in range(1, 10)])
_BAD_NAME_CHARS = re.compile(r'[\x00-\x1f<>:"/\\|?*]')

# The agent's own way of handing a file over, and what Hermes turns an image one into
# before it reaches us. Deliberately NOT Hermes' regex: that one is anchored on a list of
# known extensions, and a file the owner should still be able to download (a .log, a .py,
# a Makefile) falls through it and is left on screen as raw text.
#
# Three ways to spell the path, tried in that order. Quoted is easy. Unquoted is not: half
# the folders on a Windows machine have a space in them ("Mis documentos", "informe
# final.pdf"), so the second branch lets the path run through spaces and stops it at the
# first extension followed by whitespace - without that anchor there is nothing to say
# where the path ends and the sentence resumes. The third branch is what catches a file
# with no extension at all, which can only be the run of non-space characters.
_PATH_HEAD = r"(?:[A-Za-z]:[\\/]|/|~[\\/])"
_MEDIA_RE = re.compile(
    r"""[ \t]*[`"']?MEDIA:[ \t]*"""
    r"""(?P<path>`[^`\n]+`|"[^"\n]+"|'[^'\n]+'"""
    + r"""|""" + _PATH_HEAD + r"""[^\n]*?\.[A-Za-z0-9]{1,8}(?=[\s`"',;:)\]}]|$)"""
    + r"""|""" + _PATH_HEAD + r"""[^\s`"'\n]+)"""
    r"""[`"']?""")
# The leading [ \t]* is not padding: without it, lifting a file out of the middle of a
# sentence leaves behind the two spaces that used to sit either side of it.
_DATA_IMG_RE = re.compile(
    r"[ \t]*!\[[^\]\n]*\]\((?P<url>data:image/[A-Za-z0-9.+-]+;base64,[A-Za-z0-9+/=]+)\)")

# Ids the browser may name, and nothing else. Two tables because they are two different
# permissions: an upload id lets the owner ATTACH a file she just chose, a grant id lets
# her DOWNLOAD a file the agent just offered. Neither is a path.
_UPLOADS = collections.OrderedDict()
_GRANTS = collections.OrderedDict()


def hermes_home():
    env = os.environ.get("HERMES_HOME")
    if env:
        return env
    local = os.environ.get("LOCALAPPDATA")
    if local and os.path.isdir(os.path.join(local, "hermes")):
        return os.path.join(local, "hermes")
    return os.path.join(os.path.expanduser("~"), ".hermes")


def profile_home(profile=None):
    if not profile or profile == "default":
        return hermes_home()
    return os.path.join(hermes_home(), "profiles", profile)


def _read_env(path):
    out = {}
    try:
        with io.open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    out[k.strip()] = v.strip().strip('"').strip("'")
    except OSError:
        pass
    return out


def env_of(profile=None, hermes=None):
    path = ""
    try:
        path = hermes_ctl.env_path(hermes, profile)
    except Exception:  # noqa: BLE001
        pass
    return _read_env(path or os.path.join(profile_home(profile), ".env"))


def _port_free(port):
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind((HOST, port))
        return True
    except OSError:
        return False


def _configured_port(profile=None, hermes=None):
    """The port this profile's api_server is set to, if any."""
    raw = hermes_ctl.config_get("platforms.%s.extra.port" % PLATFORM, hermes, profile)
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        env = env_of(profile, hermes).get("API_SERVER_PORT", "")
        try:
            return int(env)
        except (TypeError, ValueError):
            return None


def _taken_ports(hermes=None, install_dir=None):
    """Ports already handed to another agent's api_server, so two never collide."""
    taken = set()
    try:
        from . import agents_registry
        profiles = [None] + [a.get("profile") or a.get("slug")
                             for a in agents_registry.list_agents(install_dir)]
    except Exception:  # noqa: BLE001
        profiles = [None]
    for prof in profiles:
        p = _configured_port(prof, hermes)
        if p:
            taken.add(p)
    return taken


def pick_port(profile=None, hermes=None, install_dir=None):
    """This profile's port if it has one, else the first free one nobody claims."""
    mine = _configured_port(profile, hermes)
    if mine:
        return mine
    taken = _taken_ports(hermes, install_dir)
    for port in range(DEFAULT_PORT, DEFAULT_PORT + PORT_SPAN):
        if port not in taken and _port_free(port):
            return port
    return None


def _http(url, key, data=None, method=None, timeout=25):
    """One request to the agent's own API server. Returns (ok, parsed_or_text, status).

    The bearer key is added HERE and nowhere the browser can see. Never logged, never
    echoed into a result, and never placed in a URL.
    """
    body = None
    headers = {"Accept": "application/json", "User-Agent": "olivaw-wizard"}
    if key:
        headers["Authorization"] = "Bearer " + key
    if data is not None:
        body = json.dumps(data).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8", "replace")
            try:
                return True, json.loads(raw), r.status
            except json.JSONDecodeError:
                return True, raw, r.status
    except urllib.error.HTTPError as e:
        raw = ""
        try:
            raw = e.read().decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            pass
        try:
            return False, json.loads(raw), e.code
        except Exception:  # noqa: BLE001
            return False, raw or str(e), e.code
    except Exception as e:  # noqa: BLE001
        return False, str(e), 0


def base_url(profile=None, hermes=None):
    port = _configured_port(profile, hermes) or DEFAULT_PORT
    return "http://%s:%d" % (HOST, port)


# ── turning it on ────────────────────────────────────────────────────────────

def enable(profile=None, hermes=None, install_dir=None, log=None):
    """Enable the agent's own API server, on loopback, with a strong generated key.

    Idempotent: an existing key is REUSED rather than rotated. Rotating it on every call
    would invalidate any client the owner has pointed at their agent, and would do so
    silently, from a button whose label says nothing about keys.
    """
    port = pick_port(profile, hermes, install_dir)
    if port is None:
        return {"ok": False, "detail": "No encontré un puerto libre para hablar con el agente."}
    key = env_of(profile, hermes).get(KEY_ENV, "").strip()
    fresh = False
    if len(key) < 32:            # covers absent, placeholder and anything Hermes would reject
        key = secrets.token_hex(KEY_BYTES)
        fresh = True
    steps = []

    def step(name, res):
        steps.append({"name": name, "ok": bool(res.get("ok")), "detail": res.get("detail", "")})

    if fresh:
        step("clave", hermes_ctl.set_env_vars({KEY_ENV: key}, hermes, profile))
    step("puerto", hermes_ctl.config_set("platforms.%s.extra.port" % PLATFORM, str(port),
                                         hermes, profile))
    # Loopback is written explicitly rather than left to Hermes' default: the default is
    # right today, and a config file that SAYS 127.0.0.1 is the difference between "we are
    # sure" and "we assume" for something that dispatches terminal-capable work.
    step("host", hermes_ctl.config_set("platforms.%s.extra.host" % PLATFORM, HOST,
                                       hermes, profile))
    step("activar", hermes_ctl.config_set("platforms.%s.enabled" % PLATFORM, "true",
                                          hermes, profile))
    ok = all(s["ok"] for s in steps)
    if log and ok:
        log("talk: %s can be reached at %s:%d (key %s)"
            % (profile or "default", HOST, port, "generated" if fresh else "kept"))
    return {"ok": ok, "port": port, "steps": steps, "key_generated": fresh,
            "detail": ("Listo. Falta reiniciar el agente para que empiece a escuchar."
                       if ok else "No pude configurarlo del todo.")}


def disable(profile=None, hermes=None, log=None):
    """Turn it off. The key is LEFT in place so turning it back on keeps the same one."""
    r = hermes_ctl.config_set("platforms.%s.enabled" % PLATFORM, "false", hermes, profile)
    if log and r.get("ok"):
        log("talk: %s no longer listens for browser chat" % (profile or "default"))
    return {"ok": bool(r.get("ok")), "detail": r.get("detail", "")}


def status(profile=None, hermes=None, install_dir=None):
    """Configured? Listening? Answering? Three different questions, three answers.

    A UI that collapses them says "not available" for a gateway that simply has not
    restarted yet, which is the state right after the owner enables it.
    """
    key = env_of(profile, hermes).get(KEY_ENV, "").strip()
    enabled = (hermes_ctl.config_get("platforms.%s.enabled" % PLATFORM, hermes, profile)
               or "").strip().lower() in ("true", "1", "yes", "on")
    port = _configured_port(profile, hermes)
    out = {"ok": True, "enabled": enabled, "configured": bool(key) and bool(port),
           "port": port, "has_key": bool(key), "reachable": False, "ready": False}
    if not (enabled and key and port):
        out["detail"] = ("Aún no está activado. Actívalo y podrás escribirle desde aquí, "
                         "igual que por Telegram." if not enabled else
                         "Falta configurarlo del todo.")
        return out
    ok, data, code = _http("http://%s:%d/health" % (HOST, port), key, timeout=5)
    out["reachable"] = bool(ok)
    if not ok and code == 0:
        out["detail"] = ("Está configurado pero el agente todavía no escucha. Se activa al "
                         "reiniciarse, normalmente en menos de un minuto.")
        return out
    if code == 401:
        # Only reachable if the running gateway holds a DIFFERENT key than the .env we just
        # read - i.e. it started before the key was written. Naming that beats "unauthorized".
        out["detail"] = ("El agente escucha pero con otra clave: arrancó antes del último "
                         "cambio. Se arregla en cuanto se reinicie.")
        return out
    out["ready"] = bool(ok)
    out["detail"] = ("Puedes escribirle desde aquí." if ok else
                     "Responde algo inesperado en %s:%d." % (HOST, port))
    if isinstance(data, dict) and data.get("model"):
        out["model"] = data["model"]
    return out


# ── files, both ways ─────────────────────────────────────────────────────────

def workspace_for(profile=None, install_dir=None):
    """The folder this agent actually works in - where an attachment belongs.

    Not a shared inbox: a file dropped on Daneel's chat has no business landing in the
    main agent's workspace, where a different agent would read it. An agent with no
    registry row still gets its OWN folder rather than falling back to the default one.
    """
    prof = (profile or "default").strip() or "default"
    if prof != "default":
        try:
            from . import agents_registry
            for a in agents_registry.list_agents(install_dir):
                if (a.get("profile") or a.get("slug")) == prof and a.get("workspace"):
                    return a["workspace"]
            return os.path.join(agents_registry.agent_dir(prof, install_dir), "workspace")
        except Exception:  # noqa: BLE001
            pass
    return os.environ.get("CLAUDE_BRIDGE_WORKSPACE",
                          os.path.join(os.path.expanduser("~"), "hermes-workspace"))


def attach_dir(profile=None, install_dir=None):
    d = os.path.join(workspace_for(profile, install_dir), ATTACH_DIR)
    os.makedirs(d, exist_ok=True)
    return d


def _human(n):
    size = float(n or 0)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return "%d %s" % (size, unit) if unit == "B" else "%.1f %s" % (size, unit)
        size /= 1024.0
    return "%d B" % int(n or 0)


def kind_of(name):
    return "image" if os.path.splitext(name or "")[1].lower() in _IMAGE_MIME else "file"


def mime_of(name):
    ext = os.path.splitext(name or "")[1].lower()
    return _IMAGE_MIME.get(ext) or _MIME.get(ext) or "application/octet-stream"


def safe_name(raw):
    """A file name that can only ever be a name - never a path, never a device.

    The browser sends whatever the operating system gave it and this writes to disk, so
    what makes a name dangerous is removed rather than detected: both separators and the
    characters Windows forbids, the leading dots that are what turns a name into a walk up
    the tree, and the reserved device names that make an open() hang on a serial port.
    """
    name = str(raw or "").replace("\\", "/").split("/")[-1]
    name = _BAD_NAME_CHARS.sub("_", name).strip().strip(". ")
    if not name:
        name = "archivo"
    stem, ext = os.path.splitext(name)
    if stem.lower() in _WIN_RESERVED:
        stem = "_" + stem
    if len(stem) > 60:
        stem = stem[:60]
    if len(ext) > 12:
        ext = ext[:12]
    return (stem or "archivo") + ext


def _unique(folder, name):
    """Never overwrite. Two screenshots pasted in a row are both called image.png."""
    stem, ext = os.path.splitext(name)
    cand, n = name, 2
    while os.path.exists(os.path.join(folder, cand)):
        if n > 99:
            return "%s-%s%s" % (stem, secrets.token_hex(3), ext)
        cand = "%s (%d)%s" % (stem, n, ext)
        n += 1
    return cand


def _remember(table, rec):
    """Mint the id the browser gets instead of a path, and keep the table bounded."""
    tok = secrets.token_urlsafe(12)
    table[tok] = rec
    while len(table) > GRANT_LIMIT:
        table.popitem(last=False)
    return tok


def _is_secret(path):
    """True for anything that must not leave through this door, wherever it sits.

    The check that matters when a web page the agent read talks it into ending an
    otherwise innocent answer with MEDIA: and the path of the profile's .env.
    """
    p = str(path or "")
    name = os.path.basename(p).lower()
    stem, ext = os.path.splitext(name)
    if name in _SECRET_NAMES or ext in _SECRET_EXT:
        return True
    if name.startswith("id_rsa") or name.startswith("id_ed25519"):
        return True
    if {seg.lower() for seg in re.split(r"[\\/]+", p) if seg} & _SECRET_DIRS:
        return True
    if ext in _CONFIGISH and any(w in stem for w in _SECRET_WORDS):
        return True
    return False


def _clean_path(raw):
    """A path out of the agent's own text, or "" - resolved, existing, not a credential."""
    cand = str(raw or "").strip()
    if len(cand) >= 2 and cand[0] == cand[-1] and cand[0] in "`\"'":
        cand = cand[1:-1].strip()
    cand = cand.strip("`\"'").rstrip(",.;:)]}")
    if not cand:
        return ""
    try:
        p = os.path.realpath(os.path.expanduser(cand))
    except (OSError, ValueError):
        return ""
    if not os.path.isabs(p) or not os.path.isfile(p) or _is_secret(p):
        return ""
    return p


def _b64(raw):
    """Strict base64, data-URL prefix tolerated. Strict because this writes to disk."""
    s = str(raw or "").strip()
    if s.startswith("data:"):
        s = s.split(",", 1)[-1]
    return base64.b64decode(re.sub(r"\s+", "", s), validate=True)


def save_upload(name="", data_b64="", profile=None, install_dir=None):
    """Put one file the owner attached into the agent's own folder, and mint its id.

    It is written to disk even when it is an image that will also travel inline: the agent
    may want to crop it, attach it to an email, or look at it again three turns later, and
    a data URL that existed only inside one request cannot be opened by any of its tools.
    """
    try:
        raw = _b64(data_b64)
    except Exception:  # noqa: BLE001
        return {"ok": False, "detail": "No pude leer ese archivo."}
    if not raw:
        return {"ok": False, "detail": "Ese archivo está vacío."}
    if len(raw) > MAX_UPLOAD_BYTES:
        return {"ok": False,
                "detail": "Pesa %s y el máximo por archivo es %s."
                          % (_human(len(raw)), _human(MAX_UPLOAD_BYTES))}
    folder = attach_dir(profile, install_dir)
    fname = _unique(folder, safe_name(name))
    path = os.path.join(folder, fname)
    try:
        with io.open(path, "wb") as fh:
            fh.write(raw)
    except OSError as e:
        return {"ok": False, "detail": "No pude guardarlo en la carpeta del agente: %s" % e}
    rec = {"name": fname, "path": path, "size": len(raw),
           "kind": kind_of(fname), "mime": mime_of(fname)}
    rec["id"] = _remember(_UPLOADS, rec)
    return {"ok": True, "file": dict(rec), "folder": folder}


def read_files_out(text):
    """Lift the files out of a reply, leaving the sentence the owner is meant to read.

    By the time this runs Hermes has already rewritten MEDIA: tags for small images into
    base64 markdown and left every other tag exactly as the agent wrote it - so both forms
    arrive in one string, and the old chat escaped the lot: a megabyte of base64, or a
    bare Windows path, printed as the answer.

    A tag whose path does not check out is LEFT in the text on purpose. A file quietly
    vanishing from an answer is worse than a visible path the owner can ask about, and it
    is also the only trace that something was refused.
    """
    found = []
    body = str(text or "")

    def _img(m):
        url = m.group("url")
        head = url[5:url.index(";")] if ";" in url else "image/png"
        ext = {"image/jpeg": ".jpg", "image/png": ".png", "image/gif": ".gif",
               "image/webp": ".webp", "image/bmp": ".bmp"}.get(head, ".png")
        size = int(len(url.split(",", 1)[-1]) * 3 / 4)
        found.append({"kind": "image", "name": "imagen-%d%s" % (len(found) + 1, ext),
                      "size": size, "human": _human(size), "data_url": url})
        return ""

    body = _DATA_IMG_RE.sub(_img, body)

    def _media(m):
        p = _clean_path(m.group("path"))
        if not p:
            return m.group(0)
        try:
            size = os.path.getsize(p)
        except OSError:
            return m.group(0)
        name = os.path.basename(p)
        item = {"kind": kind_of(name), "name": name, "size": size, "human": _human(size),
                "folder": os.path.dirname(p)}
        if size <= MAX_DOWNLOAD_BYTES:
            item["id"] = _remember(_GRANTS, {"name": name, "path": p, "mime": mime_of(name)})
        found.append(item)
        return ""

    body = _MEDIA_RE.sub(_media, body)
    body = re.sub(r"[ \t]+\n", "\n", body)
    body = re.sub(r"\n{3,}", "\n\n", body).strip()
    return body, found


def file_bytes(file_id=""):
    """Hand back one file the agent itself offered, by the id minted when it offered it.

    Everything is checked again here rather than trusted from when the grant was made: the
    path is re-resolved (a symlink can be swapped between the reply and the click), it
    must still be a file, it must still pass _is_secret, and it must still be small enough
    to go through a browser.
    """
    rec = _GRANTS.get(str(file_id or ""))
    if not rec:
        return {"ok": False,
                "detail": "Ese archivo ya no está disponible. Pídeselo otra vez al agente."}
    try:
        p = os.path.realpath(rec["path"])
    except (OSError, ValueError):
        p = ""
    if not p or not os.path.isfile(p) or _is_secret(p):
        return {"ok": False, "detail": "Ese archivo ya no está donde estaba."}
    size = os.path.getsize(p)
    if size > MAX_DOWNLOAD_BYTES:
        return {"ok": False,
                "detail": "Pesa %s, demasiado para el navegador. Está en %s"
                          % (_human(size), os.path.dirname(p))}
    try:
        with io.open(p, "rb") as fh:
            data = fh.read()
    except OSError as e:
        return {"ok": False, "detail": "No pude leerlo: %s" % e}
    return {"ok": True, "name": rec["name"], "mime": rec["mime"], "size": size,
            "data_b64": base64.b64encode(data).decode("ascii")}


# ── talking ──────────────────────────────────────────────────────────────────

def _require(profile, hermes):
    key = env_of(profile, hermes).get(KEY_ENV, "").strip()
    port = _configured_port(profile, hermes)
    if not key or not port:
        return None, None, {"ok": False,
                            "detail": "Todavía no está activado para este agente."}
    return key, port, None


def _safe_session(sid):
    return bool(sid) and _SESSION_RE.match(str(sid)) is not None


def sessions(profile=None, hermes=None, limit=20):
    key, port, err = _require(profile, hermes)
    if err:
        return err
    ok, data, code = _http("http://%s:%d/api/sessions?limit=%d" % (HOST, port, int(limit)), key)
    if not ok:
        return {"ok": False, "detail": _explain(code, data)}
    rows = data.get("data") if isinstance(data, dict) else data
    return {"ok": True, "sessions": rows if isinstance(rows, list) else []}


def create(profile=None, hermes=None, title=""):
    key, port, err = _require(profile, hermes)
    if err:
        return err
    url = "http://%s:%d/api/sessions" % (HOST, port)
    payload = {"title": title[:120]} if title else {}
    ok, data, code = _http(url, key, data=payload, method="POST")
    # Hermes requires session titles to be UNIQUE and answers 400 when one is reused. A
    # title is a nicety here - the conversation works perfectly without one - so losing the
    # title beats losing the conversation. Learned from the live server: the second call
    # with the same title failed while the first had already created a session.
    if not ok and payload and code == 400 and "already in use" in str(data):
        ok, data, code = _http(url, key, data={}, method="POST")
    if not ok:
        return {"ok": False, "detail": _explain(code, data)}
    sid = _session_id(data)
    return {"ok": bool(sid), "session_id": sid, "session": (data or {}).get("session") or data,
            "detail": "" if sid else
                      "El agente creó la conversación pero no devolvió un identificador."}


def _session_id(data):
    """The id out of a create/read response.

    Hermes nests it: ``{"object": "hermes.session", "session": {"id": ...}}``. Read off the
    live server rather than guessed - the first version looked for a top-level ``id``,
    which parsed fine, found nothing, and reported "ok: False, detail: None". The flat
    forms are kept as fallbacks in case the shape moves again.
    """
    if not isinstance(data, dict):
        return ""
    inner = data.get("session")
    if isinstance(inner, dict) and inner.get("id"):
        return str(inner["id"])
    return str(data.get("id") or data.get("session_id") or "")


def history(profile=None, hermes=None, session_id=""):
    if not _safe_session(session_id):
        return {"ok": False, "detail": "Conversación no válida."}
    key, port, err = _require(profile, hermes)
    if err:
        return err
    ok, data, code = _http("http://%s:%d/api/sessions/%s/messages"
                           % (HOST, port, urllib.parse.quote(session_id, safe="")), key)
    if not ok:
        return {"ok": False, "detail": _explain(code, data)}
    rows = data.get("data") if isinstance(data, dict) else data
    return {"ok": True, "messages": rows if isinstance(rows, list) else []}


def _attached_note(picked):
    """What the agent is told about the files, in the same message as the question.

    The path is the point. Hermes refuses `file` parts outright, so a PDF cannot ride
    inside the turn - but this agent is on the same machine as the file, and naming where
    it landed is all it needs to open it with the tools it already has.
    """
    lines = ["- %s (%s): %s" % (f["name"], _human(f["size"]), f["path"]) for f in picked]
    return ("\n\n[Archivos que te acabo de adjuntar, ya guardados en tu carpeta de "
            "trabajo:\n%s\nÁbrelos con tus herramientas si necesitas lo que hay dentro.]"
            % "\n".join(lines))


def send(profile=None, hermes=None, session_id="", text="", files=None, timeout=600):
    """One agent turn, with whatever was attached and whatever comes back.

    Long timeout on purpose - a real turn can use tools for minutes.

    `files` is a list of UPLOAD IDS, never paths. The browser uploaded them a moment ago
    and got ids back; this is where an id turns into something the agent can use. An id
    this process never minted is dropped rather than guessed at, which is what keeps
    "attach a file" from also meaning "read any file on this machine".

    Images travel BOTH ways at once: inline as a data URL so the agent literally sees the
    screenshot, and as a path so it can work on the file. Anything over
    MAX_INLINE_IMAGE_BYTES goes by path alone - Hermes rejects a request body over 10MB,
    and an image that bounced off that limit would have failed the whole turn.
    """
    if not _safe_session(session_id):
        return {"ok": False, "detail": "Conversación no válida."}
    picked = []
    for tok in list(files or [])[:MAX_FILES_PER_TURN]:
        rec = _UPLOADS.get(str(tok))
        if rec and os.path.isfile(rec["path"]):
            picked.append(rec)
    body_text = (text or "").strip()
    if not body_text and not picked:
        return {"ok": False, "detail": "Escribe algo primero."}
    key, port, err = _require(profile, hermes)
    if err:
        return err

    if picked:
        if not body_text:
            body_text = ("Te adjunto %s." % (picked[0]["name"] if len(picked) == 1
                                             else "%d archivos" % len(picked)))
        body_text += _attached_note(picked)

    parts = [{"type": "text", "text": body_text}]
    for f in picked:
        if f["kind"] != "image" or f["size"] > MAX_INLINE_IMAGE_BYTES:
            continue
        try:
            with io.open(f["path"], "rb") as fh:
                blob = base64.b64encode(fh.read()).decode("ascii")
        except OSError:
            continue
        parts.append({"type": "image_url",
                      "image_url": {"url": "data:%s;base64,%s" % (f["mime"], blob)}})
    # A turn with no image stays a plain string, exactly as before: Hermes collapses a
    # text-only part list anyway, and the string is the shape its prompt cache was built
    # against. Nothing about typing a sentence changed because attachments now exist.
    message = parts if len(parts) > 1 else body_text

    ok, data, code = _http(
        "http://%s:%d/api/sessions/%s/chat" % (HOST, port,
                                               urllib.parse.quote(session_id, safe="")),
        key, data={"message": message, "system_message": TURN_BRIEF},
        method="POST", timeout=timeout)
    if not ok:
        return {"ok": False, "detail": _explain(code, data)}
    msg = ((data or {}).get("message") or {}).get("content", "")
    reply, out = read_files_out(msg)
    return {"ok": True, "reply": reply, "files": out,
            "session_id": (data or {}).get("session_id") or session_id,
            "usage": (data or {}).get("usage")}


def _explain(code, data):
    """Turn an HTTP failure into something the owner can act on."""
    if code == 0:
        return ("El agente dejó de escuchar. Suele ser un reinicio en curso; espera unos "
                "segundos y vuelve a intentar.")
    if code == 401:
        return ("El agente arrancó con otra clave. Se arregla al reiniciarse; si insiste, "
                "vuelve a activarlo aquí.")
    if code == 404:
        return "Esa conversación ya no existe."
    detail = ""
    if isinstance(data, dict):
        detail = str(((data.get("error") or {}) if isinstance(data.get("error"), dict)
                      else {}).get("message") or data.get("detail") or "")
    elif isinstance(data, str):
        detail = data[:200]
    return ("El agente respondió con un error (%s)%s"
            % (code, (": " + detail) if detail else "."))
