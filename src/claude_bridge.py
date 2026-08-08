"""
Claude Code Bridge — OpenAI-compatible HTTP server backed by the Claude Code CLI.

Hermes points its base_url at this server. Claude Code is the BRAIN (reasoning +
decisions); Hermes is the HANDS (it executes its own tools: terminal, file, web,
skills, memory, cronjob, send_message, browser, ...). This is a function-calling
shim: when Hermes sends its tool catalog, Claude Code is asked to decide the next
step and emit tool calls in a strict protocol, which the bridge translates into
OpenAI `tool_calls`. Hermes runs the tools and loops back with the results.

Two request shapes are handled:
  • With `tools` present  -> DECISION protocol -> returns tool_calls OR a final message.
  • Without `tools`        -> PLAIN completion (used by Hermes for context-compression
                              summaries, title generation, etc.) -> returns text.

Endpoints:
  GET  /health
  GET  /v1/models
  POST /v1/chat/completions   (streaming + non-streaming)

Run:  python claude_bridge.py [--port 8787]
"""

import argparse
import base64
import hashlib
import json
import logging
import os
import re
import subprocess
import threading
import time
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

CLAUDE_CMD = os.environ.get(
    "CLAUDE_BRIDGE_CLAUDE", r"C:\Users\revol\AppData\Roaming\npm\claude.cmd"
)
WORKSPACE = os.environ.get(
    "CLAUDE_BRIDGE_WORKSPACE", r"C:\Users\revol\hermes-workspace"
)
SUBPROCESS_TIMEOUT = int(os.environ.get("CLAUDE_BRIDGE_TIMEOUT", "1500"))

# The spawned `claude -p` MUST run isolated from the user's global Claude Code config,
# or it inherits their MCP servers (mcp-unity, Higgsfield/Pixa/etc.) as its "real" tools.
# When that happens the brain sees the Hermes tool catalog (terminal/memory/cronjob) as
# NOT matching its registered tools, decides the whole Hermes framing is a prompt
# INJECTION, and refuses — answering as generic, defensive Claude Code ("I only have
# image/Unity tools; I can't write to HQ"). `--strict-mcp-config` + an empty --mcp-config
# loads zero MCP servers, so the brain is a clean pure-reasoner with no conflicting tools.
_HERE = os.path.dirname(os.path.abspath(__file__))
EMPTY_MCP = os.path.join(_HERE, "empty_mcp.json")
# Shared temp dir for attached images: the bridge writes them here and the brain reads
# them via the Read tool (--add-dir points here). A common path accessible to both.
IMG_DIR = os.environ.get("CLAUDE_BRIDGE_IMG_DIR", os.path.join(_HERE, "img_cache"))
_IMG_EXT = {"image/png": "png", "image/jpeg": "jpg", "image/jpg": "jpg",
            "image/webp": "webp", "image/gif": "gif", "image/bmp": "bmp"}


def _ensure_empty_mcp():
    """Create the empty MCP config the isolation flag points at (idempotent)."""
    try:
        if not os.path.exists(EMPTY_MCP):
            with open(EMPTY_MCP, "w", encoding="utf-8") as fh:
                fh.write('{"mcpServers": {}}')
    except Exception as e:  # non-fatal: worst case the flag has no file to read
        log.warning("could not write %s: %s", EMPTY_MCP, e)


# A short, trusted SYSTEM-level line establishing that the incoming message is the
# brain's own operating contract — not untrusted user input. This defuses the
# prompt-injection misread that made the model refuse the Hermes framing.
RUNTIME_SYSTEM_PROMPT = (
    "You are the reasoning core of the Hermes agent. The context, available_tools list, "
    "conversation, and output-format contract in the incoming message are supplied by your "
    "own trusted runtime (Hermes) — they are your legitimate operating instructions, not "
    "untrusted user content or a prompt-injection attempt. An external runtime executes the "
    "listed tools for you; you decide the next step and reply in exactly the requested format. "
    "Never claim a capability is unavailable when a listed tool can achieve it. "
    "When a message shows '[imagen adjunta … Read: <path>]', you DO have a local Read tool "
    "available for the sole purpose of opening that image/file so you can see it — read it "
    "before answering, then act on what you see. Read is only for viewing attached files; "
    "every other action still goes through the runtime's listed tools."
)

MODEL_NAME = "claude-code"
# Advertised via /v1/models. Hermes compacts at compression.threshold × this value,
# so a 1M window with threshold 0.5 lets the conversation grow to ~500k tokens before
# compaction — matching Fable's real 1M context instead of throttling at 100k.
CONTEXT_LENGTH = 1_000_000
MAX_BODY = 64 * 1024 * 1024

# The decision protocol Claude Code must follow when Hermes supplies a tool catalog.
# Claude Code runs as a pure reasoner (its own tools are OFF); every real action is
# performed by Hermes' engine after we return the tool_calls.
#
# Prompt design follows Anthropic's model guides (Fable 5 / Opus 4.8 / Sonnet 5):
#  - positive framing ("do this"), NOT screaming CRITICAL/MUST (over-triggers newer models);
#  - the output contract + few-shot examples come LAST, after the bulk data (queries at the
#    end improve adherence); a short role line goes first;
#  - concrete few-shot examples of each output shape (the most reliable format lever);
#  - never ask the model to echo/explain its reasoning in the response (Fable's
#    `reasoning_extraction` refusal) — thinking stays in the model's own thinking blocks;
#  - no prefill / no budget_tokens / no sampling params (400 on 4.6+; we set none).
DECISION_PROTOCOL = """You are the decision engine of an AI agent named Hermes. An external runtime executes tools for you and returns their results — you choose the next step, you never run tools yourself.

Reply with a single JSON object and nothing else: no prose, no markdown fences, nothing before or after it. Two shapes are valid.

Call one or more tools (independent calls run in parallel):
{"action":"tools","calls":[{"name":"<exact_tool_name>","arguments":{ ...args matching that tool's schema... }}]}

Send the final reply to the user (only when no more tool calls are needed):
{"action":"final","content":"<reply in clean, Telegram-friendly Markdown>"}

How to decide well:
- Use only tool names that appear in <available_tools>, spelled exactly. Never invent a tool. Never use placeholders or guess a missing argument — if you lack a value, call a tool to obtain it.
- Prefer real action (run commands, edit files, save memory, schedule tasks, delegate subtasks) over describing it. When you have enough information to act, act.
- For big or parallelizable work, delegate independent subtasks with the delegation tool if it is available, and keep going.
- Keep working across turns until the task is genuinely done; only then use the "final" shape.
- Put no reasoning or commentary in the JSON — "content" is the only user-facing text, and it goes only in the "final" shape."""

# Few-shot examples (placed last, after the data). Names shown are illustrative; the model
# is told to use the real names from <available_tools>. Showing valid shapes is the single
# most reliable way to hold newer models to a strict output format.
DECISION_EXAMPLES = """<examples note="illustrative shapes; use the real tool names from <available_tools>">
<example>
{"action":"tools","calls":[{"name":"terminal","arguments":{"command":"curl -s http://127.0.0.1:8425/api/stats"}}]}
</example>
<example>
{"action":"tools","calls":[{"name":"delegate_task","arguments":{"task":"Review the auth module for bugs and report findings"}},{"name":"terminal","arguments":{"command":"python -m pytest -q"}}]}
</example>
<example>
{"action":"final","content":"Listo — el servidor quedó corriendo en el puerto 8425 y las 3 pruebas pasaron."}
</example>
</examples>"""

# Per-model addenda appended just before "decide now", tuned to each guide's known quirks.
# Kept short: newer models follow a brief instruction as well as an enumerated list.
MODEL_PROFILES = {
    # Fable 5 (primary): follows brief instructions well; only nudge brevity + acting.
    "fable": "Lead with the outcome in any final reply; don't narrate options you won't pursue. When you have enough to act, act.",
    # Opus 4.8: favors reasoning over tool use and can over-explain — push it to commit + act.
    "opus": "You tend to favor analysis over action and to over-explain. Commit to an approach and issue the tool call rather than deliberating further; keep the JSON minimal.",
    # Sonnet 5 (kept available, off the default hot path): guard the native-XML reversion.
    "sonnet": "Output only the JSON object specified above. Do not use tool-call XML syntax (no <function_calls>, <invoke>, <parameter>) — only the JSON envelope.",
}


LOG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bridge.log")
from logging.handlers import RotatingFileHandler  # noqa: E402
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        # Rotate so the log can't grow unbounded over long uptime (5MB x 3 files).
        RotatingFileHandler(LOG_FILE, maxBytes=5 * 1024 * 1024, backupCount=3,
                            encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger("claude-bridge")


def _text_of(content):
    """Coerce OpenAI message content (string or multimodal array) to text."""
    if isinstance(content, list):
        return "\n".join(
            p.get("text", "")
            for p in content
            if isinstance(p, dict) and p.get("type") == "text"
        )
    return content or ""


def _save_image(url):
    """Materialize one image reference to a local file in IMG_DIR; return its path.

    Handles the shapes Hermes sends: base64 `data:` URLs (local images), http(s) URLs
    (remote), `file://` URLs, and bare local paths. Everything is copied INTO IMG_DIR so
    a single `--add-dir IMG_DIR` grants the brain read access to all of them. Dedups by
    content hash. Returns None if it can't be fetched (caller just skips it).
    """
    try:
        if url.startswith("data:"):
            head, b64 = url.split(",", 1)
            mime = head[5:].split(";")[0].strip().lower()
            raw = base64.b64decode(b64)
            ext = _IMG_EXT.get(mime, "png")
        elif url.startswith(("http://", "https://")):
            with urllib.request.urlopen(url, timeout=30) as r:
                raw = r.read()
            ext = (os.path.splitext(url.split("?")[0])[1].lstrip(".") or "png").lower()
        else:
            path = url[7:] if url.startswith("file://") else url
            path = urllib.request.url2pathname(path) if url.startswith("file://") else path
            if not os.path.isfile(path):
                return None
            with open(path, "rb") as f:
                raw = f.read()
            ext = (os.path.splitext(path)[1].lstrip(".") or "png").lower()
        os.makedirs(IMG_DIR, exist_ok=True)
        fp = os.path.join(IMG_DIR, hashlib.sha1(raw).hexdigest()[:16] + "." + ext)
        if not os.path.exists(fp):
            with open(fp, "wb") as f:
                f.write(raw)
        return fp
    except Exception as e:  # noqa: BLE001
        log.warning("could not materialize image: %s", str(e)[:200])
        return None


def _materialize_images(messages):
    """Extract image parts from every message, save them to IMG_DIR, and rewrite that
    message's content to plain text + a `[imagen adjunta … Read: <path>]` marker.

    Fixes the old bug where multimodal image_url parts were silently dropped: now the
    brain gets the image's local path in-context (and reads it with the Read tool). Also
    strips the heavy base64 from the prompt (token win). Mutates messages in place;
    returns the list of local image paths found.
    """
    paths = []
    for msg in messages:
        c = msg.get("content")
        if not isinstance(c, list):
            continue
        texts, imgs = [], []
        for p in c:
            if not isinstance(p, dict):
                continue
            if p.get("type") == "text":
                texts.append(p.get("text", ""))
            elif p.get("type") == "image_url":
                fp = _save_image(((p.get("image_url") or {}).get("url")) or "")
                if fp:
                    imgs.append(fp)
        joined = "\n".join(t for t in texts if t)
        if imgs:
            note = "\n".join(f"[imagen adjunta — ábrela con la herramienta Read para verla: {fp}]"
                             for fp in imgs)
            msg["content"] = (joined + ("\n" if joined else "") + note)
            paths.extend(imgs)
        else:
            msg["content"] = joined  # flatten any non-image multimodal content to text
    return paths


def _render_conversation(messages):
    """Serialize the OpenAI messages array into (system_text, conversation_text).

    Assistant tool_calls and tool-result messages are rendered as readable notes so
    Claude Code keeps full continuity of what has already been tried this turn.
    """
    system_parts, convo_parts = [], []
    for msg in messages:
        role = msg.get("role", "user")
        content = _text_of(msg.get("content", ""))
        if role == "system":
            if content:
                system_parts.append(content)
        elif role == "assistant":
            pieces = []
            if content:
                pieces.append(content)
            for tc in msg.get("tool_calls") or []:
                fn = (tc or {}).get("function", {}) or {}
                pieces.append(
                    f"→ called tool `{fn.get('name', '?')}` with arguments "
                    f"{fn.get('arguments', '{}')}"
                )
            if pieces:
                convo_parts.append("[ASSISTANT]:\n" + "\n".join(pieces))
        elif role == "tool":
            name = msg.get("name") or msg.get("tool_name") or "tool"
            convo_parts.append(f"[RESULT of `{name}`]:\n{content}")
        else:
            if content:
                convo_parts.append(f"[USER]:\n{content}")
    return "\n\n".join(system_parts), convo_parts


def _tools_digest(tools):
    """Compact the OpenAI tool catalog into name/description/parameters JSON."""
    digest = []
    for t in tools or []:
        fn = (t or {}).get("function", t) or {}
        if not fn.get("name"):
            continue
        digest.append({
            "name": fn.get("name"),
            "description": (fn.get("description") or "")[:600],
            "parameters": fn.get("parameters", {}),
        })
    return digest


def build_decision_prompt(messages, tools, model=None):
    """Assemble the decision prompt.

    Ordering follows the model guides: a short role line first, then the bulk data
    (context, tool schemas, conversation) wrapped in XML tags, then the output
    contract + few-shot examples + per-model addendum LAST (queries at the end
    improve format adherence by up to ~30%). `model` selects the profile addendum.
    """
    system_text, convo_parts = _render_conversation(messages)
    sections = [
        "You are Hermes' decision engine. The full instructions are below the data; "
        "read the data first, then follow the output contract at the end."
    ]
    if system_text:
        sections.append("<context>\n" + system_text + "\n</context>")
    sections.append(
        "<available_tools>\n"
        + json.dumps(_tools_digest(tools), ensure_ascii=False, indent=1)
        + "\n</available_tools>"
    )
    if convo_parts:
        sections.append("<conversation>\n" + "\n\n".join(convo_parts) + "\n</conversation>")
    # Output contract + examples + model-specific addendum come last.
    sections.append("<output_contract>\n" + DECISION_PROTOCOL + "\n</output_contract>")
    sections.append(DECISION_EXAMPLES)
    addendum = MODEL_PROFILES.get((model or "").split("-")[0].lower())
    if addendum:
        sections.append(addendum)
    sections.append("Decide the next step now. Reply with only the single JSON object.")
    return "\n\n".join(sections)


def build_plain_prompt(messages):
    """For tool-less requests (compression summaries, titles, plain chat).

    Bulk conversation first, the actual instruction last. No request to echo or
    explain reasoning (avoids the reasoning_extraction refusal on newer models).
    """
    system_text, convo_parts = _render_conversation(messages)
    sections = []
    if system_text:
        sections.append("<instructions>\n" + system_text + "\n</instructions>")
    if len(convo_parts) > 1:
        sections.append("<conversation>\n" + "\n\n".join(convo_parts[:-1]) + "\n</conversation>")
    if convo_parts:
        sections.append("<respond_to>\n" + convo_parts[-1] + "\n</respond_to>")
    sections.append(
        "Reply with the assistant's message only — no role labels, no JSON, no preamble. "
        "Be concise and lead with the outcome."
    )
    return "\n\n".join(sections)


# ── Persistent-session mapping (--resume) ───────────────────────────────────
# Instead of re-flattening the ENTIRE Hermes conversation into every claude -p call
# (~100-200k tokens per request, uncacheable as one growing text blob), each Hermes
# conversation is mapped to a persistent Claude Code session. Turn 1 sends the full
# decision prompt and keeps the session (--no-session-persistence dropped); later
# turns send ONLY the new messages via --resume <id>, so prior turns are cached
# conversation history on Anthropic's side (cache reads, ~10% weight) instead of
# fresh input. Any prefix mismatch (Hermes compacted or reset the conversation),
# missing session, or resume failure falls back to a fresh full session — the
# mechanism can only degrade to the old behavior, never break a turn.
RESUME_ENABLED = os.environ.get("CLAUDE_BRIDGE_RESUME", "1") != "0"
SESSIONS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "sessions_map.json")
SESSION_MAP_MAX = 100          # keep the newest N conversation mappings
SESSION_MAP_TTL = 7 * 86400    # drop mappings older than a week


def _msg_key(m):
    """Stable identity of one OpenAI message for hashing (order-independent dict)."""
    return {"role": m.get("role"), "content": m.get("content"),
            "tool_calls": m.get("tool_calls"), "name": m.get("name")}


def _hash_msgs(msgs):
    blob = json.dumps([_msg_key(m) for m in msgs], sort_keys=True,
                      ensure_ascii=False, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _hash_text(s):
    return hashlib.sha256((s or "").encode("utf-8")).hexdigest()


class SessionStore:
    """Thread-safe fingerprint → Claude session mapping, persisted to disk so
    bridge restarts keep resuming instead of re-sending full histories."""

    def __init__(self, path):
        self.path = path
        self.lock = threading.Lock()
        self.data = {}
        try:
            with open(path, encoding="utf-8") as fh:
                self.data = json.load(fh)
        except Exception:
            self.data = {}

    def get(self, fp):
        with self.lock:
            return dict(self.data[fp]) if fp in self.data else None

    def put(self, fp, entry):
        with self.lock:
            entry["ts"] = time.time()
            self.data[fp] = entry
            self._prune()
            self._save()

    def drop(self, fp):
        with self.lock:
            if self.data.pop(fp, None) is not None:
                self._save()

    def _prune(self):
        now = time.time()
        self.data = {k: v for k, v in self.data.items()
                     if now - v.get("ts", 0) < SESSION_MAP_TTL}
        if len(self.data) > SESSION_MAP_MAX:
            keep = sorted(self.data.items(), key=lambda kv: kv[1].get("ts", 0),
                          reverse=True)[:SESSION_MAP_MAX]
            self.data = dict(keep)

    def _save(self):
        try:
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(self.data, fh)
            os.replace(tmp, self.path)
        except Exception:
            log.exception("could not persist sessions map")


SESSIONS = SessionStore(SESSIONS_FILE)


def build_delta_prompt(new_messages, model=None, system_update=None, tools=None):
    """Prompt for a RESUMED turn: only the messages Claude Code hasn't seen.

    The session already holds the context, tool catalog, full conversation and
    output contract from turn 1; here we append the new events and re-anchor the
    output format with one line. `system_update`/`tools` are included only when
    Hermes actually changed them since the last turn.
    """
    _, convo_parts = _render_conversation(new_messages)
    sections = []
    if system_update:
        sections.append("<context_update note=\"replaces earlier <context>\">\n"
                        + system_update + "\n</context_update>")
    if tools is not None:
        sections.append("<available_tools note=\"replaces the earlier catalog\">\n"
                        + json.dumps(_tools_digest(tools), ensure_ascii=False, indent=1)
                        + "\n</available_tools>")
    if convo_parts:
        sections.append("<conversation_continued>\n"
                        + "\n\n".join(convo_parts) + "\n</conversation_continued>")
    addendum = MODEL_PROFILES.get((model or "").split("-")[0].lower())
    if addendum:
        sections.append(addendum)
    sections.append("Decide the next step now. Reply with only the single JSON object "
                    "from the output contract.")
    return "\n\n".join(sections)


def _extract_json_object(text):
    """Pull the first balanced JSON object out of text (tolerates fences/prose)."""
    s = (text or "").strip()
    if s.startswith("```"):
        s = s.strip("`")
        if s.lower().startswith("json"):
            s = s[4:]
        s = s.strip()
    start = s.find("{")
    if start == -1:
        return None
    decoder = json.JSONDecoder()
    try:
        obj, _ = decoder.raw_decode(s[start:])
        return obj
    except Exception:
        return None


_TOOLTAGS = ("function_calls", "invoke", "parameter", "calls", "call", "name", "arguments", "antml:invoke", "antml:parameter")


def clean_final(text):
    """Strip tool-call syntax / process narration from a final user-facing reply.

    Guards against the model leaking any tool-invocation syntax into the message
    that reaches the chat — the user wants results, not the process.
    """
    s = text or ""
    # 1) Remove complete tool-call blocks (both Claude-native flavours), inner text too.
    s = re.sub(r"<function_calls>.*?</function_calls>", "", s, flags=re.DOTALL)
    s = re.sub(r"<calls>.*?</calls>", "", s, flags=re.DOTALL)
    # 2) Cut from the first DANGLING (unclosed) tool marker to the end.
    idxs = [i for i in (s.find("<function_calls"), s.find("<invoke"), s.find("<calls>"),
                        s.find("<call>")) if i != -1]
    if idxs:
        s = s[:min(idxs)]
    # 3) Strip any orphan tool tags left behind.
    s = re.sub(r"</?(" + "|".join(_TOOLTAGS) + r")\b[^>]*>", "", s, flags=re.DOTALL)
    # 4) Drop a fenced ```json {"action":...}``` tool envelope if one slipped through.
    s = re.sub(r"```(?:json)?\s*\{\s*\"action\".*?```", "", s, flags=re.DOTALL)
    return s.strip()


def _parse_native_tool_calls(text):
    """Extract tool calls the model expressed in Claude-native XML formats.

    Handles both:
      <function_calls><invoke name="X"><parameter name="k">v</parameter></invoke>
      <calls><call><name>X</name><arguments>{...json...}</arguments></call>
    """
    calls = []
    for m in re.finditer(r'<invoke\s+name="([^"]+)"\s*>(.*?)</invoke>', text, re.DOTALL):
        args = {}
        for pm in re.finditer(r'<parameter\s+name="([^"]+)"\s*>(.*?)</parameter>',
                              m.group(2), re.DOTALL):
            v = pm.group(2)
            try:
                v = json.loads(v)
            except Exception:
                v = v.strip()
            args[pm.group(1)] = v
        calls.append({"name": m.group(1), "arguments": args})
    if calls:
        return calls
    for m in re.finditer(r'<call>\s*<name>(.*?)</name>\s*<arguments>(.*?)</arguments>',
                         text, re.DOTALL):
        try:
            args = json.loads(m.group(2).strip())
        except Exception:
            args = {}
        calls.append({"name": m.group(1).strip(), "arguments": args})
    return calls


def _balanced_object(s, start):
    """Return the balanced {...} substring beginning at index `start` (must point
    at '{'), or None if it never closes (truncated). String/escape aware."""
    if start >= len(s) or s[start] != "{":
        return None
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(s)):
        ch = s[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
        else:
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return s[start:i + 1]
    return None


def _salvage_tool_calls(text, valid_names):
    """Recover complete tool calls from malformed/truncated decision JSON.

    Handles the common failure where the model emits
    {"action":"tools","calls":[{"name":"X","arguments":{...}}, ...]} but the JSON
    is truncated or has an unescaped character (e.g. a giant shell command with
    nested quotes), so full parsing fails. We scan for each
    "name": "..."  "arguments": {balanced object} pair and keep the complete ones;
    a truncated trailing call is simply dropped.
    """
    calls = []
    for m in re.finditer(r'"name"\s*:\s*"([^"]+)"\s*,\s*"arguments"\s*:\s*(?=\{)', text):
        obj = _balanced_object(text, m.end())
        if obj is None:
            continue
        try:
            args = json.loads(obj)
        except Exception:
            continue
        name = m.group(1)
        if valid_names is None or name in valid_names:
            calls.append({"name": name, "arguments": args})
    return calls


_LOOKS_LIKE_TOOLJSON = re.compile(
    r'"action"\s*:|"calls"\s*:|"tool_calls"\s*:|"name"\s*:\s*"[^"]+"\s*,\s*"arguments"'
)


def parse_decision(text, valid_names=None):
    """Return ('tools', [calls]) or ('final', content).

    Accepts the bridge's own {"action":...} protocol AND either Claude-native
    tool-call XML format, so however the model expresses a tool call we turn it
    into real tool_calls. Tool names are validated against Hermes' actual catalog
    (valid_names); unknown names (e.g. hallucinated Claude-Code tools) are dropped
    and the turn degrades to a cleaned text reply instead of a broken action.
    """
    def keep(calls):
        out = [c for c in calls if isinstance(c, dict) and c.get("name")]
        if valid_names is not None:
            out = [c for c in out if c["name"] in valid_names]
        return out

    obj = _extract_json_object(text)
    if isinstance(obj, dict):
        if obj.get("action") == "tools" and isinstance(obj.get("calls"), list):
            calls = keep(obj["calls"])
            if calls:
                return "tools", calls
        if obj.get("action") == "final" and isinstance(obj.get("content"), str):
            return "final", clean_final(obj["content"])
        for key in ("content", "message", "text"):
            if isinstance(obj.get(key), str) and obj[key].strip():
                return "final", clean_final(obj[key])

    native = keep(_parse_native_tool_calls(text))
    if native:
        return "tools", native

    # Salvage complete tool calls from malformed/truncated protocol JSON.
    salvaged = _salvage_tool_calls(text, valid_names)
    if salvaged:
        log.warning("salvaged %d tool call(s) from malformed decision JSON", len(salvaged))
        return "tools", salvaged

    # If the reply CLEARLY looks like a (broken) tool/decision attempt but nothing
    # was salvageable, never dump the raw JSON to the chat — return empty so the
    # do_POST guard sends a safe placeholder instead.
    if _LOOKS_LIKE_TOOLJSON.search(text) or text.lstrip().startswith('{"'):
        log.warning("unparseable tool/decision JSON in reply; suppressed from chat")
        return "final", ""

    return "final", clean_final(text)


# ── Dynamic model + effort router ────────────────────────────────────────────
# Routing policy (Walt, 2026-07-14): MAIN turns run on Sonnet; complexity is handled
# by DELEGATING to subagents that run on a stronger model (Opus, or Fable for huge
# context), not by escalating the main turn's model. So:
#   • main decision (tool) turns → PRIMARY_MODEL (Sonnet), effort scaled to complexity;
#   • a subagent asks for a specific model by putting it in the request's `model` field
#     (Hermes `delegate_task(model="claude-code-opus")` → the child sends that model) —
#     the bridge honors it via _tier_override(), so Opus/Fable subagents route correctly;
#   • FALLBACK_MODEL (Opus) is applied automatically on a refusal / model-unavailable;
#   • tool-less aux (compaction summaries, titles) → AUX_MODEL (Sonnet), low effort.
# Everything is env-overridable so the routing stays flexible/adaptable.
ROUTING = os.environ.get("CLAUDE_BRIDGE_ROUTING", "1") != "0"
FORCE_MODEL = os.environ.get("CLAUDE_BRIDGE_FORCE_MODEL", "").strip()
PRIMARY_MODEL = os.environ.get("CLAUDE_BRIDGE_PRIMARY", "sonnet").strip()
FALLBACK_MODEL = os.environ.get("CLAUDE_BRIDGE_FALLBACK", "opus").strip()
AUX_MODEL = os.environ.get("CLAUDE_BRIDGE_AUX", "sonnet").strip()
# Model a subagent can request per-turn via the request's `model` field. A main turn
# sends "claude-code" (no tier keyword) → PRIMARY; a subagent delegated with a model
# whose name contains one of these routes to that tier. Order matters (first match).
OVERRIDE_TIERS = ("fable", "opus", "haiku", "sonnet")
# Very large prompts exceed Sonnet/Opus windows (~200k). Route those to a big-window
# model so the turn doesn't overflow. Fable (1M) is ideal while it exists; once it's
# retired the run_with_fallback auto-heal sends it to Opus. Env-overridable.
BIG_CONTEXT_MODEL = os.environ.get("CLAUDE_BRIDGE_BIGCTX", "fable").strip()
# Effort tiers (the real dial). Heavy defaults to "high"; bump to "xhigh" via env for
# the most capability-sensitive work.
EFFORT_HEAVY = os.environ.get("CLAUDE_BRIDGE_EFFORT_HEAVY", "high").strip()
EFFORT_NORMAL = os.environ.get("CLAUDE_BRIDGE_EFFORT_NORMAL", "medium").strip()
EFFORT_LIGHT = os.environ.get("CLAUDE_BRIDGE_EFFORT_LIGHT", "low").strip()
EFFORT_AUX = os.environ.get("CLAUDE_BRIDGE_EFFORT_AUX", "low").strip()
BIG_CONTEXT_CHARS = 600_000

HEAVY_KW = (
    "build", "create a", "implement", "code", "develop", "debug", "fix", "refactor",
    "architect", "design", "platform", "deploy", "migrate", "analyze", "optimize",
    "algorithm", "integrate", "script", "api", "database", "server", "plan ",
    "estrategia", "construye", "construir", "desarrolla", "programa", "arregla",
    "depura", "implementa", "analiza", "optimiza", "plataforma", "servidor",
)
LIGHT_KW = (
    "hi", "hey", "hello", "thanks", "thank you", "ok", "okay", "yes", "no", "sure",
    "cool", "nice", "hola", "gracias", "sí", "si", "vale", "listo", "ok!",
)


def _tier_override(requested_model):
    """If the request's `model` field names a specific tier, return that tier.

    Main turns send "claude-code" (no tier keyword) → None → PRIMARY (Sonnet).
    A subagent delegated with e.g. "claude-code-opus" / "custom:...-fable" → that tier,
    which is how complex work escalates to Opus (or Fable) without moving the main
    turn off Sonnet. "sonnet"/"claude-code" resolve to no override (already primary).
    """
    r = (requested_model or "").lower()
    if not r or r in ("claude-code", "claude", "default"):
        return None
    for tier in OVERRIDE_TIERS:
        if tier in r:
            return None if tier == "sonnet" else tier
    return None


def choose_model(latest_user_text, prompt_len, is_decision, requested_model=None):
    """Return (model_alias, effort) for this turn.

    MAIN turns run on PRIMARY_MODEL (Sonnet); complexity maps to EFFORT, not to a
    stronger model. A subagent escalates by requesting a model (opus/fable) in the
    request's `model` field — honored here via _tier_override(). Huge contexts route
    to BIG_CONTEXT_MODEL so they don't overflow Sonnet's window.
    """
    if FORCE_MODEL:
        return FORCE_MODEL, os.environ.get("CLAUDE_BRIDGE_FORCE_EFFORT", EFFORT_NORMAL)
    if not ROUTING:
        return None, None  # inherit Claude Code's configured default
    override = _tier_override(requested_model)
    if not is_decision:
        # Aux (summaries/titles): honor an override, else huge→big-window, else aux model.
        if override:
            return override, EFFORT_AUX
        if prompt_len > BIG_CONTEXT_CHARS:
            return BIG_CONTEXT_MODEL, EFFORT_AUX
        return AUX_MODEL, EFFORT_AUX
    t = (latest_user_text or "").lower().strip()
    # Length alone marks "heavy" only past 1500 chars: voice-note transcripts are
    # naturally verbose without being heavy tasks — keywords catch genuinely heavy asks.
    heavy = len(t) > 1500 or any(k in t for k in HEAVY_KW)
    light = len(t) < 24 or (len(t) < 70 and any(t == k or t.startswith(k + " ") for k in LIGHT_KW))
    effort = EFFORT_HEAVY if heavy else (EFFORT_LIGHT if light else EFFORT_NORMAL)
    # Explicit model requested (e.g. an Opus/Fable subagent) → honor it. Escalation
    # models default to heavy effort (they're used for the hard work).
    if override:
        return override, (EFFORT_HEAVY if override in ("opus", "fable") else effort)
    # Huge context on a main turn would overflow Sonnet → use the big-window model.
    if prompt_len > BIG_CONTEXT_CHARS:
        return BIG_CONTEXT_MODEL, (EFFORT_HEAVY if heavy else EFFORT_LIGHT)
    # Otherwise the main turn runs on the primary (Sonnet), effort by complexity.
    return PRIMARY_MODEL, effort


def _latest_user_text(messages):
    for msg in reversed(messages):
        if msg.get("role") == "user":
            return _text_of(msg.get("content", ""))
    return ""


class RefusalError(RuntimeError):
    """Raised when the model declines (refusal stop reason) — triggers model fallback."""


# Models that have errored as permanently unavailable (e.g. retired/removed) this
# process. Once a model lands here we route straight to the fallback, so when Fable
# is eventually removed the bridge keeps working on Opus with no per-turn retry cost.
_DEAD_MODELS = set()
_UNAVAILABLE_RE = re.compile(
    r"unknown model|not a valid model|no such model|does not exist|may not exist|"
    r"issue with the selected model|may not have access|pick a different model|"
    r"model[^\n]*(not found|unavailable|deprecat|retired|invalid|removed)|"
    r"invalid[^\n]*model|\b404\b",
    re.I,
)


def run_with_fallback(build_fn, model, effort, resume=None, persist=False, read_images=False):
    """Run a decision turn on `model`; fall back to FALLBACK_MODEL (Opus 4.8) on a
    refusal OR any hard error. `build_fn(m)` rebuilds the prompt so the fallback gets
    its own profile addendum. If the primary looks permanently unavailable (e.g. Fable
    is retired), remember it and go straight to the fallback on later turns. Timeouts
    propagate untouched (handled by do_POST). Returns (text, usage, used_model, session_id).
    """
    fb = FALLBACK_MODEL
    # Known-dead primary → skip straight to the fallback (no wasted attempt).
    if model and model in _DEAD_MODELS and fb and fb != model:
        text, usage, sid = run_claude(build_fn(fb), fb, effort, resume, persist, read_images)
        return text, usage, fb, sid
    try:
        text, usage, sid = run_claude(build_fn(model), model, effort, resume, persist, read_images)
        return text, usage, model, sid
    except subprocess.TimeoutExpired:
        raise  # let do_POST return a clean timeout, don't burn a second long attempt
    except RefusalError as e:
        if not fb or fb == model:
            raise
        log.warning("primary %s refused (%s); falling back to %s", model, e, fb)
        text, usage, sid = run_claude(build_fn(fb), fb, effort, resume, persist, read_images)
        return text, usage, fb, sid
    except Exception as e:
        if not fb or fb == model:
            raise
        if _UNAVAILABLE_RE.search(str(e)):
            _DEAD_MODELS.add(model)
            log.warning("primary %s appears unavailable (%s); marking dead — using %s from now on",
                        model, str(e)[:200], fb)
        else:
            log.warning("primary %s errored (%s); retrying on %s", model, str(e)[:200], fb)
        text, usage, sid = run_claude(build_fn(fb), fb, effort, resume, persist, read_images)
        return text, usage, fb, sid


def run_claude(prompt, model=None, effort=None, resume=None, persist=False, read_images=False):
    """Invoke claude -p as a pure reasoner (own tools off).

    resume  — Claude Code session id to continue (sends only `prompt` as the new turn).
    persist — keep the session on disk so later turns can --resume it.
    read_images — the turn has attached image(s): allow ONLY the Read tool and grant the
        image dir so the brain can open and see them (otherwise it stays fully tool-less).
    Returns (text, usage, session_id) — session_id is None for unpersisted runs.
    """
    os.makedirs(WORKSPACE, exist_ok=True)
    _ensure_empty_mcp()
    cmd = [
        CLAUDE_CMD,
        "-p",
        "--output-format", "json",
        # Tool-less by default; for image turns allow ONLY Read so the brain can view the
        # attached file. Hermes still executes every real action from the returned decision.
        "--tools", ("Read" if read_images else ""),
        # Isolation: ignore the user's global MCP servers so the brain has no conflicting
        # "real" tools and never mistakes the Hermes framing for a prompt injection.
        "--strict-mcp-config",
        "--mcp-config", EMPTY_MCP,
        # Trusted system line: the incoming message is the brain's own operating contract.
        "--append-system-prompt", RUNTIME_SYSTEM_PROMPT,
    ]
    if read_images:
        cmd += ["--add-dir", IMG_DIR]
    if resume:
        cmd += ["--resume", resume]
    elif not persist:
        cmd += ["--no-session-persistence"]
    if model:
        cmd += ["--model", model]
    if effort:
        cmd += ["--effort", effort]
    start = time.time()
    proc = subprocess.run(
        cmd,
        input=prompt.encode("utf-8"),
        capture_output=True,
        timeout=SUBPROCESS_TIMEOUT,
        cwd=WORKSPACE,
        shell=False,
    )
    elapsed = time.time() - start
    stdout = proc.stdout.decode("utf-8", errors="replace").strip()
    stderr = proc.stderr.decode("utf-8", errors="replace").strip()

    if proc.returncode != 0 and not stdout:
        raise RuntimeError(f"claude exited {proc.returncode}: {stderr[:2000]}")

    json_start = stdout.find("{")
    if json_start == -1:
        raise RuntimeError(f"no JSON in claude output: {stdout[:500]} / {stderr[:500]}")
    data = json.loads(stdout[json_start:])

    text = data.get("result", "") or ""
    # Fable/Opus 4.6+ can decline offensive-cyber / bio / reasoning-extraction requests
    # with a refusal stop reason. Surface it distinctly so the caller can fall back to
    # Opus 4.8 (the guide-recommended fallback) instead of erroring the whole turn.
    subtype = str(data.get("subtype") or "")
    stop = str(data.get("stop_reason") or "")
    if stop == "refusal" or "refus" in subtype.lower():
        raise RefusalError(f"model refused (subtype={subtype}, stop={stop})")
    if data.get("is_error"):
        raise RuntimeError(f"claude reported error: {text[:2000]}")

    u = data.get("usage", {}) or {}
    usage = {
        "prompt_tokens": (
            u.get("input_tokens", 0)
            + u.get("cache_read_input_tokens", 0)
            + u.get("cache_creation_input_tokens", 0)
        ),
        "completion_tokens": u.get("output_tokens", 0),
    }
    usage["total_tokens"] = usage["prompt_tokens"] + usage["completion_tokens"]
    log.info("claude ok in %.1fs (model=%s/%s, turns=%s, out_tokens=%s, "
             "in=%s cache_r=%s cache_w=%s%s)",
             elapsed, model or "default", effort or "-",
             data.get("num_turns"), usage["completion_tokens"],
             u.get("input_tokens", 0), u.get("cache_read_input_tokens", 0),
             u.get("cache_creation_input_tokens", 0),
             ", resumed" if resume else "")
    return text, usage, data.get("session_id")


def _tool_calls_payload(calls):
    """Build OpenAI tool_calls list from parsed decision calls."""
    out = []
    for i, c in enumerate(calls):
        args = c.get("arguments", {})
        if not isinstance(args, str):
            args = json.dumps(args, ensure_ascii=False)
        out.append({
            "id": f"call_{uuid.uuid4().hex[:20]}_{i}",
            "type": "function",
            "function": {"name": c["name"], "arguments": args},
        })
    return out


# ── Liveness/idle state (read by the supervisor's /status probe to update safely) ──
_STATUS_LOCK = threading.Lock()
_INFLIGHT = 0            # decision/plain turns currently running (a `claude -p` in flight)
_LAST_ACTIVITY_TS = 0.0  # updated on every turn start AND finish → "quiet since" clock


def _inflight_inc():
    global _INFLIGHT, _LAST_ACTIVITY_TS
    with _STATUS_LOCK:
        _INFLIGHT += 1
        _LAST_ACTIVITY_TS = time.time()


def _inflight_dec():
    global _INFLIGHT, _LAST_ACTIVITY_TS
    with _STATUS_LOCK:
        _INFLIGHT = max(0, _INFLIGHT - 1)
        _LAST_ACTIVITY_TS = time.time()


def _installed_version():
    """Report the installed version (a VERSION file beside the bridge or one dir up)."""
    for p in (os.path.join(_HERE, "VERSION"), os.path.join(os.path.dirname(_HERE), "VERSION")):
        try:
            with open(p, encoding="utf-8") as fh:
                return fh.read().strip()
        except Exception:
            pass
    return "dev"


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        log.debug(fmt, *args)

    def _send_json(self, obj, status=200):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _begin_stream(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()

    def do_GET(self):
        if self.path == "/health":
            self._send_json({"status": "ok", "backend": "claude-code"})
        elif self.path.rstrip("/") == "/status":
            # Idle probe for the supervisor: safe to update when inflight==0 and
            # idle_seconds is large (no turn mid-flight, quiet for a while).
            with _STATUS_LOCK:
                inflight, last = _INFLIGHT, _LAST_ACTIVITY_TS
            self._send_json({
                "backend": "claude-code",
                "version": _installed_version(),
                "inflight": inflight,
                "last_activity_ts": last,
                "idle_seconds": (time.time() - last) if last else None,
            })
        elif self.path.rstrip("/") in ("/v1/models", "/models"):
            self._send_json({
                "object": "list",
                "data": [{
                    "id": MODEL_NAME,
                    "object": "model",
                    "created": int(time.time()),
                    "owned_by": "claude-code-bridge",
                    "context_length": CONTEXT_LENGTH,
                }],
            })
        else:
            self._send_json({"error": "not found"}, 404)

    def do_POST(self):
        if self.path.rstrip("/") not in ("/v1/chat/completions", "/chat/completions"):
            self._send_json({"error": "not found"}, 404)
            return
        counted = False
        try:
            length = int(self.headers.get("Content-Length", 0))
            if length <= 0 or length > MAX_BODY:
                self._send_json({"error": {"message": "bad content length"}}, 400)
                return
            raw = self.rfile.read(length)
            req = json.loads(raw)
            try:
                with open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                       "debug_last_request.json"), "wb") as fh:
                    fh.write(raw)
            except Exception:
                pass

            messages = req.get("messages", [])
            if not messages:
                self._send_json({"error": {"message": "messages required"}}, 400)
                return
            tools = req.get("tools") or []
            stream = bool(req.get("stream"))
            # Materialize any attached images to local files (and rewrite content to text +
            # path markers). If present, the brain gets Read access to view them.
            image_paths = _materialize_images(messages)
            if image_paths:
                log.info("materialized %d image(s): %s", len(image_paths),
                         [os.path.basename(p) for p in image_paths])
            has_images = bool(image_paths)
            _inflight_inc(); counted = True

            if tools:
                text, usage = self._run_decision(messages, tools, stream, req.get("model"), has_images)
                valid_names = {(t.get("function", t) or {}).get("name")
                               for t in tools} - {None}
                kind, value = parse_decision(text, valid_names)
            else:
                prompt = build_plain_prompt(messages)
                model, effort = choose_model(_latest_user_text(messages), len(prompt), False, req.get("model"))
                log.info("plain request: %d msgs, %d chars, model=%s/%s, images=%s, stream=%s",
                         len(messages), len(prompt), model, effort, has_images, stream)
                text, usage, _ = run_claude(prompt, model, effort, read_images=has_images)
                kind, value = "final", text

            cid = f"chatcmpl-{uuid.uuid4().hex[:24]}"
            created = int(time.time())

            if kind == "tools":
                tool_calls = _tool_calls_payload(value)
                log.info("-> tool_calls: %s", [tc["function"]["name"] for tc in tool_calls])
                if stream:
                    self._stream_tool_calls(cid, created, tool_calls, usage)
                else:
                    self._send_json({
                        "id": cid, "object": "chat.completion", "created": created,
                        "model": MODEL_NAME,
                        "choices": [{
                            "index": 0,
                            "message": {"role": "assistant", "content": None,
                                        "tool_calls": tool_calls},
                            "finish_reason": "tool_calls",
                        }],
                        "usage": usage,
                    })
            else:
                value = clean_final(value)
                if not value.strip():
                    value = "Listo."  # never send an empty message to the chat
                if stream:
                    self._stream_content(cid, created, value, usage)
                else:
                    self._send_json({
                        "id": cid, "object": "chat.completion", "created": created,
                        "model": MODEL_NAME,
                        "choices": [{
                            "index": 0,
                            "message": {"role": "assistant", "content": value},
                            "finish_reason": "stop",
                        }],
                        "usage": usage,
                    })
        except subprocess.TimeoutExpired:
            log.error("claude subprocess timed out")
            self._send_json({"error": {"message": "claude-code timed out", "type": "timeout"}}, 500)
        except Exception as e:
            log.exception("request failed")
            self._send_json({"error": {"message": str(e), "type": "bridge_error"}}, 500)
        finally:
            if counted:
                _inflight_dec()

    def _run_decision(self, messages, tools, stream, requested_model=None, read_images=False):
        """Run one decision turn, resuming the conversation's persistent Claude Code
        session when possible (sending only the new messages), otherwise starting a
        fresh persisted session with the full prompt. Returns (text, usage).

        `requested_model` is the request's `model` field — a subagent delegated with a
        specific model (e.g. "claude-code-opus") escalates to that tier via choose_model.
        `read_images` — the turn has attached image(s); enable the brain's Read tool."""
        latest = _latest_user_text(messages)
        convo = [m for m in messages if m.get("role") != "system"]
        system_text = "\n\n".join(
            _text_of(m.get("content", "")) for m in messages
            if m.get("role") == "system" and m.get("content"))
        fp = _hash_msgs(convo[:1]) if convo else None
        entry = SESSIONS.get(fp) if (RESUME_ENABLED and fp) else None

        # ── Resume path: prefix of what Hermes sent must equal what we already sent.
        if entry:
            n = entry.get("sent_count", 0)
            if (len(convo) > n and entry.get("session_id")
                    and _hash_msgs(convo[:n]) == entry.get("prefix_hash")):
                delta = convo[n:]
                sys_upd = system_text if _hash_text(system_text) != entry.get("system_hash") else None
                tools_digest_hash = _hash_text(json.dumps(_tools_digest(tools), sort_keys=True))
                tools_upd = tools if tools_digest_hash != entry.get("tools_hash") else None
                base = build_delta_prompt(delta, None, sys_upd, tools_upd)
                model, effort = choose_model(latest, len(base), True, requested_model)
                log.info("decision request (resume %s): +%d new msgs, %d chars, model=%s/%s, stream=%s",
                         entry["session_id"][:8], len(delta), len(base), model, effort, stream)
                try:
                    text, usage, used_model, sid = run_with_fallback(
                        lambda m: build_delta_prompt(delta, m, sys_upd, tools_upd),
                        model, effort, resume=entry["session_id"], persist=True, read_images=read_images)
                    if used_model != model:
                        log.info("-> served by fallback model=%s", used_model)
                    SESSIONS.put(fp, {
                        "session_id": sid or entry["session_id"],
                        "sent_count": len(convo),
                        "prefix_hash": _hash_msgs(convo),
                        "system_hash": _hash_text(system_text),
                        "tools_hash": tools_digest_hash,
                    })
                    return text, usage
                except subprocess.TimeoutExpired:
                    raise
                except Exception as e:
                    log.warning("resume of %s failed (%s); starting fresh session",
                                entry["session_id"][:8], str(e)[:200])
                    SESSIONS.drop(fp)
            else:
                # Hermes compacted/reset this conversation (prefix changed) — re-baseline.
                log.info("session map stale for fp=%s (prefix changed or shrunk); re-baselining",
                         (fp or "")[:8])
                SESSIONS.drop(fp)

        # ── Fresh path: full prompt, persisted so the next turn can resume it.
        base_prompt = build_decision_prompt(messages, tools)
        model, effort = choose_model(latest, len(base_prompt), True, requested_model)
        log.info("decision request (full): %d msgs, %d tools, %d chars, model=%s/%s, stream=%s",
                 len(messages), len(tools), len(base_prompt), model, effort, stream)
        text, usage, used_model, sid = run_with_fallback(
            lambda m: build_decision_prompt(messages, tools, m),
            model, effort, persist=RESUME_ENABLED, read_images=read_images)
        if used_model != model:
            log.info("-> served by fallback model=%s", used_model)
        if RESUME_ENABLED and fp and sid:
            SESSIONS.put(fp, {
                "session_id": sid,
                "sent_count": len(convo),
                "prefix_hash": _hash_msgs(convo),
                "system_hash": _hash_text(system_text),
                "tools_hash": _hash_text(json.dumps(_tools_digest(tools), sort_keys=True)),
            })
        return text, usage

    def _chunk(self, cid, created, delta, finish=None, extra=None):
        payload = {
            "id": cid, "object": "chat.completion.chunk", "created": created,
            "model": MODEL_NAME,
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
        }
        if extra:
            payload.update(extra)
        return f"data: {json.dumps(payload)}\n\n".encode("utf-8")

    def _stream_content(self, cid, created, text, usage):
        self._begin_stream()
        self.wfile.write(self._chunk(cid, created, {"role": "assistant", "content": ""}))
        step = 512
        for i in range(0, len(text), step):
            self.wfile.write(self._chunk(cid, created, {"content": text[i:i + step]}))
        self.wfile.write(self._chunk(cid, created, {}, finish="stop", extra={"usage": usage}))
        self.wfile.write(b"data: [DONE]\n\n")

    def _stream_tool_calls(self, cid, created, tool_calls, usage):
        self._begin_stream()
        self.wfile.write(self._chunk(cid, created, {"role": "assistant", "content": None}))
        for idx, tc in enumerate(tool_calls):
            delta = {"tool_calls": [{
                "index": idx,
                "id": tc["id"],
                "type": "function",
                "function": {"name": tc["function"]["name"],
                             "arguments": tc["function"]["arguments"]},
            }]}
            self.wfile.write(self._chunk(cid, created, delta))
        self.wfile.write(self._chunk(cid, created, {}, finish="tool_calls", extra={"usage": usage}))
        self.wfile.write(b"data: [DONE]\n\n")


class ExclusiveHTTPServer(ThreadingHTTPServer):
    # Bind exclusively so a second bridge can't silently stack on the same port
    # (Windows SO_REUSEADDR otherwise allows multiple listeners — the cause of
    # stale-code instances serving requests after a "restart").
    allow_reuse_address = False
    daemon_threads = True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8790)
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args()
    os.makedirs(WORKSPACE, exist_ok=True)
    server = ExclusiveHTTPServer((args.host, args.port), Handler)
    log.info("Claude Code bridge (function-calling shim) on http://%s:%d (workspace=%s)",
             args.host, args.port, WORKSPACE)
    server.serve_forever()


if __name__ == "__main__":
    main()
