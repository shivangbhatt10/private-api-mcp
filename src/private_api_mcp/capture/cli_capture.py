"""Lightweight CLI hook to test live browser capture from the command line."""

from __future__ import annotations

import argparse
import asyncio
import sys
from typing import Optional

from private_api_mcp.capture.cdp_listener import CDPNetworkListener
from private_api_mcp.capture.session import CaptureSession


async def run_cli_capture(
    url: Optional[str] = None,
    duration: float = 5.0,
    cdp_url: str = "http://127.0.0.1:9222",
    user_data_dir: str = ".browser_profile",
    headless: bool = False,
    output: Optional[str] = None,
    action: Optional[str] = None,
) -> int:
    session = CaptureSession()
    listener = CDPNetworkListener(session=session)

    print(f"[*] Starting CDP Network Listener...")
    print(f"[*] Target CDP URL: {cdp_url}")
    print(f"[*] Profile Directory: {user_data_dir} (headless={headless})")

    try:
        context = await listener.connect_or_launch(
            cdp_url=cdp_url,
            user_data_dir=user_data_dir,
            headless=headless,
        )

        if action:
            session.mark_action(action)
            print(f"[*] Marked action checkpoint: '{action}'")

        if url:
            print(f"[*] Navigating to: {url}")
            page = context.pages[0] if context.pages else await context.new_page()
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=15000)
            except Exception as e:
                print(f"[!] Warning during navigation: {e}")

        print(f"[*] Capturing network traffic for {duration:.1f}s...")
        await asyncio.sleep(duration)

        exchanges = session.get_exchanges(limit=1000)
        print(f"\n[+] Capture complete! Total captured API calls: {len(exchanges)}")

        for i, ex in enumerate(exchanges, 1):
            status = ex.response_status or "---"
            action_tag = f" [{ex.action_label}]" if ex.action_label else ""
            print(f"  {i:2d}. {ex.method:4s} {status} {ex.url}{action_tag}")

        if output:
            count = session.dump_to_jsonl(output)
            print(f"[+] Saved {count} exchanges to {output}")

        return len(exchanges)

    finally:
        print("[*] Stopping listener and cleaning up...")
        await listener.stop()
        print("[*] Done.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Live browser network traffic capture CLI.")
    parser.add_argument("--url", type=str, default=None, help="Initial URL to navigate to")
    parser.add_argument(
        "--duration", type=float, default=5.0, help="Duration in seconds to capture (default: 5.0)"
    )
    parser.add_argument(
        "--cdp-url", type=str, default="http://127.0.0.1:9222", help="Remote debugging CDP URL"
    )
    parser.add_argument(
        "--user-data-dir", type=str, default=".browser_profile", help="Browser user data directory"
    )
    parser.add_argument(
        "--headless", action="store_true", help="Launch browser in headless mode"
    )
    parser.add_argument(
        "--output", type=str, default=None, help="Optional .jsonl file to save captured exchanges"
    )
    parser.add_argument(
        "--action", type=str, default=None, help="Optional action marker to apply"
    )

    args = parser.parse_args()
    try:
        asyncio.run(
            run_cli_capture(
                url=args.url,
                duration=args.duration,
                cdp_url=args.cdp_url,
                user_data_dir=args.user_data_dir,
                headless=args.headless,
                output=args.output,
                action=args.action,
            )
        )
    except KeyboardInterrupt:
        print("\n[*] Capture cancelled by user.")
        sys.exit(0)


if __name__ == "__main__":
    main()

