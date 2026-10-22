#!/usr/bin/env python3
"""Tailnet-only, read-only operations dashboard for the Feldenkrais Mini."""

from __future__ import annotations

import html
import json
import os
import re
import socket
import sqlite3
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PYTHON = ROOT / ".venv-mini/bin/python"
PORT = int(os.getenv("OPERATIONS_DASHBOARD_PORT", "8787"))
HOST = os.getenv("OPERATIONS_DASHBOARD_HOST", "127.0.0.1")
ATLAS_DATABASE = Path(os.getenv(
    "ATLAS_DATABASE_PATH",
    "/Users/hermes/src/feldenkrais-index/var/feldenkrais-index.db",
))
LABELS = (
    "com.feldenkrais.atlas-intake",
    "com.feldenkrais.youtube-ingest", "com.feldenkrais.transcription",
    "com.feldenkrais.lite-enrichment", "com.feldenkrais.atlas-projection",
)
LOG_FILES = {
    "transcription": ROOT / "var/log/transcription-worker.log",
    "lite enrichment": ROOT / "var/log/lite-enrichment-worker.log",
    "audio intake": ROOT / "var/log/youtube-ingest-worker.log",
    "atlas intake": ROOT / "var/log/atlas-intake-worker.log",
    "atlas projection": ROOT / "var/log/atlas-projection.log",
    "atlas projection errors": ROOT / "var/log/atlas-projection.error.log",
    "crawler": Path("/Users/hermes/src/feldenkrais-index/var/log/crawler.log"),
}
SECRET_PATTERNS = (
    re.compile(r"(?i)(aws_access_key_id|aws_secret_access_key|password|token|cookie)\s*[=:]\s*\S+"),
    re.compile(r"(?i)(postgres(?:ql)?://)[^\s]+"),
    re.compile(r"(?i)(https?://\S+)[?&](?:x-amz-[^\s]+|signature=[^\s]+)"),
)
_STATUS_CACHE: tuple[float, dict] | None = None
_STATUS_LOCK = threading.Lock()


def _command(arguments: list[str], *, cwd: Path = ROOT, timeout: int = 20) -> dict:
    started = time.monotonic()
    try:
        result = subprocess.run(arguments, cwd=cwd, capture_output=True, text=True,
                                timeout=timeout, check=False)
        text = result.stdout.strip()
        return {"ok": result.returncode == 0,
                "data": json.loads(text) if text.startswith("{") else None,
                "summary": (text.splitlines()[-1] if text else result.stderr.strip())[:400],
                "elapsed_ms": round((time.monotonic() - started) * 1000)}
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError) as error:
        return {"ok": False, "data": None, "summary": str(error)[:400],
                "elapsed_ms": round((time.monotonic() - started) * 1000)}


def _worker(label: str) -> dict:
    # The jobs are system launch daemons running as hermes; a machine not yet
    # converted (ops/mini/install-launch-daemons.sh) still has them as agents.
    for domain in ("system", f"gui/{os.getuid()}"):
        result = subprocess.run(["launchctl", "print", f"{domain}/{label}"],
                                capture_output=True, text=True, timeout=3, check=False)
        if not result.returncode:
            break
    if result.returncode:
        return {"label": label.removeprefix("com.feldenkrais."), "state": "unavailable",
                "detail": _sanitize(result.stderr.strip())}
    raw = result.stdout
    state = "running" if "state = running" in raw else "waiting"
    pid = re.search(r"\bpid = (\d+)", raw)
    exit_status = re.search(r"last exit code = (-?\d+)", raw)
    return {"label": label.removeprefix("com.feldenkrais."), "state": state,
            "pid": int(pid.group(1)) if pid else None,
            "last_exit": int(exit_status.group(1)) if exit_status else None}


def _crawler_worker() -> dict:
    """Report the canonical cron-driven crawler, not the unused launchd template."""
    processes = subprocess.run(
        ["pgrep", "-f", "run_crawl_(cycle|campaign)\\.py|feldenkrais-index (run|crawl)"],
        capture_output=True, text=True, timeout=3, check=False,
    )
    if processes.returncode == 0:
        return {"label": "atlas-crawler", "state": "running",
                "pid": int(processes.stdout.splitlines()[0]), "last_exit": None}
    schedule = subprocess.run(["crontab", "-l"], capture_output=True, text=True,
                              timeout=3, check=False)
    if schedule.returncode == 0 and "ops/mini/run-crawler.sh" in schedule.stdout:
        return {"label": "atlas-crawler", "state": "scheduled", "pid": None,
                "last_exit": None, "detail": "cron every 6 hours"}
    return {"label": "atlas-crawler", "state": "unavailable", "pid": None,
            "last_exit": None, "detail": "no active process or cron schedule"}


def _atlas_command(command: str) -> list[str]:
    shell_command = (
        "cd /Users/hermes/src/feldenkrais-index && "
        "set -a && source .env && set +a && "
        f".venv/bin/feldenkrais-index {command}"
    )
    return ["/bin/zsh", "-lc", shell_command]


def status() -> dict:
    commands = {
        "transcription": ([str(PYTHON), "-m", "ingest.local_transcribe", "status"], ROOT / "src/kb", 20),
        "enrichment": ([str(PYTHON), "-m", "ingest.lite_enrich", "status", "--limit", "5000"], ROOT / "src/kb", 35),
        "youtube": ([str(PYTHON), "-m", "ingest.youtube", "status"], ROOT / "src/kb", 20),
        "atlas_health": (_atlas_command("health"), ROOT, 25),
        "atlas_operations": (_atlas_command("operations-report"), ROOT, 25),
    }
    with ThreadPoolExecutor(max_workers=len(commands) + len(LABELS)) as pool:
        futures = {name: pool.submit(_command, args, cwd=cwd, timeout=timeout)
                   for name, (args, cwd, timeout) in commands.items()}
        worker_futures = [pool.submit(_crawler_worker)] + [pool.submit(_worker, label) for label in LABELS]
        results = {name: future.result() for name, future in futures.items()}
        workers = [future.result() for future in worker_futures]
    db_activity = _database_activity()
    disk = os.statvfs(ROOT)
    health = results["atlas_health"].get("data") or {}
    operations = results["atlas_operations"].get("data") or {}
    return {
        "reported_at": datetime.now(UTC).isoformat(), "host": socket.gethostname(),
        "workers": workers, **results, "database_activity": db_activity,
        "disk_free_gb": round(disk.f_bavail * disk.f_frsize / 1024**3, 1),
        "logs": _logs(), "diagnostics": _diagnostics(workers, health, operations, results),
    }


def cached_status(*, max_age_seconds: int = 15) -> dict:
    """Avoid multiplying the expensive read-only status queries across tabs."""
    global _STATUS_CACHE
    with _STATUS_LOCK:
        if _STATUS_CACHE and time.monotonic() - _STATUS_CACHE[0] <= max_age_seconds:
            return _STATUS_CACHE[1]
        payload = status()
        _STATUS_CACHE = (time.monotonic(), payload)
        return payload


def _database_activity() -> dict:
    empty = {window: {"transcribed": None, "audio_staged": None} for window in ("1h", "24h")}
    today = datetime.now(UTC).date()
    days = [(today - timedelta(days=offset)).isoformat() for offset in range(29, -1, -1)]
    daily = {
        "crawler_accepted": {day: 0 for day in days},
        "media_staged": {day: 0 for day in days},
        "transcribed": {day: 0 for day in days},
        "gemini_completed": {day: 0 for day in days},
    }
    errors = []
    try:
        import psycopg2
        connection = psycopg2.connect(os.environ["DATABASE_URL"], connect_timeout=5)
        try:
            with connection.cursor() as cursor:
                for label, interval in (("1h", "1 hour"), ("24h", "24 hours")):
                    cursor.execute("SELECT count(*) FROM transcription_job WHERE status='completed' "
                                   "AND updated_at >= NOW() - %s::interval", (interval,))
                    empty[label]["transcribed"] = cursor.fetchone()[0]
                    cursor.execute("SELECT count(*) FROM youtube_queue WHERE status='completed' "
                                   "AND updated_at >= NOW() - %s::interval", (interval,))
                    empty[label]["audio_staged"] = cursor.fetchone()[0]
                cursor.execute(
                    """
                    SELECT day::text, stage, count(*)::int FROM (
                        SELECT (created_at AT TIME ZONE 'UTC')::date AS day,
                               'media_staged' AS stage, id::text AS item
                        FROM document
                        WHERE origin='feldenkrais_atlas' AND type_id IN (2, 3)
                          AND created_at >= NOW() - INTERVAL '30 days'
                        UNION ALL
                        SELECT day, 'transcribed', document_id::text FROM (
                            SELECT document_id,
                                   MIN(created_at AT TIME ZONE 'UTC')::date AS day
                            FROM analysis
                            WHERE type_id=1 AND created_at >= NOW() - INTERVAL '30 days'
                            GROUP BY document_id
                        ) completed_transcripts
                        UNION ALL
                        SELECT (updated_at AT TIME ZONE 'UTC')::date,
                               'gemini_completed', key
                        FROM cache
                        WHERE updated_at >= NOW() - INTERVAL '30 days'
                          AND data ? 'summary' AND data ? 'lesson_type'
                    ) events
                    GROUP BY day, stage ORDER BY day, stage
                    """
                )
                for day, stage, count in cursor.fetchall():
                    if stage in daily and day in daily[stage]:
                        daily[stage][day] = count
        finally:
            connection.close()
    except Exception as error:  # noqa: BLE001 - dashboard survives dependency failure
        empty["error"] = _sanitize(str(error))
        errors.append(f"pipeline database: {_sanitize(str(error))}")
    try:
        uri = f"file:{ATLAS_DATABASE}?mode=ro"
        with sqlite3.connect(uri, uri=True, timeout=2) as connection:
            rows = connection.execute(
                """
                SELECT substr(created_at, 1, 10) AS day, count(*)
                FROM resource
                WHERE created_at >= datetime('now', '-30 days')
                GROUP BY day ORDER BY day
                """
            ).fetchall()
        for day, count in rows:
            if day in daily["crawler_accepted"]:
                daily["crawler_accepted"][day] = count
    except Exception as error:  # noqa: BLE001 - independent read-only data source
        errors.append(f"crawler database: {_sanitize(str(error))}")
    empty["throughput_30d"] = {
        "days": days,
        "series": {name: [values[day] for day in days] for name, values in daily.items()},
        "errors": errors,
    }
    return empty


def _diagnostics(workers: list[dict], health: dict, operations: dict, commands: dict) -> list[dict]:
    notices = []
    latest = health.get("latest_run") or {}; cycle = operations.get("cycle_health") or {}
    age = cycle.get("last_successful_cycle_age_hours")
    if age is None or age > 24:
        notices.append({"severity": "error", "message": "No successful crawler cycle is visible in the last 24 hours."})
    if cycle.get("consecutive_failed_cycles", 0) >= 2:
        notices.append({"severity": "error", "message": f"Crawler has failed {cycle['consecutive_failed_cycles']} consecutive cycles."})
    elif latest.get("status") not in (None, "succeeded", "running"):
        notices.append({"severity": "warn", "message": f"Latest crawler cycle ended {latest.get('status')}."})
    for worker in workers:
        if worker["state"] == "unavailable" or (worker.get("last_exit") not in (None, 0)):
            notices.append({"severity": "warn", "message": f"{worker['label']} is unavailable or last exited {worker.get('last_exit')}."})
    for name, result in commands.items():
        if not result.get("ok"):
            notices.append({"severity": "error", "message": f"{name.replace('_', ' ')} status failed: {result.get('summary') or 'no detail'}"})
        elif result.get("elapsed_ms", 0) > 10_000:
            notices.append({"severity": "warn", "message": f"{name.replace('_', ' ')} status is slow ({result['elapsed_ms']/1000:.1f}s)."})
    return notices or [{"severity": "ok", "message": "No immediate operational warnings detected."}]


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        result = datetime.fromisoformat(value)
        return result.replace(tzinfo=UTC) if result.tzinfo is None else result.astimezone(UTC)
    except ValueError:
        return None


def _sanitize(message: str) -> str:
    clean = message.replace("\x1b", "")
    for pattern in SECRET_PATTERNS:
        clean = pattern.sub("[redacted]", clean)
    return clean[:700]


def _logs() -> dict:
    activity, errors = [], []
    for worker, path in LOG_FILES.items():
        if not path.is_file():
            continue
        with path.open("rb") as stream:
            stream.seek(max(0, path.stat().st_size - 128_000))
            lines = stream.read().decode("utf-8", "replace").splitlines()[-300:]
        for line in lines:
            line = _sanitize(line.strip())
            if not line or line.startswith(("Traceback", "File \"", "^")):
                continue
            entry = {"worker": worker, "message": line}
            if re.search(r"(?i)error|exception|failed|deadlock|timeout|403|denied", line):
                errors.append(entry)
            elif re.search(r"(?i)start|claim|transcrib|complete|stag|process|publish|upload|accept|discover", line):
                activity.append(entry)
    return {"activity": activity[-40:][::-1], "errors": errors[-30:][::-1]}


def _m(value: object) -> str:
    return "—" if value is None else html.escape(str(value))


def _cards(items: list[tuple[str, object, str]]) -> str:
    return "".join(f'<article class="metric"><span>{html.escape(label)}</span><strong>{_m(value)}</strong><small>{html.escape(note)}</small></article>'
                   for label, value, note in items)


def _log_rows(entries: list[dict], empty: str) -> str:
    return "".join(f'<li><b>{html.escape(item["worker"])}</b><code>{html.escape(item["message"])}</code></li>'
                   for item in entries) or f'<li class="empty">{html.escape(empty)}</li>'


def _throughput_charts(throughput: dict) -> str:
    days = throughput.get("days") or []
    series = throughput.get("series") or {}
    labels = {
        "crawler_accepted": ("Crawler accepted", "new catalog resources"),
        "media_staged": ("Media staged", "recordings made available"),
        "transcribed": ("Transcribed", "first completed transcripts"),
        "gemini_completed": ("Gemini completed", "lesson-card outputs"),
    }
    charts = []
    for key, (label, note) in labels.items():
        values = list(series.get(key) or [0] * len(days))
        peak = max(values, default=0)
        bars = []
        for index, (day, value) in enumerate(zip(days, values, strict=False)):
            height = 0 if peak == 0 else max(2, round(value / peak * 86))
            x = 8 + index * 12
            y = 94 - height
            tick = day[5:] if index % 5 == 0 or index == len(days) - 1 else ""
            bars.append(
                f'<rect x="{x}" y="{y}" width="8" height="{height}">'
                f'<title>{html.escape(day)}: {value}</title></rect>'
                + (f'<text x="{x + 4}" y="110">{html.escape(tick)}</text>' if tick else "")
            )
        charts.append(
            '<article class="throughput-chart">'
            f'<header><div><strong>{html.escape(label)}</strong><small>{html.escape(note)}</small></div>'
            f'<b>{sum(values):,}<small>30-day total · peak {peak}</small></b></header>'
            f'<svg viewBox="0 0 376 118" role="img" aria-label="{html.escape(label)}, daily for 30 days">'
            f'<line x1="6" y1="94" x2="372" y2="94"/>{"".join(bars)}</svg></article>'
        )
    errors = "".join(f'<li>{html.escape(error)}</li>' for error in throughput.get("errors") or [])
    warning = f'<ul class="throughput-errors">{errors}</ul>' if errors else ""
    return f'<div class="throughput-grid">{"".join(charts)}</div>{warning}'


def render(payload: dict) -> bytes:
    trans = payload["transcription"].get("data") or {}; jobs = trans.get("jobs") or {}
    enrich = payload["enrichment"].get("data") or {}; youtube = payload["youtube"].get("data") or {}
    health = payload["atlas_health"].get("data") or {}; quality = health.get("catalog_quality") or {}
    operations = payload["atlas_operations"].get("data") or {}; tasks = operations.get("tasks") or {}; frontiers = operations.get("frontiers") or {}
    recent = operations.get("recent_windows") or {}; db = payload["database_activity"]
    throughput = _throughput_charts(db.get("throughput_30d") or {})
    state_names = {"running": "working now", "waiting": "idle · scheduler healthy",
                   "scheduled": "idle · scheduled", "unavailable": "unavailable"}
    worker_cards = []
    for item in payload["workers"]:
        detail = html.escape(state_names.get(item["state"], item["state"]))
        if item.get("pid"):
            detail += f" · pid {item['pid']}"
        if item.get("last_exit") not in (None, 0):
            detail += f" · exit {item['last_exit']}"
        if item.get("detail"):
            detail += f" · {html.escape(item['detail'])}"
        worker_cards.append(
            f'<article class="service"><i class="{item["state"]}"></i><div>'
            f'<strong>{html.escape(item["label"].replace("-", " ").title())}</strong>'
            f'<small>{detail}</small></div></article>'
        )
    workers = "".join(worker_cards)
    notices = "".join(f'<li class="{n["severity"]}">{html.escape(n["message"])}</li>' for n in payload["diagnostics"])
    queue_rows = []
    for name, counts in (("Crawler tasks", tasks.get("by_status") or {}), ("Crawler task types", tasks.get("by_type") or {}), ("Transcription", jobs), ("Audio intake", youtube)):
        if name == "Crawler task types":
            counts = {key: sum(v for status, v in value.items() if status in {"queued", "partial", "failed"}) for key, value in counts.items()}
        queue_rows.append((name, counts))
    queues = "".join(f'<tr><th>{html.escape(name)}</th><td>{"".join(f"<span class=chip>{html.escape(str(k))} <b>{_m(v)}</b></span>" for k,v in counts.items()) or "—"}</td></tr>' for name, counts in queue_rows)
    source_rows = "".join(f'<tr><td>{html.escape(row.get("url") or "")}</td><td>{_m(row.get("pending_units"))}</td><td>{_m((row.get("last_batch") or {}).get("fetch_attempts"))}</td><td>{_m(((row.get("last_batch") or {}).get("outcomes") or {}).get("accepted", 0))}</td><td>{_m((row.get("scheduling_inputs") or {}).get("consecutive_zero_accepted"))}</td></tr>' for row in (frontiers.get("source_performance") or [])[:50]) or '<tr><td colspan="5">No source telemetry yet.</td></tr>'
    one = recent.get("1h") or {}; day = recent.get("24h") or {}
    hour_cards = _cards([("Crawler runs", one.get("runs"), "started"), ("Fetches", one.get("fetch_request_attempts"), "guarded requests"), ("Discovered", one.get("discovered"), "candidates"), ("Accepted", one.get("accepted"), "new resources"), ("Transcribed", (db.get("1h") or {}).get("transcribed"), "lessons"), ("Audio staged", (db.get("1h") or {}).get("audio_staged"), "files")])
    day_cards = _cards([("Crawler runs", day.get("runs"), "started"), ("Fetches", day.get("fetch_request_attempts"), "guarded requests"), ("Discovered", day.get("discovered"), "candidates"), ("Accepted", day.get("accepted"), "new resources"), ("Transcribed", (db.get("24h") or {}).get("transcribed"), "lessons"), ("Audio staged", (db.get("24h") or {}).get("audio_staged"), "files")])
    growth = health.get("database_growth") or {}; backup = health.get("backup_growth") or {}; cycle = operations.get("cycle_health") or health.get("cycle_health") or {}
    inventory = _cards([("Free lessons", quality.get("free_lessons"), "catalog"), ("Sources", health.get("sources"), "catalog"), ("Transcribed", jobs.get("completed"), "banked"), ("Lite complete", enrich.get("complete"), "Gemini"), ("Frontier", frontiers.get("pending_units"), "URLs/docs"), ("Disk free", payload["disk_free_gb"], "GB"), ("Crawler DB", round(growth.get("bytes", 0) / 1024**2, 1) if growth.get("bytes") is not None else None, "MB"), ("Latest backup", round(backup.get("latest_bytes", 0) / 1024**2, 1) if backup.get("latest_bytes") is not None else None, "MB"), ("Last crawl success", cycle.get("last_successful_cycle_age_hours"), "hours ago")])
    css = """:root{--ink:#172d29;--muted:#65736f;--paper:#f3efe4;--card:#fffdf7;--line:#d8d3c5;--green:#3c715c;--amber:#c68a36;--red:#b55243}*{box-sizing:border-box}body{margin:0;background:var(--paper);color:var(--ink);font-family:system-ui,sans-serif}main{width:min(1180px,92vw);margin:auto;padding:3rem 0 5rem}h1{font:500 clamp(2.5rem,6vw,5rem)/1 Georgia,serif;margin:.3rem 0}h2{font:500 1.8rem Georgia,serif;margin:.3rem 0 1rem}.eyebrow{color:#a85839;text-transform:uppercase;letter-spacing:.13em;font-size:.7rem;font-weight:800}.lede,small{color:var(--muted)}.title-row{display:flex;justify-content:space-between;align-items:end;gap:2rem}.title-row button{border:1px solid var(--ink);background:var(--ink);color:white;padding:.8rem 1rem;border-radius:999px;cursor:pointer}.title-row button:disabled{opacity:.6}section{margin-top:2.7rem}.services,.metrics{display:grid;grid-template-columns:repeat(3,1fr);gap:.8rem}.service,.metric{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:1rem}.service{display:flex;gap:.75rem;align-items:center}.service strong,.service small{display:block}.service i{width:.75rem;height:.75rem;border-radius:50%;background:#aaa}.service i.running{background:var(--green);box-shadow:0 0 0 4px #3c715c20}.service i.waiting,.service i.scheduled{background:var(--amber)}.metric span,.metric small{display:block}.metric span{font-size:.72rem;text-transform:uppercase;letter-spacing:.08em;color:var(--muted)}.metric strong{font:500 2.1rem Georgia,serif;display:block;margin:.3rem 0}.alerts{padding:0;list-style:none}.alerts li{padding:.8rem 1rem;background:var(--card);border-left:5px solid var(--green);margin:.4rem 0}.alerts .warn{border-color:var(--amber)}.alerts .error{border-color:var(--red)}.throughput-grid{display:grid;grid-template-columns:repeat(2,1fr);gap:.8rem}.throughput-chart{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:1rem}.throughput-chart header{display:flex;justify-content:space-between;gap:1rem}.throughput-chart header strong,.throughput-chart header small,.throughput-chart header b{display:block}.throughput-chart header b{text-align:right;font:500 1.5rem Georgia,serif}.throughput-chart header b small{font:400 .68rem system-ui,sans-serif}.throughput-chart svg{width:100%;height:auto;margin-top:.5rem;overflow:visible}.throughput-chart rect{fill:var(--green)}.throughput-chart line{stroke:var(--line);stroke-width:1}.throughput-chart text{fill:var(--muted);font:7px system-ui;text-anchor:middle}.throughput-errors{color:var(--red);font-size:.8rem}.alerts{padding:0;list-style:none}table{width:100%;border-collapse:collapse;background:var(--card);font-size:.8rem}th,td{padding:.7rem;border:1px solid var(--line);text-align:left;vertical-align:top}.chip{display:inline-block;padding:.28rem .55rem;border:1px solid var(--line);border-radius:999px;margin:.15rem}.filter{display:flex;gap:.7rem;align-items:center;margin:0 0 .7rem;color:var(--muted);font-size:.8rem}.filter input{min-width:18rem;padding:.55rem .7rem;border:1px solid var(--line);border-radius:8px;background:var(--card)}details{margin-top:1rem;background:var(--card);border:1px solid var(--line);padding:.8rem 1rem}summary{cursor:pointer;font-weight:700}pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:.72rem}.logs{display:grid;grid-template-columns:1fr 1fr;gap:1rem}.logs ol{list-style:none;padding:0;max-height:420px;overflow:auto}.logs li{display:grid;grid-template-columns:7rem 1fr;gap:.6rem;border-top:1px solid var(--line);padding:.6rem 0}.logs b{font-size:.68rem;color:#a85839;text-transform:uppercase}.logs code{font-size:.72rem;overflow-wrap:anywhere}footer{margin-top:3rem;border-top:1px solid var(--line);padding-top:1rem;color:var(--muted);font-size:.75rem}footer a{color:var(--ink)}@media(max-width:760px){.services,.metrics,.throughput-grid{grid-template-columns:repeat(2,1fr)}.logs{grid-template-columns:1fr}.title-row{align-items:start;flex-direction:column}.filter input{min-width:0;flex:1}}@media(max-width:600px){.throughput-grid{grid-template-columns:1fr}}@media(max-width:460px){.services,.metrics{grid-template-columns:1fr}}"""
    body = f'''<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex,nofollow"><title>Mini operations</title><style>{css}</style></head><body><main><p class="eyebrow">Tailnet only · read only</p><div class="title-row"><div><h1>Mini operations</h1><p class="lede">What is working, what just happened, and what is waiting.</p></div><button type="button" id="refresh">Refresh snapshot</button></div><p id="refresh-state"><small>Snapshot generated {html.escape(payload["reported_at"])}.</small></p><section><p class="eyebrow">Now</p><h2>Services</h2><div class="services">{workers}</div><ul class="alerts">{notices}</ul></section><section><p class="eyebrow">Past hour</p><h2>Recent throughput</h2><div class="metrics">{hour_cards}</div></section><section><p class="eyebrow">Past 24 hours</p><h2>Daily throughput</h2><div class="metrics">{day_cards}</div></section><section><p class="eyebrow">Past 30 days · UTC</p><h2>Pipeline throughput by day</h2>{throughput}</section><section><p class="eyebrow">Queues</p><h2>Work waiting</h2><table><tbody>{queues}</tbody></table><p><small>Oldest actionable crawler task: {_m(tasks.get("oldest_actionable_age_hours"))} hours · owner review: {_m(tasks.get("owner_review_pending"))} · worker actionable: {_m(tasks.get("worker_actionable"))} · failed/partial history: {_m(tasks.get("unresolved_attempts"))}</small></p><details><summary>Oldest actionable crawler tasks</summary><pre>{html.escape(json.dumps(tasks.get("oldest_actionable") or [], indent=2))}</pre></details></section><section><p class="eyebrow">Current state</p><h2>Inventory and capacity</h2><div class="metrics">{inventory}</div><details><summary>Largest crawler tables</summary><pre>{html.escape(json.dumps(growth.get("largest_tables") or [], indent=2))}</pre></details></section><section><p class="eyebrow">Adaptive crawler inputs</p><h2>Yield and unfinished work by source</h2><label class="filter">Filter sources <input id="source-filter" type="search" placeholder="domain or URL"></label><table id="sources"><thead><tr><th>Source</th><th>Pending</th><th>Last fetches</th><th>Accepted</th><th>Zero-yield streak</th></tr></thead><tbody>{source_rows}</tbody></table></section><section class="logs"><article><p class="eyebrow">Recent activity</p><h2>Worker log</h2><ol>{_log_rows(payload["logs"]["activity"], "No matching activity in bounded log tails.")}</ol></article><article><p class="eyebrow">Errors</p><h2>Diagnostic trail</h2><ol>{_log_rows(payload["logs"]["errors"], "No matching errors in bounded log tails.")}</ol></article></section><footer>Generated on {html.escape(payload["host"])}. Status queries run in parallel; this page has no crawler or analysis controls. <a href="/api/status">Raw status JSON</a>.</footer></main><script>const refresh=document.querySelector('#refresh');refresh.addEventListener('click',()=>{{refresh.disabled=true;refresh.textContent='Refreshing…';location.reload()}});const filter=document.querySelector('#source-filter');filter.addEventListener('input',()=>{{const q=filter.value.toLowerCase();document.querySelectorAll('#sources tbody tr').forEach(row=>row.hidden=!row.textContent.toLowerCase().includes(q))}});</script></body></html>'''
    return body.encode()


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path == "/healthz":
            content = b'{"ok":true}'
            self.send_response(200); self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(content))); self.end_headers()
            self.wfile.write(content); return
        if self.path == "/api/status":
            content = json.dumps(cached_status(), default=str).encode()
            self.send_response(200); self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(content))); self.send_header("Cache-Control", "no-store")
            self.end_headers(); self.wfile.write(content); return
        if self.path not in {"/", "/index.html"}:
            self.send_error(404); return
        content = render(cached_status())
        self.send_response(200); self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(content))); self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff"); self.send_header("X-Frame-Options", "DENY")
        self.end_headers(); self.wfile.write(content)

    def log_message(self, format: str, *args: object) -> None:
        print(f"operations-dashboard: {format % args}")


if __name__ == "__main__":
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
