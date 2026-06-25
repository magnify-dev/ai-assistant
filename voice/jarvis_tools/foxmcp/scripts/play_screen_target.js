(() => {
  const visible = (el) => {
    if (!el) return false;
    const style = getComputedStyle(el);
    const rect = el.getBoundingClientRect();
    return style.display !== "none" && style.visibility !== "hidden" && rect.width > 0 && rect.height > 0;
  };
  const video = document.querySelector("video");
  if (video && !video.paused && !video.ended) return JSON.stringify({ playing: true });
  const originX = window.mozInnerScreenX ?? window.screenX ?? 0;
  const originY = window.mozInnerScreenY ?? window.screenY ?? 0;
  const selectors = [
    ".ytp-large-play-button",
    ".ytp-play-button",
    ".ytp-cued-thumbnail-overlay-image",
    "#movie_player"
  ];
  for (const selector of selectors) {
    const elements = selector === "#movie_player"
      ? [document.querySelector(selector)].filter(Boolean)
      : Array.from(document.querySelectorAll(selector));
    for (const el of elements) {
      if (!visible(el)) continue;
      const label = `${el.getAttribute?.("aria-label") || ""} ${el.getAttribute?.("title") || ""}`.toLowerCase();
      if (label.includes("pause") && !label.includes("play")) continue;
      const rect = el.getBoundingClientRect();
      return JSON.stringify({
        screenX: Math.round(originX + rect.left + rect.width / 2),
        screenY: Math.round(originY + rect.top + rect.height / 2),
        selector
      });
    }
  }
  return JSON.stringify({ error: "no target" });
})()
