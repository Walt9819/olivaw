#!/usr/bin/env python3
"""
hqctl — terse command-line client for the iGalenus HQ ops platform.

Purpose: let the Hermes brain read and WRITE to HQ by running a SHORT command and
getting a SHORT confirmation back — instead of hand-writing verbose curl + JSON
(which wastes tokens and gets the field names wrong). Uses the correct HQ schema.

Zero dependencies (stdlib urllib). Talks to http://127.0.0.1:8425 (override with
the HQ_URL env var). Run `hqctl help` for the full command list.

Examples:
  hqctl activity "Llamar a Marlon 3:30pm" --detail "ofrecer iGalenus gratis" --due 2026-08-06
  hqctl call --client "Marlon" --reason "demo" --callback 2026-08-06
  hqctl feedback --client "Maria" --text "quiere reportes PDF" --sentiment pos --tag reportes
  hqctl improvement "Export PDF" --ship 2026-08-10 --client "Maria"
  hqctl client "Dr. Ruiz" --specialty Cardiologia --stage pql --looking "agenda"
  hqctl stage 3 active          # move client 3 to stage 'active'
  hqctl notify 4                # mark improvement 4: client notified
  hqctl done 12                 # mark activity 12 done
  hqctl today                   # the day's agenda: due-today + overdue + callbacks
  hqctl stats | insights | attention | pipeline | feed
  hqctl get activities --today | --overdue | --due 2026-08-06 | --status open
  hqctl search "facturacion CFDI"
  hqctl client-view 3           # 360 dossier for client 3
"""
import argparse
import json
import os
import sys
import urllib.request
import urllib.parse
import urllib.error

# Always emit UTF-8 regardless of the Windows console codepage, so whatever captures
# hqctl's stdout (Hermes' terminal tool) can decode it cleanly — otherwise the "·"
# separator / accented client names would produce invalid bytes and break the capture.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE = os.environ.get("HQ_URL", "http://127.0.0.1:8425").rstrip("/")
ENTITIES = ("activities", "calls", "feedback", "improvements", "clients")
# Be forgiving about singular names the model may type (activity → activities, …).
ENTITY_ALIASES = {"activity": "activities", "call": "calls", "improvement": "improvements",
                  "client": "clients"}


def _norm_entity(name):
    n = ENTITY_ALIASES.get(name, name)
    if n not in ENTITIES:
        _die(f"unknown entity '{name}' (use one of: {', '.join(ENTITIES)})")
    return n


def _today():
    import datetime
    return datetime.date.today().isoformat()


def _req(method, path, body=None, timeout=30):
    url = BASE + path
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(url, data=data, method=method,
                               headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        _die(f"HTTP {e.code}: {raw[:300]}")
    except urllib.error.URLError as e:
        _die(f"cannot reach HQ at {BASE} ({e.reason}). Is hq.py running? "
             f"Start it: python platform/hq.py (or platform/start-hq.cmd).")
    try:
        return json.loads(raw) if raw.strip() else {}
    except Exception:
        return raw


def _die(msg):
    print("error: " + msg, file=sys.stderr)
    sys.exit(1)


def _ok(msg):
    print(msg)
    sys.exit(0)


def _create(entity, fields):
    fields = {k: v for k, v in fields.items() if v is not None}
    if not fields:
        _die("nothing to create (no fields given)")
    res = _req("POST", f"/api/{entity}", fields)
    singular = {"activities": "activity", "calls": "call", "feedback": "feedback",
                "improvements": "improvement", "clients": "client"}.get(entity, entity)
    if isinstance(res, dict) and res.get("id"):
        _ok(f"ok · {singular} #{res['id']} created")
    _ok(f"ok · {entity}: {json.dumps(res)[:200]}")


def _patch(entity, rid, fields):
    entity = _norm_entity(entity)
    fields = {k: v for k, v in fields.items() if v is not None}
    if not fields:
        _die("nothing to update")
    _req("PATCH", f"/api/{entity}/{rid}", fields)
    _ok(f"ok · {entity} #{rid} updated ({', '.join(fields)})")


# ---- read helpers (compact output to save tokens) ----------------------------
def _fmt_rows(rows, cols):
    if not rows:
        return "(none)"
    out = []
    for r in rows:
        out.append(" · ".join(f"{c}={r.get(c)}" for c in cols if r.get(c) not in (None, "", "[]")))
    return "\n".join(out)


def cmd_stats(_):
    s = _req("GET", "/api/stats").get("kpis", {})
    _ok(" · ".join(f"{k}:{v}" for k, v in s.items()))


def cmd_insights(_):
    d = _req("GET", "/api/insights")
    tiles = d.get("tiles", [])
    lines = [f"- {t.get('label')}: {t.get('value')} ({t.get('sub','')})" for t in tiles]
    extra = []
    if d.get("stuck"):
        extra.append("STUCK: " + ", ".join(f"{x.get('name')}({x.get('days')}d)" for x in d["stuck"]))
    if d.get("at_risk"):
        extra.append("AT-RISK: " + ", ".join(x.get("name", "?") for x in d["at_risk"]))
    if d.get("close_loop"):
        extra.append("CLOSE-LOOP: " + ", ".join(x.get("title", "?") for x in d["close_loop"]))
    _ok("\n".join(lines + extra) or "(no data)")


def cmd_attention(_):
    items = _req("GET", "/api/attention").get("items", [])
    if not items:
        _ok("Todo bajo control — no urgent items.")
    _ok("\n".join(f"{i+1}. [{it.get('urgency')}] {it.get('title')} — {it.get('reason')}"
                  f"{(' ('+it['client']+')') if it.get('client') else ''}"
                  for i, it in enumerate(items)))


def cmd_pipeline(_):
    cols = _req("GET", "/api/pipeline").get("columns", [])
    lines = []
    for c in cols:
        names = ", ".join(f"{x.get('name')}{'*' if x.get('stuck') else ''}" for x in c.get("cards", []))
        lines.append(f"{c.get('label')} ({c.get('count')}): {names}")
    _ok("\n".join(lines))


def cmd_feed(a):
    items = _req("GET", f"/api/activity_feed?limit={a.limit}").get("items", [])
    _ok("\n".join(f"{it.get('ts')} · {it.get('by')} · {it.get('summary')}" for it in items) or "(none)")


def cmd_health(_):
    latest = _req("GET", "/api/health").get("latest", [])
    _ok("\n".join(f"{r.get('state')} {r.get('url')} {r.get('ms')}ms" for r in latest) or "(no data)")


def cmd_search(a):
    d = _req("GET", "/api/search?" + urllib.parse.urlencode({"q": a.query, "k": a.k}))
    res = d.get("results", [])
    if d.get("error"):
        _die(d["error"])
    _ok("\n".join(f"[{r.get('score')}] {r.get('note')}: {(r.get('text') or '')[:160]}" for r in res) or "(no results)")


def _date_field(entity):
    return {"activities": "due_date", "calls": "callback_at",
            "improvements": "ship_date"}.get(entity)


def cmd_today(a):
    """The day's agenda: activities due today + overdue, and callbacks due today."""
    t = _today()
    acts = _req("GET", "/api/activities")
    nd = [x for x in acts if (x.get("status") or "") != "done"] if isinstance(acts, list) else []
    due = [x for x in nd if (x.get("due_date") or "") == t]
    over = sorted([x for x in nd if (x.get("due_date") or "") and x["due_date"] < t],
                  key=lambda x: x["due_date"])
    calls = _req("GET", "/api/calls")
    cbk = [c for c in calls if (c.get("status") or "") != "done"
           and (c.get("callback_at") or "") and c["callback_at"] <= t] if isinstance(calls, list) else []
    def A(x): return f"  #{x['id']} [{x.get('status') or '-'}] {(x.get('title') or '')[:90]}"
    out = [f"HOY {t} — vencen hoy: {len(due)} · vencidas: {len(over)} · callbacks: {len(cbk)}",
           "== Vencen hoy ==", "\n".join(A(x) for x in due) or "  (ninguna)",
           "== Vencidas ==", "\n".join(f"{A(x)} (due {x['due_date']})" for x in over) or "  (ninguna)"]
    if cbk:
        out += ["== Callbacks ==", "\n".join(f"  #{c['id']} {c.get('client')} — {c.get('reason','')[:60]}" for c in cbk)]
    _ok("\n".join(out))


def cmd_get(a):
    a.entity = _norm_entity(a.entity)
    qs = {k: v for k, v in (("q", a.q), ("status", a.status), ("stage", a.stage),
                            ("health", a.health), ("client", a.client)) if v}
    path = f"/api/{a.entity}" + ("?" + urllib.parse.urlencode(qs) if qs else "")
    rows = _req("GET", path)
    # Client-side date filters (the HQ API has no date filter): --due / --overdue / --today.
    df = _date_field(a.entity)
    if df and (a.due or a.overdue or a.today):
        want = _today() if a.today else a.due
        if a.overdue:
            t = _today()
            rows = [r for r in rows if (r.get(df) or "") and r[df] < t and (r.get("status") or "") != "done"]
        elif want:
            rows = [r for r in rows if (r.get(df) or "") == want]
    if not isinstance(rows, list):
        _ok(json.dumps(rows)[:800])
    colmap = {
        "activities": ["id", "title", "status", "due_date"],
        "calls": ["id", "client", "reason", "status", "callback_at"],
        "feedback": ["id", "client", "tag", "sentiment", "status"],
        "improvements": ["id", "title", "status", "ship_date", "client", "client_notified"],
        "clients": ["id", "name", "specialty", "stage", "health"],
    }
    _ok(f"{len(rows)} row(s)\n" + _fmt_rows(rows, colmap.get(a.entity, ["id"])))


def cmd_client_view(a):
    d = _req("GET", f"/api/client/{a.id}/dossier")
    if d.get("error"):
        _die(d["error"])
    c = d.get("client", {})
    L = d.get("linked", {})
    head = (f"{c.get('name')} · stage={c.get('stage')} health={d.get('health')} "
            f"days_in_stage={d.get('days_in_stage')} · {c.get('specialty')} {c.get('location')}")
    parts = [head, f"looking_for: {c.get('looking_for')}", f"notes: {c.get('notes')}"]
    for ent in ("calls", "feedback", "improvements"):
        rows = L.get(ent, [])
        parts.append(f"{ent} ({len(rows)}): " + "; ".join(str(r.get('title') or r.get('reason') or r.get('text') or '')[:40] for r in rows))
    _ok("\n".join(parts))


HELP = __doc__


def main():
    p = argparse.ArgumentParser(prog="hqctl", add_help=False)
    sub = p.add_subparsers(dest="cmd")

    def add(name, fn):
        sp = sub.add_parser(name, add_help=False)
        sp.set_defaults(fn=fn)
        return sp

    # writes
    sa = add("activity", lambda a: _create("activities", {
        "title": a.title, "detail": a.detail, "due_date": a.due, "status": a.status,
        "tags": json.dumps([t.strip() for t in a.tags.split(",")]) if a.tags else None}))
    sa.add_argument("title"); sa.add_argument("--detail"); sa.add_argument("--due")
    sa.add_argument("--status"); sa.add_argument("--tags")

    sc = add("call", lambda a: _create("calls", {
        "client": a.client, "phone": a.phone, "reason": a.reason,
        "callback_at": a.callback, "notes": a.notes}))
    sc.add_argument("--client", required=True); sc.add_argument("--phone")
    sc.add_argument("--reason"); sc.add_argument("--callback"); sc.add_argument("--notes")

    sf = add("feedback", lambda a: _create("feedback", {
        "client": a.client, "text": a.text, "tag": a.tag, "sentiment": a.sentiment}))
    sf.add_argument("--client"); sf.add_argument("--text", required=True)
    sf.add_argument("--tag"); sf.add_argument("--sentiment", choices=["pos", "neg", "neutral"])

    si = add("improvement", lambda a: _create("improvements", {
        "title": a.title, "detail": a.detail, "ship_date": a.ship, "client": a.client}))
    si.add_argument("title"); si.add_argument("--detail"); si.add_argument("--ship")
    si.add_argument("--client")

    scl = add("client", lambda a: _create("clients", {
        "name": a.name, "specialty": a.specialty, "location": a.location, "stage": a.stage,
        "looking_for": a.looking, "source": a.source, "contact": a.contact, "notes": a.notes}))
    scl.add_argument("name"); scl.add_argument("--specialty"); scl.add_argument("--location")
    scl.add_argument("--stage"); scl.add_argument("--looking"); scl.add_argument("--source")
    scl.add_argument("--contact"); scl.add_argument("--notes")

    ss = add("stage", lambda a: _patch("clients", a.id, {"stage": a.stage, "changed_by": "hermes"}))
    ss.add_argument("id"); ss.add_argument("stage")

    sn = add("notify", lambda a: _patch("improvements", a.id, {"client_notified": 1}))
    sn.add_argument("id")

    sd = add("done", lambda a: _patch("activities", a.id, {"status": "done"}))
    sd.add_argument("id")

    sp_ = add("patch", lambda a: _patch(a.entity, a.id, dict(kv.split("=", 1) for kv in a.pairs)))
    sp_.add_argument("entity", choices=ENTITIES + tuple(ENTITY_ALIASES)); sp_.add_argument("id")
    sp_.add_argument("pairs", nargs="+", help="key=value ...")

    sdel = add("del", lambda a: (_req("DELETE", f"/api/{_norm_entity(a.entity)}/{a.id}"), _ok(f"ok · {_norm_entity(a.entity)} #{a.id} deleted")))
    sdel.add_argument("entity", choices=ENTITIES + tuple(ENTITY_ALIASES)); sdel.add_argument("id")

    # reads
    add("stats", cmd_stats)
    add("insights", cmd_insights)
    add("attention", cmd_attention)
    add("pipeline", cmd_pipeline)
    sfeed = add("feed", cmd_feed); sfeed.add_argument("--limit", default="20")
    add("health", cmd_health)
    ssearch = add("search", cmd_search); ssearch.add_argument("query"); ssearch.add_argument("--k", default="6")
    add("today", cmd_today)
    sget = add("get", cmd_get)
    sget.add_argument("entity", choices=ENTITIES + tuple(ENTITY_ALIASES))
    for f in ("q", "status", "stage", "health", "client", "due"):
        sget.add_argument("--" + f)
    sget.add_argument("--overdue", action="store_true")
    sget.add_argument("--today", action="store_true")
    scv = add("client-view", cmd_client_view); scv.add_argument("id")

    if len(sys.argv) < 2 or sys.argv[1] in ("help", "-h", "--help"):
        print(HELP); sys.exit(0)
    args = p.parse_args()
    if not getattr(args, "fn", None):
        print(HELP); sys.exit(0)
    args.fn(args)


if __name__ == "__main__":
    main()
