"""Unit and integration tests for Milestone 4: FastMCP server, Unified CLI, and SKILL.md."""

import ast
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
import yaml

from private_api_mcp.cli import build_parser, main as cli_main
from private_api_mcp.models import CapturedExchange
from private_api_mcp.server import (
    _session,
    export_python_client,
    import_traffic,
    inspect_endpoint,
    list_captured_endpoints,
    mark_user_action,
    start_browser_capture,
    stop_browser_capture,
    verify_and_minimize,
)


# ============================================================================
# 1. FastMCP Server Tool Tests
# ============================================================================

def test_mcp_mark_user_action():
    _session.clear()
    msg = mark_user_action("init_search")
    assert "Action Checkpoint Marked" in msg
    assert "init_search" in msg
    assert _session.current_action_label == "init_search"

    # Empty mark
    err = mark_user_action("")
    assert "Error" in err


def test_mcp_import_traffic_curl_and_har(tmp_path: Path):
    _session.clear()

    # 1. Import cURL
    curl_cmd = (
        "curl 'https://api.example.com/v1/products/100?sort=price' "
        "-H 'Authorization: Bearer my_jwt_token' "
        "-H 'Accept: application/json'"
    )
    res_curl = import_traffic(curl_command=curl_cmd)
    assert "cURL Imported Successfully" in res_curl
    assert "https://api.example.com/v1/products/100" in res_curl
    assert len(_session) == 1

    # 2. Import HAR
    har_data = {
        "log": {
            "version": "1.2",
            "entries": [
                {
                    "request": {
                        "method": "GET",
                        "url": "https://api.example.com/v1/cart",
                        "headers": [{"name": "Authorization", "value": "Bearer token"}],
                    },
                    "response": {
                        "status": 200,
                        "headers": [{"name": "Content-Type", "value": "application/json"}],
                        "content": {"text": '{"items": []}'},
                    },
                }
            ],
        }
    }
    har_file = tmp_path / "test.har"
    har_file.write_text(yaml.dump(har_data), encoding="utf-8")
    import json
    har_file.write_text(json.dumps(har_data), encoding="utf-8")

    res_har = import_traffic(har_path=str(har_file))
    assert "HAR Ingested Successfully" in res_har
    assert len(_session) == 2


def test_mcp_list_and_inspect_endpoints():
    _session.clear()
    ex1 = CapturedExchange(
        method="GET",
        url="https://api.example.com/v1/items/42",
        headers={"Authorization": "Bearer tok123", "User-Agent": "Browser"},
        response_status=200,
        response_headers={"Content-Type": "application/json"},
        response_body={"id": 42, "name": "Widget", "price": 9.99},
        action_label="before_click",
    )
    ex2 = CapturedExchange(
        method="POST",
        url="https://api.example.com/graphql",
        headers={"Authorization": "Bearer tok123", "Content-Type": "application/json"},
        request_body={"operationName": "GetProfile", "query": "query GetProfile { me { name } }"},
        response_status=200,
        response_body={"data": {"me": {"name": "Alice"}}},
        action_label="after_click",
    )
    _session.add_exchange(ex1)
    _session.add_exchange(ex2)

    # 1. list_captured_endpoints
    table = list_captured_endpoints()
    assert "Discovered Endpoints (2)" in table
    assert "| `GET` | `GET /v1/items/{id}` |" in table
    assert "| `POST` | `POST /graphql [GetProfile]` |" in table

    # 2. list with filter
    filtered_table = list_captured_endpoints(since_mark="after_click")
    assert "POST /graphql [GetProfile]" in filtered_table

    # 3. inspect_endpoint
    inspection = inspect_endpoint("GET /v1/items/{id}")
    assert "Endpoint Inspection: `GET /v1/items/{id}`" in inspection
    assert "API_AUTH_TOKEN" in inspection
    assert "id: integer" in inspection
    assert "price: number" in inspection


def test_mcp_verify_and_minimize():
    _session.clear()
    ex = CapturedExchange(
        method="GET",
        url="https://api.example.com/v1/profile",
        headers={"Authorization": "Bearer tok123", "User-Agent": "Mozilla/5.0"},
        response_status=200,
    )
    _session.add_exchange(ex)

    msg = verify_and_minimize("GET /v1/profile", allow_mutations=False)
    assert "Replay Minimization Result" in msg
    assert "Minimal Required Headers" in msg


def test_mcp_export_python_client(tmp_path: Path):
    _session.clear()
    ex = CapturedExchange(
        method="GET",
        url="https://api.example.com/v1/users/99",
        headers={"Authorization": "Bearer my_super_token"},
        response_status=200,
        response_body={"id": 99, "username": "bob"},
    )
    _session.add_exchange(ex)

    out_py = tmp_path / "mcp_client.py"
    out_env = tmp_path / "mcp.env"

    res = export_python_client(output_py=str(out_py), output_env=str(out_env))
    assert "Python Client Exported Successfully" in res
    assert out_py.is_file()
    assert out_env.is_file()

    # Verify generated client
    code = out_py.read_text(encoding="utf-8")
    ast.parse(code)
    assert "class ApiClient:" in code
    assert "def get_users_by_id(self, id: str" in code


def test_mcp_start_and_stop_capture():
    mock_context = MagicMock()
    mock_page = MagicMock()
    mock_page.goto = AsyncMock()
    mock_context.pages = [mock_page]

    with patch("private_api_mcp.server.get_listener") as mock_get_listener:
        listener = MagicMock()
        listener._is_external_cdp = True
        listener.is_running = True
        listener.connect_or_launch = AsyncMock(return_value=mock_context)
        listener.stop = AsyncMock()
        mock_get_listener.return_value = listener

        # Start
        start_res = asyncio.run(start_browser_capture(target_url="https://example.com"))
        assert "Browser Capture Active" in start_res
        assert "https://example.com" in start_res

        # Stop
        stop_res = asyncio.run(stop_browser_capture(keep_browser_open=True))
        assert "Browser Capture Stopped" in stop_res


# ============================================================================
# 2. Unified CLI Parser & Execution Tests
# ============================================================================

def test_cli_parser_subcommands():
    parser = build_parser()

    # 1. serve
    args = parser.parse_args(["serve"])
    assert args.command == "serve"

    # 2. capture
    args = parser.parse_args(["capture", "--url", "https://example.com", "--duration", "10", "--headless"])
    assert args.command == "capture"
    assert args.url == "https://example.com"
    assert args.duration == 10.0
    assert args.headless is True

    # 3. codegen
    args = parser.parse_args(["codegen", "-i", "traffic.jsonl", "-o", "out.py", "-e", "creds.env"])
    assert args.command == "codegen"
    assert args.input == "traffic.jsonl"
    assert args.output == "out.py"
    assert args.env == "creds.env"

    # 4. import-curl
    args = parser.parse_args(["import-curl", "--curl", "curl https://api.example.com", "-o", "c.py"])
    assert args.command == "import-curl"
    assert args.curl == "curl https://api.example.com"
    assert args.output == "c.py"


def test_cli_import_curl_command(tmp_path: Path):
    out_client = tmp_path / "cli_curl_client.py"
    out_env = tmp_path / "cli_curl.env"

    curl_snippet = "curl 'https://api.myservice.com/v1/status' -H 'Authorization: Bearer cli_token'"
    exit_code = cli_main(
        [
            "import-curl",
            "--curl",
            curl_snippet,
            "-o",
            str(out_client),
            "-e",
            str(out_env),
            "--client-name",
            "ServiceCliClient",
        ]
    )
    assert exit_code == 0
    assert out_client.is_file()
    assert out_env.is_file()

    code = out_client.read_text(encoding="utf-8")
    ast.parse(code)
    assert "class ServiceCliClient:" in code


# ============================================================================
# 3. OpenClaw SKILL.md Specification Tests
# ============================================================================

def test_skill_md_specification():
    skill_path = Path("SKILL.md")
    assert skill_path.is_file(), "SKILL.md must be present in the project root."

    content = skill_path.read_text(encoding="utf-8")

    # Verify YAML frontmatter
    assert content.startswith("---")
    parts = content.split("---", 2)
    assert len(parts) >= 3, "SKILL.md must have valid frontmatter enclosed in '---'."

    metadata = yaml.safe_load(parts[1])
    assert metadata["name"] == "private-api-reverse-engineer"
    assert "description" in metadata
    assert metadata["version"] == "1.0.0"
    assert metadata["metadata"]["openclaw"]["category"] == "developer-tools"

    # Verify 5-step flow documentation
    body = parts[2]
    assert "Step 1: Connect" in body
    assert "Step 2: Mark Action Checkpoint" in body
    assert "Step 3: Prompt User for Interaction" in body
    assert "Step 4: Filter, Inspect & Minimize" in body
    assert "Step 5: Export Python Client" in body
    assert "Session Expiration & Token Refresh" in body
