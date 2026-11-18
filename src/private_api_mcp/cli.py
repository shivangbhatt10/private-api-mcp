"""Unified CLI entrypoint for private-api-re."""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
import sys

from private_api_mcp.capture.cli_capture import run_cli_capture
from private_api_mcp.capture.importers import import_from_curl
from private_api_mcp.codegen.cli_codegen import run_cli_codegen
from private_api_mcp.codegen.python_httpx import generate_python_client


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="private-api-re",
        description="The Private API Reverse-Engineer: Capture, filter, minimize, and codegen private APIs.",
    )
    subparsers = parser.add_subparsers(dest="command", help="Available subcommands")

    # 1. serve
    serve_parser = subparsers.add_parser("serve", help="Run the FastMCP server over stdio")

    # 2. capture
    cap_parser = subparsers.add_parser("capture", help="Run live browser traffic capture from the terminal")
    cap_parser.add_argument("--url", type=str, default=None, help="Initial URL to navigate to")
    cap_parser.add_argument(
        "--duration", type=float, default=5.0, help="Duration in seconds to capture (default: 5.0)"
    )
    cap_parser.add_argument(
        "--cdp-url", type=str, default="http://127.0.0.1:9222", help="Remote debugging CDP URL"
    )
    cap_parser.add_argument(
        "--user-data-dir", type=str, default=".browser_profile", help="Browser user data directory"
    )
    cap_parser.add_argument(
        "--headless", action="store_true", help="Launch browser in headless mode"
    )
    cap_parser.add_argument(
        "--output", "-o", type=str, default=None, help="Output .jsonl file to save captured exchanges"
    )
    cap_parser.add_argument(
        "--action", type=str, default=None, help="Optional action marker to apply"
    )

    # 3. codegen
    cg_parser = subparsers.add_parser("codegen", help="Generate Python client from a saved capture JSONL")
    cg_parser.add_argument(
        "--input", "-i", type=str, required=True, help="Input captured .jsonl file"
    )
    cg_parser.add_argument(
        "--output", "-o", type=str, default="client.py", help="Output Python client file"
    )
    cg_parser.add_argument(
        "--env", "-e", type=str, default=".env", help="Output .env credentials file"
    )
    cg_parser.add_argument(
        "--client-name", type=str, default="ApiClient", help="Name of client class"
    )
    cg_parser.add_argument(
        "--base-url", type=str, default=None, help="Base URL (inferred if omitted)"
    )

    # 4. import-curl
    curl_parser = subparsers.add_parser(
        "import-curl", help="Generate a client directly from a pasted cURL snippet"
    )
    curl_parser.add_argument(
        "--curl", "-c", type=str, required=True, help="Raw cURL command snippet"
    )
    curl_parser.add_argument(
        "--output", "-o", type=str, default="client.py", help="Output Python client file"
    )
    curl_parser.add_argument(
        "--env", "-e", type=str, default=".env", help="Output .env credentials file"
    )
    curl_parser.add_argument(
        "--client-name", type=str, default="ApiClient", help="Name of client class"
    )

    return parser


def main(args: list[str] | None = None) -> int:
    parser = build_parser()
    parsed = parser.parse_args(args)

    if not parsed.command:
        parser.print_help()
        return 0

    if parsed.command == "serve":
        from private_api_mcp.server import main as server_main
        server_main()
        return 0

    elif parsed.command == "capture":
        try:
            count = asyncio.run(
                run_cli_capture(
                    url=parsed.url,
                    duration=parsed.duration,
                    cdp_url=parsed.cdp_url,
                    user_data_dir=parsed.user_data_dir,
                    headless=parsed.headless,
                    output=parsed.output,
                    action=parsed.action,
                )
            )
            return 0
        except KeyboardInterrupt:
            print("\n[*] Capture cancelled by user.")
            return 0

    elif parsed.command == "codegen":
        return run_cli_codegen(
            input_file=parsed.input,
            output_file=parsed.output,
            env_file=parsed.env,
            client_name=parsed.client_name,
            base_url=parsed.base_url,
        )

    elif parsed.command == "import-curl":
        exchange = import_from_curl(parsed.curl)
        python_code, env_content = generate_python_client(
            clusters=[exchange],
            client_name=parsed.client_name,
        )

        out_py = Path(parsed.output).resolve()
        out_py.parent.mkdir(parents=True, exist_ok=True)
        out_py.write_text(python_code, encoding="utf-8")

        out_env = Path(parsed.env).resolve()
        out_env.parent.mkdir(parents=True, exist_ok=True)
        out_env.write_text(env_content, encoding="utf-8")

        print(f"[+] Successfully converted cURL into Python client: {out_py}")
        print(f"[+] Saved companion credentials: {out_env}")
        return 0

    return 0


if __name__ == "__main__":
    sys.exit(main())
