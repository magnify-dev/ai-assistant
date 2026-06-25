(() => {
  const video = document.querySelector("video");
  if (!video) {
    return JSON.stringify({ hasVideo: false, title: document.title, url: location.href });
  }
  const title =
    document.querySelector("h1 yt-formatted-string")?.innerText ||
    document.querySelector("h1")?.innerText ||
    document.title ||
    "";
  return JSON.stringify({
    hasVideo: true,
    paused: video.paused,
    ended: video.ended,
    muted: video.muted,
    currentTime: Math.round(video.currentTime || 0),
    duration: Math.round(video.duration || 0),
    title,
    url: location.href
  });
})()
