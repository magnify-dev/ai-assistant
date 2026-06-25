(function () {
  const BRIDGE_URL = "http://127.0.0.1:8765/context";
  const COMMAND_URL = "http://127.0.0.1:8765/command";
  const COMMAND_RESULT_URL = "http://127.0.0.1:8765/command-result";
  let lastPayload = "";
  let lastSentAt = 0;

  function isVisible(el) {
    if (!el || !(el instanceof Element)) return false;
    const style = window.getComputedStyle(el);
    const rect = el.getBoundingClientRect();
    return style.display !== "none" &&
      style.visibility !== "hidden" &&
      rect.width > 0 &&
      rect.height > 0;
  }

  function unique(items) {
    return Array.from(new Set(items.filter(Boolean)));
  }

  function words(text) {
    return String(text || "").toLowerCase().match(/[a-z0-9]+/g) || [];
  }

  function textOf(el) {
    return (el ? (el.innerText || el.textContent || "") : "").trim().replace(/\s+/g, " ");
  }

  function attr(el, name) {
    return (el.getAttribute(name) || "").trim().replace(/\s+/g, " ");
  }

  function elementLabel(el) {
    const labelledBy = attr(el, "aria-labelledby")
      .split(/\s+/)
      .map((id) => textOf(document.getElementById(id)))
      .filter(Boolean)
      .join(" ");
    const closestTitle = textOf(
      el.closest("ytd-playlist-video-renderer, ytd-video-renderer, ytd-rich-item-renderer")
        ?.querySelector("#video-title, a#video-title, h3")
    );
    const imageAlt = Array.from(el.querySelectorAll ? el.querySelectorAll("img[alt]") : [])
      .map((img) => attr(img, "alt"))
      .find(Boolean) || "";
    return (
      textOf(el) ||
      attr(el, "aria-label") ||
      attr(el, "title") ||
      attr(el, "placeholder") ||
      String(el.value || "").trim() ||
      labelledBy ||
      closestTitle ||
      imageAlt ||
      ""
    ).trim().replace(/\s+/g, " ");
  }

  function describeInteractable(el, index, videoOrdinal) {
    const tag = el.tagName.toLowerCase();
    const href = el.href || "";
    const aria = attr(el, "aria-label");
    const title = attr(el, "title");
    const role = attr(el, "role");
    const label = elementLabel(el);
    const haystack = `${label} ${aria} ${title} ${role} ${href} ${String(el.className || "")}`.toLowerCase();
    let kind = role || tag;
    if (href.includes("/watch") || href.includes("watch?v=")) kind = "video-link";
    else if (href.includes("playlist") || href.includes("list=")) kind = "playlist-link";
    else if (tag === "button" || role === "button") kind = "button";
    let action = "";
    if (haystack.includes("pause")) action = "pause";
    else if (haystack.includes("play")) action = "play";
    return {
      index,
      kind,
      action,
      text: label,
      href,
      aria,
      title,
      role,
      ordinal: kind === "video-link" ? videoOrdinal : 0
    };
  }

  function interactableCandidates() {
    const raw = Array.from(document.querySelectorAll(
      "a[href], button, input:not([type='hidden']), select, textarea, summary, [role='button'], [role='link'], [role='menuitem'], [role='option'], [tabindex]:not([tabindex='-1'])"
    )).filter(isVisible);
    const seen = new Set();
    const candidates = [];
    let videoOrdinal = 0;

    for (const el of raw) {
      const data = describeInteractable(el, candidates.length, 0);
      if (data.kind === "video-link") videoOrdinal += 1;
      data.ordinal = data.kind === "video-link" ? videoOrdinal : 0;
      const key = `${data.kind}|${data.text}|${data.aria}|${data.title}|${data.href}`;
      if (seen.has(key)) continue;
      seen.add(key);
      if (data.text || data.aria || data.title || data.href || data.action) {
        candidates.push({ el, data });
      }
    }

    const video = document.querySelector("video");
    if (video) {
      candidates.unshift({
        el: video,
        data: {
          index: -1,
          kind: "video-player",
          action: video.paused ? "play" : "pause",
          text: video.paused ? "Video player paused" : "Video player playing",
          href: location.href,
          aria: "video player",
          title: document.title || "",
          role: "",
          ordinal: 0
        }
      });
    }
    return candidates.slice(0, 300);
  }

  function sleep(ms) {
    return new Promise((resolve) => setTimeout(resolve, ms));
  }

  function activate(el) {
    if (!el) return;
    el.scrollIntoView({ block: "center", inline: "center" });
    for (const type of ["pointerdown", "mousedown", "pointerup", "mouseup", "click"]) {
      el.dispatchEvent(new MouseEvent(type, { bubbles: true, cancelable: true, view: window }));
    }
  }

  async function playVideo(video) {
    const playButtons = [
      ".ytp-large-play-button",
      ".ytp-play-button",
      "button[aria-label*='Play']",
      "button[title*='Play']"
    ];
    for (const selector of playButtons) {
      const button = Array.from(document.querySelectorAll(selector))
        .find((el) => isVisible(el) && !`${attr(el, "aria-label")} ${attr(el, "title")}`.toLowerCase().includes("pause"));
      if (button) {
        activate(button);
        await sleep(600);
        if (!video.paused && !video.ended) return `OK: ${selector}`;
      }
    }

    activate(video);
    await sleep(600);
    if (!video.paused && !video.ended) return "OK: video click";

    video.muted = false;
    const playResult = video.play();
    if (playResult && typeof playResult.then === "function") {
      await playResult;
    }
    await sleep(600);
    if (!video.paused && !video.ended) return "OK: video.play()";
    throw new Error("Play did not start: video is still paused");
  }

  function matchKey(text) {
    return words(text).join("");
  }

  function matchScore(query, candidate) {
    const qKey = matchKey(query);
    const cKey = matchKey(candidate);
    if (!qKey || !cKey) return 0;
    if (qKey === cKey) return 1;
    if (qKey.includes(cKey) || cKey.includes(qKey)) return 0.92;
    const queryWords = new Set(words(query));
    const candidateWords = new Set(words(candidate));
    let overlap = 0;
    for (const word of queryWords) {
      if (candidateWords.has(word)) overlap += 1;
    }
    return overlap / Math.max(1, new Set([...queryWords, ...candidateWords]).size);
  }

  function queryOrdinal(query) {
    const lowered = String(query || "").toLowerCase();
    const ordinals = { first: 1, "1st": 1, second: 2, "2nd": 2, third: 3, "3rd": 3 };
    for (const [word, value] of Object.entries(ordinals)) {
      if (new RegExp(`\\b${word}\\b`).test(lowered)) return value;
    }
    return null;
  }

  function bestClickable(query) {
    const queryWords = new Set(words(query));
    if (!queryWords.size) return null;
    const candidates = interactableCandidates();
    const playIntent = /\b(play|start|resume)\b/i.test(query);
    const videoIntent = /\b(video|song|track|music)\b/i.test(query);
    const wantedOrdinal = queryOrdinal(query);
    let best = null;
    let bestScore = 0;
    for (const candidate of candidates) {
      const item = candidate.data;
      const haystack = `${item.text} ${item.aria} ${item.title} ${item.href} ${item.kind} ${item.action}`;
      const fields = [item.text, item.aria, item.title, item.href, item.kind, item.action].filter(Boolean);
      let score = Math.max(...fields.map((field) => matchScore(query, field)));
      const candidateWords = new Set(words(haystack));
      let overlap = 0;
      for (const word of queryWords) {
        if (candidateWords.has(word)) overlap += 1;
      }
      score += 0.35 * (overlap / Math.max(1, new Set([...queryWords, ...candidateWords]).size));
      if (playIntent) {
        if (item.action === "play") score += 1.5;
        if (haystack.toLowerCase().includes("play")) score += 0.5;
        if (item.kind === "video-player") score += 0.7;
        if (item.kind === "video-link") score += 0.35;
      }
      if (videoIntent && ["video-link", "video-player"].includes(item.kind)) score += 0.7;
      if (wantedOrdinal && item.ordinal) score += item.ordinal === wantedOrdinal ? 2 : -0.25;
      if (score > bestScore) {
        best = candidate;
        bestScore = score;
      }
    }
    return bestScore >= 0.6 ? best : null;
  }

  function collectContext() {
    const visibleText = unique(
      (document.body ? document.body.innerText : "")
        .split(/\n+/)
        .map((line) => line.trim().replace(/\s+/g, " "))
        .filter((line) => line && line.length <= 160)
    ).slice(0, 250);

    const links = Array.from(document.querySelectorAll("a"))
      .filter(isVisible)
      .map((a) => ({
        text: (a.innerText || a.textContent || a.getAttribute("aria-label") || "")
          .trim()
          .replace(/\s+/g, " "),
        href: a.href || ""
      }))
      .filter((item) => item.text && item.href)
      .slice(0, 200);

    return {
      url: location.href,
      title: document.title,
      visibleText,
      links,
      interactables: interactableCandidates().map((candidate) => candidate.data),
      capturedAt: new Date().toISOString()
    };
  }

  async function sendContext(force = false) {
    try {
      const context = collectContext();
      const payload = JSON.stringify(context);
      const now = Date.now();
      if (!force && payload === lastPayload && now - lastSentAt < 5000) return;
      lastPayload = payload;
      lastSentAt = now;

      await fetch(BRIDGE_URL, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: payload
      });
    } catch (_err) {
      // Jarvis may not be running. Stay quiet inside the page.
    }
  }

  async function postCommandResult(result) {
    try {
      await fetch(COMMAND_RESULT_URL, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(result)
      });
    } catch (_err) {
      // Jarvis may not be running.
    }
  }

  async function pollCommand() {
    try {
      const response = await fetch(COMMAND_URL);
      const data = await response.json();
      const command = data && data.command;
      if (!command) return;

      if (command.action === "navigate") {
        location.href = command.url;
        await postCommandResult({ id: command.id, ok: true, message: "OK" });
        return;
      }

      if (command.action === "click") {
        const target = bestClickable(command.query);
        if (!target) {
          await postCommandResult({
            id: command.id,
            ok: false,
            error: `No visible element matched: ${command.query}`
          });
          return;
        }
        const label = target.data.text || target.data.aria || target.data.title || target.data.href || target.data.kind;
        if (target.data.kind === "video-player") {
          const message = await playVideo(target.el);
          await postCommandResult({ id: command.id, ok: true, message, label });
          setTimeout(() => sendContext(true), 1000);
          return;
        } else {
          target.el.scrollIntoView({ block: "center", inline: "center" });
          target.el.click();
        }
        await postCommandResult({ id: command.id, ok: true, message: "OK", label });
        setTimeout(() => sendContext(true), 1000);
        return;
      }

      await postCommandResult({ id: command.id, ok: false, error: `Unknown action: ${command.action}` });
    } catch (_err) {
      // Stay quiet; Jarvis may not be running yet.
    }
  }

  sendContext(true);
  window.addEventListener("focus", () => sendContext(true));
  window.addEventListener("popstate", () => setTimeout(() => sendContext(true), 500));
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) sendContext(true);
  });

  const observer = new MutationObserver(() => sendContext(false));
  if (document.body) {
    observer.observe(document.body, { childList: true, subtree: true, characterData: true });
  }
  setInterval(() => sendContext(false), 3000);
  setInterval(pollCommand, 500);
})();
