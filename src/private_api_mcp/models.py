"""Core data models for private-api-mcp."""

from __future__ import annotations

import re
import time
import uuid
from typing import Any, Optional, Union
from pydantic import BaseModel, Field, model_validator


def make_redacted_preview(value: str) -> str:
    """Generate a safe, redacted preview of a sensitive token or cookie value."""
    if not value:
        return ""
    # If bearer token
    if value.lower().startswith("bearer "):
        token = value[7:].strip()
        if len(token) <= 8:
            return "Bearer ****"
        return f"Bearer {token[:4]}...{token[-4:]}"
    if len(value) <= 8:
        return "****"
    return f"{value[:4]}...{value[-4:]}"


def suggest_env_var_name(name: str, location: str = "header") -> str:
    """Suggest an idiomatic environment variable name based on credential name."""
    clean_name = re.sub(r"[^A-Za-z0-9]+", "_", name).upper().strip("_")
    if clean_name.startswith("X_"):
        clean_name = clean_name[2:]
    if "AUTHORIZATION" in clean_name or "AUTH" in clean_name:
        return "API_AUTH_TOKEN"
    if "COOKIE" in clean_name or location == "cookie":
        return f"API_COOKIE_{clean_name}"
    if clean_name in ("API_KEY", "APIKEY", "KEY"):
        return "API_KEY"
    if "API_KEY" in clean_name or "APIKEY" in clean_name or "TOKEN" in clean_name:
        return f"API_{clean_name}"
    return f"API_HEADER_{clean_name}"


class AuthCredential(BaseModel):
    """An identified authentication credential (header or cookie)."""

    name: str = Field(..., description="Identified header or cookie name (e.g. 'Authorization', 'session_id')")
    value: str = Field(..., description="Full credential secret value")
    redacted_preview: str = Field("", description="Redacted preview safe for display and logging")
    env_var_name: str = Field("", description="Recommended environment variable name (e.g. 'API_AUTH_TOKEN')")
    location: str = Field("header", description="'header' or 'cookie'")

    @model_validator(mode="after")
    def populate_defaults(self) -> AuthCredential:
        if not self.redacted_preview:
            self.redacted_preview = make_redacted_preview(self.value)
        if not self.env_var_name:
            self.env_var_name = suggest_env_var_name(self.name, self.location)
        return self


class CapturedExchange(BaseModel):
    """Represents a single captured HTTP request/response exchange."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), description="Unique exchange identifier")
    method: str = Field(..., description="HTTP request method, e.g. GET, POST")
    url: str = Field(..., description="Full URL of the request")
    normalized_path: str = Field("", description="Normalized path with IDs collapsed, e.g. /api/v1/items/{id}")
    headers: dict[str, str] = Field(default_factory=dict, description="Request headers")
    cookies: dict[str, str] = Field(default_factory=dict, description="Request cookies")
    query_params: dict[str, Any] = Field(default_factory=dict, description="Parsed query parameters")
    request_body: Optional[Union[dict, list, str, bytes, Any]] = Field(
        None, description="Request body payload (parsed JSON or raw text/bytes)"
    )
    response_status: Optional[int] = Field(None, description="HTTP response status code, e.g. 200, 404")
    response_headers: dict[str, str] = Field(default_factory=dict, description="Response headers")
    response_body: Optional[Union[dict, list, str, bytes, Any]] = Field(
        None, description="Response body payload (parsed JSON or raw text/bytes)"
    )
    timestamp: float = Field(default_factory=time.time, description="Unix timestamp of when the request was made")
    action_label: Optional[str] = Field(
        None, description="Optional action marker label (e.g. 'clicked_search_button')"
    )

    @property
    def request_method(self) -> str:
        """Alias for method."""
        return self.method

    @property
    def is_json_response(self) -> bool:
        """Check whether response is a parsed JSON structure."""
        return isinstance(self.response_body, (dict, list))

    @property
    def is_json_request(self) -> bool:
        """Check whether request is a parsed JSON structure."""
        return isinstance(self.request_body, (dict, list))


class EndpointCluster(BaseModel):
    """A cluster of requests sharing the same signature and inferred schema."""

    signature: str = Field(..., description="Normalized signature, e.g. 'POST /graphql [GetUser]' or 'GET /api/v1/items/{id}'")
    method: str = Field(..., description="HTTP method")
    path_pattern: str = Field(..., description="Normalized path pattern")
    operation_name: Optional[str] = Field(None, description="GraphQL operation name if applicable")
    exchanges: list[CapturedExchange] = Field(default_factory=list, description="Sample exchanges captured for this endpoint")
    request_schema: Optional[dict[str, Any]] = Field(None, description="Inferred JSON Schema for request bodies")
    response_schema: Optional[dict[str, Any]] = Field(None, description="Inferred JSON Schema for response bodies")
    compact_response_fields: list[str] = Field(default_factory=list, description="Compact list of response field dot-paths")
    query_param_names: list[str] = Field(default_factory=list, description="Observed query parameter names")
    header_names: list[str] = Field(default_factory=list, description="Observed request header names")

