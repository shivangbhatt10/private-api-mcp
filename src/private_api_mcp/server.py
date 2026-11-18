"""FastMCP server exposing token-efficient tools for private API reverse-engineering."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

from fastmcp import FastMCP

from private_api_mcp.capture.cdp_listener import CDPNetworkListener
from private_api_mcp.capture.importers import import_from_curl, import_from_har
from private_api_mcp.capture.session import CaptureSession
from private_api_mcp.codegen.python_httpx import generate_python_client
from private_api_mcp.engine.auth_analyzer import (
    classify_headers,
    extract_credentials,
    extract_env_mapping,
    generate_env_example_content,
)
from private_api_mcp.engine.cluster import cluster_exchanges
from private_api_mcp.engine.minimizer import replay_and_minimize
from private_api_mcp.engine.schema_inferrer import extract_compact_fields

# Instantiate FastMCP server
mcp = FastMCP("private-api-reverse-engineer")

# Shared state across MCP tool invocations
_session = CaptureSession(max_buffer=2000)
_listener: Optional[CDPNetworkListener] = None


def get_session() -> CaptureSession:
    """Access the shared capture session instance."""
    return _session


def get_listener() -> CDPNetworkListener:
    """Access or initialize the shared CDP network listener instance."""
    global _listener
    if _listener is None:
        _listener = CDPNetworkListener(session=_session)
    return _listener


# ============================================================================
# Tool 1: start_browser_capture
# ============================================================================
@mcp.tool()
async def start_browser_capture(
    target_url: Optional[str] = None,
    cdp_url: str = "http://127.0.0.1:9222",
    headless: bool = False,
) -> str:
    """Start the CDP network listener. Attaches to an active Chrome instance or launches a persistent profile.

    Args:
        target_url: Optional URL to open in Chrome immediately.
        cdp_url: Chrome remote debugging URL (default: http://127.0.0.1:9222).
        headless: Whether to run headless (default: False for interactive observation).
    """
    listener = get_listener()

    try:
        context = await listener.connect_or_launch(cdp_url=cdp_url, headless=headless)
        mode = "Attached to existing Chrome instance" if listener._is_external_cdp else "Launched persistent profile"

        nav_msg = ""
        if target_url:
            page = context.pages[0] if context.pages else await context.new_page()
            try:
                await page.goto(target_url, wait_until="domcontentloaded", timeout=20000)
                nav_msg = f"\n- **Navigated to**: `{target_url}`"
            except Exception as e:
                nav_msg = f"\n- **Navigation note**: {e}"

        return (
            f"### Browser Capture Active\n\n"
            f"- **Status**: Listening on CDP `{cdp_url}`\n"
            f"- **Mode**: {mode}{nav_msg}\n"
            f"- **Current Session Buffer**: `{len(_session)}` exchanges captured\n\n"
            f"*Tip: Call `mark_user_action('step_label')` right before triggering an action to cleanly isolate requests.*"
        )
    except Exception as e:
        return f"### Failed to Start Browser Capture\n\nError: `{e}`"


# ============================================================================
# Tool 2: mark_user_action
# ============================================================================
@mcp.tool()
def mark_user_action(label: str) -> str:
    """Record a timestamp checkpoint to isolate subsequent API traffic triggered by a user action.

    Args:
        label: Descriptive label for the user action (e.g., 'click_search', 'submit_login').
    """
    if not label or not label.strip():
        return "Error: Action label cannot be empty."

    checkpoint = _session.mark_action(label.strip())
    return (
        f"### Action Checkpoint Marked\n\n"
        f"- **Label**: `{checkpoint['label']}`\n"
        f"- **Timestamp**: `{checkpoint['timestamp']:.2f}`\n\n"
        f"Subsequent captured requests in Chrome will be tagged with `{checkpoint['label']}`. "
        f"You may now instruct the user to perform the action in the browser."
    )


# ============================================================================
# Tool 3: stop_browser_capture
# ============================================================================
@mcp.tool()
async def stop_browser_capture(keep_browser_open: bool = True) -> str:
    """Stop the network listener and return an executive summary of captured endpoints.

    Args:
        keep_browser_open: If True, detaches listeners without closing the user's Chrome window.
    """
    global _listener
    if _listener and _listener.is_running:
        if keep_browser_open and _listener._is_external_cdp:
            # Only detach listeners for external CDP
            _listener._hooked_pages.clear()
            _listener._is_running = False
        else:
            await _listener.stop()

    exchanges = _session.get_exchanges(limit=1000)
    clusters = cluster_exchanges(exchanges)

    domains = set()
    for ex in exchanges:
        host = urlparse(ex.url).hostname
        if host:
            domains.add(host)

    return (
        f"### Browser Capture Stopped\n\n"
        f"- **Total Captured Requests**: `{len(exchanges)}`\n"
        f"- **Unique Endpoints Identified**: `{len(clusters)}`\n"
        f"- **Observed Domains**: {', '.join(f'`{d}`' for d in sorted(domains)) or 'None'}\n\n"
        f"Use `list_captured_endpoints()` to inspect discovered APIs or `export_python_client()` to generate code."
    )


# ============================================================================
# Tool 4: list_captured_endpoints
# ============================================================================
@mcp.tool()
def list_captured_endpoints(
    since_mark: Optional[str] = None,
    domain: Optional[str] = None,
    search: Optional[str] = None,
) -> str:
    """Return a compact, token-efficient Markdown table of unique discovered endpoints.

    Args:
        since_mark: Filter to requests captured after a specific `mark_user_action` checkpoint.
        domain: Filter to a specific hostname (e.g. 'api.example.com').
        search: Filter signatures matching a substring.
    """
    exchanges = _session.get_exchanges(since_mark=since_mark, domain=domain, limit=2000)
    if not exchanges:
        filter_desc = []
        if since_mark:
            filter_desc.append(f"since_mark='{since_mark}'")
        if domain:
            filter_desc.append(f"domain='{domain}'")
        f_str = f" with filters ({', '.join(filter_desc)})" if filter_desc else ""
        return f"No captured API calls found{f_str}."

    clusters = cluster_exchanges(exchanges)
    sorted_signatures = sorted(clusters.keys())

    if search:
        search_lower = search.lower().strip()
        sorted_signatures = [s for s in sorted_signatures if search_lower in s.lower()]

    if not sorted_signatures:
        return f"No endpoints matched search filter '{search}'."

    table_rows = [
        "| Method | Signature | Calls | Status | Content-Type | Action Mark |",
        "|:-------|:----------|:-----:|:------:|:-------------|:------------|",
    ]

    for sig in sorted_signatures:
        cluster = clusters[sig]
        sample = cluster.exchanges[0] if cluster.exchanges else None

        status_str = str(sample.response_status) if sample and sample.response_status else "---"
        ctype = "json"
        if sample and sample.response_headers:
            raw_ctype = sample.response_headers.get("content-type", "")
            if "json" in raw_ctype:
                ctype = "application/json"
            elif raw_ctype:
                ctype = raw_ctype.split(";")[0]

        action_mark = sample.action_label if sample and sample.action_label else "-"
        table_rows.append(
            f"| `{cluster.method}` | `{cluster.signature}` | {len(cluster.exchanges)} | `{status_str}` | `{ctype}` | `{action_mark}` |"
        )

    return (
        f"### Discovered Endpoints ({len(sorted_signatures)})\n\n"
        + "\n".join(table_rows)
        + "\n\n*Call `inspect_endpoint(endpoint_signature)` for payload schemas, or `verify_and_minimize(endpoint_signature)` to test replay outside Chrome.*"
    )


# ============================================================================
# Tool 5: inspect_endpoint
# ============================================================================
@mcp.tool()
def inspect_endpoint(endpoint_signature: str) -> str:
    """Inspect detailed headers, auth credentials, query parameters, and inferred schemas for an endpoint.

    Args:
        endpoint_signature: Signature as shown in list_captured_endpoints (e.g. 'GET /api/v1/items/{id}').
    """
    clusters = cluster_exchanges(_session.get_exchanges(limit=2000))
    cluster = clusters.get(endpoint_signature.strip())

    if not cluster:
        # Try fuzzy match
        for sig, cl in clusters.items():
            if endpoint_signature.strip().lower() in sig.lower():
                cluster = cl
                break

    if not cluster:
        return f"Endpoint `{endpoint_signature}` not found. Call `list_captured_endpoints()` to view available signatures."

    sample = cluster.exchanges[0] if cluster.exchanges else None
    classified = classify_headers(sample.headers if sample else {})

    # Credentials
    creds = extract_credentials(sample) if sample else []
    creds_lines = []
    for c in creds:
        creds_lines.append(f"- **{c.name}** (`{c.location}`): `{c.redacted_preview}` -> `${c.env_var_name}`")

    # Compact response fields
    resp_fields = cluster.compact_response_fields
    fields_preview = "\n".join(f"  - `{f}`" for f in resp_fields[:15])
    if len(resp_fields) > 15:
        fields_preview += f"\n  - *...and {len(resp_fields) - 15} more fields*"

    # Request body summary
    req_body_desc = "None"
    if sample and sample.request_body is not None:
        if isinstance(sample.request_body, dict):
            req_fields = extract_compact_fields(sample.request_body)
            req_body_desc = "\n".join(f"  - `{f}`" for f in req_fields[:10])
        else:
            req_body_desc = f"```\n{str(sample.request_body)[:300]}\n```"

    return (
        f"### Endpoint Inspection: `{cluster.signature}`\n\n"
        f"- **Method**: `{cluster.method}`\n"
        f"- **Path Pattern**: `{cluster.path_pattern}`\n"
        f"- **Sample Observed URL**: `{sample.url if sample else cluster.path_pattern}`\n\n"
        f"#### Identified Credentials\n"
        f"{chr(10).join(creds_lines) if creds_lines else 'None identified.'}\n\n"
        f"#### Query Parameters\n"
        f"{', '.join(f'`{qp}`' for qp in cluster.query_param_names) or 'None'}\n\n"
        f"#### Request Payload Fields\n"
        f"{req_body_desc}\n\n"
        f"#### Compact Response Fields ({len(resp_fields)})\n"
        f"{fields_preview if resp_fields else 'Empty or non-JSON response.'}\n"
    )


# ============================================================================
# Tool 6: verify_and_minimize
# ============================================================================
@mcp.tool()
def verify_and_minimize(endpoint_signature: str, allow_mutations: bool = False) -> str:
    """Test-replay an endpoint outside Chrome via httpx to determine the minimum headers and cookies for 2xx.

    Args:
        endpoint_signature: The signature to verify and minimize.
        allow_mutations: If False, skips network replay on POST/PUT/DELETE for safety.
    """
    clusters = cluster_exchanges(_session.get_exchanges(limit=2000))
    cluster = clusters.get(endpoint_signature.strip())

    if not cluster:
        for sig, cl in clusters.items():
            if endpoint_signature.strip().lower() in sig.lower():
                cluster = cl
                break

    if not cluster or not cluster.exchanges:
        return f"Endpoint `{endpoint_signature}` not found or has no sample exchanges."

    sample = cluster.exchanges[0]
    res = replay_and_minimize(sample, allow_mutations=allow_mutations)

    status_icon = "SUCCESS (2xx)" if res.is_replayable else "SKIPPED / UNVERIFIED"
    min_hdr_lines = [f"- `{k}: {v}`" for k, v in res.minimal_headers.items()]
    stripped_lines = [f"`{k}`" for k in res.stripped_headers]

    return (
        f"### Replay Minimization Result: `{cluster.signature}`\n\n"
        f"- **Status**: `{status_icon}` (HTTP {res.status_code})\n"
        f"- **Replayable Outside Browser**: `{'Yes' if res.is_replayable else 'No'}`\n"
        f"- **Notes**: {res.notes}\n\n"
        f"#### Minimal Required Headers ({len(res.minimal_headers)})\n"
        f"{chr(10).join(min_hdr_lines) if min_hdr_lines else 'None needed.'}\n\n"
        f"#### Stripped Browser Noise ({len(res.stripped_headers)})\n"
        f"{', '.join(stripped_lines) if stripped_lines else 'None stripped.'}\n\n"
        f"#### Retained Cookies\n"
        f"{', '.join(f'`{k}`' for k in res.minimal_cookies.keys()) or 'None needed (zero cookie dependency).'}\n"
    )


# ============================================================================
# Tool 7: export_python_client
# ============================================================================
@mcp.tool()
def export_python_client(
    output_py: str = "client.py",
    output_env: str = ".env",
    filter_signatures: Optional[list[str]] = None,
) -> str:
    """Generate a self-contained, typed Python client (httpx + Pydantic v2) and companion .env on disk.

    Args:
        output_py: Destination file path for generated Python client.
        output_env: Destination file path for generated .env file.
        filter_signatures: Optional list of signatures to generate methods for (defaults to all).
    """
    all_clusters = cluster_exchanges(_session.get_exchanges(limit=2000))
    if not all_clusters:
        return "Cannot export client: No captured traffic available in the current session."

    target_clusters: list[Any] = []
    if filter_signatures:
        for sig in filter_signatures:
            if sig in all_clusters:
                target_clusters.append(all_clusters[sig])
    else:
        target_clusters = list(all_clusters.values())

    if not target_clusters:
        return f"None of the provided signatures matched captured endpoints: {filter_signatures}"

    client_code, env_content = generate_python_client(
        clusters=target_clusters,
        client_name="ApiClient",
    )

    out_py_path = Path(output_py).resolve()
    out_py_path.parent.mkdir(parents=True, exist_ok=True)
    out_py_path.write_text(client_code, encoding="utf-8")

    out_env_path = Path(output_env).resolve()
    out_env_path.parent.mkdir(parents=True, exist_ok=True)
    out_env_path.write_text(env_content, encoding="utf-8")

    # Generate .env.example
    all_creds = []
    for cl in target_clusters:
        for ex in cl.exchanges:
            all_creds.extend(extract_credentials(ex))
    example_content = generate_env_example_content(extract_env_mapping(all_creds))
    example_path = out_env_path.with_name(out_env_path.name + ".example")
    example_path.write_text(example_content, encoding="utf-8")

    return (
        f"### Python Client Exported Successfully\n\n"
        f"- **Client Code**: `{out_py_path}`\n"
        f"- **Credentials**: `{out_env_path}`\n"
        f"- **Template**: `{example_path}`\n"
        f"- **Generated Methods**: {len(target_clusters)} API methods with typed Pydantic models\n\n"
        f"**How to Run Smoke Test**:\n"
        f"```bash\n"
        f"python {out_py_path.name}\n"
        f"```"
    )


# ============================================================================
# Tool 8: import_traffic
# ============================================================================
@mcp.tool()
def import_traffic(
    curl_command: Optional[str] = None,
    har_path: Optional[str] = None,
) -> str:
    """Ingest offline cURL snippets or Chrome DevTools HAR export files into the current session.

    Args:
        curl_command: Raw cURL command string (from Chrome DevTools 'Copy as cURL').
        har_path: Path to exported .har file.
    """
    if curl_command and curl_command.strip():
        try:
            exchange = import_from_curl(curl_command)
            _session.add_exchange(exchange)
            return (
                f"### cURL Imported Successfully\n\n"
                f"- **Method**: `{exchange.method}`\n"
                f"- **URL**: `{exchange.url}`\n"
                f"- **Normalized Path**: `{exchange.normalized_path}`\n"
                f"- **Headers**: `{len(exchange.headers)}`\n"
                f"- **Cookies**: `{len(exchange.cookies)}`\n"
                f"- **Total Session Requests**: `{len(_session)}`\n"
            )
        except Exception as e:
            return f"Failed to import cURL: `{e}`"

    elif har_path and har_path.strip():
        p = Path(har_path.strip())
        if not p.is_file():
            return f"Error: HAR file not found at `{har_path}`."
        try:
            exchanges = import_from_har(p)
            for ex in exchanges:
                _session.add_exchange(ex)
            return (
                f"### HAR Ingested Successfully\n\n"
                f"- **File**: `{p.name}`\n"
                f"- **Filtered API Calls Added**: `{len(exchanges)}` (noise & telemetry discarded)\n"
                f"- **Total Session Requests**: `{len(_session)}`\n"
            )
        except Exception as e:
            return f"Failed to import HAR: `{e}`"

    return "Error: Provide either `curl_command` or `har_path` to import traffic."


def main() -> None:
    """Run the FastMCP server over standard input/output (stdio)."""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()

