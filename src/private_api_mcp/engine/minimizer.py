"""Live replay minimizer: test-replay endpoints to determine the minimal header and cookie set."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import logging
from typing import Any, Optional
from urllib.parse import urlparse

import httpx

from private_api_mcp.engine.auth_analyzer import (
    classify_headers,
    filter_tracking_cookies,
)
from private_api_mcp.models import CapturedExchange

logger = logging.getLogger(__name__)


@dataclass
class MinimizationResult:
    """Result of replaying and minimizing an endpoint's headers and cookies."""

    minimal_headers: dict[str, str] = field(default_factory=dict)
    minimal_cookies: dict[str, str] = field(default_factory=dict)
    stripped_headers: list[str] = field(default_factory=list)
    is_replayable: bool = False
    status_code: int = 0
    notes: str = ""


def is_safe_read_request(exchange: CapturedExchange) -> bool:
    """Determine if a request is a safe idempotent read (GET/HEAD/OPTIONS or GraphQL query)."""
    method = exchange.method.upper()
    if method in ("GET", "HEAD", "OPTIONS"):
        return True

    # Check for GraphQL read queries over POST
    if method == "POST":
        body = exchange.request_body
        if isinstance(body, dict):
            query = body.get("query", "")
            if isinstance(query, str) and query.strip():
                clean_q = query.strip().lower()
                # If it's a query and not a mutation
                if clean_q.startswith("query") or ("query" in clean_q and "mutation" not in clean_q):
                    return True
        elif isinstance(body, str):
            clean_b = body.strip().lower()
            if "query" in clean_b and "mutation" not in clean_b:
                return True

    return False


def _send_replay(
    client: httpx.Client,
    exchange: CapturedExchange,
    headers: dict[str, str],
    cookies: dict[str, str],
) -> httpx.Response:
    """Execute an HTTP request using the provided headers and cookies."""
    req_json: Any = None
    req_content: Any = None

    if exchange.request_body is not None:
        if isinstance(exchange.request_body, (dict, list)):
            req_json = exchange.request_body
        elif isinstance(exchange.request_body, str):
            req_content = exchange.request_body.encode("utf-8")
        elif isinstance(exchange.request_body, bytes):
            req_content = exchange.request_body

    req_headers = dict(headers)
    if cookies:
        req_headers["Cookie"] = "; ".join(f"{k}={v}" for k, v in cookies.items())

    return client.request(
        method=exchange.method,
        url=exchange.url,
        headers=req_headers,
        json=req_json,
        content=req_content,
    )


def replay_and_minimize(
    exchange: CapturedExchange,
    client: Optional[httpx.Client] = None,
    allow_mutations: bool = False,
) -> MinimizationResult:
    """Replay request with candidate header subsets to find the minimal required set for 2xx.

    Args:
        exchange: The captured request/response exchange.
        client: Optional httpx.Client instance (allows passing mock transports for testing).
        allow_mutations: If False, skips network replay on POST/PUT/DELETE mutations for safety.

    Returns:
        MinimizationResult containing the verified minimal headers, cookies, and notes.
    """
    classified = classify_headers(exchange.headers)
    auth_headers = classified["auth"]
    metadata_headers = classified["metadata"]
    browser_headers = classified["browser"]

    # Rule-based fallback if network replay is not permitted or unsafe
    rule_based_minimal_headers = {**auth_headers, **metadata_headers}
    clean_cookies = filter_tracking_cookies(exchange.cookies)

    # 1. Safety Guard for mutating requests
    if not is_safe_read_request(exchange) and not allow_mutations:
        return MinimizationResult(
            minimal_headers=rule_based_minimal_headers,
            minimal_cookies=clean_cookies,
            stripped_headers=list(browser_headers.keys()),
            is_replayable=False,
            status_code=exchange.response_status or 0,
            notes=(
                f"Skipped network replay for mutating request ({exchange.method}). "
                "Returned rule-based minimal headers. Set allow_mutations=True to force replay."
            ),
        )

    # Helper to execute within client context
    def _execute(c: httpx.Client) -> MinimizationResult:
        # Step 1: Baseline Replay with full headers and cookies
        try:
            baseline_resp = _send_replay(c, exchange, exchange.headers, exchange.cookies)
        except Exception as e:
            return MinimizationResult(
                minimal_headers=rule_based_minimal_headers,
                minimal_cookies=clean_cookies,
                stripped_headers=[],
                is_replayable=False,
                status_code=0,
                notes=f"Baseline replay failed with network error: {e}",
            )

        baseline_status = baseline_resp.status_code
        if not (200 <= baseline_status < 300):
            return MinimizationResult(
                minimal_headers=rule_based_minimal_headers,
                minimal_cookies=clean_cookies,
                stripped_headers=[],
                is_replayable=False,
                status_code=baseline_status,
                notes=f"Baseline replay returned non-2xx status code: {baseline_status}",
            )

        current_headers = dict(exchange.headers)
        current_cookies = dict(exchange.cookies)
        stripped: list[str] = []

        # Step 2: Strip Browser Bloat
        if browser_headers:
            candidate_headers = {**auth_headers, **metadata_headers}
            try:
                strip_resp = _send_replay(c, exchange, candidate_headers, current_cookies)
                if 200 <= strip_resp.status_code < 300:
                    current_headers = candidate_headers
                    stripped.extend(list(browser_headers.keys()))
            except Exception as e:
                logger.debug("Stripping browser headers caused request failure: %s", e)

        # Step 3: Minimize Cookies
        if current_cookies:
            # 3a. Try with only non-tracking cookies
            try:
                cookie_resp = _send_replay(c, exchange, current_headers, clean_cookies)
                if 200 <= cookie_resp.status_code < 300:
                    current_cookies = clean_cookies
            except Exception:
                pass

            # 3b. Try without cookies altogether
            try:
                no_cookie_resp = _send_replay(c, exchange, current_headers, {})
                if 200 <= no_cookie_resp.status_code < 300:
                    current_cookies = {}
            except Exception:
                pass

        return MinimizationResult(
            minimal_headers=current_headers,
            minimal_cookies=current_cookies,
            stripped_headers=stripped,
            is_replayable=True,
            status_code=baseline_status,
            notes="Successfully minimized headers and cookies against live baseline.",
        )

    if client is not None:
        return _execute(client)
    else:
        with httpx.Client(timeout=10.0) as temp_client:
            return _execute(temp_client)
