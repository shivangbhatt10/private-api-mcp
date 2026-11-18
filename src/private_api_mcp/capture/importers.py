"""Importers for converting cURL commands and HAR export files into CapturedExchange models."""

from __future__ import annotations

import base64
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import shlex
from typing import Any, Union
from urllib.parse import parse_qsl, urlparse

from private_api_mcp.capture.filters import should_capture_request
from private_api_mcp.engine.cluster import normalize_path
from private_api_mcp.models import CapturedExchange


def import_from_curl(curl_command: str) -> CapturedExchange:
    """Parse a multi-line or single-line cURL command snippet into a CapturedExchange.

    Supports:
        - Bash and Windows CMD line continuations (`\\` and `^`)
        - Single/double quoted URLs and bash ANSI-C strings (`$'...'`)
        - -X / --request methods (GET, POST, PUT, DELETE, PATCH, etc.)
        - -H / --header headers
        - -d / --data / --data-raw / --data-binary bodies
        - -b / --cookie cookies
        - -u / --user basic auth credentials
    """
    if not curl_command or not curl_command.strip():
        raise ValueError("curl_command cannot be empty")

    cleaned = curl_command.strip()

    # Normalize line continuations (bash '\' or cmd '^')
    cleaned = re.sub(r"[\^\\\\]\s*\r?\n", " ", cleaned)

    # Handle bash ANSI-C quotes ($'...') by removing the leading dollar sign
    cleaned = re.sub(r"\$'(.*?)'", r"'\1'", cleaned, flags=re.DOTALL)

    # Tokenize arguments
    try:
        tokens = shlex.split(cleaned, posix=True)
    except Exception:
        # Fallback for Windows cmd escaping or malformed quotes
        try:
            tokens = shlex.split(cleaned, posix=False)
        except Exception as e:
            raise ValueError(f"Failed to tokenize cURL command: {e}") from e

    if not tokens:
        raise ValueError("No tokens found in cURL command")

    method: str | None = None
    url: str | None = None
    headers: dict[str, str] = {}
    cookies: dict[str, str] = {}
    raw_body: str | None = None

    idx = 0
    if tokens[0].lower() in ("curl", "curl.exe"):
        idx = 1

    while idx < len(tokens):
        token = tokens[idx]

        if token in ("-X", "--request"):
            idx += 1
            if idx < len(tokens):
                method = tokens[idx].upper()

        elif token in ("-H", "--header"):
            idx += 1
            if idx < len(tokens):
                header_line = tokens[idx]
                if ":" in header_line:
                    h_name, h_val = header_line.split(":", 1)
                    h_name = h_name.strip()
                    h_val = h_val.strip()
                    headers[h_name] = h_val
                    if h_name.lower() == "cookie":
                        _parse_cookie_string(h_val, cookies)

        elif token in ("-b", "--cookie"):
            idx += 1
            if idx < len(tokens):
                cookie_str = tokens[idx]
                _parse_cookie_string(cookie_str, cookies)

        elif token in (
            "-d",
            "--data",
            "--data-raw",
            "--data-binary",
            "--data-ascii",
            "--data-urlencode",
        ):
            idx += 1
            if idx < len(tokens):
                data_val = tokens[idx]
                raw_body = (
                    f"{raw_body}&{data_val}" if raw_body is not None else data_val
                )

        elif token in ("-u", "--user"):
            idx += 1
            if idx < len(tokens):
                user_pass = tokens[idx]
                b64_val = base64.b64encode(user_pass.encode("utf-8")).decode("ascii")
                headers["Authorization"] = f"Basic {b64_val}"

        elif token == "--url":
            idx += 1
            if idx < len(tokens):
                url = tokens[idx]

        elif not token.startswith("-"):
            # Candidate for URL
            if url is None and (
                token.startswith("http://")
                or token.startswith("https://")
                or "://" in token
                or "." in token
            ):
                url = token

        idx += 1

    if not url:
        raise ValueError("Could not find a valid URL in the cURL command")

    if not url.startswith("http://") and not url.startswith("https://"):
        url = "https://" + url

    # Default method: POST if body present, else GET
    if method is None:
        method = "POST" if raw_body is not None else "GET"

    # Parse URL query params
    parsed_url = urlparse(url)
    query_params: dict[str, Any] = {}
    for qk, qv in parse_qsl(parsed_url.query, keep_blank_values=True):
        if qk in query_params:
            if isinstance(query_params[qk], list):
                query_params[qk].append(qv)
            else:
                query_params[qk] = [query_params[qk], qv]
        else:
            query_params[qk] = qv

    norm_path = normalize_path(parsed_url.path)

    # Parse request body (JSON if possible)
    parsed_body: Any = raw_body
    if raw_body:
        try:
            parsed_body = json.loads(raw_body)
        except Exception:
            parsed_body = raw_body

    return CapturedExchange(
        method=method,
        url=url,
        normalized_path=norm_path,
        headers=headers,
        cookies=cookies,
        query_params=query_params,
        request_body=parsed_body,
        response_status=None,
        response_headers={},
        response_body=None,
    )


def import_from_har(
    har_content_or_path: Union[str, bytes, dict, Path],
) -> list[CapturedExchange]:
    """Ingest a Chrome DevTools HAR export file or JSON dict with noise filtering applied.

    Args:
        har_content_or_path: Path to .har file, raw HAR JSON string/bytes, or parsed dict.

    Returns:
        List of CapturedExchange objects corresponding to valid, filtered API calls.
    """
    har_data: dict[str, Any]

    if isinstance(har_content_or_path, dict):
        har_data = har_content_or_path
    elif isinstance(har_content_or_path, (str, Path)):
        p = Path(har_content_or_path)
        if p.is_file():
            with open(p, "r", encoding="utf-8") as f:
                har_data = json.load(f)
        else:
            har_data = json.loads(str(har_content_or_path))
    elif isinstance(har_content_or_path, bytes):
        har_data = json.loads(har_content_or_path.decode("utf-8"))
    else:
        raise TypeError(f"Unsupported HAR input type: {type(har_content_or_path)}")

    log = har_data.get("log", {})
    entries = log.get("entries", [])
    exchanges: list[CapturedExchange] = []

    for entry in entries:
        req = entry.get("request", {})
        url = req.get("url", "")
        method = req.get("method", "GET").upper()
        resource_type = entry.get("_resourceType")

        # Apply noise & telemetry filtering
        if not should_capture_request(url=url, method=method, resource_type=resource_type):
            continue

        # Extract headers
        headers: dict[str, str] = {}
        for h in req.get("headers", []):
            h_name = h.get("name")
            if h_name:
                headers[h_name] = h.get("value", "")

        # Extract cookies
        cookies: dict[str, str] = {}
        for c in req.get("cookies", []):
            c_name = c.get("name")
            if c_name:
                cookies[c_name] = c.get("value", "")

        # Merge headers['cookie'] if present
        for h_name, h_val in headers.items():
            if h_name.lower() == "cookie":
                _parse_cookie_string(h_val, cookies)

        # Extract query params from queryString or url
        parsed_url = urlparse(url)
        query_params: dict[str, Any] = {}
        for qs_item in req.get("queryString", []):
            k = qs_item.get("name")
            v = qs_item.get("value", "")
            if k:
                query_params[k] = v

        if not query_params and parsed_url.query:
            for qk, qv in parse_qsl(parsed_url.query, keep_blank_values=True):
                query_params[qk] = qv

        # Request body
        post_data = req.get("postData", {})
        post_text = post_data.get("text")
        parsed_req_body: Any = post_text
        if post_text:
            try:
                parsed_req_body = json.loads(post_text)
            except Exception:
                parsed_req_body = post_text

        # Response
        resp = entry.get("response", {})
        resp_status = resp.get("status")

        resp_headers: dict[str, str] = {}
        for h in resp.get("headers", []):
            h_name = h.get("name")
            if h_name:
                resp_headers[h_name] = h.get("value", "")

        resp_content = resp.get("content", {})
        resp_text = resp_content.get("text")
        encoding = resp_content.get("encoding")

        if encoding == "base64" and resp_text:
            try:
                decoded_bytes = base64.b64decode(resp_text)
                resp_text = decoded_bytes.decode("utf-8", errors="replace")
            except Exception:
                pass

        parsed_resp_body: Any = resp_text
        if resp_text:
            try:
                parsed_resp_body = json.loads(resp_text)
            except Exception:
                parsed_resp_body = resp_text

        # Timestamp
        timestamp: float
        started_dt = entry.get("startedDateTime")
        if started_dt:
            try:
                # Replace trailing 'Z' if present for ISO parsing in 3.10
                clean_dt = started_dt.replace("Z", "+00:00")
                timestamp = datetime.fromisoformat(clean_dt).timestamp()
            except Exception:
                timestamp = datetime.now(timezone.utc).timestamp()
        else:
            timestamp = datetime.now(timezone.utc).timestamp()

        norm_path = normalize_path(parsed_url.path)

        exchanges.append(
            CapturedExchange(
                method=method,
                url=url,
                normalized_path=norm_path,
                headers=headers,
                cookies=cookies,
                query_params=query_params,
                request_body=parsed_req_body,
                response_status=resp_status,
                response_headers=resp_headers,
                response_body=parsed_resp_body,
                timestamp=timestamp,
            )
        )

    return exchanges


def _parse_cookie_string(cookie_str: str, out_dict: dict[str, str]) -> None:
    """Parse key=value; key2=val2 string into dictionary."""
    if not cookie_str:
        return
    for item in cookie_str.split(";"):
        item = item.strip()
        if not item:
            continue
        if "=" in item:
            k, v = item.split("=", 1)
            out_dict[k.strip()] = v.strip()
        else:
            out_dict[item] = ""

