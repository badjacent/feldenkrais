"""NYC crawler administration — the authenticated human control plane.

The Mac Mini discovers and crawls; these screens let the owner review what it
found, set provider/venue policy, inspect evidence and failures, and see
whether the weekly search is covering useful territory.

Boundaries (see docs/NYC_CRAWLER_ADMIN_SPEC.md):

* every route is admin-only — extracted text, prompts, crawler errors, decision
  history, and candidate records never appear on a public route;
* the only policy mutation implemented today is the audited A/B/C/D review;
  factual edits, duplicate merge, and defer wait on the `nyc_directory_change`
  migration and are shown as disabled with the reason;
* nothing here shells into the Mini, runs Hermes, or touches the public Atlas
  publication gate. A task retry only changes queue state.
"""

import html as html_mod
import json
from datetime import datetime, timezone
from urllib.parse import urlencode
from uuid import UUID

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

import nyc_control as _control
import web as _web
from db.nyc import (
    NYC_TIER_INTERVAL_DAYS,
    NycAdminError,
    NycNotFoundError,
    NycSchemaMissingError,
    NycStaleFormError,
)

router = APIRouter()

BASE = "/admin/nyc"

NAV = (
    (BASE, "Overview"),
    (f"{BASE}/providers", "Providers"),
    (f"{BASE}/venues", "Venues"),
    (f"{BASE}/events", "Events"),
    (f"{BASE}/discovery", "Discovery"),
    (f"{BASE}/tasks", "Tasks"),
    (f"{BASE}/decisions", "Decisions"),
)

# Milestone 6 in the spec: these need the nyc_directory_change audit table.
PENDING_MIGRATION_NOTE = (
    "Factual edits (name, schedule URLs), duplicate merge, and deferral are "
    "disabled until the <code>nyc_directory_change</code> migration lands — "
    "they have no durable audit trail yet. A/B/C/D review is audited in "
    "<code>nyc_directory_decision</code> and is available now."
)


# ── Auth ─────────────────────────────────────────────────────────────────

def _is_local_request(request: Request) -> bool:
    """Local development runs with full access, as the rest of the site does.

    Only a genuine localhost Host header off Lambda qualifies, so the deployed
    console still requires the admin role.
    """
    if _web.IS_LAMBDA:
        return False
    host = request.headers.get("host", "")
    return host.startswith("localhost") or host.startswith("127.0.0.1")


def _guard(request: Request):
    """Return (user, response). A response means: stop and return it.

    Anonymous follows the existing login flow; a signed-in non-admin gets 403.
    On localhost the console is open, matching the site's local-dev convention.
    """
    user = _web._auth_mod.get_current_user(request) if _web._auth_mod else None
    local = _is_local_request(request)
    if not user and local:
        user = {"login": "localhost", "tier": "admin", "local": True}
    if not user:
        return None, HTMLResponse(_web._login_page_html(), status_code=401)
    if user.get("tier") != "admin" and not local:
        return None, HTMLResponse(
            _shell("Forbidden", "<div class='card'><h1>403 — admin only</h1>"
                   "<p class='muted'>The NYC directory console is restricted to "
                   "the site administrator.</p></div>", user=user, nav=False),
            status_code=403,
        )
    if not _web._db:
        return None, HTMLResponse(
            _shell("Unavailable", "<div class='card'><h1>Database not configured</h1>"
                   "<p class='muted'>DATABASE_URL is not set for this process.</p></div>",
                   user=user, nav=False),
            status_code=503,
        )
    return user, None


def _actor(user: dict) -> str:
    return str(user.get("login") or user.get("user_id") or "unknown")


def _queue_provider_crawl(provider_id: str, actor: str) -> str:
    """Put one narrowly-scoped crawl request on the Mini's private S3 queue.

    The wire format lives in nyc_control; the provider UUID is re-parsed here so
    nothing a browser supplied can reach the control prefix as a path.
    """
    provider_id = str(UUID(provider_id))
    bucket = _web.KB_BUCKET
    if not bucket:
        raise NycAdminError("KB_BUCKET is not configured; crawl request was not queued")
    result = _control.put_request(_web.get_s3_client(), bucket, provider_id, actor)
    return result["request_id"]


def _schema_error_page(user: dict, exc: NycSchemaMissingError) -> HTMLResponse:
    items = "".join(f"<li><code>atlas_nyc.{_h(t)}</code></li>" for t in exc.missing)
    body = f"""
    <div class="card">
      <h1>NYC schema not present</h1>
      <p>This console expects the <code>atlas_nyc</code> schema, which Atlas
         migrations own. The site never creates these tables.</p>
      <p class="muted">Missing:</p>
      <ul>{items}</ul>
      <p class="muted">Run the Atlas NYC migration against this database
         (<code>scripts/migrate_nyc_database.py</code>), then reload.</p>
    </div>"""
    return HTMLResponse(_shell("NYC schema missing", body, user=user), status_code=503)


# ── Formatting helpers ───────────────────────────────────────────────────

def _h(value) -> str:
    return html_mod.escape("" if value is None else str(value))


def _ts(value, *, with_time: bool = True) -> str:
    if not value:
        return "—"
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return _h(value)
    fmt = "%Y-%m-%d %H:%M" if with_time else "%Y-%m-%d"
    return value.strftime(fmt)


def _ago(value) -> str:
    if not value:
        return "never"
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return _h(value)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    delta = (datetime.now(timezone.utc) - value).total_seconds()
    past = delta >= 0
    delta = abs(delta)
    if delta < 90:
        out = f"{int(delta)}s"
    elif delta < 5400:
        out = f"{int(delta // 60)}m"
    elif delta < 172800:
        out = f"{int(delta // 3600)}h"
    else:
        out = f"{int(delta // 86400)}d"
    return f"{out} ago" if past else f"in {out}"


def _tier(tier: str | None) -> str:
    tier = (tier or "?").upper()
    every = {"A": "daily", "B": "7 days", "C": "30 days", "D": "never"}.get(tier, "")
    return (f'<span class="tier tier-{_h(tier.lower())}" title="checked {_h(every)}">'
            f'{_h(tier)}</span>')


def _chip(text: str, kind: str = "") -> str:
    return f'<span class="chip chip-{_h(kind)}">{_h(text)}</span>'


def _link(url: str | None, label: str | None = None, *, max_len: int = 48) -> str:
    if not url:
        return "—"
    shown = label or url
    if len(shown) > max_len:
        shown = shown[: max_len - 1] + "…"
    return f'<a href="{_h(url)}" target="_blank" rel="noopener">{_h(shown)}</a>'


def _urls(values, *, limit: int = 4) -> str:
    values = list(values or [])
    if not values:
        return "<span class='muted'>none</span>"
    shown = "<br>".join(_link(u, max_len=52) for u in values[:limit])
    if len(values) > limit:
        shown += f"<br><span class='muted'>+{len(values) - limit} more</span>"
    return shown


def _json_block(payload, *, limit: int = 4000) -> str:
    text = json.dumps(payload, indent=2, sort_keys=True, default=str)
    truncated = len(text) > limit
    return (f"<pre class='code'>{_h(text[:limit])}"
            f"{'…' if truncated else ''}</pre>")


def _qs(**params) -> str:
    clean = {k: v for k, v in params.items() if v}
    return ("?" + urlencode(clean)) if clean else ""


# ── Page shell ───────────────────────────────────────────────────────────

_CSS = """
:root {
  --ink: #1c2024; --muted: #6a7480; --line: #dfe3e8; --bg: #f6f7f9;
  --card: #ffffff; --accent: #1c5fa8; --warn: #9a6212; --bad: #a63232;
  --good: #26694a;
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--ink);
  font: 14px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }
a { color: var(--accent); text-decoration: none; }
a:hover { text-decoration: underline; }
header.topbar { background: #fff; border-bottom: 1px solid var(--line);
  padding: 10px 22px; display: flex; gap: 18px; align-items: baseline;
  flex-wrap: wrap; position: sticky; top: 0; z-index: 5; }
header.topbar .brand { font-weight: 700; letter-spacing: -0.01em; }
header.topbar nav { display: flex; gap: 14px; flex-wrap: wrap; }
header.topbar nav a { color: var(--muted); }
header.topbar nav a.on { color: var(--ink); font-weight: 600;
  border-bottom: 2px solid var(--accent); }
header.topbar .who { margin-left: auto; color: var(--muted); font-size: 12px; }
main { padding: 20px 22px 60px; max-width: 1500px; }
h1 { font-size: 20px; margin: 0 0 4px; }
h2 { font-size: 15px; margin: 0 0 10px; }
h3 { font-size: 13px; margin: 18px 0 6px; text-transform: uppercase;
  letter-spacing: 0.06em; color: var(--muted); }
.card { background: var(--card); border: 1px solid var(--line); border-radius: 8px;
  padding: 16px 18px; margin-bottom: 16px; }
.grid { display: grid; gap: 16px; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); }
.stats { display: grid; gap: 10px; grid-template-columns: repeat(auto-fit, minmax(130px, 1fr)); }
.stat { background: var(--card); border: 1px solid var(--line); border-radius: 8px;
  padding: 10px 12px; }
.stat .n { font-size: 22px; font-weight: 650; font-variant-numeric: tabular-nums; }
.stat .k { font-size: 11px; color: var(--muted); text-transform: uppercase;
  letter-spacing: 0.05em; }
.stat.alert .n { color: var(--bad); }
table { border-collapse: collapse; width: 100%; font-size: 13px; }
.scroll { overflow-x: auto; }
th, td { text-align: left; padding: 7px 9px; border-bottom: 1px solid var(--line);
  vertical-align: top; }
th { font-size: 11px; text-transform: uppercase; letter-spacing: 0.05em;
  color: var(--muted); font-weight: 600; white-space: nowrap; }
tr:last-child td { border-bottom: none; }
td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; }
.muted { color: var(--muted); }
.small { font-size: 12px; }
.tier { display: inline-block; min-width: 18px; text-align: center; padding: 1px 5px;
  border-radius: 4px; font-weight: 700; font-size: 11px; border: 1px solid var(--line); }
.tier-a { background: #e7f1fb; color: #14508f; border-color: #bcd7f0; }
.tier-b { background: #eaf3ec; color: #26694a; border-color: #c6e0cf; }
.tier-c { background: #f3f0e8; color: #7a6420; border-color: #e2d9c0; }
.tier-d { background: #f6ecec; color: #a63232; border-color: #eccfcf; }
.chip { display: inline-block; padding: 1px 7px; border-radius: 10px; font-size: 11px;
  border: 1px solid var(--line); background: #f2f4f6; color: var(--muted); }
.chip-good { background: #eaf3ec; color: #26694a; border-color: #c6e0cf; }
.chip-warn { background: #f9f1e3; color: #8a5c11; border-color: #ecdcbb; }
.chip-bad { background: #f9ecec; color: #a63232; border-color: #eccfcf; }
.chip-info { background: #e9f0f8; color: #14508f; border-color: #c9dcf0; }
form.inline { display: inline-flex; gap: 5px; align-items: center; flex-wrap: wrap; }
input[type=text], input[type=search], select, textarea {
  font: inherit; padding: 5px 7px; border: 1px solid var(--line); border-radius: 5px;
  background: #fff; color: var(--ink); }
textarea { width: 100%; min-height: 62px; }
button { font: inherit; padding: 5px 11px; border-radius: 5px; cursor: pointer;
  border: 1px solid var(--line); background: #fff; color: var(--ink); }
button:hover { border-color: #b7bfc8; }
button.primary { background: var(--accent); border-color: var(--accent); color: #fff; }
button.danger { background: #fff; border-color: #e0bcbc; color: var(--bad); }
button.danger:hover { background: #fdf5f5; }
button[disabled] { opacity: 0.45; cursor: not-allowed; }
.filters { display: flex; gap: 8px; align-items: center; flex-wrap: wrap;
  margin-bottom: 12px; }
.banner { border-radius: 8px; padding: 10px 14px; margin-bottom: 14px;
  border: 1px solid var(--line); background: #fff; }
.banner.ok { border-color: #c6e0cf; background: #f2f9f4; color: #1e5638; }
.banner.err { border-color: #eccfcf; background: #fdf4f4; color: #8f2a2a; }
.banner.note { border-color: #e2d9c0; background: #fbf8f0; color: #6f5a1c; }
pre.code { background: #f4f5f7; border: 1px solid var(--line); border-radius: 6px;
  padding: 10px; overflow-x: auto; font-size: 12px; line-height: 1.45;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  white-space: pre-wrap; word-break: break-word; }
.kv { display: grid; grid-template-columns: 160px 1fr; gap: 4px 14px; font-size: 13px; }
.kv dt { color: var(--muted); }
.kv dd { margin: 0; }
.rowsep { border-top: 1px solid var(--line); margin: 14px 0; }
.legend { display: flex; gap: 14px; flex-wrap: wrap; font-size: 12px;
  color: var(--muted); }
"""


def _shell(title: str, body: str, *, user: dict | None = None,
           active: str = "", nav: bool = True) -> str:
    nav_html = ""
    if nav:
        links = "".join(
            f'<a href="{_h(href)}" class="{"on" if href == active else ""}">{_h(label)}</a>'
            for href, label in NAV
        )
        nav_html = f"<nav>{links}</nav>"
    who = _h(user.get("login", "")) if user else ""
    return f"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_h(title)} — NYC directory</title>
<style>{_CSS}</style>
</head><body>
<header class="topbar">
  <span class="brand"><a href="{BASE}" style="color:inherit">NYC directory</a></span>
  {nav_html}
  <span class="who">{who} &middot; <a href="/">site</a></span>
</header>
<main>{body}</main>
</body></html>"""


def _banners(request: Request) -> str:
    out = []
    msg = request.query_params.get("msg")
    err = request.query_params.get("err")
    if msg:
        out.append(f"<div class='banner ok'>{_h(msg)}</div>")
    if err:
        out.append(f"<div class='banner err'>{_h(err)}</div>")
    return "".join(out)


# ── Overview ─────────────────────────────────────────────────────────────

@router.get("/admin/nyc", response_class=HTMLResponse)
async def nyc_overview(request: Request):
    user, blocked = _guard(request)
    if blocked:
        return blocked
    try:
        data = _web._db.nyc_overview()
    except NycSchemaMissingError as exc:
        return _schema_error_page(user, exc)

    counts = data["counts"]
    providers_total = sum(r["total"] for r in data["providers_by_state"])
    providers_active = sum(r["total"] for r in data["providers_by_state"]
                           if r["status"] == "active")
    venues_total = sum(r["total"] for r in data["venues_by_state"])
    venues_active = sum(r["total"] for r in data["venues_by_state"]
                        if r["status"] == "active")
    pending = (len(data["provider_candidates"]) + len(data["venue_candidates"])
               + counts["tasks_open"] + counts["tasks_failed"])

    stats = [
        ("Providers", providers_total, f"{providers_active} active", False),
        ("Venues", venues_total, f"{venues_active} active", False),
        ("Offerings", counts["offerings"], f"{counts['offerings_active']} active", False),
        ("Occurrences", counts["occurrences"],
         f"{counts['occurrences_upcoming']} upcoming", False),
        ("Due now", len(data["providers_due"]) + len(data["venues_due"]),
         f"{len(data['providers_due'])} providers · {len(data['venues_due'])} venues",
         False),
        ("Needs review", pending, "candidates + open/failed tasks", pending > 0),
    ]
    stat_html = "".join(
        f"<div class='stat{' alert' if alert else ''}'><div class='n'>{n}</div>"
        f"<div class='k'>{_h(label)}</div><div class='muted small'>{_h(sub)}</div></div>"
        for label, n, sub, alert in stats
    )

    # Latest weekly discovery run.
    if data["runs"]:
        run = data["runs"][0]
        duration = ""
        if run.get("finished_at"):
            duration = f"{(run['finished_at'] - run['started_at']).total_seconds():.1f}s"
        else:
            duration = "still running"
        disposition = (f"{run['queries_completed']}/{run['queries_planned']} queries · "
                       f"{run['results_seen']} results · {run['candidates_added']} added · "
                       f"{run['candidates_updated']} updated · "
                       f"{run['results_rejected']} rejected")
        run_html = f"""
        <div class="kv">
          <dt>Started</dt><dd>{_ts(run['started_at'])} <span class="muted">({_ago(run['started_at'])})</span></dd>
          <dt>Status</dt><dd>{_run_status_chip(run['status'])} <span class="muted">{_h(duration)}</span></dd>
          <dt>Disposition</dt><dd>{_h(disposition)}</dd>
          <dt>Detail</dt><dd><a href="{BASE}/discovery/{_h(run['id'])}">observations →</a></dd>
        </div>"""
    else:
        run_html = ("<p class='muted'>No discovery run recorded yet. The Mini writes "
                    "one <code>nyc_discovery_run</code> per weekly search.</p>")

    # Crawler health.
    if data["crawler"]:
        rows = "".join(
            f"<tr><td>{_h(label)}</td><td class='num'>{w['checks']}</td>"
            f"<td class='num'>{w['failures']}</td></tr>"
            for label, w in data["crawler"].items()
        )
        health = f"""<table><tr><th>window</th><th class="num">checks</th>
          <th class="num">failures</th></tr>{rows}</table>"""
        if data["recent_failures"]:
            fail_rows = "".join(
                f"<tr><td>{_ts(f['fetched_at'])}</td><td>{_link(f['url'])}</td>"
                f"<td>{_h(f['status_code'] or '')}</td>"
                f"<td class='small muted'>{_h((f['error'] or '')[:120])}</td></tr>"
                for f in data["recent_failures"]
            )
            health += ("<h3>Recent failures</h3><div class='scroll'><table>"
                       "<tr><th>when</th><th>url</th><th>code</th><th>error</th></tr>"
                       f"{fail_rows}</table></div>")
        elif any(w["checks"] for w in data["crawler"].values()):
            health += "<p class='muted small'>No failures in the last 7 days.</p>"
        else:
            health += ("<p class='muted small'>No fetches logged in the last 7 days — "
                       "the Mini has not run, or is serving everything from cache.</p>")
    else:
        health = ("<p class='muted'><code>atlas_nyc.fetch_log</code> is not present in "
                  "this schema, so crawler checks and failures are not recorded.</p>")

    due_counts = {}
    for row in data["providers_by_state"]:
        if row["due"]:
            due_counts[row["tier"]] = due_counts.get(row["tier"], 0) + row["due"]
    due_by_tier = " ".join(
        f"{_tier(t)} <span class='muted'>{due_counts.get(t, 0)} due</span>"
        for t in ("A", "B", "C")
    ) or "<span class='muted'>nothing due</span>"

    due_rows = "".join(
        f"<tr><td>{_tier(p['tier'])}</td>"
        f"<td><a href='{BASE}/providers/{_h(p['id'])}'>{_h(p['name'])}</a></td>"
        f"<td>{_link(p['canonical_url'])}</td>"
        f"<td class='muted'>{_ago(p['last_crawled_at'])}</td></tr>"
        for p in data["providers_due"][:20]
    ) or "<tr><td colspan='4' class='muted'>Nothing due.</td></tr>"
    venue_due_rows = "".join(
        f"<tr><td>{_tier(v['tier'])}</td>"
        f"<td><a href='{BASE}/venues/{_h(v['id'])}'>{_h(v['name'])}</a></td>"
        f"<td class='muted'>{_ago(v['last_crawled_at'])}</td></tr>"
        for v in data["venues_due"][:20]
    ) or "<tr><td colspan='3' class='muted'>Nothing due.</td></tr>"

    upcoming_rows = "".join(
        f"<tr><td>{_ts(o['starts_at'])}</td>"
        f"<td>{_h(o['title'])}</td>"
        f"<td><a href='{BASE}/providers/{_h(o['provider_id'])}'>{_h(o['provider_name'])}</a></td>"
        f"<td class='muted'>{_h(o['venue_name'] or '')}"
        f"{' · ' + _h(o['area']) if o['area'] else ''}</td>"
        f"<td class='muted'>{_ago(o['last_confirmed_at'])}</td></tr>"
        for o in data["upcoming"]
    ) or "<tr><td colspan='5' class='muted'>No confirmed upcoming occurrences.</td></tr>"

    review_bits = []
    if data["provider_candidates"]:
        rows = "".join(
            f"<tr><td><a href='{BASE}/providers/{_h(p['id'])}'>{_h(p['name'])}</a></td>"
            f"<td>{_link(p['canonical_url'])}</td>"
            f"<td class='muted'>{_ago(p['created_at'])}</td></tr>"
            for p in data["provider_candidates"][:15]
        )
        review_bits.append(
            f"<h3>Candidate providers ({len(data['provider_candidates'])})</h3>"
            f"<table><tr><th>name</th><th>url</th><th>found</th></tr>{rows}</table>")
    if data["venue_candidates"]:
        rows = "".join(
            f"<tr><td><a href='{BASE}/venues/{_h(v['id'])}'>{_h(v['name'])}</a></td>"
            f"<td>{_h(v['status'])}</td><td class='muted'>{_h(v['area'] or '')}</td>"
            f"<td class='muted'>{_ago(v['created_at'])}</td></tr>"
            for v in data["venue_candidates"][:15]
        )
        review_bits.append(
            f"<h3>Unreviewed venues ({len(data['venue_candidates'])})</h3>"
            f"<table><tr><th>name</th><th>state</th><th>area</th><th>found</th></tr>"
            f"{rows}</table>")
    if data["offerings_needing_review"]:
        rows = "".join(
            f"<tr><td>{_h(o['title'])}</td>"
            f"<td><a href='{BASE}/providers/{_h(o['provider_id'])}'>{_h(o['provider_name'])}</a></td>"
            f"<td class='muted'>{_h(o['venue_name'] or '')}</td></tr>"
            for o in data["offerings_needing_review"]
        )
        review_bits.append(
            "<h3>Offerings with no dated occurrence and no stated recurrence</h3>"
            f"<table><tr><th>title</th><th>provider</th><th>venue</th></tr>{rows}</table>")
    if data["open_tasks"]:
        rows = "".join(
            f"<tr><td><a href='{BASE}/tasks/{_h(t['id'])}'>{_task_status_chip(t['status'])}</a></td>"
            f"<td>{_h(t['provider_name'] or '')}</td>"
            f"<td>{_link(t['source_url'])}</td>"
            f"<td class='muted'>{_ago(t['created_at'])}</td></tr>"
            for t in data["open_tasks"]
        )
        review_bits.append(
            f"<h3>Open and failed enrichment tasks</h3><table>"
            f"<tr><th>state</th><th>provider</th><th>page</th><th>age</th></tr>{rows}</table>")
    review_html = "".join(review_bits) or "<p class='muted'>Nothing waiting on review.</p>"

    decision_rows = "".join(
        f"<tr><td>{_ts(d['decided_at'])}</td><td>{_h(d['entity_type'])}</td>"
        f"<td>{_h(d['entity_name'] or d['entity_id'])}</td>"
        f"<td>{_decision_chip(d['decision'])} {_tier(d['tier'])}</td>"
        f"<td class='muted'>{_h(d['actor'])}</td></tr>"
        for d in data["recent_decisions"]
    ) or "<tr><td colspan='5' class='muted'>No decisions recorded.</td></tr>"

    body = f"""
    {_banners(request)}
    <h1>NYC operations</h1>
    <p class="muted small">Private console. The Mini crawls; this is where policy is
      set. Nothing here publishes <code>/nyc/</code> or touches the Atlas release gate.
      Read at {_ts(data['generated_at'])} UTC.</p>
    <div class="stats" style="margin:14px 0 18px">{stat_html}</div>
    <div class="grid">
      <div class="card"><h2>Latest weekly discovery run</h2>{run_html}</div>
      <div class="card"><h2>Crawler health</h2>{health}</div>
    </div>
    <div class="grid">
      <div class="card"><h2>Providers due now</h2>
        <div class="legend" style="margin-bottom:8px">{due_by_tier}</div>
        <table><tr><th>tier</th><th>name</th><th>url</th><th>last crawl</th></tr>
        {due_rows}</table>
        <p class="small muted" style="margin-top:8px">Cadence: A daily · B every 7 days ·
          C every 30 days · D never.</p>
      </div>
      <div class="card"><h2>Venues due now</h2>
        <table><tr><th>tier</th><th>name</th><th>last crawl</th></tr>{venue_due_rows}</table>
      </div>
    </div>
    <div class="card"><h2>Upcoming confirmed occurrences</h2>
      <div class="scroll"><table>
      <tr><th>starts</th><th>title</th><th>provider</th><th>venue</th><th>confirmed</th></tr>
      {upcoming_rows}</table></div>
      <p class="small muted" style="margin-top:8px">
        <a href="{BASE}/events">All events and evidence →</a></p>
    </div>
    <div class="card"><h2>Needs review</h2>{review_html}</div>
    <div class="card"><h2>Recent decisions</h2>
      <table><tr><th>when</th><th>type</th><th>entity</th><th>decision</th><th>actor</th></tr>
      {decision_rows}</table>
      <p class="small muted" style="margin-top:8px">
        <a href="{BASE}/decisions">Full decision history →</a></p>
    </div>
    """
    return HTMLResponse(_shell("Overview", body, user=user, active=BASE))


def _run_status_chip(status: str) -> str:
    kind = {"succeeded": "good", "running": "info", "failed": "bad"}.get(status, "warn")
    return _chip(status, kind)


def _task_status_chip(status: str) -> str:
    kind = {"done": "good", "queued": "info", "running": "info",
            "failed": "bad"}.get(status, "warn")
    return _chip(status, kind)


def _decision_chip(decision: str) -> str:
    return _chip(decision, "good" if decision == "approved" else "bad")


def _entity_status_chip(status: str) -> str:
    kind = {"active": "good", "candidate": "warn", "observed": "warn",
            "retired": "bad"}.get(status, "")
    return _chip(status, kind)


# ── Review form fragments ────────────────────────────────────────────────

def _review_form(entity_type: str, entity: dict, *, compact: bool) -> str:
    """A/B/C/D decision form. Approval is always an explicit action."""
    action = f"{BASE}/{entity_type}s/{_h(entity['id'])}/review"
    stamp = entity["updated_at"].isoformat() if entity.get("updated_at") else ""
    current = (entity.get("crawl_state") or {}).get("review_decision")
    hidden = (f'<input type="hidden" name="expected_updated_at" value="{_h(stamp)}">')
    if compact:
        buttons = "".join(
            f'<button name="tier" value="{t}" title="approve as tier {t} '
            f'({NYC_TIER_INTERVAL_DAYS[t]}d)">{t}</button>'
            for t in ("A", "B", "C")
        )
        return f"""
        <form class="inline" method="post" action="{action}">
          {hidden}
          <input type="hidden" name="decision" value="approved">
          {buttons}
          <button class="danger" name="decision_override" value="rejected"
                  title="reject — never check again">D</button>
        </form>"""
    options = "".join(
        f'<option value="{t}"{" selected" if _current_tier(entity) == t else ""}>'
        f'{t} — every {NYC_TIER_INTERVAL_DAYS[t]} day(s)</option>'
        for t in ("A", "B", "C")
    )
    note = ""
    if current:
        note = (f"<p class='small muted'>Current: {_decision_chip(current)} "
                f"{_tier(_current_tier(entity))} by "
                f"{_h((entity.get('crawl_state') or {}).get('reviewed_by', '?'))} "
                f"on {_ts((entity.get('crawl_state') or {}).get('reviewed_at'))}</p>")
    return f"""
    <form method="post" action="{action}">
      {hidden}
      {note}
      <div class="filters">
        <label class="small muted" for="tier-select">Tier</label>
        <select id="tier-select" name="tier">{options}</select>
        <button class="primary" name="decision" value="approved">Approve</button>
        <button class="danger" name="decision" value="rejected">Reject (D — never check)</button>
      </div>
      <label class="small muted" for="rationale">Review rationale</label>
      <textarea id="rationale" name="rationale"
        placeholder="Why this decision? Recorded in nyc_directory_decision."></textarea>
    </form>
    <p class="small muted">Approval is a separate explicit action — it is never inferred
      from any other edit.</p>"""


# ── Expedited check ("Check soon") ───────────────────────────────────────

def _latest_check_requests(provider_ids) -> tuple[dict, bool]:
    """(latest request per provider, whether status is tracked at all)."""
    try:
        shape = _web._db.nyc_crawl_request_shape()
        if not shape:
            return {}, False
        return _web._db.nyc_latest_crawl_requests(list(provider_ids)), True
    except Exception as exc:  # a control-plane read never breaks a page
        print(f"[nyc-admin] crawl-request read failed: {exc}", flush=True)
        return {}, False


def _check_state(provider: dict, request_row: dict | None, tracked: bool) -> dict:
    """Whether Check soon is offered, and why not when it isn't."""
    if not _web.KB_BUCKET:
        return {"enabled": False,
                "reason": "no control bucket configured for this deployment",
                "request": request_row, "tracked": tracked}
    eligible, reason = _check_eligibility(provider)
    if not eligible:
        return {"enabled": False, "reason": reason, "request": request_row,
                "tracked": tracked}
    if request_row and not _control.is_terminal(request_row.get("status")):
        return {"enabled": False,
                "reason": (f"a request is already in flight "
                           f"({request_row.get('status') or 'pending'})"),
                "request": request_row, "tracked": tracked}
    return {"enabled": True, "reason": "", "request": request_row, "tracked": tracked}


def _check_eligibility(provider: dict) -> tuple[bool, str]:
    """Only an approved, still-crawled provider may be checked early."""
    if provider.get("check_eligible") is not None:
        return bool(provider["check_eligible"]), provider.get("check_blocked_reason", "")
    if provider.get("status") != "active" or not provider.get("enabled", True):
        return False, "only an approved provider can be checked early"
    tier = provider.get("tier") or _current_tier(provider)
    if tier not in ("A", "B", "C"):
        return False, "a tier D record is never crawled again"
    return True, ""


def _check_soon_control(provider: dict, state: dict, *, compact: bool) -> str:
    """The owner-only Check soon button plus whatever status we can show."""
    action = f"{BASE}/providers/{_h(provider['id'])}/check-soon"
    disabled = "" if state["enabled"] else " disabled"
    title = _h(state["reason"]) if state["reason"] else "ask the Mini to crawl this next"
    button = (f'<form class="inline" method="post" action="{action}">'
              f'<button{disabled} title="{title}">Check soon</button></form>')
    request_row = state["request"]
    bits = []
    if request_row:
        status = request_row.get("status") or "pending"
        kind = ("good" if status in ("succeeded", "completed")
                else "bad" if status in ("dead", "rejected")
                else "warn" if status == "failed" else "info")
        when = _ago(request_row.get("requested_at"))
        attempts = request_row.get("attempts")
        tries = f" · attempt {attempts}" if attempts else ""
        bits.append(f'{_chip(status, kind)} '
                    f'<span class="muted">{when}{_h(tries)}</span>')
        if not compact and request_row.get("detail"):
            bits.append(f'<span class="muted">{_h(str(request_row["detail"])[:200])}</span>')
    elif not state["tracked"]:
        bits.append('<span class="muted">status not tracked yet '
                    '(<code>nyc_crawl_request</code> absent)</span>')
    if not compact and state["reason"]:
        bits.append(f'<span class="muted">{_h(state["reason"])}</span>')
    joined = "<br>".join(bits)
    return f'{button}<div class="small">{joined}</div>' if joined else button


def _request_check(provider_id: str, actor: str) -> dict:
    """Load the provider server-side, then drop one control object."""
    provider = _web._db.nyc_provider_for_check(provider_id)
    requests, tracked = _latest_check_requests([provider["id"]])
    state = _check_state(provider, requests.get(str(provider["id"])), tracked)
    if not state["enabled"]:
        return {"ok": False, "error": state["reason"], "provider": provider}
    # provider["id"] is the server-loaded UUID; nothing from the browser is sent.
    request_id = _queue_provider_crawl(provider["id"], actor)
    return {"ok": True, "request_id": request_id, "provider": provider}


def _current_tier(entity: dict) -> str:
    tier = str((entity.get("crawl_state") or {}).get("tier") or "C").upper()
    return tier if tier in ("A", "B", "C", "D") else "C"


def _apply_review(request: Request, entity_type: str, entity_id: str,
                  decision: str, tier: str, rationale: str | None,
                  expected_updated_at: str | None, redirect_to: str):
    user, blocked = _guard(request)
    if blocked:
        return blocked
    try:
        result = _web._db.nyc_review_entry(
            entity_type=entity_type,
            entity_id=entity_id,
            decision=decision,
            tier=tier,
            actor=_actor(user),
            rationale=(rationale or "").strip() or None,
            expected_updated_at=expected_updated_at or None,
        )
    except NycSchemaMissingError as exc:
        return _schema_error_page(user, exc)
    except NycStaleFormError:
        return RedirectResponse(
            redirect_to + _qs(err=(
                "This record changed after the form was loaded, so nothing was "
                "written. The current values are shown below — review again if you "
                "still want the change.")),
            status_code=303)
    except NycNotFoundError as exc:
        return RedirectResponse(f"{BASE}/{entity_type}s" + _qs(err=str(exc)),
                                status_code=303)
    except (ValueError, NycAdminError) as exc:
        return RedirectResponse(redirect_to + _qs(err=str(exc)), status_code=303)
    msg = (f"{result['name']}: {result['decision']} at tier {result['tier']} "
           f"(was {result['previous_status']}/{result['previous_tier']}). "
           f"Audit {result['audit_id'][:8]}.")
    signal = _signal_after_policy_change(entity_type, entity_id, result, _actor(user))
    if signal:
        msg += " " + signal
    return RedirectResponse(redirect_to + _qs(msg=msg), status_code=303)


def _signal_after_policy_change(entity_type: str, entity_id: str, result: dict,
                                actor: str) -> str:
    """Best-effort expedited check after an approval — the decision is committed.

    A failure here is reported in the banner and never rolls the audited tier
    decision back; the provider is already due to the Mini on its next run.
    """
    if entity_type != "provider" or result.get("decision") != "approved":
        return ""
    if result.get("tier") not in ("A", "B", "C") or not _web.KB_BUCKET:
        return ""
    try:
        requests, _tracked = _latest_check_requests([entity_id])
        pending = requests.get(str(entity_id))
        if pending and not _control.is_terminal(pending.get("status")):
            return "An expedited check was already in flight."
        request_id = _queue_provider_crawl(entity_id, actor)
    except Exception as exc:
        return f"Tier saved, but the expedited-check signal failed: {exc}"
    return f"Expedited check requested ({request_id[:8]})."


# ── Providers ────────────────────────────────────────────────────────────

@router.get("/admin/nyc/providers", response_class=HTMLResponse)
async def nyc_providers(request: Request):
    user, blocked = _guard(request)
    if blocked:
        return blocked
    p = request.query_params
    tier, status, area = p.get("tier", ""), p.get("status", ""), p.get("area", "")
    due, q = p.get("due", ""), p.get("q", "")
    try:
        rows = _web._db.nyc_providers(tier=tier or None, status=status or None,
                                      area=area or None, due=due or None, q=q or None)
        areas = _web._db.nyc_event_areas()
    except NycSchemaMissingError as exc:
        return _schema_error_page(user, exc)

    filters = _filter_bar(
        f"{BASE}/providers", q,
        [("tier", tier, [("", "any tier"), ("A", "A"), ("B", "B"), ("C", "C"), ("D", "D")]),
         ("status", status, [("", "any status"), ("candidate", "candidate"),
                             ("active", "active"), ("retired", "retired")]),
         ("area", area, [("", "any area")] + [(a, a) for a in areas]),
         ("due", due, [("", "due or not"), ("due", "due now"), ("not_due", "not due")])],
    )

    check_requests, check_tracked = _latest_check_requests([r["id"] for r in rows])

    body_rows = []
    for r in rows:
        state = r.get("crawl_state") or {}
        check_state = _check_state(r, check_requests.get(str(r["id"])), check_tracked)
        rationale = state.get("review_rationale")
        evidence = []
        if r["new_evidence"]:
            evidence.append(_chip(f"{r['new_evidence']} new since review", "warn"))
        if r["observation_count"]:
            evidence.append(f"<span class='muted small'>{r['observation_count']} "
                            f"search hit(s), last {_ago(r['last_observed_at'])}</span>")
        if r["failed_tasks"]:
            evidence.append(_chip(f"{r['failed_tasks']} failed task", "bad"))
        if r["open_tasks"]:
            evidence.append(_chip(f"{r['open_tasks']} queued task", "info"))
        result_bits = []
        if r["offering_count"]:
            result_bits.append(f"{r['offering_count']} offering(s), "
                               f"{r['upcoming_occurrences']} upcoming")
        if r["evidence_count"]:
            result_bits.append(f"{r['evidence_count']} evidence rows, "
                               f"last {_ago(r['last_evidence_at'])}")
        if not result_bits:
            result_bits.append("<span class='muted'>nothing extracted yet</span>")
        body_rows.append(f"""
        <tr>
          <td>{_tier(r['tier'])}<div class="small muted">
            {_entity_status_chip(r['status'])}{" · due" if r['is_due'] else ""}</div></td>
          <td><a href="{BASE}/providers/{_h(r['id'])}">{_h(r['name'])}</a>
            <div class="small">{_link(r['canonical_url'])}</div></td>
          <td class="small">{_urls(r['schedule_urls'], limit=2)}</td>
          <td class="small muted">{_ago(r['last_crawled_at'])}</td>
          <td class="small">{'<br>'.join(result_bits)}</td>
          <td class="small">{' '.join(evidence) or '<span class="muted">—</span>'}</td>
          <td class="small muted">{_h((rationale or '')[:140]) or '—'}</td>
          <td>{_review_form('provider', r, compact=True)}
            <div class="small" style="margin-top:4px">
              {_check_soon_control(r, check_state, compact=True)}</div></td>
        </tr>""")

    table = f"""<div class="scroll"><table>
      <tr><th>tier</th><th>provider</th><th>schedule urls</th><th>last crawl</th>
          <th>most recent result</th><th>new discovery evidence</th>
          <th>review rationale</th><th>review</th></tr>
      {''.join(body_rows) or "<tr><td colspan='8' class='muted'>No providers match.</td></tr>"}
    </table></div>"""

    body = f"""
    {_banners(request)}
    <h1>Providers</h1>
    <p class="muted small">{len(rows)} shown. Approve sets the crawl cadence the Mini
      obeys on its next run; D is never checked again.</p>
    <div class="banner note">{PENDING_MIGRATION_NOTE}</div>
    <div class="card">{filters}{table}</div>
    """
    return HTMLResponse(_shell("Providers", body, user=user, active=f"{BASE}/providers"))


@router.get("/admin/nyc/providers/{provider_id}", response_class=HTMLResponse)
async def nyc_provider_detail(request: Request, provider_id: str):
    user, blocked = _guard(request)
    if blocked:
        return blocked
    try:
        data = _web._db.nyc_provider_detail(provider_id)
    except NycSchemaMissingError as exc:
        return _schema_error_page(user, exc)
    except NycNotFoundError:
        return HTMLResponse(_shell("Not found",
                                   "<div class='card'><h1>Provider not found</h1></div>",
                                   user=user, active=f"{BASE}/providers"), status_code=404)

    p = data["provider"]
    state = p.get("crawl_state") or {}
    check_requests, check_tracked = _latest_check_requests([p["id"]])
    check_state = _check_state(p, check_requests.get(str(p["id"])), check_tracked)
    observations_in_state = state.get("discovery_observations") or []

    offering_rows = "".join(
        f"<tr><td>{_h(o['title'])}<div class='small muted'>{_h(o['teacher'] or '')}</div></td>"
        f"<td class='small'>{_h(o['venue_name'] or '')}<div class='muted'>{_h(o['area'] or '')}</div></td>"
        f"<td class='small'>{_h(o['recurrence_text'] or '—')}</td>"
        f"<td class='num'>{o['occurrence_count']}</td>"
        f"<td class='num'>{o['upcoming_count']}</td>"
        f"<td class='small'>{_link(o['registration_url'])}</td>"
        f"<td class='small muted'>{_ago(o['updated_at'])}</td></tr>"
        for o in data["offerings"]
    ) or "<tr><td colspan='7' class='muted'>No offerings extracted.</td></tr>"

    decision_rows = "".join(
        f"<tr><td>{_ts(d['decided_at'])}</td><td>{_decision_chip(d['decision'])}</td>"
        f"<td>{_tier(d['previous_tier'])} → {_tier(d['tier'])}</td>"
        f"<td class='muted'>{_h(d['previous_status'])}</td>"
        f"<td class='muted'>{_h(d['actor'])}</td>"
        f"<td class='small'>{_h(d['rationale'] or '')}</td></tr>"
        for d in data["decisions"]
    ) or "<tr><td colspan='6' class='muted'>Never reviewed.</td></tr>"

    obs_rows = "".join(
        f"<tr><td>{_ts(o['observed_at'])}</td><td class='small'>{_h(o['query'])}</td>"
        f"<td class='num'>{o['rank']}</td><td class='small'>{_link(o['url'])}</td>"
        f"<td>{_chip(o['disposition'], 'bad' if o['disposition'] == 'rejected' else 'info')}</td>"
        f"<td class='small muted'>{_h(o['reason'] or '')}</td></tr>"
        for o in data["observations"]
    )
    obs_rows += "".join(
        f"<tr><td>{_ts(o.get('observed_at'))}</td><td class='small'>{_h(o.get('query'))}</td>"
        f"<td class='num muted'>—</td><td class='small'>{_link(o.get('url'))}</td>"
        f"<td>{_chip('crawl_state', 'info')}</td>"
        f"<td class='small muted'>{_h((o.get('snippet') or '')[:110])}</td></tr>"
        for o in observations_in_state[:25]
    )
    obs_rows = obs_rows or "<tr><td colspan='6' class='muted'>No search hits recorded.</td></tr>"

    evidence_rows = "".join(
        f"<tr><td>{_ts(e['observed_at'])}</td><td>{_chip(e['extraction_method'], 'info')}</td>"
        f"<td class='small'>{_link(e['source_url'])}</td>"
        f"<td class='small'>{_h(json.dumps(e['facts'], default=str)[:160])}</td></tr>"
        for e in data["evidence"][:25]
    ) or "<tr><td colspan='4' class='muted'>No evidence rows.</td></tr>"

    task_rows = "".join(
        f"<tr><td><a href='{BASE}/tasks/{_h(t['id'])}'>{_task_status_chip(t['status'])}</a></td>"
        f"<td class='small'>{_link(t['source_url'])}</td>"
        f"<td class='num'>{t['attempts']}</td>"
        f"<td class='small muted'>{_h((t['error'] or '')[:100])}</td>"
        f"<td class='small muted'>{_ago(t['created_at'])}</td></tr>"
        for t in data["tasks"]
    ) or "<tr><td colspan='5' class='muted'>No enrichment tasks.</td></tr>"

    body = f"""
    {_banners(request)}
    <p class="small muted"><a href="{BASE}/providers">← providers</a></p>
    <h1>{_h(p['name'])}</h1>
    <div class="grid">
      <div class="card">
        <h2>Record</h2>
        <dl class="kv">
          <dt>Status</dt><dd>{_entity_status_chip(p['status'])}
            {'· enabled' if p['enabled'] else '· disabled'}</dd>
          <dt>Tier</dt><dd>{_tier(p['tier'])}
            <span class="muted">{'due now' if p['is_due'] else 'not due'}</span></dd>
          <dt>Canonical URL</dt><dd>{_link(p['canonical_url'], max_len=70)}</dd>
          <dt>Schedule URLs</dt><dd>{_urls(p['schedule_urls'], limit=8)}</dd>
          <dt>Registration email</dt><dd>{_h(p['public_registration_email'] or '—')}</dd>
          <dt>Last crawl</dt><dd>{_ts(p['last_crawled_at'])}
            <span class="muted">({_ago(p['last_crawled_at'])})</span></dd>
          <dt>Created</dt><dd>{_ts(p['created_at'])}</dd>
          <dt>Updated</dt><dd>{_ts(p['updated_at'])}</dd>
        </dl>
        <p class="small muted" style="margin-top:10px">Factual fields are read-only in
          this milestone.</p>
      </div>
      <div class="card"><h2>Review</h2>{_review_form('provider', p, compact=False)}
        <hr style="border:0;border-top:1px solid var(--line);margin:16px 0">
        <h3>Expedited check</h3>
        {_check_soon_control(p, check_state, compact=False)}
        <p class="small muted">Queues one provider-only crawl request through S3. The
          Mini polls that prefix every five minutes; nothing here touches the crawler
          directly, and a failed write never undoes an audited tier decision.</p>
      </div>
    </div>
    <div class="card"><h2>Offerings</h2><div class="scroll"><table>
      <tr><th>title</th><th>venue</th><th>recurrence</th><th class="num">dated</th>
          <th class="num">upcoming</th><th>registration</th><th>updated</th></tr>
      {offering_rows}</table></div></div>
    <div class="card"><h2>Decision history</h2><table>
      <tr><th>when</th><th>decision</th><th>tier</th><th>was</th><th>actor</th>
          <th>rationale</th></tr>{decision_rows}</table></div>
    <div class="card"><h2>Discovery evidence</h2><div class="scroll"><table>
      <tr><th>when</th><th>query</th><th class="num">rank</th><th>url</th>
          <th>disposition</th><th>note</th></tr>{obs_rows}</table></div></div>
    <div class="card"><h2>Extraction evidence</h2><div class="scroll"><table>
      <tr><th>when</th><th>method</th><th>source</th><th>facts</th></tr>
      {evidence_rows}</table></div></div>
    <div class="card"><h2>Enrichment tasks</h2><table>
      <tr><th>state</th><th>page</th><th class="num">attempts</th><th>error</th>
          <th>age</th></tr>{task_rows}</table></div>
    <div class="card"><h2>crawl_state</h2>{_json_block(state)}</div>
    """
    return HTMLResponse(_shell(p["name"], body, user=user, active=f"{BASE}/providers"))


@router.post("/admin/nyc/providers/{provider_id}/review")
async def nyc_provider_review(
    request: Request,
    provider_id: str,
    decision: str = Form(default="approved"),
    decision_override: str = Form(default=""),
    tier: str = Form(default="C"),
    rationale: str = Form(default=""),
    expected_updated_at: str = Form(default=""),
):
    if decision_override:
        decision = decision_override
    return _apply_review(request, "provider", provider_id, decision, tier,
                         rationale, expected_updated_at,
                         f"{BASE}/providers/{provider_id}")


@router.post("/admin/nyc/providers/{provider_id}/check-soon")
async def nyc_provider_check_soon(request: Request, provider_id: str):
    """Ask the Mini to crawl one approved provider before its tier is due."""
    user, blocked = _guard(request)
    if blocked:
        return blocked
    target = f"{BASE}/providers/{provider_id}"
    try:
        result = _request_check(provider_id, _actor(user))
    except NycSchemaMissingError as exc:
        return _schema_error_page(user, exc)
    except NycNotFoundError as exc:
        return RedirectResponse(f"{BASE}/providers" + _qs(err=str(exc)), status_code=303)
    except (ValueError, NycAdminError) as exc:
        return RedirectResponse(target + _qs(err=str(exc)), status_code=303)
    except Exception as exc:  # S3 refused the write; nothing else changed
        return RedirectResponse(
            target + _qs(err=f"The crawl request was not queued: {exc}"),
            status_code=303)
    if not result["ok"]:
        return RedirectResponse(target + _qs(err=result["error"]), status_code=303)
    return RedirectResponse(
        target + _qs(msg=(f"Crawl requested ({result['request_id'][:8]}). The Mini "
                          f"should pick it up within five minutes.")),
        status_code=303)


# ── Venues ───────────────────────────────────────────────────────────────

@router.get("/admin/nyc/venues", response_class=HTMLResponse)
async def nyc_venues(request: Request):
    user, blocked = _guard(request)
    if blocked:
        return blocked
    p = request.query_params
    tier, status, area = p.get("tier", ""), p.get("status", ""), p.get("area", "")
    due, q = p.get("due", ""), p.get("q", "")
    try:
        rows = _web._db.nyc_venues(tier=tier or None, status=status or None,
                                   area=area or None, due=due or None, q=q or None)
        areas = _web._db.nyc_event_areas()
    except NycSchemaMissingError as exc:
        return _schema_error_page(user, exc)

    filters = _filter_bar(
        f"{BASE}/venues", q,
        [("tier", tier, [("", "any tier"), ("A", "A"), ("B", "B"), ("C", "C"), ("D", "D")]),
         ("status", status, [("", "any status"), ("observed", "observed"),
                             ("candidate", "candidate"), ("active", "active"),
                             ("retired", "retired")]),
         ("area", area, [("", "any area")] + [(a, a) for a in areas]),
         ("due", due, [("", "due or not"), ("due", "due now"), ("not_due", "not due")])],
    )

    body_rows = "".join(f"""
        <tr>
          <td>{_tier(v['tier'])}<div class="small muted">
            {_entity_status_chip(v['status'])}{" · due" if v['is_due'] else ""}</div></td>
          <td><a href="{BASE}/venues/{_h(v['id'])}">{_h(v['name'])}</a>
            <div class="small">{_link(v['canonical_url'])}</div></td>
          <td class="small">{_h(v['address_text'] or '—')}
            <div class="muted">{_h(v['area'] or '')}</div></td>
          <td class="small">{_urls(v['schedule_urls'], limit=2)}</td>
          <td class="num">{v['offering_count']}</td>
          <td class="num">{v['provider_count']}</td>
          <td class="small muted">{_ago(v['last_crawled_at'])}</td>
          <td class="small muted">
            {_h(((v.get('crawl_state') or {}).get('review_rationale') or '')[:120]) or '—'}</td>
          <td>{_review_form('venue', v, compact=True)}</td>
        </tr>""" for v in rows)

    body = f"""
    {_banners(request)}
    <h1>Venues</h1>
    <p class="muted small">{len(rows)} shown. A venue tier controls venue-calendar
      discovery only — it does not approve the providers or occurrences found there.</p>
    <div class="banner note">{PENDING_MIGRATION_NOTE}</div>
    <div class="card">{filters}
      <div class="scroll"><table>
        <tr><th>tier</th><th>venue</th><th>address</th><th>schedule urls</th>
            <th class="num">offerings</th><th class="num">providers</th>
            <th>last crawl</th><th>review rationale</th><th>review</th></tr>
        {body_rows or "<tr><td colspan='9' class='muted'>No venues match.</td></tr>"}
      </table></div>
    </div>"""
    return HTMLResponse(_shell("Venues", body, user=user, active=f"{BASE}/venues"))


@router.get("/admin/nyc/venues/{venue_id}", response_class=HTMLResponse)
async def nyc_venue_detail(request: Request, venue_id: str):
    user, blocked = _guard(request)
    if blocked:
        return blocked
    try:
        data = _web._db.nyc_venue_detail(venue_id)
    except NycSchemaMissingError as exc:
        return _schema_error_page(user, exc)
    except NycNotFoundError:
        return HTMLResponse(_shell("Not found",
                                   "<div class='card'><h1>Venue not found</h1></div>",
                                   user=user, active=f"{BASE}/venues"), status_code=404)
    v = data["venue"]
    offering_rows = "".join(
        f"<tr><td>{_h(o['title'])}</td>"
        f"<td><a href='{BASE}/providers/{_h(o['provider_id'])}'>{_h(o['provider_name'])}</a></td>"
        f"<td class='small'>{_h(o['recurrence_text'] or '—')}</td>"
        f"<td class='num'>{o['upcoming_count']}</td>"
        f"<td class='small muted'>{_ago(o['updated_at'])}</td></tr>"
        for o in data["offerings"]
    ) or "<tr><td colspan='5' class='muted'>No offerings observed here.</td></tr>"
    provider_rows = "".join(
        f"<tr><td><a href='{BASE}/providers/{_h(pr['id'])}'>{_h(pr['name'])}</a></td>"
        f"<td>{_entity_status_chip(pr['status'])}</td>"
        f"<td class='num'>{pr['offerings']}</td></tr>"
        for pr in data["providers"]
    ) or "<tr><td colspan='3' class='muted'>None.</td></tr>"
    decision_rows = "".join(
        f"<tr><td>{_ts(d['decided_at'])}</td><td>{_decision_chip(d['decision'])}</td>"
        f"<td>{_tier(d['previous_tier'])} → {_tier(d['tier'])}</td>"
        f"<td class='muted'>{_h(d['actor'])}</td>"
        f"<td class='small'>{_h(d['rationale'] or '')}</td></tr>"
        for d in data["decisions"]
    ) or "<tr><td colspan='5' class='muted'>Never reviewed.</td></tr>"

    body = f"""
    {_banners(request)}
    <p class="small muted"><a href="{BASE}/venues">← venues</a></p>
    <h1>{_h(v['name'])}</h1>
    <div class="grid">
      <div class="card"><h2>Record</h2>
        <dl class="kv">
          <dt>Status</dt><dd>{_entity_status_chip(v['status'])}</dd>
          <dt>Tier</dt><dd>{_tier(v['tier'])}
            <span class="muted">{'due now' if v['is_due'] else 'not due'}</span></dd>
          <dt>Address</dt><dd>{_h(v['address_text'] or '—')}</dd>
          <dt>Area</dt><dd>{_h(v['area'] or '—')}</dd>
          <dt>Canonical URL</dt><dd>{_link(v['canonical_url'], max_len=70)}</dd>
          <dt>Schedule URLs</dt><dd>{_urls(v['schedule_urls'], limit=8)}</dd>
          <dt>Last crawl</dt><dd>{_ts(v['last_crawled_at'])}
            <span class="muted">({_ago(v['last_crawled_at'])})</span></dd>
          <dt>Identity key</dt><dd class="small muted">{_h(v['identity_key'])}</dd>
        </dl>
      </div>
      <div class="card"><h2>Review</h2>{_review_form('venue', v, compact=False)}
        <p class="small muted">Approving a venue enables venue-calendar discovery at
          this cadence. It does not approve any provider found here.</p></div>
    </div>
    <div class="card"><h2>Offerings observed here</h2><table>
      <tr><th>title</th><th>provider</th><th>recurrence</th><th class="num">upcoming</th>
          <th>updated</th></tr>{offering_rows}</table></div>
    <div class="card"><h2>Providers observed here</h2><table>
      <tr><th>provider</th><th>status</th><th class="num">offerings</th></tr>
      {provider_rows}</table></div>
    <div class="card"><h2>Decision history</h2><table>
      <tr><th>when</th><th>decision</th><th>tier</th><th>actor</th><th>rationale</th></tr>
      {decision_rows}</table></div>
    <div class="card"><h2>crawl_state</h2>{_json_block(v.get('crawl_state') or {})}</div>
    """
    return HTMLResponse(_shell(v["name"], body, user=user, active=f"{BASE}/venues"))


@router.post("/admin/nyc/venues/{venue_id}/review")
async def nyc_venue_review(
    request: Request,
    venue_id: str,
    decision: str = Form(default="approved"),
    decision_override: str = Form(default=""),
    tier: str = Form(default="C"),
    rationale: str = Form(default=""),
    expected_updated_at: str = Form(default=""),
):
    if decision_override:
        decision = decision_override
    return _apply_review(request, "venue", venue_id, decision, tier,
                         rationale, expected_updated_at, f"{BASE}/venues/{venue_id}")


# ── Events ───────────────────────────────────────────────────────────────

_BUCKET_LABEL = {
    "confirmed_upcoming": ("confirmed upcoming", "good"),
    "sold_out": ("sold out", "warn"),
    "cancelled": ("cancelled", "bad"),
    "unconfirmed": ("missing / unconfirmed", "bad"),
    "expired": ("expired", ""),
    "undated_recurring": ("undated recurring", "info"),
    "needs_review": ("needs human review", "warn"),
    "dated": ("dated", "good"),
}


def _bucket_chip(bucket: str) -> str:
    label, kind = _BUCKET_LABEL.get(bucket, (bucket, ""))
    return _chip(label, kind)


@router.get("/admin/nyc/events", response_class=HTMLResponse)
async def nyc_events(request: Request):
    user, blocked = _guard(request)
    if blocked:
        return blocked
    p = request.query_params
    bucket, area, q = p.get("bucket", ""), p.get("area", ""), p.get("q", "")
    provider_id = p.get("provider", "")
    try:
        occurrences = _web._db.nyc_occurrences(
            bucket=bucket or None, area=area or None, q=q or None,
            provider_id=provider_id or None)
        offerings = _web._db.nyc_offerings(
            area=area or None, q=q or None, provider_id=provider_id or None)
        areas = _web._db.nyc_event_areas()
    except NycSchemaMissingError as exc:
        return _schema_error_page(user, exc)

    undated = [o for o in offerings if o["bucket"] == "undated_recurring"]
    unresolved = [o for o in offerings if o["bucket"] == "needs_review"]

    counts = {}
    for o in occurrences:
        counts[o["bucket"]] = counts.get(o["bucket"], 0) + 1
    legend = " ".join(
        f"{_bucket_chip(b)} <span class='muted'>{n}</span>"
        for b, n in sorted(counts.items(), key=lambda kv: -kv[1])
    )

    filters = _filter_bar(
        f"{BASE}/events", q,
        [("bucket", bucket, [("", "all states")] + [
            (b, _BUCKET_LABEL[b][0]) for b in
            ("confirmed_upcoming", "sold_out", "cancelled", "unconfirmed", "expired")]),
         ("area", area, [("", "any area")] + [(a, a) for a in areas])],
        extra_hidden={"provider": provider_id},
    )

    occ_rows = "".join(f"""
      <tr>
        <td>{_bucket_chip(o['bucket'])}</td>
        <td>{_ts(o['starts_at'])}<div class="small muted">
          {_h(o['source_timezone'])}</div></td>
        <td>{_h(o['title'])}<div class="small muted">{_h(o['teacher'] or '')}</div></td>
        <td><a href="{BASE}/providers/{_h(o['provider_id'])}">{_h(o['provider_name'])}</a></td>
        <td class="small">{_h(o['venue_name'] or '')}
          <div class="muted">{_h(o['area'] or '')}</div></td>
        <td class="small">{_link(o['registration_url'])}</td>
        <td class="small">{_chip(o['extraction_method'] or 'unknown', 'info')}</td>
        <td class="small muted">{_ago(o['last_confirmed_at'])}
          {'<br>missing since ' + _ts(o['missing_since']) if o['missing_since'] else ''}</td>
        <td class="num"><a href="{BASE}/events/occurrence/{_h(o['id'])}">
          {o['evidence_count']}</a></td>
      </tr>""" for o in occurrences)

    undated_rows = "".join(f"""
      <tr>
        <td>{_bucket_chip(o['bucket'])}</td>
        <td>{_h(o['title'])}<div class="small muted">{_h(o['teacher'] or '')}</div></td>
        <td><a href="{BASE}/providers/{_h(o['provider_id'])}">{_h(o['provider_name'])}</a></td>
        <td class="small">{_h(o['recurrence_text'] or '—')}</td>
        <td class="small">{_h(o['venue_name'] or '')}
          <div class="muted">{_h(o['area'] or '')}</div></td>
        <td class="small">{_link(o['registration_url'])}</td>
        <td class="small">{_chip(o['extraction_method'] or 'unknown', 'info')}</td>
        <td class="small"><a href="{BASE}/events/offering/{_h(o['id'])}">evidence</a></td>
      </tr>""" for o in undated + unresolved)

    body = f"""
    {_banners(request)}
    <h1>Events</h1>
    <p class="muted small">Dated occurrences and the offerings behind them. An undated
      recurring offering stays visible here but is never turned into a dated
      occurrence — nothing is synthesized from text like “weekly”.</p>
    <div class="card">{filters}
      <div class="legend" style="margin-bottom:10px">{legend or 'No occurrences match.'}</div>
      <div class="scroll"><table>
        <tr><th>state</th><th>starts</th><th>title</th><th>provider</th><th>venue</th>
            <th>registration</th><th>extraction</th><th>last confirmed</th>
            <th class="num">evidence</th></tr>
        {occ_rows or "<tr><td colspan='9' class='muted'>No occurrences match.</td></tr>"}
      </table></div>
    </div>
    <div class="card">
      <h2>Undated recurring offerings and unresolved records</h2>
      <p class="small muted">{len(undated)} undated recurring · {len(unresolved)}
        with neither a date nor a stated recurrence (these need a human).</p>
      <div class="scroll"><table>
        <tr><th>state</th><th>title</th><th>provider</th><th>recurrence</th><th>venue</th>
            <th>registration</th><th>extraction</th><th></th></tr>
        {undated_rows or "<tr><td colspan='8' class='muted'>None.</td></tr>"}
      </table></div>
    </div>"""
    return HTMLResponse(_shell("Events", body, user=user, active=f"{BASE}/events"))


@router.get("/admin/nyc/events/{kind}/{entity_id}", response_class=HTMLResponse)
async def nyc_event_evidence(request: Request, kind: str, entity_id: str):
    user, blocked = _guard(request)
    if blocked:
        return blocked
    if kind not in ("offering", "occurrence"):
        return HTMLResponse(_shell("Not found",
                                   "<div class='card'><h1>Unknown record type</h1></div>",
                                   user=user, active=f"{BASE}/events"), status_code=404)
    try:
        rows = _web._db.nyc_evidence_for(
            offering_id=entity_id if kind == "offering" else None,
            occurrence_id=entity_id if kind == "occurrence" else None,
        )
    except NycSchemaMissingError as exc:
        return _schema_error_page(user, exc)

    cards = "".join(f"""
      <div class="card">
        <dl class="kv">
          <dt>Observed</dt><dd>{_ts(e['observed_at'])} ({_ago(e['observed_at'])})</dd>
          <dt>Method</dt><dd>{_chip(e['extraction_method'], 'info')}</dd>
          <dt>Source</dt><dd>{_link(e['source_url'], max_len=90)}</dd>
          <dt>Content hash</dt><dd class="small muted">{_h(e['content_hash'])}</dd>
          <dt>Provider</dt><dd><a href="{BASE}/providers/{_h(e['provider_id'])}">
            {_h(e['provider_id'])}</a></dd>
        </dl>
        {_json_block(e['facts'])}
      </div>""" for e in rows)

    body = f"""
    <p class="small muted"><a href="{BASE}/events">← events</a></p>
    <h1>Evidence for {_h(kind)}</h1>
    <p class="muted small">{_h(entity_id)} · {len(rows)} evidence row(s), newest first.</p>
    {cards or "<div class='card muted'>No evidence recorded for this record.</div>"}
    """
    return HTMLResponse(_shell("Evidence", body, user=user, active=f"{BASE}/events"))


# ── Discovery ────────────────────────────────────────────────────────────

@router.get("/admin/nyc/discovery", response_class=HTMLResponse)
async def nyc_discovery(request: Request):
    user, blocked = _guard(request)
    if blocked:
        return blocked
    try:
        runs = _web._db.nyc_discovery_runs()
        coverage = _web._db.nyc_discovery_coverage()
    except NycSchemaMissingError as exc:
        return _schema_error_page(user, exc)

    run_rows = "".join(f"""
      <tr>
        <td><a href="{BASE}/discovery/{_h(r['id'])}">{_ts(r['started_at'])}</a>
          <div class="small muted">{_ago(r['started_at'])}</div></td>
        <td>{_run_status_chip(r['status'])}</td>
        <td class="num">{r['duration_seconds']:.1f}s</td>
        <td class="num">{r['queries_completed']}/{r['queries_planned']}</td>
        <td class="num">{r['results_seen']}</td>
        <td class="num">{r['candidates_added']}</td>
        <td class="num">{r['candidates_updated']}</td>
        <td class="num">{r['results_rejected']}</td>
        <td class="num">{r['observation_count']}</td>
      </tr>""" for r in runs)

    query_rows = "".join(f"""
      <tr>
        <td class="small">{_h(c['query'])}</td>
        <td class="num">{c['results']}</td>
        <td class="num">{c['distinct_urls']}</td>
        <td class="num">{c['providers']}</td>
        <td class="num">{c['rejected']}</td>
        <td class="num">{c['unlinked']}</td>
        <td class="small muted">{_ago(c['last_seen'])}</td>
      </tr>""" for c in coverage["by_query"])

    area_rows = "".join(
        f"<tr><td>{_h(a['area'])}</td><td class='num'>{a['offerings']}</td>"
        f"<td class='num'>{a['providers']}</td></tr>"
        for a in coverage["by_area"]
    ) or "<tr><td colspan='3' class='muted'>No offerings yet.</td></tr>"

    host_rows = "".join(
        f"<tr><td class='small'>{_h(h['host'])}</td><td class='num'>{h['providers']}</td>"
        f"<td class='num'>{h['active']}</td></tr>"
        for h in coverage["by_host"]
    )

    body = f"""
    {_banners(request)}
    <h1>Discovery</h1>
    <p class="muted small">Which searches find new sources, which query families are
      redundant or noisy, which areas are under-covered, and whether each run finished.</p>
    <div class="card"><h2>Weekly runs</h2><div class="scroll"><table>
      <tr><th>started</th><th>status</th><th class="num">duration</th>
          <th class="num">queries</th><th class="num">results</th><th class="num">added</th>
          <th class="num">updated</th><th class="num">rejected</th>
          <th class="num">observations</th></tr>
      {run_rows or "<tr><td colspan='9' class='muted'>No runs recorded.</td></tr>"}
    </table></div></div>
    <div class="card"><h2>Query productivity (recent runs)</h2><div class="scroll"><table>
      <tr><th>query</th><th class="num">results</th><th class="num">distinct urls</th>
          <th class="num">providers</th><th class="num">rejected</th>
          <th class="num">unlinked</th><th>last seen</th></tr>
      {query_rows or "<tr><td colspan='7' class='muted'>No observations recorded.</td></tr>"}
    </table>
    <p class="small muted" style="margin-top:8px">A query with many results and zero
      providers is noise. A query whose distinct URLs are all already-known providers is
      redundant; one with unlinked results is finding something the pipeline is not
      converting into candidates.</p></div></div>
    <div class="grid">
      <div class="card"><h2>Coverage by area</h2><table>
        <tr><th>area</th><th class="num">offerings</th><th class="num">providers</th></tr>
        {area_rows}</table>
        <p class="small muted" style="margin-top:8px">Boroughs absent from this table are
          not being covered by any current source.</p></div>
      <div class="card"><h2>Source hosts</h2><div class="scroll"><table>
        <tr><th>host</th><th class="num">providers</th><th class="num">active</th></tr>
        {host_rows}</table></div></div>
    </div>"""
    return HTMLResponse(_shell("Discovery", body, user=user, active=f"{BASE}/discovery"))


@router.get("/admin/nyc/discovery/{run_id}", response_class=HTMLResponse)
async def nyc_discovery_run(request: Request, run_id: str):
    user, blocked = _guard(request)
    if blocked:
        return blocked
    disposition = request.query_params.get("disposition", "")
    try:
        data = _web._db.nyc_discovery_run_detail(run_id,
                                                 disposition=disposition or None)
    except NycSchemaMissingError as exc:
        return _schema_error_page(user, exc)
    except NycNotFoundError:
        return HTMLResponse(_shell("Not found",
                                   "<div class='card'><h1>Run not found</h1></div>",
                                   user=user, active=f"{BASE}/discovery"), status_code=404)
    r = data["run"]
    duration = ((r["finished_at"] - r["started_at"]).total_seconds()
                if r.get("finished_at") else None)

    disp_links = " ".join(
        f"<a href='{BASE}/discovery/{_h(run_id)}{_qs(disposition=d['disposition'])}'>"
        f"{_chip(d['disposition'], 'bad' if d['disposition'] == 'rejected' else 'info')}"
        f" {d['n']}</a>"
        for d in data["by_disposition"]
    )

    obs_rows = "".join(f"""
      <tr>
        <td class="small">{_h(o['query'])}</td>
        <td class="num">{o['rank']}</td>
        <td class="small">{_link(o['url'], max_len=60)}
          <div class="muted">{_h((o['title'] or '')[:80])}</div></td>
        <td class="small muted">{_h((o['snippet'] or '')[:160])}</td>
        <td>{_chip(o['disposition'], 'bad' if o['disposition'] == 'rejected' else 'info')}</td>
        <td class="small">{(f"<a href='{BASE}/providers/" + _h(o['provider_id']) + "'>"
                            + _h(o['provider_name'] or o['provider_id']) + "</a>")
                          if o['provider_id'] else "<span class='muted'>unlinked</span>"}</td>
        <td class="small muted">{_h(o['reason'] or '')}</td>
      </tr>""" for o in data["observations"])

    query_rows = "".join(
        f"<tr><td class='small'>{_h(c['query'])}</td><td class='num'>{c['results']}</td>"
        f"<td class='num'>{c['known']}</td><td class='num'>{c['candidates']}</td>"
        f"<td class='num'>{c['rejected']}</td><td class='num'>{c['providers']}</td></tr>"
        for c in data["by_query"]
    )

    body = f"""
    <p class="small muted"><a href="{BASE}/discovery">← discovery</a></p>
    <h1>Discovery run {_ts(r['started_at'])}</h1>
    <div class="card"><dl class="kv">
      <dt>Status</dt><dd>{_run_status_chip(r['status'])}
        {"<span class='muted'>ended normally</span>" if r['status'] == 'succeeded'
         else "<span class='muted'>did not finish cleanly</span>"}</dd>
      <dt>Duration</dt><dd>{f"{duration:.1f}s" if duration is not None else "still running"}</dd>
      <dt>Queries</dt><dd>{r['queries_completed']} of {r['queries_planned']} completed</dd>
      <dt>Results</dt><dd>{r['results_seen']} seen · {r['candidates_added']} added ·
        {r['candidates_updated']} updated · {r['results_rejected']} rejected</dd>
      <dt>Dispositions</dt><dd>{disp_links or '—'}
        {" · <a href='" + BASE + "/discovery/" + _h(run_id) + "'>all</a>"
         if disposition else ""}</dd>
    </dl>
    <h3>Report</h3>{_json_block(r.get('report') or {})}</div>
    <div class="card"><h2>By query</h2><table>
      <tr><th>query</th><th class="num">results</th><th class="num">known providers</th>
          <th class="num">candidates</th><th class="num">rejected</th>
          <th class="num">distinct providers</th></tr>
      {query_rows or "<tr><td colspan='6' class='muted'>No observations.</td></tr>"}
    </table></div>
    <div class="card"><h2>Observations{f" — {_h(disposition)}" if disposition else ""}</h2>
      <div class="scroll"><table>
      <tr><th>query</th><th class="num">rank</th><th>url</th><th>snippet</th>
          <th>disposition</th><th>provider</th><th>reason</th></tr>
      {obs_rows or "<tr><td colspan='7' class='muted'>No observations.</td></tr>"}
    </table></div></div>"""
    return HTMLResponse(_shell("Discovery run", body, user=user,
                               active=f"{BASE}/discovery"))


# ── Tasks ────────────────────────────────────────────────────────────────

@router.get("/admin/nyc/tasks", response_class=HTMLResponse)
async def nyc_tasks(request: Request):
    user, blocked = _guard(request)
    if blocked:
        return blocked
    status = request.query_params.get("status", "")
    try:
        rows = _web._db.nyc_tasks(status=status or None)
    except NycSchemaMissingError as exc:
        return _schema_error_page(user, exc)

    filters = _filter_bar(
        f"{BASE}/tasks", None,
        [("status", status, [("", "any state"), ("queued", "queued"),
                             ("running", "running"), ("failed", "failed"),
                             ("done", "done")])],
    )

    task_rows = "".join(f"""
      <tr>
        <td>{_task_status_chip(t['status'])}</td>
        <td class="small muted">{_ago(t['created_at'])}</td>
        <td>{(f"<a href='{BASE}/providers/" + _h(t['provider_id']) + "'>"
              + _h(t['current_provider_name'] or t['provider_name'] or '?') + "</a>")
             if t['provider_id'] else _h(t['provider_name'] or '—')}</td>
        <td class="small">{_link(t['source_url'], max_len=54)}</td>
        <td class="num">{t['attempts']}</td>
        <td class="small muted">{_h((t['error'] or '')[:90]) or '—'}</td>
        <td class="small">{_h(t['extraction_window'] or '')}</td>
        <td><a href="{BASE}/tasks/{_h(t['id'])}">open</a>
          {_task_actions(t, compact=True)}</td>
      </tr>""" for t in rows)

    body = f"""
    {_banners(request)}
    <h1>Schedule-review tasks</h1>
    <p class="muted small">{len(rows)} shown. These are the pages the crawler could not
      read structurally and queued for LLM extraction on the Mini. Retrying changes queue
      state only — this site never runs Hermes inside a web request.</p>
    <div class="card">{filters}<div class="scroll"><table>
      <tr><th>state</th><th>age</th><th>provider</th><th>page</th><th class="num">attempts</th>
          <th>error</th><th>window</th><th></th></tr>
      {task_rows or "<tr><td colspan='8' class='muted'>No tasks.</td></tr>"}
    </table></div></div>"""
    return HTMLResponse(_shell("Tasks", body, user=user, active=f"{BASE}/tasks"))


def _task_actions(task: dict, *, compact: bool) -> str:
    action = f"{BASE}/tasks/{_h(task['id'])}/action"
    stamp = f'<input type="hidden" name="expected_status" value="{_h(task["status"])}">'
    running = task["status"] == "running"
    disabled = " disabled" if running else ""
    retry = (f'<button name="action" value="retry"{disabled} '
             f'title="requeue for the Mini worker">retry</button>')
    reviewed = (f'<button name="action" value="mark_reviewed"{disabled} '
                f'title="close without extraction">mark reviewed</button>')
    inner = retry + reviewed if not compact else retry
    return (f'<form class="inline" method="post" action="{action}">{stamp}{inner}</form>'
            + ("<div class='small muted'>claimed by the Mini</div>" if running and not compact
               else ""))


@router.get("/admin/nyc/tasks/{task_id}", response_class=HTMLResponse)
async def nyc_task_detail(request: Request, task_id: str):
    user, blocked = _guard(request)
    if blocked:
        return blocked
    try:
        t = _web._db.nyc_task_detail(task_id)
    except NycSchemaMissingError as exc:
        return _schema_error_page(user, exc)
    except NycNotFoundError:
        return HTMLResponse(_shell("Not found",
                                   "<div class='card'><h1>Task not found</h1></div>",
                                   user=user, active=f"{BASE}/tasks"), status_code=404)

    excerpt_note = (f"first {len(t['text_excerpt'])} of {t['text_excerpt_chars']} characters"
                    if t["text_excerpt_truncated"]
                    else f"{t['text_excerpt_chars']} characters")
    provider_id = (t.get("payload_without_excerpt") or {}).get("provider_id")

    body = f"""
    {_banners(request)}
    <p class="small muted"><a href="{BASE}/tasks">← tasks</a></p>
    <h1>Schedule review task</h1>
    <div class="grid">
      <div class="card"><h2>State</h2>
        <dl class="kv">
          <dt>Status</dt><dd>{_task_status_chip(t['status'])}</dd>
          <dt>Attempts</dt><dd>{t['attempts']}</dd>
          <dt>Priority</dt><dd>{t['priority']}</dd>
          <dt>Created</dt><dd>{_ts(t['created_at'])} ({_ago(t['created_at'])})</dd>
          <dt>Started</dt><dd>{_ts(t['started_at'])}</dd>
          <dt>Finished</dt><dd>{_ts(t['finished_at'])}</dd>
          <dt>Provider</dt><dd>{(f"<a href='{BASE}/providers/" + _h(provider_id) + "'>"
                                 + _h(t['current_provider_name'] or '(unknown)') + "</a>")
                                if provider_id else '—'}</dd>
          <dt>Source page</dt><dd>{_link((t.get('payload_without_excerpt') or {}).get('source_url'), max_len=80)}</dd>
          <dt>Error</dt><dd class="small">{_h(t['error'] or '—')}</dd>
        </dl>
        <div class="rowsep"></div>
        {_task_actions(t, compact=False)}
        <p class="small muted">Retry clears the attempt counter and returns the task to
          the queue for the Mini worker to claim.</p>
      </div>
      <div class="card"><h2>Result</h2>{_json_block(t.get('result') or {})}
        <h3>Payload</h3>{_json_block(t.get('payload_without_excerpt') or {})}</div>
    </div>
    <div class="card"><h2>Extracted text excerpt</h2>
      <p class="small muted">Admin-only. {_h(excerpt_note)}.</p>
      <pre class="code">{_h(t['text_excerpt'])}</pre></div>"""
    return HTMLResponse(_shell("Task", body, user=user, active=f"{BASE}/tasks"))


@router.post("/admin/nyc/tasks/{task_id}/action")
async def nyc_task_action(
    request: Request,
    task_id: str,
    action: str = Form(...),
    expected_status: str = Form(default=""),
):
    user, blocked = _guard(request)
    if blocked:
        return blocked
    target = f"{BASE}/tasks/{task_id}"
    try:
        result = _web._db.nyc_task_action(
            task_id=task_id, action=action, actor=_actor(user),
            expected_status=expected_status or None,
        )
    except NycSchemaMissingError as exc:
        return _schema_error_page(user, exc)
    except NycStaleFormError as exc:
        return RedirectResponse(
            target + _qs(err=("This task changed state (now "
                              f"{exc.current['status']}) after the page was loaded; "
                              "nothing was written.")),
            status_code=303)
    except NycNotFoundError as exc:
        return RedirectResponse(f"{BASE}/tasks" + _qs(err=str(exc)), status_code=303)
    except (ValueError, NycAdminError) as exc:
        return RedirectResponse(target + _qs(err=str(exc)), status_code=303)
    verb = "requeued for the Mini" if result["action"] == "retry" else "marked reviewed"
    return RedirectResponse(
        target + _qs(msg=f"Task {verb} (was {result['previous_status']})."),
        status_code=303)


# ── Decision history ─────────────────────────────────────────────────────

@router.get("/admin/nyc/decisions", response_class=HTMLResponse)
async def nyc_decisions(request: Request):
    user, blocked = _guard(request)
    if blocked:
        return blocked
    entity_type = request.query_params.get("entity_type", "")
    try:
        rows = _web._db.nyc_decisions(entity_type=entity_type or None)
    except NycSchemaMissingError as exc:
        return _schema_error_page(user, exc)

    filters = _filter_bar(
        f"{BASE}/decisions", None,
        [("entity_type", entity_type, [("", "providers and venues"),
                                       ("provider", "providers"), ("venue", "venues")])],
    )
    rows_html = "".join(f"""
      <tr>
        <td>{_ts(d['decided_at'])}<div class="small muted">{_ago(d['decided_at'])}</div></td>
        <td>{_h(d['entity_type'])}</td>
        <td><a href="{BASE}/{_h(d['entity_type'])}s/{_h(d['entity_id'])}">
          {_h(d['entity_name'] or d['entity_id'])}</a></td>
        <td>{_decision_chip(d['decision'])}</td>
        <td>{_tier(d['previous_tier'])} → {_tier(d['tier'])}</td>
        <td class="muted small">{_h(d['previous_status'] or '')}</td>
        <td class="small">{_h(d['actor'])}</td>
        <td class="small">{_h(d['rationale'] or '')}</td>
      </tr>""" for d in rows)

    body = f"""
    <h1>Decision history</h1>
    <p class="muted small">Every A/B/C/D decision written by this console or by the Atlas
      <code>nyc-review</code> CLI, newest first. Admin-only.</p>
    <div class="card">{filters}<div class="scroll"><table>
      <tr><th>when</th><th>type</th><th>entity</th><th>decision</th><th>tier</th>
          <th>was</th><th>actor</th><th>rationale</th></tr>
      {rows_html or "<tr><td colspan='8' class='muted'>No decisions recorded.</td></tr>"}
    </table></div></div>"""
    return HTMLResponse(_shell("Decisions", body, user=user, active=f"{BASE}/decisions"))


# ── Filter bar ───────────────────────────────────────────────────────────

def _filter_bar(action: str, q: str | None,
                selects: list[tuple[str, str, list[tuple[str, str]]]],
                *, extra_hidden: dict | None = None) -> str:
    parts = []
    for name, current, options in selects:
        opts = "".join(
            f'<option value="{_h(value)}"{" selected" if value == current else ""}>'
            f'{_h(label)}</option>'
            for value, label in options
        )
        parts.append(f'<select name="{_h(name)}">{opts}</select>')
    if q is not None:
        parts.append(f'<input type="search" name="q" value="{_h(q)}" '
                     f'placeholder="name or URL contains…" size="26">')
    hidden = "".join(
        f'<input type="hidden" name="{_h(k)}" value="{_h(v)}">'
        for k, v in (extra_hidden or {}).items() if v
    )
    return (f'<form class="filters" method="get" action="{_h(action)}">{hidden}'
            f'{"".join(parts)}<button>Filter</button>'
            f'<a class="small muted" href="{_h(action)}">reset</a></form>')
