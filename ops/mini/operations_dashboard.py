#!/usr/bin/env python3
"""Tailnet-only, read-only status page for the Feldenkrais Mini workers."""

from __future__ import annotations

import html
import json
import os
import re
import socket
import subprocess
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PYTHON = ROOT / ".venv-mini/bin/python"
PORT = int(os.getenv("OPERATIONS_DASHBOARD_PORT", "8787"))
HOST = os.getenv("OPERATIONS_DASHBOARD_HOST", "127.0.0.1")
LABELS = (
    "com.feldenkrais.atlas-crawler",
    "com.feldenkrais.atlas-intake",
    "com.feldenkrais.youtube-ingest",
    "com.feldenkrais.transcription",
    "com.feldenkrais.lite-enrichment",
    "com.feldenkrais.atlas-projection",
)
LOG_FILES = {
    "transcription": ROOT / "var/log/transcription-worker.log",
    "lite enrichment": ROOT / "var/log/lite-enrichment-worker.log",
    "audio intake": ROOT / "var/log/youtube-ingest-worker.log",
    "atlas intake": ROOT / "var/log/atlas-intake-worker.log",
    "atlas projection": ROOT / "var/log/atlas-projection-worker.log",
    "analysis": ROOT / "var/log/analysis-worker.log",
}
SECRET_PATTERNS = (
    re.compile(r"(?i)(aws_access_key_id|aws_secret_access_key|password|token|cookie)\s*[=:]\s*\S+"),
    re.compile(r"(?i)(postgres(?:ql)?://)[^\s]+"),
    re.compile(r"(?i)(https?://\S+)[?&](?:x-amz-[^\s]+|signature=[^\s]+)"),
)


def _command(arguments: list[str], *, cwd: Path = ROOT, timeout: int = 12) -> dict:
    try:
        result = subprocess.run(
            arguments, cwd=cwd, capture_output=True, text=True, timeout=timeout, check=False
        )
        text = result.stdout.strip()
        return {
            "ok": result.returncode == 0,
            "data": json.loads(text) if text.startswith("{") else None,
            "summary": text.splitlines()[-1][:240] if text else result.stderr.strip()[:240],
        }
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError) as error:
        return {"ok": False, "data": None, "summary": str(error)[:240]}


def _worker(label: str) -> dict:
    result = _command(["launchctl", "print", f"gui/{os.getuid()}/{label}"], timeout=3)
    text = result["summary"] if not result["ok"] else ""
    if result["ok"]:
        raw = subprocess.run(
            ["launchctl", "print", f"gui/{os.getuid()}/{label}"],
            capture_output=True, text=True, timeout=3, check=False,
        ).stdout
        state = "running" if "state = running" in raw else "waiting"
        return {"label": label.removeprefix("com.feldenkrais."), "state": state}
    return {"label": label.removeprefix("com.feldenkrais."), "state": "unavailable", "detail": text}


def status() -> dict:
    transcription = _command(
        [str(PYTHON), "-m", "ingest.local_transcribe", "status"], cwd=ROOT / "src/kb"
    )
    enrichment = _command(
        [str(PYTHON), "-m", "ingest.lite_enrich", "status", "--limit", "5000"],
        cwd=ROOT / "src/kb", timeout=30,
    )
    youtube = _command([str(PYTHON), "-m", "ingest.youtube", "status"], cwd=ROOT / "src/kb")
    atlas_health = _command(
        [
            "/bin/zsh", "-lc",
            "cd /Users/hermes/src/feldenkrais-index && set -a && source .env && set +a && .venv/bin/feldenkrais-index health",
        ],
        timeout=20,
    )
    disk = os.statvfs(ROOT)
    return {
        "reported_at": time.strftime("%Y-%m-%d %H:%M:%S %Z"),
        "host": socket.gethostname(),
        "workers": [_worker(label) for label in LABELS],
        "transcription": transcription,
        "enrichment": enrichment,
        "youtube": youtube,
        "disk_free_gb": round(disk.f_bavail * disk.f_frsize / 1024**3, 1),
        "logs": _logs(),
        "activity_24h": _activity_24h(atlas_health.get("data")),
        "inventory": _inventory(
            atlas_health.get("data"), transcription.get("data"), youtube.get("data")
        ),
    }


def _activity_24h(atlas_health: dict | None) -> dict:
    counts = {"transcribed": None, "audio_staged": None}
    try:
        import psycopg2

        connection = psycopg2.connect(os.environ["DATABASE_URL"], connect_timeout=5)
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT count(*) FROM transcription_job "
                    "WHERE status = 'completed' AND updated_at >= NOW() - INTERVAL '24 hours'"
                )
                counts["transcribed"] = cursor.fetchone()[0]
                cursor.execute(
                    "SELECT count(*) FROM youtube_queue "
                    "WHERE status = 'completed' AND updated_at >= NOW() - INTERVAL '24 hours'"
                )
                counts["audio_staged"] = cursor.fetchone()[0]
        finally:
            connection.close()
    except (ImportError, KeyError, OSError, Exception) as error:
        # The dashboard remains available when the operational database is not.
        counts["database_error"] = _sanitize(str(error))

    latest = (atlas_health or {}).get("latest_run") or {}
    stats = latest.get("stats") or {}
    finished = latest.get("finished_at")
    in_window = False
    if finished:
        try:
            from datetime import datetime, timedelta, timezone

            timestamp = datetime.fromisoformat(finished)
            if timestamp.tzinfo is None:
                timestamp = timestamp.replace(tzinfo=timezone.utc)
            in_window = timestamp >= datetime.now(timezone.utc) - timedelta(hours=24)
        except ValueError:
            pass
    counts["crawler"] = {
        "in_window": in_window,
        "finished_at": finished,
        "domains_checked": len(stats.get("fetches_by_domain") or {}),
        "fetch_attempts": stats.get("fetch_request_attempts"),
        "discovered": stats.get("discovered"),
        "accepted": stats.get("accepted"),
        "youtube_items": stats.get("yt_dlp_items_enumerated"),
        "sources_completed": stats.get("seeds_completed"),
    }
    return counts


def _inventory(
    atlas_health: dict | None, transcription: dict | None, youtube: dict | None
) -> dict:
    quality = (atlas_health or {}).get("catalog_quality") or {}
    platforms = quality.get("by_platform") or {}
    return {
        "transcribed": ((transcription or {}).get("jobs") or {}).get("completed"),
        "audio_staged": (youtube or {}).get("completed"),
        "catalog_resources": (atlas_health or {}).get("resources"),
        "catalog_sources": (atlas_health or {}).get("sources"),
        "free_lessons": quality.get("free_lessons"),
        "youtube_lessons": (platforms.get("youtube") or {}).get("lessons"),
        "website_lessons": (platforms.get("website") or {}).get("lessons"),
        "soundcloud_lessons": (platforms.get("soundcloud") or {}).get("lessons"),
        "podcast_lessons": (platforms.get("podcast") or {}).get("lessons"),
    }


def _sanitize(message: str) -> str:
    clean = message.replace("\x1b", "")
    for pattern in SECRET_PATTERNS:
        clean = pattern.sub("[redacted]", clean)
    return clean[:500]


def _logs() -> dict:
    activity: list[dict] = []
    errors: list[dict] = []
    for worker, path in LOG_FILES.items():
        if not path.is_file():
            continue
        # Only the bounded tail is read. The paths are fixed above and never
        # come from the request.
        with path.open("rb") as stream:
            stream.seek(max(0, path.stat().st_size - 64_000))
            lines = stream.read().decode("utf-8", "replace").splitlines()[-160:]
        for line in lines:
            line = _sanitize(line.strip())
            if not line or line.startswith(("Traceback", "File \"", "^")):
                continue
            entry = {"worker": worker, "message": line}
            if re.search(r"(?i)error|exception|failed|deadlock|timeout|403|denied", line):
                errors.append(entry)
            elif re.search(r"(?i)transcrib|completed|staging|processing|published|uploaded", line):
                activity.append(entry)
    return {"activity": activity[-30:][::-1], "errors": errors[-20:][::-1]}


def render(payload: dict) -> bytes:
    trans = payload["transcription"].get("data") or {}
    enrichment = payload["enrichment"].get("data") or {}
    jobs = trans.get("jobs") or {}
    youtube = payload["youtube"].get("data") or {}
    worker_cards = "".join(
        f'<article><span class="dot {html.escape(item["state"])}"></span>'
        f'<strong>{html.escape(item["label"].replace("-", " ").title())}</strong>'
        f'<small>{html.escape(item["state"])}</small></article>'
        for item in payload["workers"]
    )
    def log_rows(entries: list[dict], empty: str) -> str:
        if not entries:
            return f'<li class="empty">{html.escape(empty)}</li>'
        return "".join(
            f'<li><span>{html.escape(item["worker"])}</span><code>{html.escape(item["message"])}</code></li>'
            for item in entries
        )

    activity_rows = log_rows(payload["logs"]["activity"], "No recent activity lines found.")
    error_rows = log_rows(payload["logs"]["errors"], "No recent errors found.")
    day = payload["activity_24h"]
    crawl = day["crawler"]
    inventory = payload["inventory"]
    metric = lambda value: "—" if value is None else str(value)
    window_note = "latest crawler run within 24h" if crawl["in_window"] else "no crawler run completed within 24h"
    body = f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex,nofollow">
<title>Feldenkrais Mini operations</title><style>
:root{{--ink:#17322d;--muted:#60706b;--paper:#f4f0e5;--card:#fffdf7;--line:#d7d4c8;--moss:#426d5b;--clay:#c56f45;--sun:#edbd62}}*{{box-sizing:border-box}}body{{margin:0;background:var(--paper);color:var(--ink);font-family:Georgia,serif}}main{{width:min(1050px,90vw);margin:auto;padding:4rem 0}}h1{{font-size:clamp(2.8rem,7vw,5.5rem);font-weight:500;line-height:.95;margin:.2rem 0 1rem;max-width:12ch}}h2{{font-size:2rem;font-weight:500}}.eyebrow,.label{{color:var(--clay);font:750 .7rem system-ui;letter-spacing:.12em;text-transform:uppercase}}.lede{{color:var(--muted);font-size:1.15rem}}.grid{{display:grid;grid-template-columns:repeat(3,1fr);gap:1rem;margin:3rem 0}}.card,.workers article{{padding:1.3rem;border:1px solid var(--line);border-radius:.4rem 1.3rem .4rem .4rem;background:var(--card)}}.card strong{{display:block;font-size:2.5rem;font-weight:500;margin:.6rem 0}}.card small,.workers small,footer{{color:var(--muted);font: .78rem system-ui}}.workers{{display:grid;grid-template-columns:repeat(5,1fr);gap:.7rem}}.workers article{{display:grid;grid-template-columns:auto 1fr;gap:.4rem .6rem;align-items:center}}.workers small{{grid-column:2}}.dot{{width:.65rem;height:.65rem;border-radius:50%;background:#aaa}}.dot.running{{background:var(--moss);box-shadow:0 0 0 4px #426d5b22}}.dot.waiting{{background:var(--sun)}}.day{{margin-top:3.5rem}}.day-grid{{display:grid;grid-template-columns:repeat(4,1fr);border-top:1px solid var(--line);border-left:1px solid var(--line)}}.day-grid article{{display:grid;gap:.25rem;padding:1rem;border-right:1px solid var(--line);border-bottom:1px solid var(--line);background:var(--card)}}.day-grid strong{{font-size:1.8rem;font-weight:500}}.day-grid span,.day>small{{color:var(--muted);font:.75rem/1.3 system-ui}}.day>small{{display:block;margin-top:.7rem}}.logs{{display:grid;grid-template-columns:1fr 1fr;gap:1rem;margin-top:3rem}}.log-panel{{min-width:0}}.log-panel ol{{max-height:380px;overflow:auto;margin:0;padding:0;border-top:1px solid var(--line);list-style:none}}.log-panel li{{display:grid;grid-template-columns:7rem 1fr;gap:.8rem;padding:.75rem .2rem;border-bottom:1px solid var(--line)}}.log-panel li span{{color:var(--clay);font:700 .68rem system-ui;text-transform:uppercase}}.log-panel code{{min-width:0;overflow-wrap:anywhere;color:var(--ink);font:.74rem/1.4 ui-monospace,monospace}}.log-panel .empty{{display:block;color:var(--muted)}}footer{{margin-top:3rem;border-top:1px solid var(--line);padding-top:1rem}}@media(max-width:700px){{.grid,.workers,.logs,.day-grid{{grid-template-columns:1fr 1fr}}}}@media(max-width:600px){{.logs{{grid-template-columns:1fr}}}}@media(max-width:440px){{.grid,.workers,.day-grid{{grid-template-columns:1fr}}}}
</style></head><body><main><p class="eyebrow">Private · tailnet only</p><h1>Work on the Mini</h1><p class="lede">Live read-only status from {html.escape(payload['host'])}. Reload the page for current status.</p>
<section class="grid"><article class="card"><p class="label">Transcription</p><strong>{jobs.get('processing', 0)}</strong><p>processing</p><small>{trans.get('unclaimed', '?')} waiting · {jobs.get('completed', '?')} completed</small></article>
<article class="card"><p class="label">Lite enrichment</p><strong>{enrichment.get('complete', '?')}</strong><p>lessons complete</p><small>{enrichment.get('partial', '?')} partial · {enrichment.get('missing', '?')} waiting</small></article>
<article class="card"><p class="label">Audio intake</p><strong>{youtube.get('local_pending', '?')}</strong><p>URLs waiting</p><small>{youtube.get('completed', '?')} completed</small></article>
<article class="card"><p class="label">Storage</p><strong>{payload['disk_free_gb']} GB</strong><p>free locally</p><small>Media archive remains in S3</small></article></section>
<p class="eyebrow">Workers</p><section class="workers">{worker_cards}</section>
<section class="day"><p class="eyebrow">Last 24 hours</p><h2>Work completed</h2><div class="day-grid">
<article><strong>{metric(day['transcribed'])}</strong><span>lessons transcribed</span></article>
<article><strong>{metric(day['audio_staged'])}</strong><span>audio files staged</span></article>
<article><strong>{metric(crawl['domains_checked'])}</strong><span>web domains checked</span></article>
<article><strong>{metric(crawl['sources_completed'])}</strong><span>sources completed</span></article>
<article><strong>{metric(crawl['discovered'])}</strong><span>candidates discovered</span></article>
<article><strong>{metric(crawl['accepted'])}</strong><span>resources accepted</span></article>
<article><strong>{metric(crawl['fetch_attempts'])}</strong><span>guarded fetch attempts</span></article>
<article><strong>{metric(crawl['youtube_items'])}</strong><span>YouTube items enumerated</span></article></div><small>{html.escape(window_note)}</small></section>
<section class="day totals"><p class="eyebrow">Current inventory</p><h2>What exists now</h2><div class="day-grid">
<article><strong>{metric(inventory['catalog_resources'])}</strong><span>catalog resources</span></article>
<article><strong>{metric(inventory['free_lessons'])}</strong><span>free lessons</span></article>
<article><strong>{metric(inventory['catalog_sources'])}</strong><span>catalog sources</span></article>
<article><strong>{metric(inventory['transcribed'])}</strong><span>completed transcriptions</span></article>
<article><strong>{metric(inventory['audio_staged'])}</strong><span>audio files staged</span></article>
<article><strong>{metric(inventory['youtube_lessons'])}</strong><span>YouTube lessons</span></article>
<article><strong>{metric(inventory['website_lessons'])}</strong><span>website lessons</span></article>
<article><strong>{metric(inventory['soundcloud_lessons'])}</strong><span>SoundCloud lessons</span></article>
<article><strong>{metric(inventory['podcast_lessons'])}</strong><span>podcast lessons</span></article></div>
<small>Current distinct records; crawler revisits are not counted again.</small></section>
<section class="logs"><article class="log-panel"><p class="eyebrow">Recent activity</p><h2>Work log</h2><ol>{activity_rows}</ol></article>
<article class="log-panel"><p class="eyebrow">Recent errors</p><h2>Needs attention</h2><ol>{error_rows}</ol></article></section>
<footer>Reported {html.escape(payload['reported_at'])}. This page has no controls and does not start analysis or crawling.</footer></main></body></html>"""
    return body.encode()


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path not in {"/", "/index.html"}:
            self.send_error(404)
            return
        content = render(status())
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.end_headers()
        self.wfile.write(content)

    def log_message(self, format: str, *args: object) -> None:
        print(f"operations-dashboard: {format % args}")


if __name__ == "__main__":
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
