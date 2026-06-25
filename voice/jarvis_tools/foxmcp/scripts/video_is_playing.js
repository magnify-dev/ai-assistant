(() => {
  const video = document.querySelector("video");
  return JSON.stringify({ playing: Boolean(video && !video.paused && !video.ended) });
})()
