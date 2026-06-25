(() => {
  const visible = (el) => {
    const style = getComputedStyle(el);
    const rect = el.getBoundingClientRect();
    return style.display !== "none" && style.visibility !== "hidden" && rect.width > 0 && rect.height > 0;
  };
  const textOf = (el) => (el ? (el.innerText || el.textContent || "").trim().replace(/\s+/g, " ") : "");
  const attr = (el, name) => (el.getAttribute(name) || "").trim().replace(/\s+/g, " ");
  const label = (el) => {
    const labelledBy = attr(el, "aria-labelledby")
      .split(/\s+/)
      .map((id) => textOf(document.getElementById(id)))
      .filter(Boolean)
      .join(" ");
    const closestTitle = textOf(el.closest("ytd-playlist-video-renderer, ytd-video-renderer, ytd-rich-item-renderer")?.querySelector("#video-title, a#video-title, h3"));
    const imageAlt = Array.from(el.querySelectorAll ? el.querySelectorAll("img[alt]") : [])
      .map((img) => attr(img, "alt"))
      .find(Boolean) || "";
    return (
      textOf(el) || attr(el, "aria-label") || attr(el, "title") || attr(el, "placeholder") ||
      String(el.value || "").trim() || labelledBy || closestTitle || imageAlt
    ).trim().replace(/\s+/g, " ");
  };
  const describe = (el, index, videoOrdinal) => {
    const tag = el.tagName.toLowerCase();
    const href = el.href || "";
    const aria = attr(el, "aria-label");
    const title = attr(el, "title");
    const role = attr(el, "role");
    const classes = String(el.className || "");
    const itemText = label(el);
    const haystack = `${itemText} ${aria} ${title} ${role} ${href} ${classes}`.toLowerCase();
    let kind = role || tag;
    if (classes.includes("ytp-play-button") || classes.includes("ytp-large-play-button")) kind = "play-button";
    else if (href.includes("/watch") || href.includes("watch?v=")) kind = "video-link";
    else if (href.includes("playlist") || href.includes("list=")) kind = "playlist-link";
    else if (tag === "button" || role === "button") kind = "button";
    let action = "";
    if (kind === "play-button") action = "play";
    else if (/\bpause\b/i.test(haystack) && !/\bplay\b/i.test(haystack)) action = "pause";
    else if (/\bplay\b/i.test(haystack) && !/playlist/i.test(href)) action = "play";
    return {
      index,
      kind,
      action,
      text: itemText,
      href,
      aria,
      title,
      role,
      ordinal: kind === "video-link" ? videoOrdinal : 0
    };
  };

  const raw = Array.from(document.querySelectorAll(
    "a[href], button, input:not([type='hidden']), select, textarea, summary, [role='button'], [role='link'], [role='menuitem'], [role='option'], [tabindex]:not([tabindex='-1'])"
  )).filter(visible);
  const seen = new Set();
  const items = [];
  let videoOrdinal = 0;
  for (const el of raw) {
    const data = describe(el, items.length, 0);
    if (data.kind === "video-link") videoOrdinal += 1;
    data.ordinal = data.kind === "video-link" ? videoOrdinal : 0;
    const key = `${data.kind}|${data.text}|${data.aria}|${data.title}|${data.href}`;
    if (seen.has(key)) continue;
    seen.add(key);
    if (data.text || data.aria || data.title || data.href || data.action) items.push(data);
  }

  for (const row of document.querySelectorAll(
    "ytd-grid-playlist-renderer, ytd-playlist-renderer"
  )) {
    if (!visible(row)) continue;
    const link = row.querySelector("a[href*='list='], a[href*='/playlist']");
    if (!link || !visible(link)) continue;
    const titleEl = row.querySelector(
      "#video-title, #title, yt-formatted-string#title, a#video-title, h3 a, #text"
    );
    const itemText = (
      textOf(titleEl) || attr(link, "aria-label") || attr(link, "title") || ""
    ).trim().replace(/\s+/g, " ");
    if (!itemText) continue;
    const href = link.href || "";
    const key = `playlist-link|${itemText}|${href}`;
    if (seen.has(key)) continue;
    seen.add(key);
    items.push({
      index: items.length,
      kind: "playlist-link",
      action: "open",
      text: itemText,
      href,
      aria: attr(link, "aria-label"),
      title: attr(link, "title"),
      role: attr(link, "role") || "link",
      ordinal: 0
    });
  }

  for (const link of document.querySelectorAll("a[href*='list=']")) {
    if (!visible(link)) continue;
    const href = link.href || "";
    if (!/list=PL/i.test(href)) continue;
    if (/\/watch/i.test(href.split("?")[0])) continue;
    const row = link.closest("ytd-grid-playlist-renderer, ytd-playlist-renderer, ytd-playlist-thumbnail");
    const titleEl = row
      ? row.querySelector("#video-title, #title, yt-formatted-string#title, h3, span")
      : null;
    const itemText = (
      textOf(titleEl) || textOf(link) || attr(link, "aria-label") || attr(link, "title") || ""
    ).trim().replace(/\s+/g, " ");
    if (!itemText || itemText.length > 120) continue;
    const key = `playlist-link|${itemText}|${href}`;
    if (seen.has(key)) continue;
    seen.add(key);
    items.push({
      index: items.length,
      kind: "playlist-link",
      action: "open",
      text: itemText,
      href,
      aria: attr(link, "aria-label"),
      title: attr(link, "title"),
      role: attr(link, "role") || "link",
      ordinal: 0
    });
  }

  const video = document.querySelector("video");
  if (video && /\/watch|\/shorts/i.test(location.pathname)) {
    items.unshift({
      index: -1,
      kind: "video-player",
      action: video.paused ? "play" : "pause",
      text: video.paused ? "Video player paused" : "Video player playing",
      href: location.href,
      aria: "video player",
      title: document.title || "",
      role: "",
      ordinal: 0
    });
  }
  return JSON.stringify(items.slice(0, 300));
})()
