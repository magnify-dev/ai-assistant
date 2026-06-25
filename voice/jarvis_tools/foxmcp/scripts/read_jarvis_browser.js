(() => {
  const visible = (el) => {
    const style = getComputedStyle(el);
    const rect = el.getBoundingClientRect();
    return style.visibility !== 'hidden' && style.display !== 'none' && rect.width > 0 && rect.height > 0;
  };
  const lines = document.body.innerText
    .split(/\n+/)
    .map(s => s.trim())
    .filter(Boolean)
    .filter(s => s.length <= 120);

  const links = Array.from(document.querySelectorAll('a'))
    .filter(visible)
    .map(a => ({
      text: (a.innerText || a.textContent || '').trim().replace(/\s+/g, ' '),
      href: a.href || ''
    }))
    .filter(x => x.text && x.text.length <= 120);

  return JSON.stringify({
    title: document.title,
    url: location.href,
    visibleText: Array.from(new Set(lines)).slice(0, 120),
    links: links.slice(0, 80)
  });
})()
