(async () => {
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const visible = (el) => {
    if (!el) return false;
    const style = getComputedStyle(el);
    const rect = el.getBoundingClientRect();
    return style.display !== "none" && style.visibility !== "hidden" && rect.width > 0 && rect.height > 0;
  };
  const video = document.querySelector("video");
  if (!video) return "No video element found";

  const isPaused = () => video.paused || video.ended;
  const verifyPlaying = async () => {
    await sleep(700);
    return !isPaused();
  };

  const playSelectors = [
    ".ytp-large-play-button",
    ".ytp-play-button",
    ".ytp-cued-thumbnail-overlay-image",
    "#movie_player .ytp-large-play-button",
    "button.ytp-play-button",
    "button[aria-label*='Play']",
    "button[title*='Play']"
  ];

  for (const selector of playSelectors) {
    const buttons = Array.from(document.querySelectorAll(selector)).filter(visible);
    for (const button of buttons) {
      const label = `${button.getAttribute("aria-label") || ""} ${button.getAttribute("title") || ""}`.toLowerCase();
      if (label.includes("pause") && !label.includes("play")) continue;
      button.scrollIntoView({ block: "center", inline: "center" });
      button.click();
      if (await verifyPlaying()) return `OK: ${selector}`;
    }
  }

  const player = document.querySelector("#movie_player") || document.querySelector(".html5-video-player");
  if (player && visible(player)) {
    player.scrollIntoView({ block: "center", inline: "center" });
    player.click();
    if (await verifyPlaying()) return "OK: player click";
  }

  if (isPaused()) {
    try {
      video.muted = true;
      const playResult = video.play();
      if (playResult && typeof playResult.then === "function") await playResult;
      await sleep(300);
      video.muted = false;
      if (await verifyPlaying()) return "OK: muted autoplay";
    } catch (_err) {
      // Firefox blocks unmuted programmatic play without a user gesture.
    }
  }

  if (isPaused()) {
    return "Play blocked: click the YouTube play button once in Firefox, then ask again";
  }
  return "OK: already playing";
})()
