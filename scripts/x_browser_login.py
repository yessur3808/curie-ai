"""Run on Curie's server desktop: python -m scripts.x_browser_login --owner-id ID --handle HANDLE."""

import argparse
import asyncio

from scripts.ai_scout_run import load_instance_environment


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--owner-id", required=True)
    parser.add_argument("--handle", required=True)
    parser.add_argument(
        "--verify-existing",
        action="store_true",
        help="Verify and enroll an already signed-in server profile without a login prompt",
    )
    parser.add_argument(
        "--headed",
        action="store_true",
        help="Verify the existing profile in normal visible Chrome/Firefox on the server desktop",
    )
    args = parser.parse_args()
    load_instance_environment()
    from connectors.x_browser import (
        BrowserAccessError,
        enroll_existing_session,
        enroll_session,
    )

    try:
        handle = args.handle.removeprefix("@")
        if args.verify_existing:
            asyncio.run(
                enroll_existing_session(args.owner_id, handle, headless=not args.headed)
            )
        else:
            asyncio.run(enroll_session(args.owner_id, handle))
        if args.verify_existing:
            print("Curie's existing server browser session is verified and enrolled.")
    except KeyboardInterrupt:
        print("\nLogin cancelled. Curie's browser profile is retained.")
    except BrowserAccessError as exc:
        raise SystemExit(f"Login not enrolled: {exc.code}") from None


if __name__ == "__main__":
    main()
