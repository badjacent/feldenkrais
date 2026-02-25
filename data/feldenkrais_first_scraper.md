# Feldenkrais First Scraper — Design Notes

## Source

- **Site**: Feldenkrais First on Mighty Networks (`app.feldenkraisfirst.com`)
- **Auth**: Paid community membership required to see content
- **Platform**: Mighty Networks SPA — page source is an empty shell; all content loaded dynamically via JS after auth

## Audio Hosting

- Lessons are embedded as **SoundCloud players** (iframes injected into rendered DOM)
- Tracks appear to be **unlisted/private** — no public SoundCloud discovery
- Audio delivered as **HLS streams** via signed CloudFront URLs from `cf-hls-media.sndcdn.com`

### HLS Stream Details

SoundCloud serves audio as HLS (HTTP Live Streaming), not as a single MP3 download:

1. **Manifest**: When the user hits Play, the player fetches an `.m3u8` playlist file
   - URL pattern: `/playlist/{trackId}.128.mp3/playlist.m3u8?Policy=...&Signature=...&Key-Pair-Id=...`
   - Example: `https://cf-hls-media.sndcdn.com/playlist/zbugQTrGGaUi.128.mp3/playlist.m3u8?Policy=...`
2. **Segments**: The manifest lists ~127 segments (for a ~20 min lesson), each ~2–10 seconds
   - Each segment is a byte-range slice of a 128kbps MP3
   - Segment URLs are also signed CloudFront URLs
3. **Signed URL expiry**: Policy timestamps expire ~24 hours after generation
4. **Trigger**: The `.m3u8` request is only made when the user **presses Play** — it does not appear on page load

### Reassembly

`ffmpeg` handles HLS natively — it reads the manifest, downloads all segments, and concatenates:

```bash
ffmpeg -i "https://cf-hls-media.sndcdn.com/playlist/{trackId}.128.mp3/playlist.m3u8?Policy=...&Signature=...&Key-Pair-Id=..." -c:a libmp3lame -b:a 192k -vn output.mp3
```

## Implementation: Chrome Extension + Local Server

Built as a Chrome MV3 extension that intercepts HLS manifest requests and a local Python server that downloads audio and stages files.

### Architecture

```
Browser (extension)                    Local machine
┌─────────────────────┐               ┌──────────────────────┐
│ background.js       │               │ server.py (:7890)    │
│  webRequest listener│──POST /capture→│  Receives manifest   │
│  detects .m3u8 URLs │               │  Downloads via ffmpeg│
│                     │               │  Writes sidecar JSON │
│ content.js          │               │  → ~/feldenkrais-    │
│  scrapes metadata   │               │      inbox/          │
└─────────────────────┘               └──────────────────────┘
```

### Files

```
tools/ff-capture/
├── extension/
│   ├── manifest.json       # MV3 extension config
│   ├── background.js       # Service worker: webRequest → POST to server
│   ├── content.js          # Content script: scrapes page metadata
│   ├── popup.html          # Status popup UI
│   └── popup.js            # Popup logic: server health + capture history
├── server.py               # Local HTTP server (stdlib only, no deps)
└── README.md               # Setup + usage instructions
```

### Capture Flow

1. User presses Play on SoundCloud embed
2. `background.js` intercepts `.m3u8` request via `chrome.webRequest.onBeforeRequest`
3. Background asks `content.js` for page metadata (title, instructor, description, lesson text)
4. Background POSTs `{manifest_url, metadata}` to `localhost:7890/capture`
5. Server responds 202 immediately, spawns background thread
6. Thread runs `ffmpeg` to download audio, writes sidecar JSON + lesson text

### Output Format

Each capture creates a timestamped directory in `~/feldenkrais-inbox/`:

```
20260221_143052_zbugQTrGGaUi/
├── audio.mp3              # Full lesson audio (192kbps MP3)
├── audio.mp3.json         # Pipeline-compatible sidecar JSON
├── lesson_text.txt        # Page text content (if available)
└── capture_info.json      # Raw capture metadata
```

The sidecar JSON is formatted for direct ingestion by `loader.py` with `source_id: 1` (OTHER) and `type_id: 2` (AUDIO).

### Key Technical Details

- **MV3 webRequest**: observation mode (non-blocking) works in Manifest V3
- **Iframe visibility**: `webRequest` sees all browser traffic including cross-origin SoundCloud iframes
- **Deduplication**: background.js deduplicates by URL path within a 60s window
- **No external Python deps**: server uses only stdlib (http.server, subprocess, threading, json)
- **CORS**: server sends permissive CORS headers for extension fetch to localhost

## TODO

- [ ] Tune CSS selectors in content.js after inspecting live Mighty Networks lesson pages
- [ ] Add FELDENKRAIS_FIRST as a proper source in the database
- [ ] Integrate with pipeline ingestion (auto-load from inbox)
- [ ] Add transcription step (Whisper API or local)
