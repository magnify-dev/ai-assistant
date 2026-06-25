(() => {
  const visible = (el) => {
    const style = getComputedStyle(el);
    const rect = el.getBoundingClientRect();
    return style.display !== "none" && style.visibility !== "hidden" && rect.width > 0 && rect.height > 0;
  };
  const selectors = [
    "ytd-playlist-video-renderer a#video-title",
    "ytd-playlist-video-renderer a.yt-simple-endpoint",
    "ytd-video-renderer a#video-title",
    "ytd-rich-item-renderer a#video-title-link",
    "a#video-title"
  ];
  for (const selector of selectors) {
    const target = Array.from(document.querySelectorAll(selector)).find(visible);
    if (target) {
      const text = (target.innerText || target.textContent || target.getAttribute("title") || "").trim();
      target.scrollIntoView({ block: "center", inline: "center" });
      target.click();
      return `OK: ${text || target.href || selector}`;
    }
  }

  const anchors = Array.from(document.querySelectorAll("a[href*='/watch'], a[href*='watch?v=']"))
    .filter(visible)
    .filter((a) => !String(a.href || "").includes("start_radio=1"));
  if (anchors[0]) {
    const target = anchors[0];
    const text = (target.innerText || target.textContent || target.getAttribute("title") || "").trim();
    target.scrollIntoView({ block: "center", inline: "center" });
    target.click();
    return `OK: ${text || target.href}`;
  }

  return "No visible video found";
})()
