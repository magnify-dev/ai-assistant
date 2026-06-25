(() => {
  const query = __QUERY__;
  const words = String(query || "").toLowerCase().match(/[a-z0-9]+/g) || [];
  const normalizedQuery = words.join(" ");
  const visible = (el) => {
    const style = getComputedStyle(el);
    const rect = el.getBoundingClientRect();
    return style.display !== "none" && style.visibility !== "hidden" && rect.width > 0 && rect.height > 0;
  };
  const label = (el) => (
    el.innerText || el.textContent || el.getAttribute("aria-label") ||
    el.getAttribute("title") || el.getAttribute("placeholder") || el.value || ""
  ).trim().replace(/\s+/g, " ");
  const candidates = Array.from(document.querySelectorAll(
    "a, button, [role='button'], [role='link'], input[type='button'], input[type='submit']"
  )).filter(visible);
  let best = null;
  let bestScore = 0;
  for (const el of candidates) {
    const itemLabel = label(el);
    const haystack = `${itemLabel} ${el.href || ""}`.toLowerCase();
    const normalizedLabel = (itemLabel.toLowerCase().match(/[a-z0-9]+/g) || []).join(" ");
    let score = words.reduce((sum, word) => sum + (haystack.includes(word) ? 1 : 0), 0);
    if (normalizedQuery && normalizedLabel.includes(normalizedQuery)) score += 10;
    if (normalizedQuery && haystack.includes(normalizedQuery)) score += 5;
    if (score > bestScore) {
      best = el;
      bestScore = score;
    }
  }
  if (!best || bestScore < Math.max(1, Math.min(words.length, 2))) return `No visible element matched: ${query}`;
  const text = label(best);
  best.scrollIntoView({ block: "center", inline: "center" });
  best.click();
  return `OK: ${text}`;
})()
