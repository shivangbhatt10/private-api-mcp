"""Unit tests for Milestone 3: Auth Analyzer, Live Replay Minimizer & Python Client Codegen."""

import ast
import json
from pathlib import Path
import httpx
import pytest

from private_api_mcp.codegen.cli_codegen import run_cli_codegen
from private_api_mcp.codegen.python_httpx import generate_python_client
from private_api_mcp.engine.auth_analyzer import (
    classify_headers,
    extract_credentials,
    extract_env_mapping,
    filter_tracking_cookies,
    generate_env_example_content,
    generate_env_file_content,
    is_tracking_cookie,
)
from private_api_mcp.engine.cluster import cluster_exchanges
from private_api_mcp.engine.minimizer import (
    is_safe_read_request,
    replay_and_minimize,
)
from private_api_mcp.models import CapturedExchange


# ============================================================================
# 1. Auth Analyzer & Header Classifier Tests
# ============================================================================

def test_classify_headers():
    headers = {
        "Authorization": "Bearer token123",
        "X-Api-Key": "key_secret_abc",
        "Cookie": "session_id=sess_456; _ga=GA1.2.34567; _gid=GA1.2.9876",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "X-Requested-With": "XMLHttpRequest",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "Sec-Ch-Ua": '"Chromium";v="120"',
        "Sec-Fetch-Mode": "cors",
        "Accept-Encoding": "gzip, deflate, br",
        "Priority": "u=1, i",
    }

    classified = classify_headers(headers)
    auth = classified["auth"]
    metadata = classified["metadata"]
    browser = classified["browser"]

    # Auth headers
    assert "Authorization" in auth
    assert "X-Api-Key" in auth
    assert "Cookie" in auth
    # Tracking cookies (_ga, _gid) must be stripped from the classified Cookie header
    assert "session_id=sess_456" in auth["Cookie"]
    assert "_ga" not in auth["Cookie"]
    assert "_gid" not in auth["Cookie"]

    # Metadata headers
    assert "Content-Type" in metadata
    assert "Accept" in metadata
    assert "X-Requested-With" in metadata

    # Browser Bloat
    assert "User-Agent" in browser
    assert "Sec-Ch-Ua" in browser
    assert "Sec-Fetch-Mode" in browser
    assert "Accept-Encoding" in browser
    assert "Priority" in browser


def test_tracking_cookie_filter():
    cookies = {
        "session_id": "real_auth_cookie",
        "_ga": "GA1.2.1111",
        "_gid": "GA1.2.2222",
        "_fbp": "fb.1.1234",
        "cf_clearance": "cf_secret_clearance",
        "jwt_token": "valid_jwt_token",
        "mp_abc123": "mixpanel_cookie",
    }
    clean = filter_tracking_cookies(cookies)
    assert "session_id" in clean
    assert "jwt_token" in clean
    assert "_ga" not in clean
    assert "_gid" not in clean
    assert "_fbp" not in clean
    assert "cf_clearance" not in clean
    assert "mp_abc123" not in clean


def test_extract_credentials_redaction_and_env():
    ex = CapturedExchange(
        method="GET",
        url="https://api.example.com/v1/profile",
        headers={
            "Authorization": "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.supersecrettoken999",
            "X-Api-Key": "live_api_key_secret_value_12345",
        },
        cookies={
            "session_id": "sid_secret_val_xyz_8888",
            "_ga": "GA1.2.ignore_me",
        },
    )

    creds = extract_credentials(ex)
    assert len(creds) == 3

    # Check redaction: preview must be masked and not expose full raw secret
    for cred in creds:
        assert cred.redacted_preview != cred.value
        assert "..." in cred.redacted_preview or "****" in cred.redacted_preview

    # Check env mapping retains the raw values
    env_mapping = extract_env_mapping(creds)
    assert env_mapping["API_AUTH_TOKEN"] == "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.supersecrettoken999"
    assert env_mapping["API_KEY"] == "live_api_key_secret_value_12345"
    assert env_mapping["API_COOKIE_SESSION_ID"] == "sid_secret_val_xyz_8888"

    # .env contains raw secrets
    env_content = generate_env_file_content(env_mapping)
    assert 'API_AUTH_TOKEN="Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.supersecrettoken999"' in env_content

    # .env.example contains blank placeholders
    example_content = generate_env_example_content(env_mapping)
    assert 'API_AUTH_TOKEN=""' in example_content
    assert 'API_KEY=""' in example_content


# ============================================================================
# 2. Live Replay Minimizer Tests (httpx.MockTransport)
# ============================================================================

def test_replay_minimizer_strips_browser_bloat():
    # Mock server that verifies Authorization is present and returns 200
    def handler(request: httpx.Request) -> httpx.Response:
        auth_hdr = request.headers.get("Authorization")
        if auth_hdr == "Bearer secret-token":
            return httpx.Response(200, json={"status": "authenticated", "user": "alice"})
        return httpx.Response(401, json={"error": "missing or invalid authorization"})

    mock_client = httpx.Client(transport=httpx.MockTransport(handler))

    ex = CapturedExchange(
        method="GET",
        url="https://api.example.com/v1/users/me",
        headers={
            "Authorization": "Bearer secret-token",
            "User-Agent": "Mozilla/5.0 Chrome/120.0.0.0",
            "Sec-Ch-Ua": '"Chromium";v="120"',
            "Accept-Encoding": "gzip, deflate, br",
            "Accept": "application/json",
        },
        cookies={"_ga": "GA1.2.345", "theme": "dark"},
        response_status=200,
    )

    res = replay_and_minimize(ex, client=mock_client)

    assert res.is_replayable is True
    assert res.status_code == 200
    # Must preserve essential auth & accept headers
    assert "Authorization" in res.minimal_headers
    assert "Accept" in res.minimal_headers
    # Must strip browser bloat
    assert "User-Agent" not in res.minimal_headers
    assert "Sec-Ch-Ua" not in res.minimal_headers
    assert "Accept-Encoding" not in res.minimal_headers
    assert "User-Agent" in res.stripped_headers


def test_replay_minimizer_safety_guard_mutations():
    # A mock client that records calls
    mock_calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        mock_calls.append(request)
        return httpx.Response(200, json={"deleted": True})

    mock_client = httpx.Client(transport=httpx.MockTransport(handler))

    # Dangerous POST mutation
    ex = CapturedExchange(
        method="POST",
        url="https://api.example.com/v1/users/delete_all",
        headers={"Authorization": "Bearer test-key", "User-Agent": "Browser"},
        request_body={"all": True},
        response_status=200,
    )

    # 1. Without allow_mutations: Network MUST NOT be called!
    res = replay_and_minimize(ex, client=mock_client, allow_mutations=False)
    assert res.is_replayable is False
    assert len(mock_calls) == 0  # Replay safety guard prevented network execution!
    assert "Skipped network replay for mutating request" in res.notes
    assert "Authorization" in res.minimal_headers
    assert "User-Agent" in res.stripped_headers

    # 2. With allow_mutations=True: Network replay is permitted
    res_allowed = replay_and_minimize(ex, client=mock_client, allow_mutations=True)
    assert res_allowed.is_replayable is True
    assert len(mock_calls) > 0


def test_is_safe_read_request():
    # GET/HEAD are safe
    assert is_safe_read_request(CapturedExchange(method="GET", url="https://a.com")) is True
    assert is_safe_read_request(CapturedExchange(method="HEAD", url="https://a.com")) is True

    # Standard POST is not safe
    assert is_safe_read_request(CapturedExchange(method="POST", url="https://a.com")) is False

    # GraphQL POST read query is safe
    graphql_read = CapturedExchange(
        method="POST",
        url="https://a.com/graphql",
        request_body={"query": "query GetFeed { feed { id } }"},
    )
    assert is_safe_read_request(graphql_read) is True

    # GraphQL POST mutation is not safe
    graphql_mutation = CapturedExchange(
        method="POST",
        url="https://a.com/graphql",
        request_body={"query": "mutation CreatePost { createPost { id } }"},
    )
    assert is_safe_read_request(graphql_mutation) is False


# ============================================================================
# 3. Python Client Codegen Tests
# ============================================================================

def test_generate_python_client():
    ex1 = CapturedExchange(
        method="GET",
        url="https://api.myshop.com/v1/products/42",
        headers={"Authorization": "Bearer my_store_token"},
        query_params={"include_reviews": "true"},
        response_status=200,
        response_body={
            "id": 42,
            "title": "Ergonomic Chair",
            "price": 299.99,
            "in_stock": True,
        },
    )
    ex2 = CapturedExchange(
        method="POST",
        url="https://api.myshop.com/graphql",
        headers={"Authorization": "Bearer my_store_token", "Content-Type": "application/json"},
        request_body={
            "operationName": "SearchCatalog",
            "query": "query SearchCatalog($q: String!) { search(query: $q) { id title } }",
        },
        response_status=200,
        response_body={"data": {"search": [{"id": 1, "title": "Desk"}]}},
    )

    clusters = cluster_exchanges([ex1, ex2])
    python_code, env_content = generate_python_client(
        clusters=list(clusters.values()),
        client_name="MyShopClient",
    )

    # 1. AST Validation: Guarantee zero Python syntax errors
    parsed_ast = ast.parse(python_code)
    assert parsed_ast is not None

    # 2. Check structure
    assert "class MyShopClient:" in python_code
    assert 'base_url: str = "https://api.myshop.com"' in python_code
    assert "httpx.Client" in python_code

    # 3. Check methods
    # Path parameter method
    assert "def get_products_by_id(self, id: str" in python_code
    assert 'path = f"/v1/products/{id}"' in python_code
    assert "include_reviews" in python_code

    # GraphQL method
    assert "def search_catalog(self" in python_code
    assert "query SearchCatalog" in python_code

    # 4. Check Pydantic response models
    assert "class GetProductsByIdResponse(BaseModel):" in python_code
    assert "id: Optional[int] = None" in python_code
    assert "title: Optional[str] = None" in python_code
    assert "price: Optional[float] = None" in python_code
    assert "in_stock: Optional[bool] = None" in python_code

    # 5. Check .env content
    assert 'API_AUTH_TOKEN="Bearer my_store_token"' in env_content


def test_cli_codegen_command(tmp_path: Path):
    # Setup test jsonl file
    ex = CapturedExchange(
        method="GET",
        url="https://api.example.com/items/10",
        headers={"X-Api-Key": "my-secret-key"},
        response_status=200,
        response_body={"id": 10, "name": "Item 10"},
    )
    jsonl_path = tmp_path / "traffic.jsonl"
    jsonl_path.write_text(ex.model_dump_json() + "\n", encoding="utf-8")

    out_client = tmp_path / "generated_client.py"
    out_env = tmp_path / ".env"

    exit_code = run_cli_codegen(
        input_file=str(jsonl_path),
        output_file=str(out_client),
        env_file=str(out_env),
        client_name="GeneratedClient",
    )
    assert exit_code == 0
    assert out_client.is_file()
    assert out_env.is_file()
    assert (tmp_path / ".env.example").is_file()

    # Verify generated client passes ast parse
    code = out_client.read_text(encoding="utf-8")
    ast.parse(code)
    assert "class GeneratedClient:" in code

