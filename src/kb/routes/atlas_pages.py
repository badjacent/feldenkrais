"""Atlas lesson viewer: transcript + axes for Atlas/public-source lessons.

Separate entry point (/atlas) from the analyzer UI — deliberately NOT merged
with the /lesson/{id} pages. Guarded by the same authentication; every
authenticated user gets full access (no per-source access tiers here).

Player: audio with segment-synced transcript highlighting (word-level bold
when word timestamps exist), modeled on the analyzer player but standalone.
"""

import hashlib
import html as html_mod
import json

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse

import web as _web
from atlas_projection import PUBLIC_SOURCES

router = APIRouter()

_AXES_FULL_SUFFIX = ":movement_axes:axes-p2"
_AXES_LITE_SUFFIX = ":movement_axes_lite:axes-lite-p1"
_INDEX_CACHE_KEY = "atlas_view:index:v1"
_INDEX_TTL_SECONDS = 600

_AXIS_ORDER = ["flexion_extension", "rotation", "side_bending", "cross_lateral", "vigor"]
_AXIS_SHORT = {"flexion_extension": "flex/ext", "rotation": "rotation",
               "side_bending": "side-bend", "cross_lateral": "cross-lat",
               "vigor": "vigor"}


def _require_user(request: Request):
    """Same authentication as the rest of the site; no source tiers."""
    user = _web._auth_mod.get_current_user(request) if _web._auth_mod else None
    return user


def _cache_key(text: str, suffix: str) -> str:
    return hashlib.sha256((text.strip() + suffix).encode()).hexdigest()[:16]


def _axes_for_text(text: str, cache_rows: dict) -> tuple[dict | None, str | None]:
    """Coalesce full-analysis axes over lite. Returns (axes payload, source)."""
    full = cache_rows.get(_cache_key(text, _AXES_FULL_SUFFIX))
    if full and full.get("axes"):
        return full["axes"], "full"
    lite = cache_rows.get(_cache_key(text, _AXES_LITE_SUFFIX))
    if lite and lite.get("axes"):
        return lite["axes"], "lite"
    return None, None


def _build_index() -> list[dict]:
    """All transcribed public-source docs with axes (full preferred, lite fallback)."""
    with _web._db.get_cursor(commit=False) as cur:
        cur.execute("""
            SELECT d.id, d.title, ds.name AS source_name,
                   a.data->>'text' AS text,
                   (a.data->'segments'->-1->>'end')::float AS end_s
            FROM document d
            JOIN document_source ds ON ds.id = d.source_id
            JOIN analysis a ON a.document_id = d.id AND a.type_id = 1
            WHERE ds.name = ANY(%s)
            ORDER BY ds.name, d.title
        """, (PUBLIC_SOURCES,))
        rows = [dict(r) for r in cur.fetchall()]

    keys = []
    for r in rows:
        if r["text"]:
            keys.append(_cache_key(r["text"], _AXES_FULL_SUFFIX))
            keys.append(_cache_key(r["text"], _AXES_LITE_SUFFIX))
    cache_rows = {}
    if keys:
        with _web._db.get_cursor(commit=False) as cur:
            cur.execute("SELECT key, data FROM cache WHERE key = ANY(%s)", (keys,))
            cache_rows = {r["key"]: r["data"] for r in cur.fetchall()}

    out = []
    for r in rows:
        if not r["text"]:
            continue
        axes, axes_source = _axes_for_text(r["text"], cache_rows)
        out.append({
            "document_id": str(r["id"]),
            "title": r["title"] or "(untitled)",
            "source_name": r["source_name"],
            "duration_minutes": round(r["end_s"] / 60) if r.get("end_s") else None,
            "axes": {ax: v.get("value") for ax, v in (axes or {}).items()},
            "axes_source": axes_source,
        })
    return out


def _get_index() -> list[dict]:
    """Index with a short TTL cache (per-request rebuild is too heavy)."""
    from datetime import datetime, timezone
    cached = _web._db.get_cache(_INDEX_CACHE_KEY)
    if cached:
        try:
            age = (datetime.now(timezone.utc)
                   - datetime.fromisoformat(cached["built_at"])).total_seconds()
            if age < _INDEX_TTL_SECONDS:
                return cached["lessons"]
        except Exception:
            pass
    lessons = _build_index()
    _web._db.set_cache(_INDEX_CACHE_KEY, {
        "built_at": datetime.now(timezone.utc).isoformat(),
        "lessons": lessons,
    })
    return lessons


# ── API ──────────────────────────────────────────────────────────────────

@router.get("/api/atlas-view/lessons")
async def api_atlas_lessons(request: Request):
    if not _require_user(request):
        return JSONResponse({"error": "Sign in required"}, status_code=401)
    if not _web._db:
        return JSONResponse({"error": "Database not configured"}, status_code=500)
    return JSONResponse({"lessons": _get_index()})


@router.get("/api/atlas-view/lesson/{document_id}")
async def api_atlas_lesson(request: Request, document_id: str):
    """Transcript segments + axes (with evidence) + audio URL for the player."""
    if not _require_user(request):
        return JSONResponse({"error": "Sign in required"}, status_code=401)
    if not _web._db:
        return JSONResponse({"error": "Database not configured"}, status_code=500)
    doc = _web._db.get_document(document_id)
    if not doc:
        return JSONResponse({"error": "Not found"}, status_code=404)
    text = _web._db.get_document_transcription(document_id)
    if not text:
        return JSONResponse({"error": "No transcript"}, status_code=404)

    # Axes: full analysis preferred, lite fallback (provenance reported).
    keys = [_cache_key(text, _AXES_FULL_SUFFIX), _cache_key(text, _AXES_LITE_SUFFIX)]
    with _web._db.get_cursor(commit=False) as cur:
        cur.execute("SELECT key, data FROM cache WHERE key = ANY(%s)", (keys,))
        cache_rows = {r["key"]: r["data"] for r in cur.fetchall()}
    axes, axes_source = _axes_for_text(text, cache_rows)

    segments = _web._db.get_document_whisper_segments(document_id) or []
    words = _web._db.get_document_whisper_words(document_id)

    # Audio: presigned S3 first, scraper audio_url next, direct-media source_url last.
    audio_url = None
    s3_key = doc.get("s3_key") or ""
    ext = s3_key.rsplit(".", 1)[-1].lower() if "." in s3_key else ""
    if ext in ("mp3", "wav", "m4a", "aac", "ogg", "flac", "opus", "mp4", "webm") and _web.KB_BUCKET:
        try:
            from storage import Storage
            audio_url = Storage(_web.KB_BUCKET).generate_presigned_url(s3_key, expiry_seconds=43200)
        except Exception:
            pass
    if not audio_url:
        meta = doc.get("metadata") or {}
        audio_url = (meta.get("scraper") or {}).get("audio_url")
    if not audio_url:
        src = doc.get("source_url") or ""
        if src.rsplit(".", 1)[-1].lower() in ("mp3", "m4a", "wav", "ogg", "opus"):
            audio_url = src

    return JSONResponse({
        "document_id": document_id,
        "title": doc.get("title", ""),
        "source_name": doc.get("category_name") or "",
        "source_url": doc.get("source_url"),
        "audio_url": audio_url,
        "axes": axes,
        "axes_source": axes_source,
        "segments": [{"start": s.get("start"), "end": s.get("end"), "text": s.get("text", "")}
                     for s in segments],
        "words": words,
        "text": text if not segments else None,
    })


# ── Pages ────────────────────────────────────────────────────────────────

_SIGNIN = "<h1 style='font-family:sans-serif'>Sign in to view Atlas lessons. <a href='/'>Home</a></h1>"


@router.get("/atlas", response_class=HTMLResponse)
async def atlas_index_page(request: Request):
    if not _require_user(request):
        return HTMLResponse(_SIGNIN, status_code=401)
    return HTMLResponse(_build_atlas_index_html())


@router.get("/atlas/{document_id}", response_class=HTMLResponse)
async def atlas_lesson_page(request: Request, document_id: str):
    if not _require_user(request):
        return HTMLResponse(_SIGNIN, status_code=401)
    return HTMLResponse(_build_atlas_player_html(document_id))


def _build_atlas_index_html() -> str:
    lessons = _get_index()
    by_source: dict[str, list] = {}
    for l in lessons:
        by_source.setdefault(l["source_name"], []).append(l)
    sections = []
    for source, rows in sorted(by_source.items()):
        items = []
        for l in rows:
            axes_bits = " · ".join(
                f"{_AXIS_SHORT[ax]}: {l['axes'][ax].replace('_', ' ')}"
                for ax in _AXIS_ORDER if l["axes"].get(ax))
            prov = f" <span class='prov prov-{l['axes_source']}'>{l['axes_source']}</span>" if l["axes_source"] else ""
            dur = f" · {l['duration_minutes']} min" if l["duration_minutes"] else ""
            items.append(
                f"<li><a href='/atlas/{l['document_id']}'>{html_mod.escape(l['title'])}</a>"
                f"<span class='meta'>{dur}{prov}</span>"
                f"<div class='axes'>{html_mod.escape(axes_bits)}</div></li>")
        sections.append(
            f"<h2>{html_mod.escape(source)} <span class='count'>({len(rows)})</span></h2>"
            f"<ul class='lessons'>{''.join(items)}</ul>")
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Atlas Lessons</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
 body {{ font-family: -apple-system, sans-serif; max-width: 860px; margin: 24px auto; padding: 0 16px; color: #222; }}
 h1 {{ font-size: 1.4em; }} h2 {{ font-size: 1.05em; margin: 22px 0 6px; color: #444; }}
 .count {{ color: #999; font-weight: normal; }}
 ul.lessons {{ list-style: none; padding: 0; margin: 0; }}
 ul.lessons li {{ padding: 7px 4px; border-bottom: 1px solid #eee; }}
 ul.lessons a {{ color: #0a58ca; text-decoration: none; }}
 .meta {{ color: #888; font-size: 0.85em; margin-left: 6px; }}
 .axes {{ color: #667; font-size: 0.8em; margin-top: 2px; }}
 .prov {{ padding: 1px 6px; border-radius: 8px; font-size: 0.75em; }}
 .prov-full {{ background: #d4edda; color: #155724; }}
 .prov-lite {{ background: #fff3cd; color: #856404; }}
 .note {{ color: #888; font-size: 0.85em; }}
</style></head><body>
<h1>Atlas Lessons</h1>
<div class="note">{len(lessons)} public lessons with transcripts.
Axes badges: <span class="prov prov-full">full</span> = full analysis,
<span class="prov prov-lite">lite</span> = transcript-direct.</div>
{''.join(sections)}
</body></html>"""


def _build_atlas_player_html(document_id: str) -> str:
    esc_id = html_mod.escape(document_id)
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Atlas Player</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
 body {{ font-family: -apple-system, sans-serif; max-width: 860px; margin: 24px auto; padding: 0 16px; color: #222; }}
 a {{ color: #0a58ca; }}
 h1 {{ font-size: 1.25em; margin-bottom: 4px; }}
 .src {{ color: #888; font-size: 0.85em; margin-bottom: 10px; }}
 .axes-panel {{ background: #f6f7f9; border-radius: 8px; padding: 10px 14px; margin: 12px 0; font-size: 0.9em; }}
 .axes-panel span.ax {{ display: inline-block; margin: 2px 10px 2px 0; cursor: help; }}
 .axes-panel b {{ color: #445; }}
 .prov {{ padding: 1px 6px; border-radius: 8px; font-size: 0.8em; margin-left: 6px; }}
 .prov-full {{ background: #d4edda; color: #155724; }}
 .prov-lite {{ background: #fff3cd; color: #856404; }}
 .player {{ position: sticky; top: 0; background: #fff; padding: 8px 0; border-bottom: 1px solid #eee; z-index: 5; }}
 .player audio {{ width: 100%; }}
 #transcript {{ margin-top: 14px; line-height: 1.65; }}
 #transcript .seg {{ padding: 1px 3px; border-radius: 4px; cursor: pointer; }}
 #transcript .seg.active {{ background: #fff3cd; }}
 #transcript .seg .w.cur {{ font-weight: 700; color: #7a4d00; }}
 #status {{ color: #888; font-style: italic; }}
</style></head><body>
<div><a href="/atlas">&larr; Atlas lessons</a></div>
<h1 id="title"></h1>
<div class="src" id="src"></div>
<div class="axes-panel" id="axes" style="display:none"></div>
<div class="player"><audio id="audio" controls preload="auto"></audio>
 <div id="no-audio" style="display:none;color:#888;font-size:0.85em;font-style:italic;">No playable recording found; transcript only.</div></div>
<div id="status">Loading&hellip;</div>
<div id="transcript"></div>
<script>
const DOC = {json.dumps(document_id)};
const audio = document.getElementById('audio');
let segs = [], segEls = [], words = null, wordEls = [], activeSeg = -1, activeWord = -1;

function fmtAxis(name) {{ return name.replace(/_/g, ' '); }}

async function load() {{
  const r = await fetch('/api/atlas-view/lesson/' + encodeURIComponent(DOC));
  if (!r.ok) {{ document.getElementById('status').textContent = 'Failed to load (' + r.status + ')'; return; }}
  const d = await r.json();
  document.title = d.title || 'Atlas Player';
  document.getElementById('title').textContent = d.title || '(untitled)';
  const src = document.getElementById('src');
  src.textContent = d.source_name || '';
  if (d.source_url) {{
    const a = document.createElement('a'); a.href = d.source_url; a.target = '_blank';
    a.textContent = ' source page'; src.appendChild(a);
  }}
  if (d.axes) {{
    const panel = document.getElementById('axes');
    panel.style.display = '';
    for (const [ax, v] of Object.entries(d.axes)) {{
      const s = document.createElement('span'); s.className = 'ax';
      const reason = (v.evidence && v.evidence[0] && v.evidence[0].reason) || '';
      s.title = reason;
      s.innerHTML = '<b>' + fmtAxis(ax) + ':</b> ' + fmtAxis(v.value || '');
      panel.appendChild(s);
    }}
    const p = document.createElement('span');
    p.className = 'prov prov-' + d.axes_source; p.textContent = d.axes_source;
    panel.appendChild(p);
  }}
  if (d.audio_url) {{ audio.src = d.audio_url; audioUrlIssuedAt = Date.now(); }}
  else {{ audio.style.display = 'none'; document.getElementById('no-audio').style.display = ''; }}

  const tr = document.getElementById('transcript');
  segs = d.segments || [];
  words = d.words;
  if (!segs.length) {{
    tr.textContent = d.text || '(no transcript)';
  }} else {{
    let wi = 0;
    for (let i = 0; i < segs.length; i++) {{
      const el = document.createElement('span');
      el.className = 'seg'; el.dataset.i = i;
      if (words && words.length) {{
        // attach word spans belonging to this segment (by time window)
        const end = segs[i].end ?? Infinity;
        let attached = 0;
        while (wi < words.length && (words[wi].start ?? 0) < end - 0.05) {{
          const w = document.createElement('span');
          w.className = 'w'; w.textContent = words[wi].word + ' ';
          w.dataset.wi = wi; wordEls.push(w);
          el.appendChild(w); wi++; attached++;
        }}
        if (!attached) el.textContent = segs[i].text + ' ';
      }} else {{
        el.textContent = segs[i].text + ' ';
      }}
      el.addEventListener('click', () => {{
        if (audio.src && segs[el.dataset.i].start != null) {{
          audio.currentTime = segs[el.dataset.i].start; audio.play();
        }}
      }});
      tr.appendChild(el);
      segEls.push(el);
    }}
  }}
  document.getElementById('status').style.display = 'none';
}}

function findSeg(t) {{
  let lo = 0, hi = segs.length - 1, ans = -1;
  while (lo <= hi) {{
    const mid = (lo + hi) >> 1;
    if ((segs[mid].start ?? 0) <= t) {{ ans = mid; lo = mid + 1; }} else hi = mid - 1;
  }}
  return ans;
}}

// The audio src is usually a presigned S3 link; if it expires mid-session the
// element errors and resets to 0:00. Fetch a fresh link and resume where we were.
let audioUrlIssuedAt = 0, audioLastGoodTime = 0;
audio.addEventListener('error', async () => {{
  if (!audioUrlIssuedAt || Date.now() - audioUrlIssuedAt < 30000) return;
  audioUrlIssuedAt = Date.now();
  try {{
    const r = await fetch('/api/atlas-view/lesson/' + encodeURIComponent(DOC));
    const d = r.ok ? await r.json() : null;
    if (!d || !d.audio_url) return;
    const resumeAt = audioLastGoodTime;
    audio.src = d.audio_url;
    audio.addEventListener('loadedmetadata', () => {{
      if (resumeAt > 0) audio.currentTime = resumeAt;
    }}, {{ once: true }});
    audio.load();
  }} catch (e) {{ console.warn('audio url refresh failed', e); }}
}});

audio.addEventListener('timeupdate', () => {{
  const t = audio.currentTime;
  if (t > 0) audioLastGoodTime = t;
  const i = findSeg(t);
  if (i !== activeSeg) {{
    if (activeSeg >= 0 && segEls[activeSeg]) segEls[activeSeg].classList.remove('active');
    activeSeg = i;
    if (i >= 0 && segEls[i]) {{
      segEls[i].classList.add('active');
      segEls[i].scrollIntoView({{ block: 'center', behavior: 'smooth' }});
    }}
  }}
  if (words && wordEls.length) {{
    // advance word cursor (words are time-ordered)
    let wiNew = activeWord;
    while (wiNew + 1 < words.length && (words[wiNew + 1].start ?? Infinity) <= t) wiNew++;
    while (wiNew >= 0 && (words[wiNew].start ?? 0) > t) wiNew--;
    if (wiNew !== activeWord) {{
      if (activeWord >= 0 && wordEls[activeWord]) wordEls[activeWord].classList.remove('cur');
      activeWord = wiNew;
      if (wiNew >= 0 && wordEls[wiNew]) wordEls[wiNew].classList.add('cur');
    }}
  }}
}});

load();
</script>
</body></html>"""
