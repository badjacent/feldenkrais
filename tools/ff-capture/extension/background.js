// Feldenkrais Capture — Service Worker
// 1. Watches for HLS manifest requests from SoundCloud CDN (audio lessons).
// 2. Receives Vimeo embed notifications from the content script (video lessons).
// 3. Receives FP capture requests from the FP content script (direct MP3 downloads).
// All are forwarded (with page metadata) to the local capture server.

const SERVER_URL = "http://localhost:7890";
const DEDUP_WINDOW_MS = 60_000;

// Track recently seen URLs to avoid duplicates
const recentCaptures = new Map(); // url_key -> timestamp

// Capture counter for badge
let captureCount = 0;

// --- SoundCloud HLS interception ---

chrome.webRequest.onBeforeRequest.addListener(
  (details) => {
    const url = details.url;
    if (!url.includes(".m3u8")) return;

    const urlPath = new URL(url).pathname;

    if (isDuplicate(urlPath)) return;

    console.log("[Feldenkrais Capture] HLS manifest detected:", urlPath);
    handleSoundCloudCapture(details.tabId, url);
  },
  { urls: ["*://*.sndcdn.com/*"] }
);

async function handleSoundCloudCapture(tabId, manifestUrl) {
  let metadata = {};
  try {
    const response = await chrome.tabs.sendMessage(tabId, {
      type: "GET_METADATA",
    });
    if (response && response.metadata) {
      metadata = response.metadata;
    }
  } catch (err) {
    console.warn("[Feldenkrais Capture] Could not get metadata from content script:", err.message);
  }

  const payload = { manifest_url: manifestUrl, metadata };
  await sendToServer(payload, manifestUrl, metadata, "soundcloud");
}

// --- Vimeo detection from content script ---

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message.type === "VIMEO_DETECTED") {
    const { vimeo_url, metadata } = message;

    if (isDuplicate(vimeo_url)) {
      sendResponse({ received: true, duplicate: true });
      return;
    }

    console.log("[Feldenkrais Capture] Vimeo video detected:", vimeo_url);
    const payload = { vimeo_url, metadata: metadata || {} };
    sendToServer(payload, vimeo_url, metadata || {}, "vimeo");
    sendResponse({ received: true });
  } else if (message.type === "FP_CAPTURE") {
    const { audio_url, metadata } = message;

    if (isDuplicate(audio_url)) {
      sendResponse({ received: true, duplicate: true });
      return;
    }

    console.log("[Feldenkrais Capture] FP audio detected:", audio_url);
    const payload = { audio_url, metadata: metadata || {} };
    sendToServer(payload, audio_url, metadata || {}, "fp");
    sendResponse({ received: true });
  }
  return true;
});

// --- Shared helpers ---

function isDuplicate(key) {
  const now = Date.now();
  const lastSeen = recentCaptures.get(key);
  if (lastSeen && now - lastSeen < DEDUP_WINDOW_MS) return true;
  recentCaptures.set(key, now);

  // Clean up old entries
  for (const [k, ts] of recentCaptures) {
    if (now - ts > DEDUP_WINDOW_MS) recentCaptures.delete(k);
  }
  return false;
}

async function sendToServer(payload, sourceUrl, metadata, captureType) {
  try {
    const resp = await fetch(`${SERVER_URL}/capture`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });

    if (resp.ok) {
      captureCount++;
      chrome.action.setBadgeText({ text: String(captureCount) });
      chrome.action.setBadgeBackgroundColor({ color: "#4CAF50" });
      saveCapture(sourceUrl, metadata, "sent", captureType);
      console.log("[Feldenkrais Capture] Sent to server successfully");
    } else {
      saveCapture(sourceUrl, metadata, `error: ${resp.status}`, captureType);
      console.error("[Feldenkrais Capture] Server returned", resp.status);
    }
  } catch (err) {
    saveCapture(sourceUrl, metadata, `error: ${err.message}`, captureType);
    console.error("[Feldenkrais Capture] Failed to reach server:", err.message);
  }
}

async function saveCapture(sourceUrl, metadata, status, captureType) {
  const entry = {
    url: sourceUrl,
    title: metadata.title || "(unknown)",
    type: captureType || "soundcloud",
    status: status,
    timestamp: new Date().toISOString(),
  };

  const result = await chrome.storage.local.get({ captures: [] });
  const captures = result.captures;
  captures.unshift(entry);
  if (captures.length > 50) captures.length = 50;
  await chrome.storage.local.set({ captures });
}
