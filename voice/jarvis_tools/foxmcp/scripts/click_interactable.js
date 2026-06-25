(async () => {
  const target = __TARGET__;
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const visible = (el) => {
    const style = getComputedStyle(el);
    const rect = el.getBoundingClientRect();
    return style.display !== "none" && style.visibility !== "hidden" && rect.width > 0 && rect.height > 0;
  };
  const activate = (el) => {
    if (!el) return;
    el.scrollIntoView({ block: "center", inline: "center" });
    for (const type of ["pointerdown", "mousedown", "pointerup", "mouseup", "click"]) {
      el.dispatchEvent(new MouseEvent(type, { bubbles: true, cancelable: true, view: window }));
    }
  };
  const verifyPlaying = async (video) => {
    await sleep(600);
    return Boolean(video && !video.paused && !video.ended);
  };
  const textOf = (el) => (el ? (el.innerText || el.textContent || "").trim().replace(/\s+/g, " ") : "");
  const attr = (el, name) => (el.getAttribute(name) || "").trim().replace(/\s+/g, " ");
  const label = (el) => {
    const closestTitle = textOf(el.closest("ytd-playlist-video-renderer, ytd-video-renderer, ytd-rich-item-renderer")?.querySelector("#video-title, a#video-title, h3"));
    const imageAlt = Array.from(el.querySelectorAll ? el.querySelectorAll("img[alt]") : [])
      .map((img) => attr(img, "alt"))
      .find(Boolean) || "";
    return (
      textOf(el) || attr(el, "aria-label") || attr(el, "title") || attr(el, "placeholder") ||
      String(el.value || "").trim() || closestTitle || imageAlt
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
    if (href.includes("/watch") || href.includes("watch?v=")) kind = "video-link";
    else if (href.includes("playlist") || href.includes("list=")) kind = "playlist-link";
    else if (tag === "button" || role === "button") kind = "button";
    let action = "";
    if (haystack.includes("pause")) action = "pause";
    else if (/\bplay\b/i.test(haystack) && !/playlist/i.test(href)) action = "play";
    return { index, kind, action, text: itemText, href, aria, title, role, ordinal: kind === "video-link" ? videoOrdinal : 0 };
  };
  const raw = Array.from(document.querySelectorAll(
    "a[href], button, input:not([type='hidden']), select, textarea, summary, [role='button'], [role='link'], [role='menuitem'], [role='option'], [tabindex]:not([tabindex='-1'])"
  )).filter(visible);
  const items = [];
  const seen = new Set();
  let videoOrdinal = 0;
  for (const el of raw) {
    const data = describe(el, items.length, 0);
    if (data.kind === "video-link") videoOrdinal += 1;
    data.ordinal = data.kind === "video-link" ? videoOrdinal : 0;
    const key = `${data.kind}|${data.text}|${data.aria}|${data.title}|${data.href}`;
    if (seen.has(key)) continue;
    seen.add(key);
    if (data.text || data.aria || data.title || data.href || data.action) items.push({ el, data });
  }

  let item = null;
  if (target.kind === "video-player") {
    item = { el: document.querySelector("video"), data: target };
  }
  if (!item && Number.isInteger(target.index)) {
    item = items.find((entry) => entry.data.index === target.index);
  }
  if (!item && target.href) {
    item = items.find((entry) => entry.data.href === target.href);
  }
  if (!item) {
    const targetText = `${target.text || ""} ${target.aria || ""} ${target.title || ""}`.toLowerCase();
    item = items.find((entry) => targetText && `${entry.data.text} ${entry.data.aria} ${entry.data.title}`.toLowerCase().includes(targetText.trim()));
  }
  if (!item || !item.el) return `No visible element matched: ${target.text || target.aria || target.href || "selected element"}`;

  item.el.scrollIntoView({ block: "center", inline: "center" });
  item.el.click();
  const clicked = item.data.text || item.data.aria || item.data.title || item.data.href || item.data.kind;
  return `OK: ${clicked}`;
})()
