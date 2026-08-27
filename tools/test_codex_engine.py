r"""Tests for the Codex engine's side of the brain contract.

The fixtures are not invented: the failure stream was captured verbatim from codex-cli 0.150.1
(an `exec --json` run against a deliberately invalid key), and the success stream uses the event
shapes from the same CLI's documented schema. Two of these tests exist because of things the real
CLI does that a reasonable person would not guess:

  * a run can print nothing but errors and still exit 0, so "did it work" cannot be read from the
    exit code - only from whether an agent_message came back;
  * stdout carries tracing lines and human notices interleaved with the JSONL.

Run: python tools/test_codex_engine.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

import codex_engine as ce  # noqa: E402

# ── fixtures ────────────────────────────────────────────────────────────────
# Captured from a real run (invalid key). Note the interleaved non-JSON lines.
REAL_401 = r'''WARNING: proceeding, even though we could not create PATH aliases
Reading additional input from stdin...
{"type":"thread.started","thread_id":"01a041b7-f5d8-70b1-a3cd-99300e3337c0"}
{"type":"turn.started"}
2026-08-27T05:25:36.134050Z ERROR codex_api::endpoint::responses_websocket: failed to connect
{"type":"error","message":"Reconnecting... 2/5 (unexpected status 401 Unauthorized)"}
{"type":"item.completed","item":{"id":"item_0","type":"error","message":"Falling back from WebSockets to HTTPS transport."}}
{"type":"error","message":"Reconnecting... 5/5 (unexpected status 401 Unauthorized)"}
{"type":"error","message":"unexpected status 401 Unauthorized: Incorrect API key provided: sk-inval**-000."}
{"type":"turn.failed","error":{"message":"unexpected status 401 Unauthorized: Incorrect API key provided: sk-inval**-000."}}
'''

SUCCESS = r'''Reading additional input from stdin...
{"type":"thread.started","thread_id":"0199a213-81c0-7800-8aa1-bbab2a035a53"}
{"type":"turn.started"}
{"type":"item.completed","item":{"id":"item_0","type":"reasoning","text":"**Deciding**"}}
{"type":"item.completed","item":{"id":"item_1","type":"agent_message","text":"{\"action\":\"final\",\"content\":\"Listo\"}"}}
{"type":"turn.completed","usage":{"input_tokens":24763,"cached_input_tokens":24448,"output_tokens":122}}
'''

TWO_MESSAGES = (
    '{"type":"thread.started","thread_id":"t-2"}\n'
    '{"type":"item.completed","item":{"id":"i1","type":"agent_message","text":"pensando en voz alta"}}\n'
    '{"type":"item.completed","item":{"id":"i2","type":"agent_message","text":"la respuesta final"}}\n'
    '{"type":"turn.completed","usage":{"input_tokens":10,"output_tokens":2}}\n'
)

FAILS = []


def check(name, cond, extra=""):
    if cond:
        print("  ok   %s" % name)
    else:
        FAILS.append(name)
        print("  FAIL %s %s" % (name, extra))


print("parse_events")
ev = ce.parse_events(SUCCESS)
check("final text is the agent_message", ev["text"] == '{"action":"final","content":"Listo"}', ev["text"])
check("thread id captured", ev["thread_id"] == "0199a213-81c0-7800-8aa1-bbab2a035a53")
check("usage captured", ev["usage"].get("output_tokens") == 122)
check("turn completed", ev["turn_completed"] is True)
check("reasoning is not mistaken for the answer", "Deciding" not in ev["text"])
check("no errors on a clean run", ev["errors"] == [])

ev = ce.parse_events(REAL_401)
check("no text when the turn failed", ev["text"] == "", repr(ev["text"]))
check("thread id still captured", ev["thread_id"].startswith("01a041b7"))
check("errors collected", len(ev["errors"]) >= 3)
check("non-JSON lines skipped", ev["turn_completed"] is False)

ev = ce.parse_events(TWO_MESSAGES)
check("last message wins", ev["text"] == "la respuesta final", ev["text"])
check("all messages kept", len(ev["messages"]) == 2)

check("garbage in, no crash", ce.parse_events("not json at all\n{broken\n")["text"] == "")
check("empty in, no crash", ce.parse_events("")["text"] == "")

print("\nerror reporting (exit code cannot be trusted)")
ev = ce.parse_events(REAL_401)
msg = ce._best_error(ev, "", 0)
check("prefers the real cause over reconnect noise", "Incorrect API key" in msg, msg)
check("adds the fix hint on a 401", "codex login" in msg, msg)
msg2 = ce._best_error({"errors": []}, "No prompt provided via stdin.", 1)
check("falls back to stderr", "No prompt provided" in msg2, msg2)
msg3 = ce._best_error({"errors": []}, "", 0)
check("says something even with nothing to go on", "without producing an answer" in msg3, msg3)

print("\nmap_model — a Claude tier must never reach -m")
os.environ.pop("OLIVAW_CODEX_MODEL", None)
for tier in ("sonnet", "opus", "haiku", "fable", "claude-code-opus", "custom:agent-fable"):
    check("drops %r" % tier, ce.map_model(tier) == "", ce.map_model(tier))
check("passes an explicit codex model", ce.map_model("gpt-5.1-codex") == "gpt-5.1-codex")
check("empty stays empty", ce.map_model("") == "")
check("'claude-code' is not a model", ce.map_model("claude-code") == "")
os.environ["OLIVAW_CODEX_MODEL"] = "gpt-5.1-codex"
try:
    import importlib
    importlib.reload(ce)
    check("configured default is used for a tier", ce.map_model("opus") == "gpt-5.1-codex",
          ce.map_model("opus"))
    check("configured default is used for the main turn", ce.map_model("claude-code") == "gpt-5.1-codex")
    os.environ["OLIVAW_CODEX_MODEL_OPUS"] = "gpt-5.1-codex-max"
    importlib.reload(ce)
    check("per-tier override wins", ce.map_model("opus") == "gpt-5.1-codex-max", ce.map_model("opus"))
finally:
    os.environ.pop("OLIVAW_CODEX_MODEL", None)
    os.environ.pop("OLIVAW_CODEX_MODEL_OPUS", None)
    import importlib
    importlib.reload(ce)

print("\nmap_effort")
check("high stays high", ce.map_effort("high") == "high")
check("xhigh is supported", ce.map_effort("xhigh") == "xhigh")
check("unknown falls back to medium", ce.map_effort("wildly-wrong") == "medium")
check("empty falls back to medium", ce.map_effort("") == "medium")

print("\nbuild_cmd")
cmd = ce.build_cmd("codex.cmd", model=None, effort="high", out_file="X")
joined = " ".join(cmd)
check("exec subcommand", cmd[:2] == ["codex.cmd", "exec"])
check("json stream", "--json" in cmd)
check("works outside a git repo", "--skip-git-repo-check" in cmd)
check("read-only sandbox", 'sandbox_mode="read-only"' in cmd)
check("no approvals", 'approval_policy="never"' in cmd)
check("owner's MCP servers are not the brain's tools", "mcp_servers={}" in cmd)
check("no web tool", "tools.web_search=false" in cmd)
check("effort passed", 'model_reasoning_effort="high"' in cmd)
check("no -m when nothing configured", "-m" not in cmd)
check("prompt comes from stdin", cmd[-1] == "-")
check("last message file", "-o" in cmd and "X" in cmd)
check("ephemeral when not persisting", "--ephemeral" in cmd)

cmd = ce.build_cmd("codex.cmd", resume="abc-123", persist=True, effort="low")
check("resume is a subcommand with the id", cmd[1:4] == ["exec", "resume", "abc-123"], cmd[:5])
check("resumed runs are not ephemeral", "--ephemeral" not in cmd)
check("resume keeps the sandbox (it takes no -s)", 'sandbox_mode="read-only"' in cmd)
check("resume still reads stdin", cmd[-1] == "-")

cmd = ce.build_cmd("codex.cmd", persist=True)
check("persisted runs are not ephemeral", "--ephemeral" not in cmd)

here = os.path.abspath(__file__)
cmd = ce.build_cmd("codex.cmd", image_paths=[here, os.path.join("nope", "missing.png")])
check("existing image attached", "-i" in cmd and here in cmd)
check("missing image skipped", cmd.count("-i") == 1)

print("\ntool-lessness — the brain decides, the runtime acts")
cmd = ce.build_cmd("codex.cmd", effort="medium", out_file="X")
for feat in ("shell_tool", "apps", "browser_use", "computer_use", "web_search"):
    i = cmd.index(feat) if feat in cmd else -1
    check("%s disabled" % feat, i > 0 and cmd[i - 1] == "--disable", cmd)
check("mcp servers cleared", "mcp_servers={}" in cmd)
check("read-only sandbox behind it", 'sandbox_mode="read-only"' in cmd)
check("resume keeps the tools off", "shell_tool" in ce.build_cmd("codex.cmd", resume="t1"))

print("\nconsole modes")
diag, fix = ce.console_flags(False), ce.console_flags(True)
check("diagnose has no tools", "shell_tool" in diag and "--disable" in diag, diag)
check("diagnose is read-only", 'sandbox_mode="read-only"' in diag, diag)
check("diagnose does not bypass", "--dangerously-bypass-approvals-and-sandbox" not in diag, diag)
check("fix mode bypasses (the checkbox promises it)",
      "--dangerously-bypass-approvals-and-sandbox" in fix, fix)
check("fix mode does not also disable the tools it needs", "shell_tool" not in fix, fix)

print("\nfail-open when the CLI refuses the flags")
check("a renamed feature is recognised as a flag problem",
      ce.flags_rejected("error: unknown feature `shell_tool`"))
check("an unexpected argument too", ce.flags_rejected("error: unexpected argument '--disable'"))
check("a real failure is NOT treated as a flag problem",
      not ce.flags_rejected("unexpected status 401 Unauthorized: Incorrect API key"))
check("nor is a timeout", not ce.flags_rejected("model response stream ended unexpectedly"))
try:
    check("flags on by default", ce.features_enabled())
    ce.disable_feature_flags()
    check("after refusal the flags are gone", ce.tool_off_flags() == [])
    check("the sandbox still is not", 'sandbox_mode="read-only"' in ce.build_cmd("codex.cmd"))
    check("and the console still refuses to bypass in diagnose mode",
          "--dangerously-bypass-approvals-and-sandbox" not in ce.console_flags(False))
finally:
    import importlib
    importlib.reload(ce)
check("reload restores them", ce.features_enabled())

print("\nargv safety")
cmd = ce.build_cmd("codex.cmd", model="gpt-5.1-codex", effort="high", out_file="X")
check("no newline in any argument", not any("\n" in a or "\r" in a for a in cmd))

if FAILS:
    print("\n%d FAILED: %s" % (len(FAILS), ", ".join(FAILS)))
    sys.exit(1)
print("\nall codex engine cases passed")
