"""QuietBones landing page Lambda.

Serves the static HTML page, handles email signups, and serves the audio page.
Stores emails and audio metadata in the Neon PostgreSQL database.
Audio files served from S3 via presigned URLs.
"""

import json
import os

import boto3
import psycopg2
from psycopg2.extras import RealDictCursor

S3_BUCKET = os.environ.get("QUIETBONES_BUCKET", "")

# ── Shared styles ──

SHARED_STYLES = """
  @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500&display=swap');

  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

  body {
    font-family: 'Inter', -apple-system, sans-serif;
    background: #0a0a0f;
    color: #c8c8d0;
    min-height: 100vh;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    padding: 2rem;
    position: relative;
    overflow: hidden;
  }

  body::before {
    content: '';
    position: fixed;
    top: 30%;
    left: 50%;
    transform: translate(-50%, -50%);
    width: 600px;
    height: 600px;
    background: radial-gradient(circle, rgba(120, 60, 180, 0.08) 0%, transparent 70%);
    pointer-events: none;
    z-index: 0;
  }

  body::after {
    content: '';
    position: fixed;
    bottom: 10%;
    right: 20%;
    width: 400px;
    height: 400px;
    background: radial-gradient(circle, rgba(90, 50, 160, 0.05) 0%, transparent 70%);
    pointer-events: none;
    z-index: 0;
  }

  .container {
    position: relative;
    z-index: 1;
    max-width: 520px;
    width: 100%;
  }

  h1 {
    font-weight: 300;
    font-size: 2.4rem;
    letter-spacing: 0.12em;
    color: #e8e8f0;
    margin-bottom: 0.6rem;
    text-transform: lowercase;
    text-align: center;
  }

  @media (max-width: 480px) {
    h1 { font-size: 2rem; }
  }
"""

# ── Landing page ──

HTML_LANDING = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>QuietBones</title>
<style>
""" + SHARED_STYLES + """

  .container { text-align: center; max-width: 440px; }

  .tagline {
    font-weight: 300;
    font-size: 0.95rem;
    color: #8888a0;
    margin-bottom: 3rem;
    line-height: 1.6;
  }

  .description {
    font-weight: 300;
    font-size: 0.88rem;
    color: #6a6a80;
    line-height: 1.7;
    margin-bottom: 2.5rem;
  }

  form {
    display: flex;
    gap: 0.5rem;
    margin-bottom: 1rem;
  }

  input[type="email"] {
    flex: 1;
    padding: 0.75rem 1rem;
    background: rgba(255, 255, 255, 0.04);
    border: 1px solid rgba(120, 60, 180, 0.2);
    border-radius: 8px;
    color: #e0e0e8;
    font-family: inherit;
    font-size: 0.9rem;
    outline: none;
    transition: border-color 0.3s;
  }

  input[type="email"]::placeholder { color: #55556a; }
  input[type="email"]:focus { border-color: rgba(140, 80, 200, 0.5); }

  button {
    padding: 0.75rem 1.5rem;
    background: rgba(120, 60, 180, 0.15);
    border: 1px solid rgba(120, 60, 180, 0.3);
    border-radius: 8px;
    color: #b8a0d0;
    font-family: inherit;
    font-size: 0.9rem;
    font-weight: 400;
    cursor: pointer;
    transition: all 0.3s;
    white-space: nowrap;
  }

  button:hover {
    background: rgba(120, 60, 180, 0.25);
    border-color: rgba(140, 80, 200, 0.5);
    color: #d0c0e8;
  }

  button:disabled { opacity: 0.5; cursor: not-allowed; }

  .message {
    font-size: 0.85rem;
    min-height: 1.4rem;
    color: #8070a0;
    transition: opacity 0.3s;
  }

  .message.error { color: #a06070; }
  .message.success { color: #70a080; }

  @media (max-width: 480px) {
    form { flex-direction: column; }
  }
</style>
</head>
<body>
<div class="container">
  <h1>quietbones</h1>
  <p class="tagline">small, beautiful, effective resets for people who need gentle ways to relax.</p>
  <form id="signup" onsubmit="return handleSubmit(event)">
    <input type="email" name="email" placeholder="your email" required autocomplete="email">
    <button type="submit">notify me</button>
  </form>
  <p class="message" id="msg"></p>
</div>
<script>
async function handleSubmit(e) {
  e.preventDefault();
  const form = e.target;
  const btn = form.querySelector('button');
  const msg = document.getElementById('msg');
  const email = form.email.value.trim();
  if (!email) return false;

  btn.disabled = true;
  btn.textContent = '...';
  msg.textContent = '';
  msg.className = 'message';

  try {
    const res = await fetch('/signup', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({email}),
    });
    const data = await res.json();
    if (res.ok) {
      msg.textContent = data.message || 'noted.';
      msg.className = 'message success';
      form.email.value = '';
    } else {
      msg.textContent = data.error || 'something went wrong.';
      msg.className = 'message error';
    }
  } catch {
    msg.textContent = 'could not connect.';
    msg.className = 'message error';
  }

  btn.disabled = false;
  btn.textContent = 'notify me';
  return false;
}
</script>
</body>
</html>"""

# ── Audio page ──

HTML_AUDIO = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>QuietBones — Explorations</title>
<style>
""" + SHARED_STYLES + """

  .subtitle {
    font-weight: 300;
    font-size: 0.9rem;
    color: #6a6a80;
    text-align: center;
    margin-bottom: 2.5rem;
  }

  .track-list {
    list-style: none;
    display: flex;
    flex-direction: column;
    gap: 1px;
  }

  .track {
    background: rgba(255, 255, 255, 0.02);
    border: 1px solid rgba(120, 60, 180, 0.1);
    border-radius: 10px;
    padding: 1.2rem 1.4rem;
    cursor: pointer;
    transition: all 0.3s;
    margin-bottom: 0.5rem;
  }

  .track:hover {
    background: rgba(120, 60, 180, 0.06);
    border-color: rgba(120, 60, 180, 0.25);
  }

  .track.active {
    background: rgba(120, 60, 180, 0.08);
    border-color: rgba(120, 60, 180, 0.3);
  }

  .track-title {
    font-weight: 400;
    font-size: 0.95rem;
    color: #d0d0d8;
    margin-bottom: 0.3rem;
  }

  .track-desc {
    font-weight: 300;
    font-size: 0.82rem;
    color: #7070888;
    line-height: 1.5;
  }

  .track-duration {
    font-weight: 300;
    font-size: 0.75rem;
    color: #55556a;
    margin-top: 0.3rem;
  }

  .player-wrap {
    position: sticky;
    bottom: 1rem;
    margin-top: 2rem;
    background: rgba(10, 10, 15, 0.95);
    border: 1px solid rgba(120, 60, 180, 0.2);
    border-radius: 12px;
    padding: 1rem 1.2rem;
    backdrop-filter: blur(10px);
    display: none;
  }

  .player-wrap.visible { display: block; }

  .now-playing {
    font-weight: 300;
    font-size: 0.82rem;
    color: #8888a0;
    margin-bottom: 0.6rem;
  }

  audio {
    width: 100%;
    height: 36px;
    outline: none;
  }

  audio::-webkit-media-controls-panel {
    background: rgba(255, 255, 255, 0.04);
  }

  .empty {
    text-align: center;
    color: #55556a;
    font-size: 0.9rem;
    padding: 3rem 0;
  }
</style>
</head>
<body>
<div class="container">
  <h1>quietbones</h1>
  <p class="subtitle">explorations</p>
  <ul class="track-list" id="tracks"></ul>
  <div class="player-wrap" id="player-wrap">
    <div class="now-playing" id="now-playing"></div>
    <audio id="audio" controls preload="none"></audio>
  </div>
</div>
<script>
let currentTrack = null;
const audio = document.getElementById('audio');
const playerWrap = document.getElementById('player-wrap');
const nowPlaying = document.getElementById('now-playing');

async function loadTracks() {
  const res = await fetch('/api/audio');
  const data = await res.json();
  const tracks = data.tracks || [];
  const list = document.getElementById('tracks');

  if (!tracks.length) {
    list.innerHTML = '<li class="empty">no explorations yet.</li>';
    return;
  }

  tracks.forEach(t => {
    const li = document.createElement('li');
    li.className = 'track';
    li.innerHTML = `
      <div class="track-title">${esc(t.title)}</div>
      ${t.description ? '<div class="track-desc">' + esc(t.description) + '</div>' : ''}
      ${t.duration ? '<div class="track-duration">' + fmtDur(t.duration) + '</div>' : ''}
    `;
    li.onclick = () => play(t, li);
    list.appendChild(li);
  });
}

function play(track, el) {
  document.querySelectorAll('.track.active').forEach(e => e.classList.remove('active'));
  el.classList.add('active');
  audio.src = track.audio_url;
  audio.play();
  nowPlaying.textContent = track.title;
  playerWrap.classList.add('visible');
  currentTrack = track;
}

function esc(s) {
  const d = document.createElement('div');
  d.textContent = s;
  return d.innerHTML;
}

function fmtDur(s) {
  const m = Math.floor(s / 60);
  const sec = Math.floor(s % 60);
  return m + ':' + String(sec).padStart(2, '0');
}

loadTracks();
</script>
</body>
</html>"""


# ── How it works page ──

HTML_HOW = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>QuietBones — How It Works</title>
<style>
""" + SHARED_STYLES + """

  .container { max-width: 640px; }

  h1 { margin-bottom: 2rem; }

  h2 {
    font-weight: 400;
    font-size: 1.1rem;
    color: #d0d0d8;
    margin-top: 3rem;
    margin-bottom: 1rem;
    letter-spacing: 0.03em;
  }

  p, li {
    font-weight: 300;
    font-size: 0.88rem;
    color: #9090a8;
    line-height: 1.75;
    margin-bottom: 1rem;
  }

  .lede {
    font-size: 0.95rem;
    color: #a8a8c0;
    text-align: center;
    margin-bottom: 3rem;
    line-height: 1.7;
  }

  /* Pipeline diagram */
  .pipeline {
    display: flex;
    flex-direction: column;
    gap: 0;
    margin: 2rem 0;
  }

  .stage {
    display: flex;
    align-items: flex-start;
    gap: 1.2rem;
    padding: 1.2rem 0;
  }

  .stage-marker {
    flex-shrink: 0;
    width: 36px;
    height: 36px;
    border-radius: 50%;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 0.75rem;
    font-weight: 500;
    position: relative;
  }

  .stage-marker.ai {
    background: rgba(120, 60, 180, 0.15);
    border: 1px solid rgba(120, 60, 180, 0.35);
    color: #b8a0d0;
  }

  .stage-marker.human {
    background: rgba(60, 140, 120, 0.15);
    border: 1px solid rgba(60, 140, 120, 0.35);
    color: #80c0a8;
  }

  .stage-marker.both {
    background: rgba(100, 80, 160, 0.12);
    border: 1px solid rgba(100, 80, 160, 0.3);
    color: #a098c0;
  }

  /* connector line between stages */
  .stage:not(:last-child) .stage-marker::after {
    content: '';
    position: absolute;
    top: 36px;
    left: 50%;
    transform: translateX(-50%);
    width: 1px;
    height: calc(100% + 0.5rem);
    background: rgba(120, 60, 180, 0.15);
  }

  .stage-body { flex: 1; min-width: 0; }

  .stage-title {
    font-weight: 400;
    font-size: 0.9rem;
    color: #d0d0d8;
    margin-bottom: 0.25rem;
  }

  .stage-desc {
    font-weight: 300;
    font-size: 0.82rem;
    color: #7878908;
    line-height: 1.6;
  }

  .stage-tag {
    display: inline-block;
    font-size: 0.65rem;
    font-weight: 500;
    letter-spacing: 0.06em;
    text-transform: uppercase;
    padding: 2px 6px;
    border-radius: 4px;
    margin-bottom: 0.4rem;
  }

  .stage-tag.ai { background: rgba(120, 60, 180, 0.12); color: #a890c0; }
  .stage-tag.human { background: rgba(60, 140, 120, 0.12); color: #80b8a0; }
  .stage-tag.both { background: rgba(100, 80, 160, 0.1); color: #9890b0; }

  /* before/after boxes */
  .comparison {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 1rem;
    margin: 1.5rem 0;
  }

  .comp-box {
    background: rgba(255, 255, 255, 0.02);
    border: 1px solid rgba(120, 60, 180, 0.12);
    border-radius: 8px;
    padding: 1rem;
  }

  .comp-label {
    font-size: 0.7rem;
    font-weight: 500;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    margin-bottom: 0.5rem;
  }

  .comp-label.before { color: #707088; }
  .comp-label.after { color: #a890c0; }

  .comp-text {
    font-size: 0.8rem;
    font-style: italic;
    line-height: 1.6;
    color: #9090a8;
  }

  .insight {
    background: rgba(120, 60, 180, 0.05);
    border-left: 2px solid rgba(120, 60, 180, 0.25);
    padding: 1rem 1.2rem;
    margin: 1.5rem 0;
    border-radius: 0 6px 6px 0;
  }

  .insight p {
    margin-bottom: 0;
    font-size: 0.84rem;
    color: #a8a0b8;
  }

  .legend {
    display: flex;
    gap: 1.5rem;
    justify-content: center;
    margin-bottom: 2rem;
    flex-wrap: wrap;
  }

  .legend-item {
    display: flex;
    align-items: center;
    gap: 0.4rem;
    font-size: 0.75rem;
    color: #707088;
  }

  .legend-dot {
    width: 10px;
    height: 10px;
    border-radius: 50%;
  }

  .legend-dot.ai { background: rgba(120, 60, 180, 0.35); }
  .legend-dot.human { background: rgba(60, 140, 120, 0.35); }
  .legend-dot.both { background: rgba(100, 80, 160, 0.3); }

  @media (max-width: 480px) {
    .comparison { grid-template-columns: 1fr; }
  }
</style>
</head>
<body>
<div class="container">
  <h1>quietbones</h1>

  <p class="lede">
    Each exploration begins as a recording of a movement lesson.
    AI analyzes, extracts, translates, and generates —
    but a human decides what matters.
  </p>

  <div class="legend">
    <div class="legend-item"><div class="legend-dot ai"></div> AI-driven</div>
    <div class="legend-item"><div class="legend-dot human"></div> Human-driven</div>
    <div class="legend-item"><div class="legend-dot both"></div> Collaborative</div>
  </div>

  <!-- ── Pipeline diagram ── -->

  <div class="pipeline">

    <div class="stage">
      <div class="stage-marker ai">1</div>
      <div class="stage-body">
        <div class="stage-tag ai">AI</div>
        <div class="stage-title">Transcription</div>
        <div class="stage-desc">
          A recorded movement lesson — a teacher guiding students through
          subtle physical explorations — is transcribed from audio to text.
        </div>
      </div>
    </div>

    <div class="stage">
      <div class="stage-marker ai">2</div>
      <div class="stage-body">
        <div class="stage-tag ai">AI</div>
        <div class="stage-title">Multi-pass analysis</div>
        <div class="stage-desc">
          Six analysis passes decompose the lesson into its structural elements:
          how the body is organized, what differentiations are explored,
          how force travels through the skeleton, what language the teacher uses
          and why.
        </div>
      </div>
    </div>

    <div class="stage">
      <div class="stage-marker human">3</div>
      <div class="stage-body">
        <div class="stage-tag human">Human</div>
        <div class="stage-title">Creative direction</div>
        <div class="stage-desc">
          A human reads the analysis and identifies the somatic idea worth
          exploring. Not the movements themselves — the underlying principle.
          They choose a body region, a theme, and a learning goal.
        </div>
      </div>
    </div>

    <div class="stage">
      <div class="stage-marker ai">4</div>
      <div class="stage-body">
        <div class="stage-tag ai">AI</div>
        <div class="stage-title">Somatic extraction</div>
        <div class="stage-desc">
          The analysis data is distilled into the raw ingredients for an
          exploration: which bones and surfaces are involved, what
          attentional pathways exist, where local tension might be held.
        </div>
      </div>
    </div>

    <div class="stage">
      <div class="stage-marker both">5</div>
      <div class="stage-body">
        <div class="stage-tag both">Collaborative</div>
        <div class="stage-title">Voice translation</div>
        <div class="stage-desc">
          The clinical analysis is re-expressed in the QuietBones voice —
          quiet, inviting, skeletal. "Differentiation of clavicle from
          scapula" becomes "see if you can move your attention to the
          underside of that bone."
        </div>
      </div>
    </div>

    <div class="stage">
      <div class="stage-marker both">6</div>
      <div class="stage-body">
        <div class="stage-tag both">Collaborative</div>
        <div class="stage-title">Generate → critique → revise</div>
        <div class="stage-desc">
          AI generates a draft exploration. A critic evaluates it against
          design principles and the existing body of work. Revisions are
          made — sometimes by AI, sometimes by a human ear that knows
          when a transition feels wrong or a phrase is too clinical.
        </div>
      </div>
    </div>

    <div class="stage">
      <div class="stage-marker ai">7</div>
      <div class="stage-body">
        <div class="stage-tag ai">AI</div>
        <div class="stage-title">Audio synthesis</div>
        <div class="stage-desc">
          The final text is converted to speech with carefully tuned pacing
          and programmatic silences. The voice is unhurried. The pauses are
          structural — they're where the listening happens.
        </div>
      </div>
    </div>

  </div>

  <!-- ── The translation ── -->

  <h2>The hard part: translation</h2>

  <p>
    The analysis pipeline produces precise biomechanical descriptions.
    The exploration must contain none of that language. The translation
    between these two worlds is where the product lives.
  </p>

  <div class="comparison">
    <div class="comp-box">
      <div class="comp-label before">Analysis sees</div>
      <div class="comp-text">
        "Differentiation of clavicle from scapula via attentional
        cue to anterior/posterior surfaces. Transmission route:
        sternoclavicular joint → acromioclavicular joint."
      </div>
    </div>
    <div class="comp-box">
      <div class="comp-label after">Exploration says</div>
      <div class="comp-text">
        "Find the place where that clavicle meets your sternum.
        Not with your hand — with your attention. Just find that
        junction."
      </div>
    </div>
  </div>

  <div class="comparison">
    <div class="comp-box">
      <div class="comp-label before">Analysis sees</div>
      <div class="comp-text">
        "Local inhibition via sustained attentional placement.
        Progressive narrowing of attentional field to single
        rib, producing parasympathetic downregulation."
      </div>
    </div>
    <div class="comp-box">
      <div class="comp-label after">Exploration says</div>
      <div class="comp-text">
        "Let that movement become a little smaller in your
        awareness. Not by doing anything. Just by listening
        more closely, the movement may quiet down on its own.
        Just on this one rib."
      </div>
    </div>
  </div>

  <div class="insight">
    <p>
      The analysis tells you <em>what</em> to explore.
      The voice tells you <em>how</em> to listen.
      Neither is sufficient alone.
    </p>
  </div>

  <!-- ── What AI decides vs. what humans decide ── -->

  <h2>What AI decides</h2>
  <p>
    Transcription. Structural decomposition. Biomechanical classification.
    Draft generation. Audio synthesis. These are high-volume, pattern-matching
    tasks where AI is reliable and fast.
  </p>

  <h2>What humans decide</h2>
  <p>
    Which somatic idea is worth an exploration. What the learning goal
    should be. Whether a transition feels right. Whether the voice sounds
    like it's inviting or instructing. Whether "notice the breath motion"
    should become "let the space breathe." These are taste decisions —
    they define the product.
  </p>

  <div class="insight">
    <p>
      The interesting question isn't "can AI do this?"
      It's "where does the human judgment actually live,
      and what happens when you remove it?"
    </p>
  </div>

</div>
</body>
</html>"""


# ── Database ──

def _get_conn():
    return psycopg2.connect(os.environ["DATABASE_URL"])


def _ensure_tables(conn):
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS quietbones_signup (
                id SERIAL PRIMARY KEY,
                email TEXT UNIQUE NOT NULL,
                created_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS quietbones_audio (
                id SERIAL PRIMARY KEY,
                title TEXT NOT NULL,
                description TEXT DEFAULT '',
                s3_key TEXT NOT NULL,
                duration INTEGER,
                sort_order INTEGER DEFAULT 0,
                visible BOOLEAN DEFAULT TRUE,
                created_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)
        conn.commit()


def _get_audio_list(conn):
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("""
            SELECT id, title, description, s3_key, duration
            FROM quietbones_audio
            WHERE visible = TRUE
            ORDER BY sort_order, created_at
        """)
        return cur.fetchall()


def _presign_url(s3_key, expiry=7200):
    s3 = boto3.client("s3")
    return s3.generate_presigned_url(
        "get_object",
        Params={"Bucket": S3_BUCKET, "Key": s3_key},
        ExpiresIn=expiry,
    )


# ── Handler ──

def handler(event, context):
    method = event.get("requestContext", {}).get("http", {}).get("method", "GET")
    path = event.get("rawPath", "/")

    # Landing page
    if method == "GET" and path == "/":
        return {
            "statusCode": 200,
            "headers": {"Content-Type": "text/html"},
            "body": HTML_LANDING,
        }

    # How it works
    if method == "GET" and path == "/how":
        return {
            "statusCode": 200,
            "headers": {"Content-Type": "text/html"},
            "body": HTML_HOW,
        }

    # Audio page
    if method == "GET" and path == "/audio":
        return {
            "statusCode": 200,
            "headers": {"Content-Type": "text/html"},
            "body": HTML_AUDIO,
        }

    # Audio API — returns track list with presigned URLs
    if method == "GET" and path == "/api/audio":
        try:
            conn = _get_conn()
            _ensure_tables(conn)
            tracks = _get_audio_list(conn)
            conn.close()

            for t in tracks:
                t["audio_url"] = _presign_url(t["s3_key"])
                del t["s3_key"]  # don't expose internal key

            return {
                "statusCode": 200,
                "headers": {
                    "Content-Type": "application/json",
                    "Cache-Control": "no-cache",
                },
                "body": json.dumps({"tracks": tracks}),
            }
        except Exception as e:
            return {
                "statusCode": 500,
                "headers": {"Content-Type": "application/json"},
                "body": json.dumps({"error": "could not load tracks"}),
            }

    # Email signup
    if method == "POST" and path == "/signup":
        try:
            body = json.loads(event.get("body", "{}"))
            email = body.get("email", "").strip().lower()

            if not email or "@" not in email:
                return {
                    "statusCode": 400,
                    "headers": {"Content-Type": "application/json"},
                    "body": json.dumps({"error": "valid email required"}),
                }

            conn = _get_conn()
            _ensure_tables(conn)

            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO quietbones_signup (email) VALUES (%s) ON CONFLICT (email) DO NOTHING",
                    (email,),
                )
                conn.commit()
            conn.close()

            return {
                "statusCode": 200,
                "headers": {"Content-Type": "application/json"},
                "body": json.dumps({"message": "noted."}),
            }

        except Exception:
            return {
                "statusCode": 500,
                "headers": {"Content-Type": "application/json"},
                "body": json.dumps({"error": "something went wrong"}),
            }

    return {
        "statusCode": 404,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps({"error": "not found"}),
    }
