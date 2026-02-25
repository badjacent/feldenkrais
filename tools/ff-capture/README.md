# FF Capture — HLS Audio Capture Tool

Captures audio lessons from Feldenkrais First (app.feldenkraisfirst.com) by intercepting HLS manifest URLs when you press Play, downloading the full audio via ffmpeg, and staging files with pipeline-compatible sidecar JSON.

## Prerequisites

- **ffmpeg** — `brew install ffmpeg`
- **Chrome** — for the extension
- **Feldenkrais First membership** — you must be logged in

## Setup

### 1. Start the local server

```bash
python tools/ff-capture/server.py
```

Verify it's running:
```bash
curl http://localhost:7890/health
```

### 2. Install the Chrome extension

1. Open `chrome://extensions/`
2. Enable **Developer mode** (top right toggle)
3. Click **Load unpacked**
4. Select the `tools/ff-capture/extension/` directory
5. Pin the extension for easy access

## Usage

1. Start the server (`python tools/ff-capture/server.py`)
2. Browse to a lesson on `app.feldenkraisfirst.com`
3. **Press Play** on the SoundCloud audio player
4. The extension auto-captures the HLS manifest + page metadata and sends it to the server
5. The server downloads the full audio and stages it in `~/feldenkrais-inbox/`

The extension badge shows a count of successful captures. Click the extension icon to see server status and capture history.

## Output

Each capture creates a timestamped directory in `~/feldenkrais-inbox/`:

```
20260221_143052_zbugQTrGGaUi/
├── audio.mp3              # Full lesson audio (192kbps MP3)
├── audio.mp3.json         # Pipeline-compatible sidecar JSON
├── lesson_text.txt        # Page text content (if available)
└── capture_info.json      # Raw capture metadata
```

The sidecar JSON (`audio.mp3.json`) is formatted for direct ingestion by `loader.py`.

## Troubleshooting

**Server not running**: The extension popup shows connection status. Make sure `server.py` is running before pressing Play.

**ffmpeg not found**: Install with `brew install ffmpeg`. The server logs ffmpeg status at startup.

**No capture triggered**: The manifest URL is only fetched when you press Play on the SoundCloud embed. If the lesson has already been playing, try refreshing the page and pressing Play again.

**Expired URL**: Signed CloudFront URLs expire ~24 hours after generation. The server downloads immediately on capture, so this shouldn't be an issue unless the server was down when you pressed Play.

**Wrong metadata**: The DOM selectors in `content.js` are best-guess for Mighty Networks. If metadata is missing or wrong, inspect the page and update the selectors.
