"""Run on Curie's server desktop: python -m scripts.x_browser_login --owner-id ID --handle HANDLE."""

import argparse
import asyncio

from dotenv import load_dotenv


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--owner-id", required=True)
    parser.add_argument("--handle", required=True)
    parser.add_argument(
        "--verify-existing",
        action="store_true",
        help="Verify and enroll an already signed-in server profile without a login prompt",
    )
    args = parser.parse_args()
    load_dotenv()
    from connectors.x_browser import (
        BrowserAccessError,
        enroll_existing_session,
        enroll_session,
    )

    try:
        enroll = enroll_existing_session if args.verify_existing else enroll_session
        asyncio.run(enroll(args.owner_id, args.handle.removeprefix("@")))
        if args.verify_existing:
            print("Curie's existing server browser session is verified and enrolled.")
    except KeyboardInterrupt:
        print("\nLogin cancelled. Curie's browser profile is retained.")
    except BrowserAccessError as exc:
        raise SystemExit(f"Login not enrolled: {exc.code}") from None


if __name__ == "__main__":
    main()
