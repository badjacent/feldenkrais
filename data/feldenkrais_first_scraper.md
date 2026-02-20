# Feldenkrais First Scraper — Design Notes

## Source

- **Site**: Feldenkrais First on Mighty Networks (`app.feldenkraisfirst.com`)
- **Auth**: Paid community membership required to see content
- **Platform**: Mighty Networks SPA — page source is an empty shell; all content loaded dynamically via JS after auth

## Audio Hosting

- Lessons are embedded as **SoundCloud players** (iframes injected into rendered DOM)
- Tracks appear to be **unlisted/private** — no public SoundCloud discovery
- Audio served via **CloudFront signed URLs** from `cf-hls-media.sndcdn.com`
  - Format: 128kbps MP3
  - URLs expire ~24 hours after generation
  - New signed URL generated each time the player loads
  - Example path pattern: `/media/{id1}/{id2}/{id3}/{hash}.128.mp3?Policy=...&Signature=...&Key-Pair-Id=...`

## Proposed Workflow

### 1. Browser Side (Tampermonkey Userscript)

- Runs on `app.feldenkraisfirst.com`
- Adds a "Stage this" button when a SoundCloud player is detected
- On click:
  - Scrapes metadata from rendered DOM (title, instructor, description, etc.)
  - Captures the active SoundCloud stream URL (intercept via fetch monkey-patch or PerformanceObserver)
  - Triggers a browser download of a JSON request file to a watched local folder

**Stream URL capture approaches:**
```javascript
// Option A: Monkey-patch fetch
const origFetch = window.fetch;
window.fetch = function(...args) {
    if (args[0]?.toString().includes('sndcdn.com')) {
        // capture args[0] as stream URL
    }
    return origFetch.apply(this, args);
};

// Option B: PerformanceObserver (catches all resource loads)
const obs = new PerformanceObserver(list => {
    for (const entry of list.getEntries()) {
        if (entry.name.includes('sndcdn.com') && entry.name.includes('.mp3')) {
            // capture entry.name as stream URL
        }
    }
});
obs.observe({ entryTypes: ['resource'] });
```

**Request file format:**
```json
{
  "stream_url": "https://cf-hls-media.sndcdn.com/media/.../hash.128.mp3?Policy=...&Signature=...&Key-Pair-Id=...",
  "title": "The Anti-Gravity Lesson",
  "instructor": "...",
  "description": "...",
  "source_page": "https://app.feldenkraisfirst.com/...",
  "requested_at": "2026-02-18T14:30:00Z"
}
```

### 2. Local Watcher

- Watch folder: e.g., `~/feldenkrais-inbox/`
- On new JSON file:
  1. Download the MP3 from `stream_url` (must happen within ~24h before signed URL expires)
  2. Run transcription (Whisper or similar)
  3. Stage into `data/lessons/` with metadata
  4. Move request file to `processed/` subfolder

**Implementation options:**
- `fswatch` + shell script (simplest)
- Python `watchdog` library (more control)

## TODO

- [ ] Inspect rendered DOM on several lesson pages to identify CSS selectors for metadata (title, instructor, description)
- [ ] Build Tampermonkey userscript
- [ ] Build local watcher script
- [ ] Decide on transcription approach (Whisper API vs local)
- [ ] Integrate with existing pipeline (lesson loading → analysis_a → ...)
