# The Private API Reverse-Engineer (private-api-mcp)

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![FastMCP](https://img.shields.io/badge/FastMCP-enabled-green.svg)](https://github.com/jlowin/fastmcp)
[![Tests](https://img.shields.io/badge/tests-71%20passed-brightgreen.svg)](https://github.com/shivangbhatt10/private-api-mcp/actions)
[![Playwright CDP](https://img.shields.io/badge/Playwright-CDP%20Ready-orange.svg)](https://playwright.dev/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

> **Reverse-engineer undocumented, private web APIs directly from observed Chrome network traffic into clean, production-ready, typed Python clients (`httpx` + `Pydantic v2`) and MCP tools.**

`private-api-mcp` is an autonomous reverse-engineering engine and [Model Context Protocol (MCP)](https://modelcontextprotocol.io/) server built for AI agents (OpenClaw, Claude Desktop, Antigravity, Cursor) and human developers. It observes real web application traffic, filters out browser noise and telemetry, discovers and clusters endpoints, extracts and redacts authentication secrets, and test-replays requests to generate minimal, standalone API clients.

---

## Architecture & Pipeline

```
                                  +-----------------------------+
                                  |   Chrome / DevTools (CDP)   |
                                  |     Port 9222 / Profile     |
                                  +--------------+--------------+
                                                 |
                                                 v
+------------------------+        +-----------------------------+        +------------------------+
|   cURL / HAR Imports   | -----> |     CDPNetworkListener      | <----- |     Action Markers     |
+------------------------+        +--------------+--------------+        +------------------------+
                                                 |
                                                 v
                                  +-----------------------------+
                                  |   Noise & Telemetry Filter  | (75+ tracking domains blocked,
                                  |      & Ring Buffer          |  static assets dropped)
                                  +--------------+--------------+
                                                 |
                                                 v
                                  +-----------------------------+
                                  |  Path & Schema Clustering   | ({id}/{uuid} collapsing,
                                  |     & GraphQL Extractor     |  GraphQL collision prevention)
                                  +--------------+--------------+
                                                 |
                                                 v
+------------------------+        +-----------------------------+        +------------------------+
|     Auth Analyzer      | <----> |    Live Replay Minimizer    | ---->  |  Typed Python Codegen  |
| (Redaction & .env map) |        | (Safe 2xx bloat stripping)  |        | (httpx + Pydantic v2)  |
+------------------------+        +--------------+--------------+        +------------------------+
                                                 |
                                                 v
                                  +-----------------------------+
                                  |   FastMCP Server & CLI      |
                                  |  (8 Tools + OpenClaw SKILL) |
                                  +-----------------------------+
```

---

## Key Features

- **Dual-Mode CDP Listener**: Attaches seamlessly to an already-open Chrome session via remote debugging (`http://127.0.0.1:9222`) or launches an isolated, persistent browser profile.
- **Smart Noise & Telemetry Filter**: Built-in blocklist of **75+ analytics, APM, and tracking domains** (Google Analytics, Sentry, Datadog, Segment, Hotjar, Microsoft Clarity, PostHog, Bugsnag, etc.) and auto-drops static extensions (`.js`, `.css`, images, fonts, `.map`).
- **Path Normalization**: Collapses numeric IDs (`/users/123/posts/456` $\to$ `/users/{id}/posts/{id}`) and UUIDs into reusable patterns.
- **GraphQL Collision Prevention**: Extracts GraphQL `operationName` and queries from POST payloads to turn generic `/graphql` collisions into distinct signatures (e.g. `POST /graphql [GetUser]`).
- **Live Replay Minimizer**: Re-executes observed requests via `httpx` to verify that they work outside the browser, stripping out browser bloat headers (`User-Agent`, `Sec-Ch-Ua*`, `Sec-Fetch-*`, `Accept-Encoding`, `Priority`) and tracking cookies while preserving only what is required for a `2xx` response.
- **Mutation Safety Guard**: Automatically halts mutating requests (`POST`/`PUT`/`DELETE`/`PATCH`) from replaying against live production servers unless explicitly authorized (`allow_mutations=True`). Read-only GraphQL queries over POST are safely permitted.
- **Credential Redaction & Local `.env` Export**: Masks sensitive credentials (`Authorization`, `x-api-key`, session cookies) in LLM prompts (`Bearer eyJh...3456`) while safely persisting unredacted secrets to a local `.env` and `.env.example` file.
- **Typed Python Codegen**: Produces AST-validated Python code with `httpx.Client`, Pydantic v2 models for response validation, typed parameters, GraphQL variable interpolation, and smoke-test blocks.
- **FastMCP Server**: 8 token-efficient MCP tools designed to minimize context window consumption when talking to LLMs.
- **OpenClaw Skill Ready**: Comes with an official [SKILL.md](file:///c:/Users/HP/Desktop/private-api-mcp/SKILL.md) ready to drop into OpenClaw and agent harnesses.

---

## Installation

### Prerequisites
- Python 3.10 or higher
- Google Chrome (optional, for live browser observation)

### Setup

```bash
# Clone the repository
git clone https://github.com/shivangbhatt10/private-api-mcp.git
cd private-api-mcp

# Create and activate virtual environment
python -m venv .venv
source .venv/bin/activate  # On Windows: .\.venv\Scripts\activate

# Install dependencies in editable mode
pip install -e .
```

---

## Connecting to MCP Clients

### 1. OpenClaw / Claude Desktop / Antigravity Configuration

Add `private-api-reverse-engineer` to your MCP client configuration (`claude_desktop_config.json` or `openclaw_config.json`):

```json
{
  "mcpServers": {
    "private-api-reverse-engineer": {
      "command": "python",
      "args": ["-m", "private_api_mcp.server"]
    }
  }
}
```

*Note: If using a virtual environment, replace `"python"` with the absolute path to your `.venv/Scripts/python.exe` (Windows) or `.venv/bin/python` (macOS/Linux).*

---

## FastMCP Server Tools

The MCP server provides 8 specialized tools:

| Tool | Parameters | Description |
|---|---|---|
| `start_browser_capture` | `target_url`, `cdp_url`, `headless` | Connects over CDP or launches Chrome; navigates to `target_url`. |
| `mark_user_action` | `label` | Places an action checkpoint (e.g. `"before_search"`) to isolate triggered traffic. |
| `stop_browser_capture` | `keep_browser_open` | Detaches network interception and returns a summary of captured endpoints. |
| `list_captured_endpoints` | `since_mark`, `domain`, `search` | Returns a compact Markdown table of endpoints (`Method`, `Signature`, `Status`, `Action Mark`). |
| `inspect_endpoint` | `endpoint_signature` | Returns query params, masked auth tokens, and compact request/response schemas. |
| `verify_and_minimize` | `endpoint_signature`, `allow_mutations` | Live test-replays outside Chrome, strips browser bloat headers, and confirms replayability. |
| `export_python_client` | `output_py`, `output_env`, `filter_signatures` | Writes the complete typed Python client and `.env` credentials to disk. |
| `import_traffic` | `curl_command`, `har_path` | Ingests offline multi-line cURL commands or Chrome DevTools `.har` export files. |

---

## Interactive 5-Step Workflow for AI Agents

When interacting with an agent equipped with `private-api-mcp` (or using [SKILL.md](file:///c:/Users/HP/Desktop/private-api-mcp/SKILL.md)):

```
[Agent] start_browser_capture(target_url="https://app.example.com")
   │
   ▼
[Agent] mark_user_action("click_search")
   │
   ▼
[Agent] "Please click the search button in Chrome now."
   │
   ▼
[User clicks in Chrome]
   │
   ▼
[Agent] list_captured_endpoints(since_mark="click_search")
   │
   ▼
[Agent] inspect_endpoint("POST /graphql [SearchProducts]")
   │
   ▼
[Agent] verify_and_minimize("POST /graphql [SearchProducts]")
   │
   ▼
[Agent] export_python_client(output_py="client.py", output_env=".env")
```

---

## Unified Command Line Interface (`private-api-re`)

`private-api-mcp` includes a standalone CLI for developers who want to capture and generate clients directly from the terminal without an MCP client:

### 1. Run MCP Server
```bash
private-api-re serve
```

### 2. Live Terminal Capture
```bash
private-api-re capture --url https://news.ycombinator.com --duration 5 --output capture.jsonl
```

### 3. Generate Python Client from Capture File
```bash
private-api-re codegen --input capture.jsonl --output client.py --env .env --client-name MyClient
```

### 4. Convert cURL Snippet Directly to Python Client
```bash
private-api-re import-curl --curl "curl 'https://api.example.com/v1/items/42' -H 'Authorization: Bearer mytoken'" --output client.py --env .env
```

---

## Example Generated Python Client

Below is an example of what `export_python_client` or `codegen` emits:

```python
"""Auto-generated Python client for private API interaction.
Generated by private-api-mcp.
"""

from __future__ import annotations

import os
from typing import Any, Optional, Union
import httpx
from pydantic import BaseModel, ConfigDict, Field


class GetProductsByIdResponse(BaseModel):
    """Inferred API response model."""
    model_config = ConfigDict(populate_by_name=True, extra='ignore')

    id: Optional[int] = None
    title: Optional[str] = None
    price: Optional[float] = None
    in_stock: Optional[bool] = None


class ApiClient:
    """Client for interacting with https://api.myshop.com."""

    def __init__(
        self,
        base_url: str = "https://api.myshop.com",
        timeout: float = 30.0,
        api_auth_token: Optional[str] = None,
    ) -> None:
        self.base_url = base_url.rstrip('/')
        self.headers: dict[str, str] = {
            'Accept': 'application/json',
            'User-Agent': 'private-api-mcp-client/1.0',
        }
        self.cookies: dict[str, str] = {}

        # Resolve API_AUTH_TOKEN
        val_api_auth_token = api_auth_token or os.getenv('API_AUTH_TOKEN')
        if val_api_auth_token:
            self.headers['Authorization'] = val_api_auth_token

        self.client = httpx.Client(
            base_url=self.base_url,
            headers=self.headers,
            cookies=self.cookies,
            timeout=timeout,
        )

    def __enter__(self) -> ApiClient:
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close()

    def close(self) -> None:
        self.client.close()

    def get_products_by_id(self, id: str, include_reviews: Optional[str] = None) -> GetProductsByIdResponse:
        """GET /v1/products/{id}"""
        path = f"/v1/products/{id}"
        params = {k: v for k, v in {'include_reviews': include_reviews}.items() if v is not None}
        response = self.client.request('GET', path, params=params)
        response.raise_for_status()
        data = response.json()
        return GetProductsByIdResponse.model_validate(data)


if __name__ == "__main__":
    print("Initializing ApiClient...")
    with ApiClient() as client:
        print(f"Connected to {client.base_url} successfully")
```

---

## Testing

The project has comprehensive unit and integration tests covering all modules:

```bash
# Run the entire test suite
pytest -v
```

```text
============================= test session starts =============================
platform win32 -- Python 3.10.10, pytest-9.2.0, pluggy-1.7.0
rootdir: C:\Users\HP\Desktop\private-api-mcp
configfile: pyproject.toml
testpaths: tests
plugins: anyio-4.16.0, platformdirs-4.12.4
collected 71 items

tests/test_milestone1.py ...........................................     [ 60%]
tests/test_milestone2.py ...........                                     [ 76%]
tests/test_milestone3.py ........                                        [ 87%]
tests/test_milestone4.py .........                                       [100%]

============================= 71 passed in 1.37s ==============================
```

---

## Project Structure

```
private-api-mcp/
├── SKILL.md                              # OpenClaw agent skill definition
├── pyproject.toml                        # Package configuration & console scripts
├── README.md                             # Project documentation
├── src/
│   └── private_api_mcp/
│       ├── __init__.py
│       ├── models.py                     # CapturedExchange, AuthCredential, EndpointCluster
│       ├── server.py                     # FastMCP stdio server (8 tools)
│       ├── cli.py                        # Unified private-api-re CLI
│       ├── capture/
│       │   ├── __init__.py
│       │   ├── filters.py                # Telemetry blocklist (75+ domains) & static asset filter
│       │   ├── session.py                # Ring buffer session & action markers
│       │   ├── cdp_listener.py           # Playwright CDP dual-mode browser listener
│       │   ├── importers.py              # cURL & HAR offline parsers
│       │   └── cli_capture.py            # Terminal capture runner
│       ├── engine/
│       │   ├── __init__.py
│       │   ├── cluster.py                # Path normalizer ({id}, {uuid}) & GraphQL operation extractor
│       │   ├── schema_inferrer.py        # JSON Schema inferrer & compact field flattener
│       │   ├── auth_analyzer.py          # Header classification, cookie filtering & secret redactor
│       │   └── minimizer.py              # Safe live replay & header/cookie minimizer
│       └── codegen/
│           ├── __init__.py
│           ├── python_httpx.py           # httpx + Pydantic v2 client & .env generator
│           └── cli_codegen.py            # Terminal codegen runner
└── tests/
    ├── test_milestone1.py                # Models, filters, importers, clustering (43 tests)
    ├── test_milestone2.py                # CDP listener, action checkpoints, JSONL (11 tests)
    ├── test_milestone3.py                # Auth analysis, minimizer, Python codegen (8 tests)
    └── test_milestone4.py                # FastMCP server tools, CLI, SKILL.md (9 tests)
```

---

## License

MIT License. See [LICENSE](LICENSE) for details.
