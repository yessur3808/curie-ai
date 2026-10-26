"""Run on Curie's server desktop: python -m scripts.x_browser_login --owner-id ID --handle HANDLE."""

import argparse
import asyncio

from dotenv import load_dotenv


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--owner-id", required=True)
    parser.add_argument("--handle", required=True)
    args = parser.parse_args()
    load_dotenv()
    from connectors.x_browser import BrowserAccessError, enroll_session

    try:
        asyncio.run(enroll_session(args.owner_id, args.handle.removeprefix("@")))
    except KeyboardInterrupt:
        print("\nLogin cancelled. Curie's browser profile is retained.")
    except BrowserAccessError as exc:
        raise SystemExit(f"Login not enrolled: {exc.code}") from None


if __name__ == "__main__":
    main()
