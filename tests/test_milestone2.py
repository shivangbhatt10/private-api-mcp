"""Unit tests for Milestone 2: CaptureSession, CDP Listener, and Action Markers."""

import asyncio
import json
from pathlib import Path
from typing import Any, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from private_api_mcp.capture.cdp_listener import CDPNetworkListener
from private_api_mcp.capture.session import CaptureSession
from private_api_mcp.models import CapturedExchange


# ============================================================================
# Fakes for Playwright Request and Response
# ============================================================================

class FakeRequest:
    def __init__(
        self,
        method: str = "GET",
        headers: Optional[dict[str, str]] = None,
        post_data: Optional[str] = None,
        resource_type: str = "fetch",
    ) -> None:
        self.method = method
        self.headers = headers or {}
        self.post_data = post_data
        self.resource_type = resource_type


class FakeResponse:
    def __init__(
        self,
        url: str = "https://api.example.com/api/v1/items",
        status: int = 200,
        headers: Optional[dict[str, str]] = None,
        body_bytes: Optional[bytes] = b'{"status": "ok"}',
        body_error: Optional[Exception] = None,
        request: Optional[FakeRequest] = None,
    ) -> None:
        self.url = url
        self.status = status
        self.headers = headers or {"content-type": "application/json"}
        self._body_bytes = body_bytes
        self._body_error = body_error
        self.request = request or FakeRequest()

    async def body(self) -> Optional[bytes]:
        if self._body_error:
            raise self._body_error
        return self._body_bytes


# ============================================================================
# 1. CaptureSession Tests
# ============================================================================

def test_session_ring_buffer_limit():
    session = CaptureSession(max_buffer=5)
    for i in range(10):
        ex = CapturedExchange(
            method="GET",
            url=f"https://api.example.com/items/{i}",
            response_status=200,
        )
        session.add_exchange(ex)

    assert len(session) == 5
    items = session.get_exchanges(limit=10)
    assert len(items) == 5
    # Should keep only the 5 most recent (items 5 to 9)
    assert items[0].url == "https://api.example.com/items/5"
    assert items[-1].url == "https://api.example.com/items/9"


def test_session_action_markers_and_filtering():
    session = CaptureSession()

    # Pre-action requests (ts = 100, 101)
    ex1 = CapturedExchange(
        method="GET",
        url="https://api.example.com/config",
        timestamp=100.0,
    )
    ex2 = CapturedExchange(
        method="POST",
        url="https://auth.example.com/refresh",
        timestamp=101.0,
    )
    session.add_exchange(ex1)
    session.add_exchange(ex2)

    # Mark action
    checkpoint = session.mark_action("click_search_button")
    assert checkpoint["label"] == "click_search_button"
    assert checkpoint["timestamp"] > 0
    mark_ts = checkpoint["timestamp"]

    # Post-action requests
    ex3 = CapturedExchange(
        method="GET",
        url="https://api.example.com/search?q=phone",
        timestamp=mark_ts + 1.0,
    )
    ex4 = CapturedExchange(
        method="POST",
        url="https://api.otherdomain.com/telemetry_allowed",
        timestamp=mark_ts + 2.0,
    )
    session.add_exchange(ex3)
    session.add_exchange(ex4)

    # Automatically assigned active action_label
    assert ex3.action_label == "click_search_button"
    assert ex4.action_label == "click_search_button"

    # Query since mark
    since_exchanges = session.get_exchanges(since_mark="click_search_button")
    assert len(since_exchanges) == 2
    assert ex1 not in since_exchanges
    assert ex2 not in since_exchanges
    assert ex3 in since_exchanges
    assert ex4 in since_exchanges

    # Query with domain filter
    api_exchanges = session.get_exchanges(domain="api.example.com")
    assert len(api_exchanges) == 2
    assert ex1 in api_exchanges
    assert ex3 in api_exchanges

    # Query with method filter
    post_exchanges = session.get_exchanges(method="POST")
    assert len(post_exchanges) == 2
    assert ex2 in post_exchanges
    assert ex4 in post_exchanges

    # Query with limit
    limited = session.get_exchanges(limit=1)
    assert len(limited) == 1
    assert limited[0] == ex4


def test_session_jsonl_serialization(tmp_path: Path):
    session = CaptureSession()
    ex1 = CapturedExchange(
        method="GET",
        url="https://api.example.com/v1/users",
        response_status=200,
        response_body=[{"id": 1}],
    )
    ex2 = CapturedExchange(
        method="POST",
        url="https://api.example.com/v1/orders",
        request_body={"product_id": 99},
        response_status=201,
        response_body={"order_id": "ord_123"},
        action_label="place_order",
    )
    session.add_exchange(ex1)
    session.add_exchange(ex2)

    jsonl_file = tmp_path / "traffic.jsonl"
    count = session.dump_to_jsonl(jsonl_file)
    assert count == 2
    assert jsonl_file.is_file()

    # Load into fresh session
    new_session = CaptureSession()
    loaded = new_session.load_from_jsonl(jsonl_file)
    assert loaded == 2
    assert len(new_session) == 2

    loaded_items = new_session.get_exchanges()
    assert loaded_items[0].method == "GET"
    assert loaded_items[0].url == "https://api.example.com/v1/users"
    assert loaded_items[1].action_label == "place_order"
    assert loaded_items[1].response_body["order_id"] == "ord_123"


def test_session_clear():
    session = CaptureSession()
    session.mark_action("step1")
    session.add_exchange(CapturedExchange(method="GET", url="https://api.example.com/1"))
    assert len(session) == 1
    assert len(session.action_markers) == 1

    session.clear()
    assert len(session) == 0
    assert len(session.action_markers) == 0
    assert session.current_action_label is None


# ============================================================================
# 2. CDP Network Listener Tests
# ============================================================================

def test_listener_drops_static_and_telemetry():
    session = CaptureSession()
    listener = CDPNetworkListener(session=session)

    # 1. Static asset (.css)
    static_resp = FakeResponse(
        url="https://example.com/static/style.css",
        request=FakeRequest(resource_type="stylesheet"),
    )
    res = asyncio.run(listener.handle_response(static_resp))
    assert res is None
    assert len(session) == 0

    # 2. Static script (.js)
    js_resp = FakeResponse(
        url="https://example.com/app.bundle.js?v=2",
        request=FakeRequest(resource_type="script"),
    )
    res = asyncio.run(listener.handle_response(js_resp))
    assert res is None
    assert len(session) == 0

    # 3. Telemetry (Sentry)
    sentry_resp = FakeResponse(
        url="https://o123.ingest.sentry.io/api/42/envelope/",
        request=FakeRequest(method="POST"),
    )
    res = asyncio.run(listener.handle_response(sentry_resp))
    assert res is None
    assert len(session) == 0

    # 4. OPTIONS preflight
    options_resp = FakeResponse(
        url="https://api.example.com/v1/users",
        request=FakeRequest(method="OPTIONS"),
    )
    res = asyncio.run(listener.handle_response(options_resp))
    assert res is None
    assert len(session) == 0


def test_listener_parses_valid_json_request_and_response():
    session = CaptureSession()
    listener = CDPNetworkListener(session=session)
    session.mark_action("fetch_profile")

    req = FakeRequest(
        method="POST",
        headers={
            "Authorization": "Bearer test-jwt-token",
            "Cookie": "session_id=abc_secret_123; tracking=no",
            "Content-Type": "application/json",
        },
        post_data=json.dumps({"filter": "active", "limit": 25}),
        resource_type="fetch",
    )
    resp = FakeResponse(
        url="https://api.example.com/v1/accounts/12345/users?page=1",
        status=200,
        headers={"Content-Type": "application/json; charset=utf-8"},
        body_bytes=json.dumps({"users": [{"id": 1, "username": "admin"}]}).encode("utf-8"),
        request=req,
    )

    exchange = asyncio.run(listener.handle_response(resp))
    assert exchange is not None
    assert exchange.method == "POST"
    assert exchange.url == "https://api.example.com/v1/accounts/12345/users?page=1"
    assert exchange.normalized_path == "/v1/accounts/{id}/users"
    assert exchange.headers["Authorization"] == "Bearer test-jwt-token"
    assert exchange.cookies["session_id"] == "abc_secret_123"
    assert exchange.cookies["tracking"] == "no"
    assert exchange.query_params["page"] == "1"
    assert exchange.request_body == {"filter": "active", "limit": 25}
    assert exchange.response_status == 200
    assert exchange.response_body == {"users": [{"id": 1, "username": "admin"}]}
    assert exchange.action_label == "fetch_profile"

    # Confirmed recorded into session
    assert len(session) == 1
    assert session.get_exchanges()[0] == exchange


def test_listener_handles_aborted_body_gracefully():
    session = CaptureSession()
    listener = CDPNetworkListener(session=session)

    # Response whose body() raises an aborted stream error
    broken_resp = FakeResponse(
        url="https://api.example.com/v1/stream",
        status=200,
        headers={"Content-Type": "application/json"},
        body_error=RuntimeError("Response body is not available: stream closed"),
        request=FakeRequest(method="GET"),
    )

    # Must NOT raise unhandled exception
    exchange = asyncio.run(listener.handle_response(broken_resp))
    assert exchange is not None
    assert exchange.url == "https://api.example.com/v1/stream"
    assert exchange.response_status == 200
    assert exchange.response_body is None  # Handled safely as None
    assert len(session) == 1


# ============================================================================
# 3. Dual Connection Strategy Mock Tests
# ============================================================================

def test_cdp_endpoint_available_mock():
    listener = CDPNetworkListener()

    # 1. When endpoint returns 200
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    with patch("httpx.AsyncClient.get", new_callable=AsyncMock, return_value=mock_resp):
        res = asyncio.run(listener.is_cdp_endpoint_available("http://127.0.0.1:9222"))
        assert res is True

    # 2. When endpoint raises or fails
    with patch("httpx.AsyncClient.get", new_callable=AsyncMock, side_effect=Exception("Connection refused")):
        res = asyncio.run(listener.is_cdp_endpoint_available("http://127.0.0.1:9222"))
        assert res is False


def test_connect_or_launch_attach_cdp():
    session = CaptureSession()
    listener = CDPNetworkListener(session=session)

    mock_page = MagicMock()
    mock_context = MagicMock()
    mock_context.pages = [mock_page]
    mock_browser = MagicMock()
    mock_browser.contexts = [mock_context]

    mock_playwright = MagicMock()
    mock_playwright.chromium.connect_over_cdp = AsyncMock(return_value=mock_browser)

    mock_pw_manager = MagicMock()
    mock_pw_manager.start = AsyncMock(return_value=mock_playwright)

    with patch.object(listener, "is_cdp_endpoint_available", new_callable=AsyncMock, return_value=True), \
         patch("private_api_mcp.capture.cdp_listener.async_playwright", return_value=mock_pw_manager):

        ctx = asyncio.run(listener.connect_or_launch("http://127.0.0.1:9222"))
        assert ctx == mock_context
        assert listener._is_external_cdp is True
        assert listener.is_running is True

        # Verify page listener was attached
        mock_page.on.assert_any_call("response", mock_page.on.call_args_list[0][0][1])

        # Test stop
        asyncio.run(listener.stop())
        assert listener.is_running is False
        mock_browser.close.assert_called_once()
        mock_playwright.stop.assert_called_once()


def test_connect_or_launch_launch_persistent():
    session = CaptureSession()
    listener = CDPNetworkListener(session=session)

    mock_page = MagicMock()
    mock_context = MagicMock()
    mock_context.pages = [mock_page]
    mock_context.close = AsyncMock()

    mock_playwright = MagicMock()
    mock_playwright.chromium.launch_persistent_context = AsyncMock(return_value=mock_context)

    mock_pw_manager = MagicMock()
    mock_pw_manager.start = AsyncMock(return_value=mock_playwright)

    with patch.object(listener, "is_cdp_endpoint_available", new_callable=AsyncMock, return_value=False), \
         patch("private_api_mcp.capture.cdp_listener.async_playwright", return_value=mock_pw_manager):

        ctx = asyncio.run(listener.connect_or_launch("http://127.0.0.1:9222", user_data_dir=".test_profile"))
        assert ctx == mock_context
        assert listener._is_external_cdp is False
        assert listener.is_running is True

        mock_playwright.chromium.launch_persistent_context.assert_called_once()

        # Test stop
        asyncio.run(listener.stop())
        assert listener.is_running is False
        mock_context.close.assert_called_once()
        mock_playwright.stop.assert_called_once()


def test_cli_capture_run(tmp_path: Path):
    from private_api_mcp.capture.cli_capture import run_cli_capture

    mock_page = MagicMock()
    mock_page.goto = AsyncMock()

    mock_context = MagicMock()
    mock_context.pages = [mock_page]

    output_file = tmp_path / "cli_traffic.jsonl"

    with patch("private_api_mcp.capture.cdp_listener.CDPNetworkListener.connect_or_launch", new_callable=AsyncMock, return_value=mock_context), \
         patch("private_api_mcp.capture.cdp_listener.CDPNetworkListener.stop", new_callable=AsyncMock), \
         patch("asyncio.sleep", new_callable=AsyncMock):

        count = asyncio.run(
            run_cli_capture(
                url="https://api.example.com",
                duration=0.1,
                action="test_action",
                output=str(output_file),
            )
        )
        assert count == 0
        mock_page.goto.assert_called_once()

