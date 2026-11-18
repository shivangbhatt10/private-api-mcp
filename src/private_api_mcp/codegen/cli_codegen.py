"""CLI command for generating a Python client and .env from a captured JSONL file."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

from private_api_mcp.capture.session import CaptureSession
from private_api_mcp.codegen.python_httpx import generate_python_client
from private_api_mcp.engine.auth_analyzer import (
    extract_credentials,
    extract_env_mapping,
    generate_env_example_content,
)


def run_cli_codegen(
    input_file: str,
    output_file: str = "client.py",
    env_file: str = ".env",
    client_name: str = "ApiClient",
    base_url: str | None = None,
) -> int:
    input_path = Path(input_file)
    if not input_path.is_file():
        print(f"[!] Error: Input file '{input_file}' not found.")
        return 1

    session = CaptureSession()
    loaded_count = session.load_from_jsonl(input_path)
    if loaded_count == 0:
        print(f"[!] Warning: No exchanges loaded from '{input_file}'.")
        return 1

    exchanges = session.get_exchanges(limit=10000)
    print(f"[*] Loaded {len(exchanges)} exchanges from '{input_file}'")

    # Generate Python code and .env content
    python_code, env_content = generate_python_client(
        clusters=exchanges,
        client_name=client_name,
        base_url=base_url,
    )

    # Write Python client code
    out_path = Path(output_file)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(python_code, encoding="utf-8")
    print(f"[+] Generated Python client: {out_path.resolve()}")

    # Write .env
    env_path = Path(env_file)
    env_path.parent.mkdir(parents=True, exist_ok=True)
    env_path.write_text(env_content, encoding="utf-8")
    print(f"[+] Generated credentials file: {env_path.resolve()}")

    # Write .env.example
    all_creds = []
    for ex in exchanges:
        all_creds.extend(extract_credentials(ex))
    env_mapping = extract_env_mapping(all_creds)
    example_content = generate_env_example_content(env_mapping)

    example_path = env_path.with_name(env_path.name + ".example")
    example_path.write_text(example_content, encoding="utf-8")
    print(f"[+] Generated credentials template: {example_path.resolve()}")

    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate typed Python client (httpx + Pydantic) from captured network traffic."
    )
    parser.add_argument(
        "--input", "-i", type=str, required=True, help="Input captured .jsonl file"
    )
    parser.add_argument(
        "--output", "-o", type=str, default="client.py", help="Output Python client file"
    )
    parser.add_argument(
        "--env", "-e", type=str, default=".env", help="Output .env credentials file"
    )
    parser.add_argument(
        "--client-name", type=str, default="ApiClient", help="Name of client class"
    )
    parser.add_argument(
        "--base-url", type=str, default=None, help="Base URL (inferred if omitted)"
    )

    args = parser.parse_args()
    code = run_cli_codegen(
        input_file=args.input,
        output_file=args.output,
        env_file=args.env,
        client_name=args.client_name,
        base_url=args.base_url,
    )
    sys.exit(code)


if __name__ == "__main__":
    main()

