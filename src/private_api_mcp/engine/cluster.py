"""Endpoint signature clustering, path normalization, and GraphQL operation extraction."""

from __future__ import annotations

import json
import re
from typing import Any, Optional
from urllib.parse import urlparse

from private_api_mcp.engine.schema_inferrer import (
    extract_compact_fields,
    infer_json_schema,
    merge_schemas,
)
from private_api_mcp.models import CapturedExchange, EndpointCluster

UUID_REGEX = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
INT_REGEX = re.compile(r"^\d+$")
GRAPHQL_OPERATION_REGEX = re.compile(
    r"(?:query|mutation|subscription)\s+([A-Za-z0-9_]+)", re.IGNORECASE
)


def normalize_path(path: str) -> str:
    """Normalize a URL path by collapsing numeric IDs into `{id}` and UUIDs into `{uuid}`.

    Examples:
        /api/v1/users/42/posts/100 -> /api/v1/users/{id}/posts/{id}
        /items/123e4567-e89b-12d3-a456-426614174000 -> /items/{uuid}
    """
    if not path:
        return "/"

    has_leading = path.startswith("/")
    has_trailing = path.endswith("/") and len(path) > 1

    segments = [s for s in path.strip("/").split("/") if s]
    normalized_segments = []

    for seg in segments:
        if INT_REGEX.match(seg):
            normalized_segments.append("{id}")
        elif UUID_REGEX.match(seg):
            normalized_segments.append("{uuid}")
        else:
            normalized_segments.append(seg)

    res = "/".join(normalized_segments)
    if has_leading:
        res = "/" + res
    if has_trailing and not res.endswith("/"):
        res = res + "/"
    return res or "/"


def extract_graphql_operation_name(
    body: Any,
    url_path: str = "",
) -> Optional[str]:
    """Extract GraphQL operationName from request body (dict or raw string) or query text.

    Returns:
        Operation name string (e.g. 'GetUser') or None if not a named GraphQL operation.
    """
    if isinstance(body, str):
        try:
            body = json.loads(body)
        except Exception:
            pass

    if isinstance(body, dict):
        op = body.get("operationName")
        if isinstance(op, str) and op.strip():
            return op.strip()

        # Fallback to inspecting query string
        query = body.get("query")
        if isinstance(query, str):
            match = GRAPHQL_OPERATION_REGEX.search(query)
            if match:
                return match.group(1)

    elif isinstance(body, list) and body:
        # Batched queries
        names = []
        for item in body:
            name = extract_graphql_operation_name(item, url_path)
            if name:
                names.append(name)
        if names:
            return "+".join(names)

    elif isinstance(body, str):
        match = GRAPHQL_OPERATION_REGEX.search(body)
        if match:
            return match.group(1)

    return None


def generate_endpoint_signature(
    method: str,
    path: str,
    operation_name: Optional[str] = None,
) -> str:
    """Generate a unique endpoint signature string.

    Examples:
        POST /graphql [GetUser]
        GET /api/v1/items/{id}
    """
    clean_method = method.upper()
    clean_path = normalize_path(path)
    if operation_name:
        return f"{clean_method} {clean_path} [{operation_name}]"
    return f"{clean_method} {clean_path}"


def cluster_exchanges(
    exchanges: list[CapturedExchange],
) -> dict[str, EndpointCluster]:
    """Group captured exchanges into endpoint clusters and infer schemas."""
    clusters: dict[str, EndpointCluster] = {}

    for ex in exchanges:
        parsed_url = urlparse(ex.url)
        norm_path = normalize_path(parsed_url.path)
        ex.normalized_path = norm_path

        op_name = extract_graphql_operation_name(ex.request_body, parsed_url.path)
        sig = generate_endpoint_signature(ex.method, norm_path, op_name)

        if sig not in clusters:
            clusters[sig] = EndpointCluster(
                signature=sig,
                method=ex.method.upper(),
                path_pattern=norm_path,
                operation_name=op_name,
                exchanges=[],
                query_param_names=[],
                header_names=[],
            )

        cluster = clusters[sig]
        cluster.exchanges.append(ex)

        # Aggregate query parameters
        for qk in ex.query_params.keys():
            if qk not in cluster.query_param_names:
                cluster.query_param_names.append(qk)

        # Aggregate headers
        for hk in ex.headers.keys():
            if hk not in cluster.header_names:
                cluster.header_names.append(hk)

        # Merge request schema
        if ex.is_json_request:
            req_s = infer_json_schema(ex.request_body)
            cluster.request_schema = merge_schemas(cluster.request_schema, req_s)

        # Merge response schema
        if ex.is_json_response:
            resp_s = infer_json_schema(ex.response_body)
            cluster.response_schema = merge_schemas(cluster.response_schema, resp_s)

            # Collect compact response fields
            compact = extract_compact_fields(ex.response_body)
            for f in compact:
                if f not in cluster.compact_response_fields:
                    cluster.compact_response_fields.append(f)

    # Sort aggregated lists for deterministic outputs
    for cluster in clusters.values():
        cluster.query_param_names.sort()
        cluster.header_names.sort()
        cluster.compact_response_fields.sort()

    return clusters

