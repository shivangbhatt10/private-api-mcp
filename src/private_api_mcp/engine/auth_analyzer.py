"""Auth categorization, tracking cookie filtering, and secret credential extraction."""

from __future__ import annotations

import re
from typing import Any, Optional
from urllib.parse import urlparse

from private_api_mcp.models import AuthCredential, CapturedExchange


def parse_cookie_string(cookie_str: str, out_dict: Optional[dict[str, str]] = None) -> dict[str, str]:
    """Parse key=value; key2=val2 string into dictionary."""
    if out_dict is None:
        out_dict = {}
    if not cookie_str:
        return out_dict
    for item in cookie_str.split(";"):
        item = item.strip()
        if not item:
            continue
        if "=" in item:
            k, v = item.split("=", 1)
            out_dict[k.strip()] = v.strip()
        else:
            out_dict[item] = ""
    return out_dict


_parse_cookie_string = parse_cookie_string

# Known tracking / analytics / telemetry cookie name prefixes & patterns
TRACKING_COOKIE_PATTERNS = [
    re.compile(p, re.IGNORECASE)
    for p in (
        r"^_ga",
        r"^_gid",
        r"^_gat",
        r"^_fbp",
        r"^_fbc",
        r"^_clck",
        r"^_clsk",
        r"^ajs_",
        r"^cf_clearance",
        r"^__cf_bm",
        r"^_cfuvid",
        r"^_hj",
        r"^mp_",
        r"^amp_",
        r"^hubspotutk",
        r"^_uetsid",
        r"^_uetvid",
        r"^_pin_unauth",
        r"^_sentry",
        r"^_dd_s",
        r"^_segment",
        r"^intercom-",
        r"^_vwo_",
        r"^optimizely",
        r"^__zlcmid",
        r"^__livechat",
    )
]

# Headers that strictly belong to Auth & Security
AUTH_HEADER_NAMES = {
    "authorization",
    "x-api-key",
    "api-key",
    "apikey",
    "x-auth-token",
    "auth-token",
    "x-access-token",
    "access-token",
    "x-csrf-token",
    "x-xsrf-token",
    "csrf-token",
    "xsrf-token",
    "x-session-id",
    "session-id",
    "proxy-authorization",
    "cookie",
}

# Headers that belong to API Metadata
METADATA_HEADER_NAMES = {
    "content-type",
    "accept",
    "x-requested-with",
    "apollographql-client-name",
    "apollographql-client-version",
    "cache-control",
    "pragma",
    "if-none-match",
    "if-modified-since",
}

# Headers that represent Browser Bloat / Noise
BROWSER_NOISE_HEADER_NAMES = {
    "user-agent",
    "accept-encoding",
    "accept-language",
    "priority",
    "upgrade-insecure-requests",
    "dnt",
    "sec-gpc",
    "connection",
    "keep-alive",
    "host",
    "referer",
    "origin",
}


def is_tracking_cookie(cookie_name: str) -> bool:
    """Check if cookie name matches any known analytics/tracking pattern."""
    clean_name = cookie_name.strip()
    return any(pattern.search(clean_name) for pattern in TRACKING_COOKIE_PATTERNS)


def filter_tracking_cookies(cookies: dict[str, str]) -> dict[str, str]:
    """Filter out known tracking cookies, preserving only session and auth cookies."""
    return {k: v for k, v in cookies.items() if not is_tracking_cookie(k)}


def classify_headers(headers: dict[str, str]) -> dict[str, dict[str, str]]:
    """Categorize headers into Auth/Security, API Metadata, and Browser Bloat.

    Returns:
        dict with keys 'auth', 'metadata', 'browser'.
    """
    classified: dict[str, dict[str, str]] = {
        "auth": {},
        "metadata": {},
        "browser": {},
    }

    for name, value in headers.items():
        lower_name = name.lower().strip()

        # Check for browser noise (sec-*, user-agent, accept-encoding, etc.)
        if lower_name.startswith("sec-ch-") or lower_name.startswith("sec-fetch-") or lower_name in BROWSER_NOISE_HEADER_NAMES:
            classified["browser"][name] = value

        # Check for auth & security
        elif lower_name in AUTH_HEADER_NAMES or any(
            token in lower_name for token in ("auth", "token", "jwt", "csrf", "xsrf", "api-key", "apikey")
        ):
            if lower_name == "cookie":
                # Filter cookies to exclude tracking noise
                cookie_dict: dict[str, str] = {}
                _parse_cookie_string(value, cookie_dict)
                clean_cookies = filter_tracking_cookies(cookie_dict)
                if clean_cookies:
                    cookie_val = "; ".join(f"{k}={v}" for k, v in clean_cookies.items())
                    classified["auth"][name] = cookie_val
            else:
                classified["auth"][name] = value

        # Check for metadata
        elif lower_name in METADATA_HEADER_NAMES or lower_name.startswith("x-"):
            classified["metadata"][name] = value

        # Fallback: treat unrecognized headers as metadata
        else:
            classified["metadata"][name] = value

    return classified


def extract_credentials(exchange: CapturedExchange) -> list[AuthCredential]:
    """Detect and extract Bearer tokens, API keys, CSRF headers, and session cookies.

    Redacts values for safe display/prompts and maps them to environment variable names.
    """
    credentials: list[AuthCredential] = []
    seen_names: set[str] = set()

    # 1. Inspect request headers
    for name, val in exchange.headers.items():
        lower = name.lower()
        if lower in AUTH_HEADER_NAMES or any(
            t in lower for t in ("auth", "token", "jwt", "csrf", "xsrf", "api-key", "apikey", "session")
        ):
            if lower == "cookie":
                continue

            if name not in seen_names and val.strip():
                cred = AuthCredential(
                    name=name,
                    value=val,
                    location="header",
                )
                credentials.append(cred)
                seen_names.add(name)

    # 2. Inspect cookies (filtered for non-tracking cookies)
    cookies_to_inspect: dict[str, str] = dict(exchange.cookies)
    cookie_hdr = exchange.headers.get("cookie") or exchange.headers.get("Cookie")
    if cookie_hdr:
        _parse_cookie_string(cookie_hdr, cookies_to_inspect)

    filtered_cookies = filter_tracking_cookies(cookies_to_inspect)
    for c_name, c_val in filtered_cookies.items():
        if c_name not in seen_names and c_val.strip():
            cred = AuthCredential(
                name=c_name,
                value=c_val,
                location="cookie",
            )
            credentials.append(cred)
            seen_names.add(c_name)

    return credentials


def extract_env_mapping(credentials: list[AuthCredential]) -> dict[str, str]:
    """Map recommended environment variable names to their raw secret values."""
    mapping: dict[str, str] = {}
    for cred in credentials:
        var_name = cred.env_var_name
        # Disambiguate if duplicate env var names occur
        if var_name in mapping and mapping[var_name] != cred.value:
            suffix = re.sub(r"[^A-Za-z0-9]+", "_", cred.name).upper()
            var_name = f"{var_name}_{suffix}"
        mapping[var_name] = cred.value
    return mapping


def generate_env_file_content(env_mapping: dict[str, str]) -> str:
    """Generate the contents of a .env file with actual captured secret values."""
    lines = ["# Auto-generated by private-api-mcp", ""]
    for k, v in sorted(env_mapping.items()):
        # Escape quotes if necessary
        clean_v = v.replace('"', '\\"')
        lines.append(f'{k}="{clean_v}"')
    return "\n".join(lines) + "\n"


def generate_env_example_content(env_mapping: dict[str, str]) -> str:
    """Generate the contents of a .env.example file with blank placeholders."""
    lines = [
        "# Auto-generated .env.example template for private-api-mcp",
        "# Copy this file to .env and populate with your credentials",
        "",
    ]
    for k in sorted(env_mapping.keys()):
        lines.append(f'{k}=""')
    return "\n".join(lines) + "\n"
