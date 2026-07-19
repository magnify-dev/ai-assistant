"""Lightweight Playwright ad/tracker blocking for browser exploration.

The research browser does not load the user's uBlock Origin extension. This module
approximates the useful parts: abort known ad/tracker hosts and hide common
sticky video/promo chrome that otherwise traps the agent in overlay loops.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)

# Host suffixes — block if request host equals or ends with these.
AD_HOST_SUFFIXES: tuple[str, ...] = (
    "doubleclick.net",
    "googlesyndication.com",
    "googleadservices.com",
    "googletagservices.com",
    "google-analytics.com",
    "googletagmanager.com",
    "adservice.google.com",
    "pagead2.googlesyndication.com",
    "amazon-adsystem.com",
    "adnxs.com",
    "adsrvr.org",
    "adform.net",
    "advertising.com",
    "adsafeprotected.com",
    "adtrafficquality.google",
    "casalemedia.com",
    "criteo.com",
    "criteo.net",
    "exoclick.com",
    "facebook.net",
    "hotjar.com",
    "moatads.com",
    "outbrain.com",
    "pubmatic.com",
    "quantserve.com",
    "rubiconproject.com",
    "scorecardresearch.com",
    "serving-sys.com",
    "taboola.com",
    "tapad.com",
    "zedo.com",
    "2mdn.net",
    "media.net",
    "openx.net",
    "smartadserver.com",
    "yieldmo.com",
    "ads-twitter.com",
    "ads.linkedin.com",
    "ads.yahoo.com",
    "ads.youtube.com",
    "partner.googleadservices.com",
    "securepubads.g.doubleclick.net",
    "static.ads-twitter.com",
    "connect.facebook.net",
    # Gaming / publisher ad stacks commonly injected on Wowhead-like sites.
    "venatusmedia.com",
    "vdo.ai",
    "snigelweb.com",
    "freestar.com",
    "freestar.io",
    "nimbus.bitdefender.com",
    "playwire.com",
    "intergi.com",
    "intergient.com",
    "nutaku.net",
    "ex.co",
    "spotxchange.com",
    "spotx.tv",
    "teads.tv",
    "lijit.com",
    "sovrn.com",
    "yieldlab.net",
    "33across.com",
    "sharethrough.com",
    "inmobi.com",
    "indexww.com",
    "3lift.com",
    "contextweb.com",
    "bidswitch.net",
    "rlcdn.com",
    "bluekai.com",
    "krxd.net",
    "demdex.net",
    "everesttech.net",
    "mathtag.com",
    "mookie1.com",
    "agkn.com",
    "adsrvr.org",
    "imrworldwide.com",
    "nfqd.com",
    "nitropay.com",
    "adskeeper.com",
    "mgid.com",
    "revcontent.com",
)

# Only very specific ad payload paths (avoid blocking first-party /news or /advertise pages).
_AD_PATH_RE = re.compile(
    r"(?:^|/)(?:"
    r"pagead(?:/|\.|$)"
    r"|adsense(?:/|\.|$)"
    r"|googleads(?:/|\.|$)"
    r"|adserver(?:/|\.|$)"
    r"|adframe(?:/|\.|$)"
    r"|gpt/pubads"
    r"|adsbygoogle"
    r")",
    re.I,
)

# Cosmetic hide — common sticky/video promo chrome that uBlock would remove.
# Keep selectors conservative so real article media is not stripped.
_COSMETIC_CSS = """
[id*="google_ads" i],
[id*="div-gpt-ad" i],
[class*="google-ad" i],
[class*="adsbox" i],
[data-ad],
[data-ad-slot],
[aria-label*="advertisement" i],
[aria-label*="Advertisement" i],
iframe[src*="doubleclick"],
iframe[src*="googlesyndication"],
iframe[src*="amazon-adsystem"],
iframe[id*="google_ads" i],
iframe[title*="Advertisement" i],
/* Sticky / interstitial video promo chrome ("Keep Watching", etc.) */
[class*="keep-watching" i],
[class*="KeepWatching" i],
[class*="video-sticky" i],
[class*="sticky-video" i],
[class*="videoSticky" i],
[class*="outstream" i],
[class*="ad-container" i],
[class*="adContainer" i],
[id*="video-ad" i],
[id*="videoAd" i],
[id*="sticky-video" i],
[id*="player-ad" i] {
  display: none !important;
  visibility: hidden !important;
  pointer-events: none !important;
  max-height: 0 !important;
  overflow: hidden !important;
}
"""


def host_is_ad(host: str) -> bool:
    host = (host or "").lower().strip(".")
    if not host:
        return False
    if host.startswith("adservice.google."):
        return True
    for suffix in AD_HOST_SUFFIXES:
        clean = suffix.lower().strip(".")
        if host == clean or host.endswith(f".{clean}"):
            return True
    labels = host.split(".")
    if len(labels) >= 3 and labels[0] in {"ad", "ads", "adservice", "adserver", "adn", "banner"}:
        return True
    return False


def url_is_ad(url: str, *, resource_type: str = "") -> bool:
    try:
        parts = urlsplit(str(url or ""))
    except Exception:
        return False
    host = (parts.hostname or "").lower()
    if host_is_ad(host):
        return True
    # Never block top-level navigations via path heuristics.
    if str(resource_type or "") == "document":
        return False
    return bool(_AD_PATH_RE.search(parts.path or ""))


def install_adblock(context: Any) -> None:
    """Abort ad/tracker network requests and hide common promo chrome."""

    def _handler(route: Any) -> None:
        request = route.request
        url = str(request.url or "")
        rtype = str(getattr(request, "resource_type", "") or "")
        if url_is_ad(url, resource_type=rtype):
            try:
                route.abort()
            except Exception:
                try:
                    route.continue_()
                except Exception:
                    pass
            return
        try:
            route.continue_()
        except Exception:
            pass

    try:
        context.route("**/*", _handler)
        # Cosmetic layer — closer to what uBlock does for sticky video/ad units.
        try:
            css_json = json.dumps(_COSMETIC_CSS)
            context.add_init_script(
                f"""() => {{
  const css = {css_json};
  const inject = () => {{
    if (document.querySelector('style[data-jarvis-adblock]')) return;
    const style = document.createElement('style');
    style.setAttribute('data-jarvis-adblock', '1');
    style.textContent = css;
    const root = document.documentElement || document.head || document.body;
    if (root) root.appendChild(style);
  }};
  inject();
  const mo = new MutationObserver(inject);
  mo.observe(document.documentElement || document, {{ childList: true, subtree: true }});
}}"""
            )
        except Exception as exc:
            logger.debug("Could not install cosmetic adblock CSS: %s", exc)
        logger.debug("Adblock installed on browser context")
    except Exception as exc:
        logger.warning("Could not install adblock: %s", exc)
