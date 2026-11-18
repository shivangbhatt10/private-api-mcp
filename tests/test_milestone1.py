"""Unit tests for Milestone 1: Models, Filters, Importers & Schema Inferrer."""

import json
from pathlib import Path
import pytest

from private_api_mcp.capture.filters import (
    TELEMETRY_DOMAINS,
    is_static_extension,
    is_telemetry_host,
    should_capture_request,
)
from private_api_mcp.capture.importers import (
    import_from_curl,
    import_from_har,
)
from private_api_mcp.engine.cluster import (
    cluster_exchanges,
    extract_graphql_operation_name,
    generate_endpoint_signature,
    normalize_path,
)
from private_api_mcp.engine.schema_inferrer import (
    extract_compact_fields,
    infer_json_schema,
    merge_schemas,
)
from private_api_mcp.models import (
    AuthCredential,
    CapturedExchange,
    EndpointCluster,
)


# ============================================================================
# 1. Models Tests
# ============================================================================

def test_captured_exchange_defaults_and_properties():
    ex = CapturedExchange(
        method="GET",
        url="https://api.example.com/items?page=1",
        query_params={"page": "1"},
        response_status=200,
        response_body={"items": [{"id": 1, "name": "Widget"}]},
    )
    assert ex.request_method == "GET"
    assert ex.is_json_response is True
    assert ex.is_json_request is False
    assert ex.id is not None
    assert ex.timestamp > 0


def test_auth_credential_redaction_and_env():
    # Bearer token
    cred1 = AuthCredential(
        name="Authorization",
        value="Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.secret123456",
        location="header",
    )
    assert cred1.redacted_preview.startswith("Bearer eyJh...")
    assert cred1.redacted_preview.endswith("3456")
    assert cred1.env_var_name == "API_AUTH_TOKEN"

    # Cookie
    cred2 = AuthCredential(
        name="session_token",
        value="abcd1234efgh5678",
        location="cookie",
    )
    assert cred2.redacted_preview == "abcd...5678"
    assert "COOKIE" in cred2.env_var_name


def test_endpoint_cluster_model():
    cluster = EndpointCluster(
        signature="GET /api/v1/items/{id}",
        method="GET",
        path_pattern="/api/v1/items/{id}",
        query_param_names=["active"],
        header_names=["Authorization"],
    )
    assert cluster.signature == "GET /api/v1/items/{id}"
    assert cluster.exchanges == []


# ============================================================================
# 2. Filters & Telemetry Blocklist Tests
# ============================================================================

def test_telemetry_domain_count():
    # Ensure blocklist contains at least 60+ known telemetry domains
    assert len(TELEMETRY_DOMAINS) >= 60


@pytest.mark.parametrize(
    "host,expected",
    [
        ("google-analytics.com", True),
        ("www.google-analytics.com", True),
        ("analytics.google.com", True),
        ("sentry.io", True),
        ("o12345.ingest.sentry.io", True),
        ("api.segment.io", True),
        ("browser-intake-datadoghq.com", True),
        ("in.hotjar.com", True),
        ("c.clarity.ms", True),
        ("api.mixpanel.com", True),
        ("api.amplitude.com", True),
        ("app.posthog.com", True),
        ("api.rollbar.com", True),
        ("notify.bugsnag.com", True),
        ("api.github.com", False),
        ("my-cool-company.org", False),
        ("internal-api.example.com", False),
    ],
)
def test_is_telemetry_host(host, expected):
    assert is_telemetry_host(host) is expected


@pytest.mark.parametrize(
    "path,expected",
    [
        ("/assets/bundle.js", True),
        ("/assets/bundle.js?v=123", True),
        ("/style/main.css", True),
        ("/images/logo.png", True),
        ("/fonts/font.woff2", True),
        ("/icons/favicon.ico", True),
        ("/assets/app.js.map", True),
        ("/api/v1/users", False),
        ("/api/v1/search?q=test.js", False),  # Query param shouldn't fool path check
    ],
)
def test_static_extensions(path, expected):
    assert is_static_extension(path) is expected


def test_should_capture_request():
    # Legitimate API call
    assert should_capture_request("https://api.example.com/v1/users", method="GET") is True
    assert should_capture_request("https://api.example.com/graphql", method="POST") is True

    # OPTIONS preflight
    assert should_capture_request("https://api.example.com/v1/users", method="OPTIONS") is False

    # Static assets
    assert should_capture_request("https://api.example.com/static/app.js") is False
    assert should_capture_request("https://api.example.com/static/logo.svg") is False
    assert (
        should_capture_request(
            "https://api.example.com/v1/users", method="GET", resource_type="image"
        )
        is False
    )

    # Telemetry
    assert should_capture_request("https://o123.ingest.sentry.io/api/42/envelope/") is False
    assert should_capture_request("https://api.segment.io/v1/t") is False

    # Discard non-http schemes
    assert should_capture_request("data:image/png;base64,iVBORw0KGgo...") is False
    assert should_capture_request("blob:https://example.com/uuid") is False


# ============================================================================
# 3. Importers Tests: cURL
# ============================================================================

def test_import_from_curl_bash_multiline():
    curl_cmd = r"""
    curl 'https://api.example.com/api/v1/items?page=2&sort=desc' \
      -X 'GET' \
      -H 'Accept: application/json' \
      -H 'Authorization: Bearer my-secret-token' \
      -b 'session_id=sess_12345; theme=dark' \
      --compressed
    """
    ex = import_from_curl(curl_cmd)
    assert ex.method == "GET"
    assert ex.url == "https://api.example.com/api/v1/items?page=2&sort=desc"
    assert ex.normalized_path == "/api/v1/items"
    assert ex.headers["Accept"] == "application/json"
    assert ex.headers["Authorization"] == "Bearer my-secret-token"
    assert ex.cookies["session_id"] == "sess_12345"
    assert ex.cookies["theme"] == "dark"
    assert ex.query_params["page"] == "2"
    assert ex.query_params["sort"] == "desc"
    assert ex.request_body is None


def test_import_from_curl_windows_cmd_post_json():
    curl_cmd = (
        'curl "https://api.example.com/v1/orders" ^\n'
        '  -X POST ^\n'
        '  -H "Content-Type: application/json" ^\n'
        '  --data-raw "{\\"item_id\\": 42, \\"quantity\\": 3}"'
    )
    ex = import_from_curl(curl_cmd)
    assert ex.method == "POST"
    assert ex.url == "https://api.example.com/v1/orders"
    assert ex.normalized_path == "/v1/orders"
    assert ex.headers["Content-Type"] == "application/json"
    assert isinstance(ex.request_body, dict)
    assert ex.request_body["item_id"] == 42
    assert ex.request_body["quantity"] == 3


def test_import_from_curl_basic_auth():
    curl_cmd = "curl -u admin:password123 https://api.example.com/admin/status"
    ex = import_from_curl(curl_cmd)
    assert ex.method == "GET"
    assert ex.headers["Authorization"].startswith("Basic ")


def test_import_from_curl_invalid():
    with pytest.raises(ValueError):
        import_from_curl("")
    with pytest.raises(ValueError):
        import_from_curl("curl -H 'Accept: text/plain'")


# ============================================================================
# 4. Importers Tests: HAR
# ============================================================================

def test_import_from_har(tmp_path: Path):
    har_fixture = {
        "log": {
            "version": "1.2",
            "entries": [
                # Entry 1: Legitimate REST API call
                {
                    "startedDateTime": "2026-11-18T10:00:00.000Z",
                    "_resourceType": "fetch",
                    "request": {
                        "method": "GET",
                        "url": "https://api.example.com/v1/users/42/profile",
                        "headers": [
                            {"name": "Authorization", "value": "Bearer token123"},
                            {"name": "Cookie", "value": "auth_sid=sid999"},
                        ],
                        "cookies": [{"name": "auth_sid", "value": "sid999"}],
                        "queryString": [],
                    },
                    "response": {
                        "status": 200,
                        "headers": [{"name": "Content-Type", "value": "application/json"}],
                        "content": {
                            "mimeType": "application/json",
                            "text": json.dumps({"id": 42, "username": "alice", "active": True}),
                        },
                    },
                },
                # Entry 2: Static asset (.js) -> MUST be filtered out
                {
                    "startedDateTime": "2026-11-18T10:00:01.000Z",
                    "_resourceType": "script",
                    "request": {
                        "method": "GET",
                        "url": "https://cdn.example.com/static/vendor.js",
                    },
                    "response": {"status": 200, "content": {"text": "console.log(1);"}},
                },
                # Entry 3: OPTIONS preflight -> MUST be filtered out
                {
                    "startedDateTime": "2026-11-18T10:00:02.000Z",
                    "request": {
                        "method": "OPTIONS",
                        "url": "https://api.example.com/v1/users/42/profile",
                    },
                    "response": {"status": 204},
                },
                # Entry 4: Telemetry (Sentry) -> MUST be filtered out
                {
                    "startedDateTime": "2026-11-18T10:00:03.000Z",
                    "request": {
                        "method": "POST",
                        "url": "https://o999.ingest.sentry.io/api/123/envelope/",
                    },
                    "response": {"status": 200},
                },
                # Entry 5: Legitimate GraphQL call with base64 encoded response
                {
                    "startedDateTime": "2026-11-18T10:00:04.000Z",
                    "_resourceType": "fetch",
                    "request": {
                        "method": "POST",
                        "url": "https://api.example.com/graphql",
                        "headers": [{"name": "Content-Type", "value": "application/json"}],
                        "postData": {
                            "mimeType": "application/json",
                            "text": json.dumps(
                                {
                                    "operationName": "GetProduct",
                                    "query": "query GetProduct { product { id name } }",
                                }
                            ),
                        },
                    },
                    "response": {
                        "status": 200,
                        "headers": [{"name": "Content-Type", "value": "application/json"}],
                        "content": {
                            "mimeType": "application/json",
                            "encoding": "base64",
                            "text": "eyJkYXRhIjogeyJwcm9kdWN0IjogeyJpZCI6ICI5OSIsICJuYW1lIjogIkxhcHRvcCJ9fX0=",  # {"data": {"product": {"id": "99", "name": "Laptop"}}}
                        },
                    },
                },
            ],
        }
    }

    # Test direct dict ingestion
    exchanges = import_from_har(har_fixture)
    assert len(exchanges) == 2

    ex1 = exchanges[0]
    assert ex1.method == "GET"
    assert ex1.url == "https://api.example.com/v1/users/42/profile"
    assert ex1.normalized_path == "/v1/users/{id}/profile"
    assert ex1.headers["Authorization"] == "Bearer token123"
    assert ex1.cookies["auth_sid"] == "sid999"
    assert ex1.response_status == 200
    assert ex1.response_body["username"] == "alice"

    ex2 = exchanges[1]
    assert ex2.method == "POST"
    assert ex2.url == "https://api.example.com/graphql"
    assert ex2.request_body["operationName"] == "GetProduct"
    assert ex2.response_body["data"]["product"]["name"] == "Laptop"

    # Test file-based ingestion
    har_file = tmp_path / "test_export.har"
    har_file.write_text(json.dumps(har_fixture), encoding="utf-8")
    file_exchanges = import_from_har(har_file)
    assert len(file_exchanges) == 2


# ============================================================================
# 5. Path Normalization & Clustering Tests
# ============================================================================

def test_normalize_path():
    assert normalize_path("/api/v1/users/123/posts/456") == "/api/v1/users/{id}/posts/{id}"
    assert normalize_path("/api/v1/users/123/") == "/api/v1/users/{id}/"
    assert (
        normalize_path("/orders/a5e31502-8f5b-48d5-b6d8-8b9a957b4c3e/status")
        == "/orders/{uuid}/status"
    )
    assert normalize_path("/graphql") == "/graphql"
    assert normalize_path("/") == "/"


def test_extract_graphql_operation_name():
    # Direct operationName key
    body1 = {"operationName": "GetUser", "query": "query GetUser { user { id } }"}
    assert extract_graphql_operation_name(body1) == "GetUser"

    # Regex fallback from query string
    body2 = {"query": "mutation CreateComment($text: String!) { addComment(text: $text) { id } }"}
    assert extract_graphql_operation_name(body2) == "CreateComment"

    # String body
    body3 = json.dumps({"operationName": "SearchCatalog"})
    assert extract_graphql_operation_name(body3) == "SearchCatalog"

    # Non-graphql payload
    assert extract_graphql_operation_name({"title": "hello world"}) is None


def test_endpoint_clustering():
    ex1 = CapturedExchange(
        method="GET",
        url="https://api.example.com/api/v1/users/100",
        query_params={"include_details": "true"},
        headers={"Authorization": "Bearer token1"},
        response_status=200,
        response_body={"id": 100, "name": "Bob", "email": "bob@example.com"},
    )
    ex2 = CapturedExchange(
        method="GET",
        url="https://api.example.com/api/v1/users/200",
        query_params={"include_details": "false", "cache": "none"},
        headers={"Authorization": "Bearer token1", "X-Custom": "custom-val"},
        response_status=200,
        response_body={"id": 200, "name": "Charlie", "role": "admin"},
    )
    ex3_graphql_1 = CapturedExchange(
        method="POST",
        url="https://api.example.com/graphql",
        headers={"Content-Type": "application/json"},
        request_body={"operationName": "GetUser", "variables": {"id": 1}},
        response_status=200,
        response_body={"data": {"user": {"id": 1, "name": "Alice"}}},
    )
    ex4_graphql_2 = CapturedExchange(
        method="POST",
        url="https://api.example.com/graphql",
        headers={"Content-Type": "application/json"},
        request_body={"operationName": "GetPosts", "variables": {"limit": 10}},
        response_status=200,
        response_body={"data": {"posts": [{"id": 1, "title": "Hello"}]}},
    )

    clusters = cluster_exchanges([ex1, ex2, ex3_graphql_1, ex4_graphql_2])

    # Should cluster into 3 distinct endpoints:
    # 1. GET /api/v1/users/{id}
    # 2. POST /graphql [GetUser]
    # 3. POST /graphql [GetPosts]
    assert len(clusters) == 3

    user_cluster = clusters["GET /api/v1/users/{id}"]
    assert len(user_cluster.exchanges) == 2
    assert user_cluster.path_pattern == "/api/v1/users/{id}"
    assert "include_details" in user_cluster.query_param_names
    assert "cache" in user_cluster.query_param_names
    assert "Authorization" in user_cluster.header_names
    assert "X-Custom" in user_cluster.header_names

    # Check merged response schema
    schema = user_cluster.response_schema
    assert schema["type"] == "object"
    assert "id" in schema["properties"]
    assert "name" in schema["properties"]
    assert "email" in schema["properties"]
    assert "role" in schema["properties"]
    # Required should be intersection (id, name)
    assert sorted(schema["required"]) == ["id", "name"]

    # Verify GraphQL collision prevention
    assert "POST /graphql [GetUser]" in clusters
    assert "POST /graphql [GetPosts]" in clusters


# ============================================================================
# 6. Schema Inferrer & Compact Fields Tests
# ============================================================================

def test_infer_json_schema_primitives():
    assert infer_json_schema("hello") == {"type": "string"}
    assert infer_json_schema(42) == {"type": "integer"}
    assert infer_json_schema(3.14) == {"type": "number"}
    assert infer_json_schema(True) == {"type": "boolean"}
    assert infer_json_schema(None) == {"type": "null"}


def test_infer_json_schema_nested():
    data = {
        "status": "success",
        "code": 200,
        "payload": {
            "items": [{"id": 1, "label": "One"}, {"id": 2, "label": "Two"}],
            "total": 2,
        },
    }
    schema = infer_json_schema(data)
    assert schema["type"] == "object"
    assert schema["properties"]["status"]["type"] == "string"
    assert schema["properties"]["code"]["type"] == "integer"
    assert schema["properties"]["payload"]["type"] == "object"
    assert schema["properties"]["payload"]["properties"]["items"]["type"] == "array"
    items_schema = schema["properties"]["payload"]["properties"]["items"]["items"]
    assert items_schema["type"] == "object"
    assert "id" in items_schema["properties"]


def test_merge_schemas():
    s1 = {
        "type": "object",
        "properties": {"a": {"type": "string"}, "b": {"type": "integer"}},
        "required": ["a", "b"],
    }
    s2 = {
        "type": "object",
        "properties": {"b": {"type": "integer"}, "c": {"type": "boolean"}},
        "required": ["b", "c"],
    }
    merged = merge_schemas(s1, s2)
    assert merged["type"] == "object"
    assert "a" in merged["properties"]
    assert "b" in merged["properties"]
    assert "c" in merged["properties"]
    # Only 'b' is in both
    assert merged["required"] == ["b"]


def test_extract_compact_fields():
    data = {
        "user": {
            "id": 123,
            "name": "Alice",
            "tags": ["admin", "staff"],
            "address": {"city": "New York", "zip": "10001"},
        },
        "orders": [{"order_id": "ord_1", "amount": 99.5}],
        "is_active": True,
    }
    compact = extract_compact_fields(data)
    assert "is_active: boolean" in compact
    assert "user.id: integer" in compact
    assert "user.name: string" in compact
    assert "user.tags[]: string" in compact
    assert "user.address.city: string" in compact
    assert "orders[].order_id: string" in compact
    assert "orders[].amount: number" in compact

