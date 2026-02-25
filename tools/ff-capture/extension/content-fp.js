// Feldenkrais Capture — Content Script for feldenkraisproject.com
// Runs on lesson pages. Extracts audio URL + metadata from Podlove player config.
// Shows a floating "Capture" button when audio is detected — user clicks to trigger download.

(function () {
  // --- Extract audio URL from Podlove player config ---
  function extractAudioUrl() {
    // Pattern: "audio":[{"url":"https:\/\/...\.mp3",...}]
    const html = document.documentElement.innerHTML;
    const match = html.match(/"audio"\s*:\s*\[\s*\{\s*"url"\s*:\s*"([^"]+)"/);
    if (!match) return null;
    // Unescape forward slashes
    return match[1].replace(/\\?\//g, "/");
  }

  // --- Extract page title ---
  function extractTitle() {
    // h1.entry-title, strip "(Patrons)" suffix
    const h1 = document.querySelector("h1.entry-title");
    if (h1) {
      return h1.textContent.trim().replace(/\s*\(Patrons\)\s*$/, "");
    }
    // Fallback: og:title
    const og = document.querySelector('meta[property="og:title"]');
    if (og && og.content) return og.content.trim();
    // Fallback: <title>
    return document.title.trim() || "Untitled";
  }

  // --- Extract duration ---
  function extractDuration() {
    const el = document.querySelector(".lesson-duration");
    if (el) return el.textContent.trim();

    // Try ld+json structured data
    for (const script of document.querySelectorAll('script[type="application/ld+json"]')) {
      try {
        const data = JSON.parse(script.textContent);
        if (data.duration) return data.duration;
      } catch {}
    }
    return null;
  }

  // --- Extract description ---
  function extractDescription() {
    const el =
      document.querySelector(".entry-content") ||
      document.querySelector("article") ||
      document.querySelector(".post-content");
    if (!el) return null;

    // Clone and strip media/scripts
    const clone = el.cloneNode(true);
    clone
      .querySelectorAll("iframe, embed, object, script, style, .podlove-web-player")
      .forEach((n) => n.remove());

    const text = clone.textContent.trim();
    return text ? text.slice(0, 500) : null;
  }

  // --- Create floating capture button ---
  function createCaptureButton(audioUrl, metadata) {
    const btn = document.createElement("button");
    btn.id = "feldenkrais-capture-btn";
    btn.textContent = "Capture";
    Object.assign(btn.style, {
      position: "fixed",
      bottom: "20px",
      right: "20px",
      zIndex: "999999",
      padding: "10px 20px",
      fontSize: "14px",
      fontWeight: "600",
      fontFamily: "-apple-system, BlinkMacSystemFont, sans-serif",
      color: "#fff",
      background: "#4CAF50",
      border: "none",
      borderRadius: "8px",
      cursor: "pointer",
      boxShadow: "0 2px 8px rgba(0,0,0,0.2)",
      transition: "background 0.2s",
    });

    btn.addEventListener("mouseenter", () => {
      if (!btn.disabled) btn.style.background = "#43A047";
    });
    btn.addEventListener("mouseleave", () => {
      if (!btn.disabled) btn.style.background = "#4CAF50";
    });

    btn.addEventListener("click", () => {
      btn.disabled = true;
      btn.textContent = "Capturing...";
      btn.style.background = "#FF9800";
      btn.style.cursor = "default";

      chrome.runtime.sendMessage(
        {
          type: "FP_CAPTURE",
          audio_url: audioUrl,
          metadata: metadata,
        },
        (response) => {
          if (chrome.runtime.lastError) {
            btn.textContent = "Error";
            btn.style.background = "#f44336";
            console.error("[Feldenkrais Capture] Send failed:", chrome.runtime.lastError.message);
            return;
          }
          if (response && response.received) {
            if (response.duplicate) {
              btn.textContent = "Already captured";
              btn.style.background = "#9E9E9E";
            } else {
              btn.textContent = "Sent!";
              btn.style.background = "#4CAF50";
            }
          } else {
            btn.textContent = "Error";
            btn.style.background = "#f44336";
          }
        }
      );
    });

    document.body.appendChild(btn);
  }

  // --- Main ---
  const audioUrl = extractAudioUrl();
  if (!audioUrl) return; // No audio on this page, nothing to do

  const metadata = {
    page_url: window.location.href,
    title: extractTitle(),
    duration: extractDuration(),
    description: extractDescription(),
  };

  console.log("[Feldenkrais Capture] Audio detected:", audioUrl);
  console.log("[Feldenkrais Capture] Title:", metadata.title);

  createCaptureButton(audioUrl, metadata);
})();
