r"""How much work each agent and each team has actually done.

Where the numbers come from
---------------------------
Hermes keeps a row per conversation - how many messages, how many tool calls, how many
tokens in and out, when it started and when it was last touched - and it keeps that row
the same way whichever brain is behind the bridge. That is the whole answer to "how do
we do this for Claude Code AND Codex": we do not. The counting is already engine-
agnostic because Hermes does it, and nothing here asks a brain anything.

What the engine IS, then, is a property of the AGENT, read from Olivaw's own registry -
so every number can be labelled with the brain that produced it, and a team that runs
two of them can be split by engine without either side knowing the other exists.

What it deliberately does not claim
-----------------------------------
* **Cost is usually zero, and that is not a bug.** Claude Code on a subscription reports
  no per-token price, so `actual_cost_usd` is 0 and summing it says nothing. It is
  reported when it is there and omitted when it is not, rather than printed as "$0.00"
  next to two million tokens - which reads as free rather than as not-measured.
* **`model` on a session is not the engine.** Most rows say "claude-code", but some say
  "hermes-agent" and some say the agent's own slug, depending on who opened the session.
  It is passed through as a detail and never used to decide which brain ran anything.
* **A gateway that is off has no numbers, not zero numbers.** An agent nobody has spoken
  to and an agent whose gateway is down look identical in a total; they are different
  states and are reported as such.
"""

import time

try:
    from . import agents_registry, talk
except ImportError:  # running from src/ rather than as a package
    from wizard import agents_registry, talk

# How many conversations to pull per agent before filtering by date. Generous because
# the filter is the window, not this: an agent with three hundred short sessions in a
# month should not silently report two hundred of them.
MAX_SESSIONS = 400

DEFAULT_DAYS = 30

# The counters worth adding up, and nothing else. Each is a plain integer in Hermes'
# session row; anything missing counts as zero, which is right for a sum.
COUNTERS = ("message_count", "tool_call_count", "api_call_count",
            "input_tokens", "output_tokens", "cache_read_tokens",
            "cache_write_tokens", "reasoning_tokens")


def _engine_of(slug, install_dir=None, default_engine="claude"):
    """Which brain this agent runs on, from Olivaw's registry rather than from a session.

    An extra agent inherits the default one's brain unless it names its own - the same
    rule launcher.py applies when it starts the bridge, kept identical here so the label
    on a number matches the process that produced it.
    """
    slug = (slug or "default").strip().lower()
    if slug != "default":
        try:
            for a in agents_registry.list_agents(install_dir):
                if (a.get("profile") or a.get("slug")) == slug:
                    got = (a.get("engine") or "").strip().lower()
                    return got or default_engine
        except Exception:  # noqa: BLE001
            pass
    return default_engine


def _when(row):
    """When this conversation last counted as activity."""
    for key in ("last_active", "ended_at", "started_at"):
        v = row.get(key)
        if isinstance(v, (int, float)) and v > 0:
            return float(v)
    return 0.0


def _blank():
    out = {k: 0 for k in COUNTERS}
    out.update({"sessions": 0, "cost_usd": 0.0, "last_active": 0.0,
                "by_source": {}, "by_model": {}})
    return out


def _add(into, row):
    into["sessions"] += 1
    for k in COUNTERS:
        v = row.get(k)
        if isinstance(v, (int, float)):
            into[k] += int(v)
    cost = row.get("actual_cost_usd")
    if not isinstance(cost, (int, float)) or cost <= 0:
        cost = row.get("estimated_cost_usd")
    if isinstance(cost, (int, float)) and cost > 0:
        into["cost_usd"] += float(cost)
    into["last_active"] = max(into["last_active"], _when(row))
    for key, field in (("by_source", "source"), ("by_model", "model")):
        name = str(row.get(field) or "?")
        into[key][name] = into[key].get(name, 0) + 1


def _merge(into, other):
    into["sessions"] += other["sessions"]
    for k in COUNTERS:
        into[k] += other[k]
    into["cost_usd"] += other["cost_usd"]
    into["last_active"] = max(into["last_active"], other["last_active"])
    for key in ("by_source", "by_model"):
        for name, n in other[key].items():
            into[key][name] = into[key].get(name, 0) + n


def for_agent(profile, hermes=None, install_dir=None, days=DEFAULT_DAYS,
              default_engine="claude"):
    """One agent's numbers over the window, or why there are none."""
    since = time.time() - max(1, int(days)) * 86400
    out = _blank()
    out["engine"] = _engine_of(profile, install_dir, default_engine)
    res = talk.sessions(profile, hermes, limit=MAX_SESSIONS)
    if not res.get("ok"):
        # Not zero. An agent whose gateway is down has no numbers to give, and saying
        # "0 messages" about it would be a measurement nobody took.
        out["known"] = False
        out["detail"] = res.get("detail", "")
        return out
    out["known"] = True
    for row in (res.get("sessions") or []):
        if not isinstance(row, dict):
            continue
        if _when(row) < since:
            continue
        _add(out, row)
    return out


def report(roster, team_data=None, hermes=None, install_dir=None, days=DEFAULT_DAYS,
           default_engine="claude"):
    """Every agent, and every team, over the same window.

    `roster` is intercom.roster() and `team_data` the teams map, both passed in so this
    reads nothing it was not given - the caller already has them, and a module that
    quietly opened its own copy would be a second answer to "which agents are there".
    """
    days = max(1, min(int(days or DEFAULT_DAYS), 365))
    # In parallel, because each agent is an HTTP call to its own gateway and seven
    # of them one after another is most of a minute on a phone - measured at about
    # thirty-five seconds before this, which is long enough that the screen looks
    # stuck rather than busy. They do not touch each other, so there is nothing to
    # order them for.
    import concurrent.futures

    wanted = [a for a in (roster or []) if (a.get("slug") or a.get("profile"))]
    agents, by_slug = [], {}
    if wanted:
        with concurrent.futures.ThreadPoolExecutor(
                max_workers=min(8, len(wanted))) as ex:
            jobs = []
            for a in wanted:
                slug = a.get("slug") or a.get("profile")
                prof = None if slug == "default" else slug
                jobs.append((a, slug, ex.submit(
                    for_agent, prof, hermes, install_dir, days, default_engine)))
            for a, slug, job in jobs:
                try:
                    row = job.result()
                except Exception as e:  # noqa: BLE001
                    row = _blank()
                    row.update({"engine": default_engine, "known": False,
                                "detail": str(e)[:160]})
                row.update({"slug": slug, "name": a.get("name") or slug})
                agents.append(row)
                by_slug[slug] = row

    teams = []
    grouped = set()
    for g in ((team_data or {}).get("teams") or []):
        if not isinstance(g, dict):
            continue
        total = _blank()
        members, engines = [], {}
        for slug in (g.get("members") or []):
            row = by_slug.get(slug)
            if not row:
                continue
            grouped.add(slug)
            members.append(slug)
            if row.get("known"):
                _merge(total, row)
            engines[row["engine"]] = engines.get(row["engine"], 0) + 1
        total.update({"id": g.get("id", ""), "name": g.get("name", ""),
                      "icon": g.get("icon", ""), "members": members,
                      "engines": engines})
        teams.append(total)

    loose = [a["slug"] for a in agents if a["slug"] not in grouped]
    return {"ok": True, "days": days, "agents": agents, "teams": teams,
            "unassigned": loose,
            # Said out loud rather than left for a reader to infer from zeros: under a
            # subscription there is no per-token price to add up, and a cost of 0 beside
            # two million tokens reads as free rather than as not-measured.
            "costed": any(a["cost_usd"] > 0 for a in agents)}
