"""Lightweight Playwright ad/tracker blocking for browser exploration."""

from __future__ import annotations

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
    r")",
    re.I,
)


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
    """Abort ad/tracker network requests on a Playwright browser context."""

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
        logger.debug("Adblock installed on browser context")
    except Exception as exc:
        logger.warning("Could not install adblock: %s", exc)
