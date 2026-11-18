"""CDP network listener using Playwright for live browser traffic capture."""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
from pathlib import Path
import time
from typing import Any, Optional, Union
from urllib.parse import parse_qsl, urlparse

import httpx

try:
    from playwright.async_api import (
        Browser,
        BrowserContext,
        Page,
        Playwright,
        Response,
        async_playwright,
    )
except ImportError:  # pragma: no cover
    Browser = Any  # type: ignore
    BrowserContext = Any  # type: ignore
    Page = Any  # type: ignore
    Playwright = Any  # type: ignore
    Response = Any  # type: ignore
    async_playwright = Any  # type: ignore

from private_api_mcp.capture.filters import should_capture_request
from private_api_mcp.capture.importers import _parse_cookie_string
from private_api_mcp.capture.session import CaptureSession
from private_api_mcp.engine.cluster import normalize_path
from private_api_mcp.models import CapturedExchange

logger = logging.getLogger(__name__)


class CDPNetworkListener:
    """Wraps Playwright to attach or launch a browser with CDP network interception."""

    def __init__(self, session: Optional[CaptureSession] = None) -> None:
        self.session = session if session is not None else CaptureSession()
        self.playwright: Optional[Playwright] = None
        self.browser: Optional[Browser] = None
        self.context: Optional[BrowserContext] = None
        self._is_external_cdp: bool = False
        self._hooked_pages: set[Any] = set()
        self._is_running: bool = False

    @property
    def is_running(self) -> bool:
        return self._is_running

    async def is_cdp_endpoint_available(self, cdp_url: str = "http://127.0.0.1:9222") -> bool:
        """Check if an active Chrome instance with remote debugging is reachable."""
        endpoint = cdp_url.rstrip("/") + "/json/version"
        try:
            async with httpx.AsyncClient(timeout=1.5) as client:
                res = await client.get(endpoint)
                return res.status_code == 200
        except Exception:
            return False

    async def connect_or_launch(
        self,
        cdp_url: str = "http://127.0.0.1:9222",
        user_data_dir: Union[str, Path] = ".browser_profile",
        headless: bool = False,
    ) -> BrowserContext:
        """Dual Connection Strategy:

        1. Test if cdp_url is responding (/json/version). If yes, attach over CDP.
        2. If not reachable, launch a persistent context with remote debugging enabled.
        """
        if self._is_running and self.context:
            return self.context

        pw_manager = async_playwright()
        self.playwright = await pw_manager.start()

        cdp_active = await self.is_cdp_endpoint_available(cdp_url)

        if cdp_active:
            logger.info("Connecting to existing browser instance at %s", cdp_url)
            self.browser = await self.playwright.chromium.connect_over_cdp(cdp_url)
            existing_contexts = self.browser.contexts
            if existing_contexts:
                self.context = existing_contexts[0]
            else:
                self.context = await self.browser.new_context()
            self._is_external_cdp = True
        else:
            logger.info(
                "Launching dedicated persistent browser context at %s (headless=%s)",
                user_data_dir,
                headless,
            )
            data_dir = Path(user_data_dir).resolve()
            data_dir.mkdir(parents=True, exist_ok=True)

            self.context = await self.playwright.chromium.launch_persistent_context(
                str(data_dir),
                headless=headless,
                args=[
                    "--remote-debugging-port=9222",
                    "--disable-blink-features=AutomationControlled",
                ],
            )
            self._is_external_cdp = False

        self._hook_context(self.context)
        self._is_running = True
        return self.context

    def _hook_context(self, context: BrowserContext) -> None:
        """Hook existing pages and any future tabs opened in the context."""
        for page in context.pages:
            self._hook_page(page)

        context.on("page", lambda new_page: self._hook_page(new_page))

    def _hook_page(self, page: Page) -> None:
        """Attach network response listeners to a single browser tab."""
        if page in self._hooked_pages:
            return

        self._hooked_pages.add(page)

        # Dispatch response interception safely into the event loop
        page.on(
            "response",
            lambda response: asyncio.create_task(self._safe_handle_response(response)),
        )

        page.on("close", lambda p: self._hooked_pages.discard(p))

    async def _safe_handle_response(self, response: Any) -> None:
        """Wrapper to prevent response handling exceptions from leaking to Playwright."""
        try:
            await self.handle_response(response)
        except Exception as e:
            logger.debug("Error handling response: %s", e)

    async def handle_response(self, response: Any) -> Optional[CapturedExchange]:
        """Process a Playwright response, filter noise, parse bodies, and record to session."""
        try:
            url = getattr(response, "url", "")
            req = getattr(response, "request", None)

            if req is None:
                return None

            method = getattr(req, "method", "GET").upper()
            resource_type = getattr(req, "resource_type", None)

            # 1. Apply noise & telemetry filters
            if not should_capture_request(url=url, method=method, resource_type=resource_type):
                return None

            # 2. Extract request headers
            req_headers: dict[str, str] = {}
            raw_headers = getattr(req, "headers", {})
            if isinstance(raw_headers, dict):
                req_headers = {str(k): str(v) for k, v in raw_headers.items()}

            # 3. Extract request body
            raw_req_body = getattr(req, "post_data", None)
            parsed_req_body: Any = raw_req_body
            if raw_req_body and isinstance(raw_req_body, str):
                try:
                    parsed_req_body = json.loads(raw_req_body)
                except Exception:
                    parsed_req_body = raw_req_body

            # 4. Extract cookies
            cookies: dict[str, str] = {}
            for h_k, h_v in req_headers.items():
                if h_k.lower() == "cookie":
                    _parse_cookie_string(h_v, cookies)

            # 5. Extract query params & normalized path
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

            # 6. Extract response status & headers
            resp_status: Optional[int] = getattr(response, "status", None)
            resp_headers: dict[str, str] = {}
            raw_resp_headers = getattr(response, "headers", {})
            if isinstance(raw_resp_headers, dict):
                resp_headers = {str(k): str(v) for k, v in raw_resp_headers.items()}

            # 7. Extract response body safely (catch aborts/closed streams/redirects)
            parsed_resp_body: Any = None
            body_callable = getattr(response, "body", None)
            if callable(body_callable):
                try:
                    body_val = body_callable()
                    if inspect.isawaitable(body_val):
                        body_val = await body_val

                    if isinstance(body_val, bytes):
                        text = body_val.decode("utf-8", errors="replace")
                        try:
                            parsed_resp_body = json.loads(text)
                        except Exception:
                            parsed_resp_body = text
                    elif isinstance(body_val, str):
                        try:
                            parsed_resp_body = json.loads(body_val)
                        except Exception:
                            parsed_resp_body = body_val
                except Exception:
                    # Gracefully handle failed or aborted response body read
                    parsed_resp_body = None

            exchange = CapturedExchange(
                method=method,
                url=url,
                normalized_path=norm_path,
                headers=req_headers,
                cookies=cookies,
                query_params=query_params,
                request_body=parsed_req_body,
                response_status=resp_status,
                response_headers=resp_headers,
                response_body=parsed_resp_body,
                timestamp=time.time(),
                action_label=self.session.current_action_label,
            )

            self.session.add_exchange(exchange)
            return exchange

        except Exception as e:
            logger.debug("Failed to process network response: %s", e)
            return None

    async def stop(self) -> None:
        """Detach network listeners and close contexts without leaving orphaned processes."""
        self._is_running = False
        self._hooked_pages.clear()

        if self.context:
            try:
                # If launched as persistent context, closing it closes the browser
                if not self._is_external_cdp:
                    await self.context.close()
            except Exception:
                pass
            self.context = None

        if self.browser:
            try:
                # Disconnect CDP connection
                await self.browser.close()
            except Exception:
                pass
            self.browser = None

        if self.playwright:
            try:
                await self.playwright.stop()
            except Exception:
                pass
            self.playwright = None
