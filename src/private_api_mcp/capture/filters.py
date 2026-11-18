"""Noise, static asset, and telemetry filtering rules."""

from __future__ import annotations

from typing import Optional
from urllib.parse import urlparse

# Over 70+ known telemetry, analytics, ad networks, logging, and crash-reporting domains
TELEMETRY_DOMAINS: set[str] = {
    # Google Analytics, Ads & Tag Manager
    "google-analytics.com",
    "analytics.google.com",
    "googletagmanager.com",
    "doubleclick.net",
    "adservice.google.com",
    "pagead2.googlesyndication.com",
    "googleads.g.doubleclick.net",
    # Sentry & Crash reporting
    "sentry.io",
    "ingest.sentry.io",
    "sentry-cdn.com",
    "bugsnag.com",
    "notify.bugsnag.com",
    "rollbar.com",
    "api.rollbar.com",
    "raygun.io",
    "api.raygun.io",
    "honeybadger.io",
    "api.honeybadger.io",
    "airbrake.io",
    "api.airbrake.io",
    "trackjs.com",
    "capture.trackjs.com",
    # Segment & CDPs
    "segment.io",
    "api.segment.io",
    "cdn.segment.com",
    "segment.com",
    "rudderstack.com",
    "dataplane.rudderstack.com",
    "snowplowanalytics.com",
    # Datadog & APM / Observability
    "datadoghq.com",
    "browser-intake-datadoghq.com",
    "browser-intake-datadoghq.eu",
    "datadog.com",
    "newrelic.com",
    "nr-data.net",
    "bam.nr-data.net",
    "dynatrace.com",
    "ruxit.com",
    "appdynamics.com",
    # Session Replay & Heatmaps
    "hotjar.com",
    "static.hotjar.com",
    "script.hotjar.com",
    "in.hotjar.com",
    "clarity.ms",
    "c.clarity.ms",
    "fullstory.com",
    "rs.fullstory.com",
    "logrocket.io",
    "r.logrocket.io",
    "mouseflow.com",
    "tools.mouseflow.com",
    "crazyegg.com",
    "script.crazyegg.com",
    "luckyorange.com",
    "tools.luckyorange.com",
    "inspectlet.com",
    "smartlook.com",
    "rec.smartlook.com",
    # Product Analytics
    "mixpanel.com",
    "api.mixpanel.com",
    "amplitude.com",
    "api.amplitude.com",
    "api2.amplitude.com",
    "heapanalytics.com",
    "cdn.heapanalytics.com",
    "posthog.com",
    "app.posthog.com",
    "us.i.posthog.com",
    "eu.i.posthog.com",
    "pendo.io",
    "data.pendo.io",
    "appcues.com",
    "api.appcues.com",
    "walkme.com",
    "sprig.com",
    "api.sprig.com",
    "usertesting.com",
    # Feature Flags & Experimentation
    "optimizely.com",
    "cdn.optimizely.com",
    "visualwebsiteoptimizer.com",
    "vwo.com",
    "launchdarkly.com",
    "events.launchdarkly.com",
    "split.io",
    "events.split.io",
    # Customer Engagement & Marketing
    "intercom.io",
    "api.intercom.io",
    "widget.intercom.io",
    "crisp.chat",
    "client.crisp.chat",
    "drift.com",
    "js.driftt.com",
    "zdassets.com",
    "zendesk.com",
    "hs-analytics.net",
    "hubspot.com",
    "api.hubspot.com",
    "braze.com",
    "onesignal.com",
    "api.onesignal.com",
    "customer.io",
    "track.customer.io",
    # Mobile / Attribution Tracking
    "branch.io",
    "api.branch.io",
    "api2.branch.io",
    "appsflyer.com",
    "t.appsflyer.com",
    "adjust.com",
    "app.adjust.com",
    "kochava.com",
    "singular.net",
    # Ad Networks & Social Trackers
    "facebook.net",
    "connect.facebook.net",
    "tiktok.com",
    "analytics.tiktok.com",
    "twitter.com",
    "analytics.twitter.com",
    "static.ads-twitter.com",
    "licdn.com",
    "snap.licdn.com",
    "pinterest.com",
    "ct.pinterest.com",
    "criteo.com",
    "static.criteo.net",
    "outbrain.com",
    "widgets.outbrain.com",
    "taboola.com",
    "trc.taboola.com",
    "quantserve.com",
    "quantcast.com",
    "scorecardresearch.com",
    "moatads.com",
    # Performance Monitoring
    "cloudflareinsights.com",
    "pingdom.net",
    "rum-collector.pingdom.net",
    "speedcurve.com",
    "lux.speedcurve.com",
    "go-mpulse.net",
    "soasta.com",
    # Regional
    "mc.yandex.ru",
    "metrika.yandex.ru",
    "hm.baidu.com",
}

# Static file extensions to discard
STATIC_EXTENSIONS: tuple[str, ...] = (
    ".js",
    ".mjs",
    ".cjs",
    ".css",
    ".woff",
    ".woff2",
    ".ttf",
    ".eot",
    ".otf",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".svg",
    ".ico",
    ".webp",
    ".avif",
    ".bmp",
    ".tiff",
    ".map",
    ".mp4",
    ".mp3",
    ".wav",
    ".ogg",
    ".webm",
    ".pdf",
    ".zip",
    ".gz",
    ".tar",
    ".wasm",
)

IGNORED_RESOURCE_TYPES: set[str] = {
    "image",
    "stylesheet",
    "font",
    "media",
    "script",
    "texttrack",
    "manifest",
}

IGNORED_SCHEMES: set[str] = {
    "data",
    "blob",
    "chrome-extension",
    "chrome",
    "javascript",
    "about",
}


def is_telemetry_host(hostname: Optional[str]) -> bool:
    """Check if the given hostname matches any known telemetry or tracking domain."""
    if not hostname:
        return False
    host = hostname.lower().strip()
    if host in TELEMETRY_DOMAINS:
        return True
    return any(host.endswith("." + domain) for domain in TELEMETRY_DOMAINS)


def is_static_extension(path: str) -> bool:
    """Check if path ends with any static asset extension (.js, .css, .png, etc.)."""
    clean_path = path.lower().split("?")[0].split("#")[0]
    return clean_path.endswith(STATIC_EXTENSIONS)


def should_capture_request(
    url: str,
    method: str = "GET",
    resource_type: Optional[str] = None,
) -> bool:
    """Determine whether an HTTP request should be captured or filtered out as noise.

    Args:
        url: Full request URL.
        method: HTTP request method (e.g. GET, POST, OPTIONS).
        resource_type: Browser resource type (e.g. fetch, xhr, stylesheet, image).

    Returns:
        True if the request represents a relevant API call, False if it is noise.
    """
    if not url:
        return False

    # Filter out OPTIONS preflights
    if method and method.upper() == "OPTIONS":
        return False

    # Filter out static resource types
    if resource_type and resource_type.lower() in IGNORED_RESOURCE_TYPES:
        return False

    try:
        parsed = urlparse(url)
    except Exception:
        return False

    # Filter out non-http schemes (e.g. data:, blob:, chrome-extension:)
    if parsed.scheme.lower() in IGNORED_SCHEMES:
        return False

    # Filter out telemetry and analytics domains
    if is_telemetry_host(parsed.hostname):
        return False

    # Filter out static asset extensions in path
    if is_static_extension(parsed.path):
        return False

    return True

