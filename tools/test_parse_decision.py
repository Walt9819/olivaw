"""Regression tests for the bridge's decision parser.

Every case marked REAL is a reply shape taken from production (hermes state.db,
Aug 2026) that the old parser failed on: it recognized neither the tool call nor a
final answer, so the raw JSON — reasoning field included — was delivered to the user
in Telegram and the tool never ran.

Run:  python tools/test_parse_decision.py
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
import claude_bridge as cb  # noqa: E402

TOOLS = {"terminal", "read_file", "write_file", "search_files", "session_search",
         "web_search", "delegate_task", "cronjob", "skill_view"}

FAILS = []


def check(label, text, want_kind, want=None, names=TOOLS):
    kind, value = cb.parse_decision(text, names)
    ok = kind == want_kind
    if ok and want is not None:
        if want_kind == "tools":
            ok = [c["name"] for c in value] == want
        else:
            ok = want in (value or "")
    if not ok:
        FAILS.append(f"{label}: got ({kind!r}, {str(value)[:120]!r})")
    return kind, value


def no_leak(label, text, names=TOOLS):
    """Whatever we return, the user must never see reasoning/envelope syntax."""
    kind, value = cb.parse_decision(text, names)
    if kind == "final":
        low = (value or "").lower()
        for k in cb._REASON_KEYS:
            if f'"{k}"' in low:
                FAILS.append(f"{label}: LEAKED {k!r} to the user: {value[:120]!r}")
                return
        # `_is_machine_envelope`, not a bare substring test: a real answer is allowed to
        # MENTION json (`cronjob({"action":"list"})`); what must never ship is a reply
        # that IS an envelope.
        if cb._is_machine_envelope(value or ""):
            FAILS.append(f"{label}: LEAKED envelope syntax: {value[:120]!r}")


# ── the contract's own shapes still work ─────────────────────────────────────
check("contract/tools",
      '{"action":"tools","calls":[{"name":"terminal","arguments":{"command":"ls"}}]}',
      "tools", ["terminal"])
check("contract/tools-multi",
      '{"action":"tools","calls":[{"name":"terminal","arguments":{"command":"ls"}},'
      '{"name":"web_search","arguments":{"query":"x"}}]}',
      "tools", ["terminal", "web_search"])
check("contract/final",
      '{"action":"final","content":"Listo — 3 pruebas pasaron."}',
      "final", "Listo")
check("plain-text-final", "Listo, ya quedó el reporte.", "final", "Listo")

# ── REAL leaked shapes (msg ids from state.db in the comments) ───────────────
# 23220 — fenced, "thought" + top-level "tool"
check("real/23220 thought+tool fenced",
      '```json\n{\n  "thought": "session_search shows that earlier in this same session I '
      'already wrote the note; the fastest reliable move is to read it from disk.",\n'
      '  "tool": "terminal",\n  "arguments": {"command": "cat vault/50-Company/x.md"}\n}\n```',
      "tools", ["terminal"])
# 23196 — fenced, "thinking" + tool
check("real/23196 thinking+tool",
      '```json\n{"thinking":"The note is written (13,023 bytes).","tool":"write_file",'
      '"arguments":{"path":"a.md","content":"x"}}\n```',
      "tools", ["write_file"])
# 23187 — bare object, newline before the key (old startswith('{"') guard missed it)
check("real/23187 bare thought+tool",
      '{\n  "thought": "Ya leí el índice completo del vault y los dos decks.",\n'
      '  "tool": "read_file",\n  "arguments": {"path": "vault/index.md"}\n}',
      "tools", ["read_file"])
# 23166 — "thoughts" (plural)
check("real/23166 thoughts+tool",
      '{\n "thoughts": "Ya tengo todo lo necesario para responder la agenda de hoy.",\n'
      ' "tool": "terminal",\n "arguments": {"command": "curl -s localhost:8425/api/stats"}\n}',
      "tools", ["terminal"])
# 23150 — "thoughts" as a LIST
check("real/23150 thoughts-list",
      '```json\n{"thoughts":["Ambas búsquedas volvieron vacías","Busco en disco"],'
      '"tool":"search_files","arguments":{"pattern":"*.md"}}\n```',
      "tools", ["search_files"])
# 23121 — "reasoning"
check("real/23121 reasoning+tool",
      '```json\n{"reasoning":"The search surfaced an existing helper script.",'
      '"tool":"terminal","arguments":{"command":"cat scripts/git-pull.sh"}}\n```',
      "tools", ["terminal"])
# 23113 / 23129 — no reasoning field at all, just {"tool","arguments"}
check("real/23113 tool-only fenced",
      '```json\n{"tool":"terminal","arguments":{"command":"cd /c/tmp && for m in \\"13 7\\"; '
      'do echo $m; done"}}\n```',
      "tools", ["terminal"])
# thought + final answer (the reasoning must be dropped, the answer kept)
check("real/thought+final",
      '```json\n{"thought":"Nothing left to do, I can answer now.",'
      '"action":"final","content":"Ya quedó: el manual está en tu carpeta."}\n```',
      "final", "Ya quedó")

# ── other plausible drifts ───────────────────────────────────────────────────
check("drift/prose-then-json",
      'Voy a revisar el archivo primero (uso {llaves} en la nota).\n\n'
      '{"action":"tools","calls":[{"name":"read_file","arguments":{"path":"a.md"}}]}',
      "tools", ["read_file"])
check("drift/tool_name+args",
      '{"tool_name":"terminal","args":{"command":"ls"}}', "tools", ["terminal"])
check("drift/tool_calls-key",
      '{"tool_calls":[{"name":"terminal","arguments":{"command":"ls"}}]}',
      "tools", ["terminal"])
check("drift/args-json-encoded",
      '{"tool":"terminal","arguments":"{\\"command\\":\\"ls\\"}"}', "tools", ["terminal"])
check("drift/inline-sibling-args",
      '{"thought":"quick check","tool":"terminal","command":"ls -la"}', "tools", ["terminal"])
check("drift/action-is-toolname",
      '{"action":"terminal","arguments":{"command":"ls"}}', "tools", ["terminal"])
check("drift/final-under-answer",
      '{"action":"final","answer":"Todo listo."}', "final", "Todo listo")
check("drift/tools-then-final-prefers-tools",
      '{"action":"final","content":"pensando"}\n'
      '{"action":"tools","calls":[{"name":"terminal","arguments":{"command":"ls"}}]}',
      "tools", ["terminal"])

# ── things that must NOT be mistaken for an envelope ─────────────────────────
kind, value = check("safe/markdown-with-json-snippet",
                    'Aquí tienes el config que pediste:\n\n```json\n'
                    '{"message":"hola","name":"demo"}\n```\n\nLo guardé en config.json.',
                    "final")
if "Lo guardé en config.json" not in (value or ""):
    FAILS.append("safe/markdown-with-json-snippet: answer body was truncated: %r" % (value,))

check("safe/unknown-tool-degrades",
      '{"thought":"generar imagen","tool":"pixa_image","arguments":{"prompt":"x"}}',
      "final")  # phantom tool -> no fake call; do_POST turns this into an honest note

# ── the leak invariant, over every case above ────────────────────────────────
for label, txt in [
    ("leak/truncated-envelope",
     '```json\n{\n  "thought": "session_search shows that earlier in this same session I '
     'already wrote the note, so the fastest move is to read it from disk and then build the '
     'deliverable",\n  "tool": "terminal",\n  "arg'),
    ("leak/reasoning-only", '{"thinking":"Let me consider the options here."}'),
    ("leak/broken-json-with-thought",
     '{"thought":"Voy a escribir el archivo","tool":"write_file","arguments":{"content":"a\nb'),
    ("leak/prose-then-broken-envelope",
     'Déjame revisar.\n{"thought":"x","tool":"nope_tool","arguments":{'),
]:
    no_leak(label, txt)

# ── clean_final must strip envelopes even if one reaches it directly ─────────
leftover = cb.clean_final('```json\n{"thought":"secreto","tool":"terminal",'
                          '"arguments":{"command":"ls"}}\n```')
if leftover:
    FAILS.append("clean_final left envelope text behind: %r" % leftover)


# ── REAL: invented reply pseudo-tools carrying a finished answer (23150/23125/23079) ──
check("real/23125 final_answer+tool_args",
      '{\n  "thoughts": "I read the script in full.",\n  "tool": "final_answer",\n'
      '  "tool_args": {"answer": "El script hace pull de los 3 repos."}\n}',
      "final", "El script hace pull")
check("real/23150 response+text",
      '{"thoughts":["Ambas búsquedas volvieron vacías"],"tool_name":"response",'
      '"tool_args":{"text":"No hay conversaciones previas sobre MCP."}}',
      "final", "No hay conversaciones previas")
check("real/23079 final_answer+tool_input",
      '{"thoughts":"Ya tengo el changelog.","tool":"final_answer",'
      '"tool_input":{"answer":"**Novedades de Claude Code**\\n\\nv2.1.234"}}',
      "final", "Novedades de Claude Code")
check("real/23187 final_answer+message",
      '{"thought":"Ya leí el índice.","tool":"final_answer",'
      '"tool_input":{"message":"Tienes dos decks en el vault."}}',
      "final", "Tienes dos decks")
# a REAL tool named like a reply verb must still be executed, not swallowed
check("safe/real-send_message-wins",
      '{"tool":"send_message","arguments":{"text":"hola"}}',
      "tools", ["send_message"], names=TOOLS | {"send_message"})

# -- REAL: a good long answer that merely QUOTES json must NOT be suppressed --------
# state.db 3673/3701/3733/12876/12927: 9k-char finished answers were classified as
# machine output (they contain `cronjob({"action":"list"})` / a "notes": key in prose)
# and replaced by the honest-failure note. The user lost a completed task.
_PROSE = ("Resumen de lo que hice hoy. " * 40 +
          'Ya habia llamado a cronjob({"action":"list"}) cuando entro la compactacion. ' +
          "Detalle completo abajo. " * 40)
check("prose-quoting-json survives", _PROSE, "final", "Resumen de lo que hice hoy")
check("prose-quoting-thought survives",
      "Aqui va el reporte. " * 60 + 'El log traia "thought": "x" en la linea 4. ' +
      "Nada mas que reportar. " * 30,
      "final", "Aqui va el reporte")
check("markdown-answer-with-json-block survives",
      "Listo, este es el config que quedo:\n\n```json\n"
      '{"action":"list","calls":3,"tool":"cronjob"}\n```\n\n'
      "Lo guarde en config.yaml y reinicie el servicio. Cualquier ajuste me dices.",
      "final", "Lo guarde en config.yaml")
# the SHORT pure envelope must still be caught (not shipped as text)
check("short-pure-envelope still blocked",
      '{"thought":"me quedo a medias y se corto el jso',
      "final", "")

# -- REAL: invented action verb carrying a genuine question (state.db id 48) --------
check("real/48 action=clarify",
      '{"action": "clarify", "question": "La lista de recordatorios esta vacia. '
      'Querias uno especifico?", "choices": ["Borrar todo", "Solo actividades"]}',
      "final", "La lista de recordatorios esta vacia")
_k, _v = cb.parse_decision(
    '{"action":"clarify","question":"Cual prefieres?","choices":["A","B"]}', TOOLS)
if "- A" not in (_v or "") or "- B" not in (_v or ""):
    FAILS.append("real/48 choices not rendered: %r" % (_v,))
check("action=ask+message",
      '{"action":"ask_user","message":"Necesito el numero de expediente."}',
      "final", "Necesito el numero")
# an unknown action verb with NO human text must not become a bogus reply
check("unknown-action-no-text blocked",
      '{"action":"noop","calls":[]}', "final", "")

for _lbl, _txt in (
        ("noleak/prose", _PROSE),
        ("noleak/clarify", '{"action":"clarify","question":"x?","thought":"secreto"}'),
        ("noleak/nested", '{"thought":"a","tool":"nope","arguments":{"b":1}}')):
    no_leak(_lbl, _txt)

# ── file tool calls: windows paths and multi-line content ────────────────────
# REAL failure (Aug 21 2026): "decision unrecovered; honest note (dropped=['file'])" every
# time the owner asked to create or read a file. The arguments are what breaks strict JSON -
# a path typed with single backslashes (\U is not a valid escape) and content with real line
# breaks - so the whole envelope was thrown away. Values matter here, not just the parse:
# a repaired call that mangles the path or the content is worse than no call.
B = chr(92)
NL = chr(10)
FILE_TOOLS = TOOLS | {"file"}


def args_of(label, text, name, want_args, names=FILE_TOOLS):
    kind, value = cb.parse_decision(text, names)
    if kind != "tools" or not value:
        FAILS.append(f"{label}: no tool call ({kind!r}, {str(value)[:100]!r})")
        return
    call = value[0]
    if call.get("name") != name:
        FAILS.append(f"{label}: wrong tool {call.get('name')!r}")
        return
    got = call.get("arguments") or {}
    for k, v in want_args.items():
        if got.get(k) != v:
            FAILS.append(f"{label}: {k} = {got.get(k)!r}, expected {v!r}")


args_of("file/windows-path-single-backslashes",
        '{"action":"tools","calls":[{"name":"file","arguments":{"action":"read",'
        '"path":"C:' + B + 'Users' + B + 'revol' + B + 'VirtusVR' + B + 'README.md"}}]}',
        "file", {"action": "read",
                 "path": "C:" + B + "Users" + B + "revol" + B + "VirtusVR" + B + "README.md"})

args_of("file/multiline-content",
        '{"action":"tools","calls":[{"name":"file","arguments":{"action":"write",'
        '"path":"nota.txt","content":"linea uno' + NL + 'linea dos"}}]}',
        "file", {"path": "nota.txt", "content": "linea uno" + NL + "linea dos"})

args_of("file/windows-path-and-multiline",
        '{"action":"tools","calls":[{"name":"file","arguments":{"action":"write",'
        '"path":"C:' + B + 'Users' + B + 'revol' + B + 'Documents' + B + 'nota.md",'
        '"content":"# Titulo' + NL + NL + '- uno' + NL + '- dos"}}]}',
        "file", {"path": "C:" + B + "Users" + B + "revol" + B + "Documents" + B + "nota.md",
                 "content": "# Titulo" + NL + NL + "- uno" + NL + "- dos"})

args_of("file/quotes-inside-content",
        '{"action":"tools","calls":[{"name":"file","arguments":{"action":"write",'
        '"path":"nota.txt","content":"el dijo "hola" y se fue"}}]}',
        "file", {"content": 'el dijo "hola" y se fue'})

args_of("file/trailing-comma",
        '{"action":"tools","calls":[{"name":"file","arguments":{"action":"read",'
        '"path":"nota.txt",}}]}',
        "file", {"action": "read", "path": "nota.txt"})

args_of("terminal/windows-path",
        '{"action":"tools","calls":[{"name":"terminal","arguments":'
        '{"command":"type C:' + B + 'Users' + B + 'revol' + B + 'nota.txt"}}]}',
        "terminal", {"command": "type C:" + B + "Users" + B + "revol" + B + "nota.txt"})

# prose first, then the call in a fence, with a raw path
args_of("file/prose-then-fenced-call",
        'Voy a crear el archivo ahora.' + NL + NL + '```json' + NL +
        '{"action":"tools","calls":[{"name":"file","arguments":{"action":"write",'
        '"path":"C:' + B + 'temp' + B + 'x.txt","content":"hola"}}]}' + NL + '```',
        "file", {"path": "C:" + B + "temp" + B + "x.txt", "content": "hola"})

# escaped JSON must be left exactly alone (the repair path must not touch valid input)
args_of("file/already-escaped-untouched",
        '{"action":"tools","calls":[{"name":"file","arguments":{"action":"write",'
        '"path":"C:' + B + B + 'temp' + B + B + 'x.txt","content":"a' + B + 'nb"}}]}',
        "file", {"path": "C:" + B + "temp" + B + "x.txt", "content": "a" + NL + "b"})

# and the repair must NOT invent tool calls out of ordinary prose that quotes json
check("file/prose-quoting-json-stays-final",
      'Para leerlo se usa file con {"action": "read", "path": "x.txt"} — pero dime cual '
      'archivo quieres antes de que lo haga.',
      "final", "dime cual archivo")

if FAILS:
    print("FAILED %d case(s):" % len(FAILS))
    for f in FAILS:
        print("  -", f)
    sys.exit(1)
print("all decision-parser cases passed")
