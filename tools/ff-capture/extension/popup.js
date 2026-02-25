// Feldenkrais Capture — Popup
// Shows server health and recent capture history.

const SERVER_URL = "http://localhost:7890";

async function checkServer() {
  const el = document.getElementById("server-status");
  try {
    const resp = await fetch(`${SERVER_URL}/health`, { signal: AbortSignal.timeout(3000) });
    if (resp.ok) {
      const data = await resp.json();
      const tools = [
        `ffmpeg ${data.ffmpeg ? "OK" : "missing"}`,
        `yt-dlp ${data.ytdlp ? "OK" : "missing"}`,
      ].join(", ");
      el.className = "status ok";
      el.innerHTML = `<span class="dot ok"></span><span>Server running — ${tools}</span>`;
    } else {
      el.className = "status err";
      el.innerHTML = `<span class="dot err"></span><span>Server error: ${resp.status}</span>`;
    }
  } catch {
    el.className = "status err";
    el.innerHTML = `<span class="dot err"></span><span>Server not running (start server.py)</span>`;
  }
}

async function loadCaptures() {
  const container = document.getElementById("captures");
  const result = await chrome.storage.local.get({ captures: [] });
  const captures = result.captures;

  if (captures.length === 0) {
    container.innerHTML = `<div class="empty">No captures yet. Navigate to a lesson page.</div>`;
    return;
  }

  container.innerHTML = captures
    .map((c) => {
      const statusClass = c.status === "sent" ? "sent" : "error";
      const time = new Date(c.timestamp).toLocaleTimeString();
      const typeLabel = c.type === "fp" ? "FP" : c.type === "vimeo" ? "V" : "SC";
      return `
      <div class="capture">
        <div class="capture-title"><span class="capture-type">${typeLabel}</span> ${escapeHtml(c.title)}</div>
        <div class="capture-meta">
          ${time} — <span class="capture-status ${statusClass}">${escapeHtml(c.status)}</span>
        </div>
      </div>`;
    })
    .join("");
}

function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str;
  return div.innerHTML;
}

checkServer();
loadCaptures();
