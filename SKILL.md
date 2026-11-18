---
name: private-api-reverse-engineer
description: Reverse-engineers undocumented private web APIs into clean, production-ready Python clients by observing Chrome network traffic.
version: 1.0.0
metadata:
  openclaw:
    category: developer-tools
    requires:
      python: ">=3.11"
---

# The Private API Reverse-Engineer (private-api-mcp)

This skill enables AI agents to reverse-engineer undocumented, private web APIs directly from observed Chrome browser network traffic and turn them into typed, production-ready Python clients (`httpx` + `Pydantic v2`).

---

## When to Activate This Skill

Activate this skill whenever the user asks to:
- Reverse-engineer an internal or private web application API (e.g., "build a Python client for this dashboard", "automate this web service").
- Capture and inspect network calls made while clicking around a web page in Chrome.
- Convert multi-line cURL commands or HAR files into a clean Python library.
- Eliminate browser bloat headers (user-agent, sec-ch-ua, etc.) to isolate the minimal required auth headers and session cookies.

---

## Interactive 5-Step Workflow

Always guide the user through the following 5-step interactive workflow:

### Step 1: Connect to Chrome
Ask the user for the target web application URL.
Start browser capture via the MCP tool:
```python
start_browser_capture(target_url="https://app.example.com", headless=False)
```
- If an existing Chrome instance is running on port 9222 (`--remote-debugging-port=9222`), the listener attaches over CDP.
- Otherwise, a dedicated persistent browser profile is opened.

### Step 2: Mark Action Checkpoint
Before asking the user to perform an action (e.g. searching, pagination, submitting a form), place an action marker checkpoint:
```python
mark_user_action(label="click_search_button")
```
This partitions the capture timeline so background noise, static assets, and pre-action polling can be filtered out.

### Step 3: Prompt User for Interaction
Instruct the user to perform the action in their browser:
> *"I have armed the network listener. Please click the Search button (or perform the desired action) in Chrome now. Let me know once you're done."*

### Step 4: Filter, Inspect & Minimize
Once the user completes the action, isolate the exact requests triggered:
1. **List captured endpoints**:
   ```python
   list_captured_endpoints(since_mark="click_search_button")
   ```
2. **Inspect the endpoint**:
   ```python
   inspect_endpoint(endpoint_signature="POST /graphql [SearchProducts]")
   ```
3. **Verify and strip bloat via live replay**:
   ```python
   verify_and_minimize(endpoint_signature="POST /graphql [SearchProducts]")
   ```
   *Note: For mutating requests (POST/PUT/DELETE modifying remote data), always ask the user for confirmation before passing `allow_mutations=True`.*

### Step 5: Export Python Client & Smoke Test
Generate the final client and companion `.env` credentials:
```python
export_python_client(output_py="client.py", output_env=".env")
```
Run a quick smoke test to verify connectivity:
```bash
python client.py
```

---

## Offline Importers (No Browser Required)

If the user already has a cURL snippet or Chrome DevTools HAR export:
```python
import_traffic(curl_command="curl 'https://api.example.com/items' -H 'Authorization: Bearer ...'")
# or
import_traffic(har_path="devtools_recording.har")
```
Then proceed directly to `inspect_endpoint` and `export_python_client`.

---

## Session Expiration & Token Refresh

- **Credential Extraction**: Sensitive tokens (`Authorization`, `x-api-key`, `session_id`) are automatically mapped to `.env` variables (e.g. `API_AUTH_TOKEN`, `API_KEY`, `API_COOKIE_SESSION_ID`).
- **Refreshing Expired Sessions**: If an API returns `401 Unauthorized` after some time:
  1. Re-open the browser or log in again.
  2. Call `mark_user_action("session_refresh")`.
  3. Re-export credentials using `export_python_client()`.
  4. The `.env` file will be refreshed with the latest valid credentials without modifying the client code.
