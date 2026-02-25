// FF Capture — Content Script
// Runs on app.feldenkraisfirst.com pages.
// 1. Responds to metadata requests from the background service worker (SoundCloud HLS).
// 2. Auto-detects Vimeo embeds and notifies the background worker.

// --- Message handler for SoundCloud flow ---
chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (message.type === "GET_METADATA") {
    const metadata = scrapeMetadata();
    sendResponse({ metadata });
  }
  return true;
});

// --- Vimeo auto-detection ---
const detectedVimeoUrls = new Set();
let scanTimeout = null;

function scanForVimeo() {
  const iframes = document.querySelectorAll("iframe");
  for (const iframe of iframes) {
    const src = iframe.src || iframe.dataset?.src || "";
    if (!src.includes("player.vimeo.com")) continue;
    if (detectedVimeoUrls.has(src)) continue;
    detectedVimeoUrls.add(src);

    console.log("[FF Capture] Vimeo embed detected:", src);

    const metadata = scrapeMetadata();
    chrome.runtime.sendMessage({
      type: "VIMEO_DETECTED",
      vimeo_url: src,
      metadata: metadata,
    });
  }
}

function debouncedScan() {
  if (scanTimeout) clearTimeout(scanTimeout);
  scanTimeout = setTimeout(scanForVimeo, 500);
}

// Initial scan
scanForVimeo();

// Watch for dynamically loaded content (Mighty Networks is an SPA)
if (document.body) {
  const observer = new MutationObserver(debouncedScan);
  observer.observe(document.body, { childList: true, subtree: true });
}

// --- Metadata scraping ---

function scrapeMetadata() {
  const metadata = {
    page_url: window.location.href,
    title: null,
    instructor: null,
    description: null,
    lesson_text: null,
  };

  // --- Title ---
  metadata.title =
    getTextContent("h1") ||
    getTextContent("[data-testid='post-title']") ||
    getTextContent(".post-title") ||
    getTextContent(".content-title") ||
    document.title;

  // --- Instructor ---
  metadata.instructor =
    getTextContent(".MuiTypography-labelLargeBold") ||
    getTextContent("[data-testid='post-author-name']") ||
    getTextContent(".author-name") ||
    getTextContent(".post-author") ||
    getTextContent(".mighty-author-name");

  // --- Description + Lesson text ---
  const descResult = extractDescription();
  metadata.description = descResult.description;
  metadata.lesson_text = descResult.lesson_text;

  return metadata;
}

function getTextContent(selector) {
  const el = document.querySelector(selector);
  if (!el) return null;
  const text = el.textContent.trim();
  return text || null;
}

function extractDescription() {
  const result = { description: null, lesson_text: null };

  const el =
    document.querySelector(".detail-layout-description") ||
    document.querySelector("[data-testid='post-body']") ||
    document.querySelector(".post-body") ||
    document.querySelector("article");

  if (!el) return result;

  // Clone and strip iframes/embeds/scripts
  const clone = el.cloneNode(true);
  clone.querySelectorAll("iframe, embed, object, script, style, .fr-embed").forEach(
    (n) => n.remove()
  );

  const fullText = clone.textContent.trim();
  if (fullText) {
    result.lesson_text = fullText;
    const firstP = clone.querySelector("p");
    if (firstP) {
      const pText = firstP.textContent.trim();
      if (pText && !pText.startsWith("#")) {
        result.description = pText;
      }
    }
    if (!result.description) {
      result.description = fullText.slice(0, 300);
    }
  }

  return result;
}
